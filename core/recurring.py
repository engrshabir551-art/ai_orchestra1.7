"""Scheduled (repeat) payments.

A schedule creates a DRAFT release when each payment falls due. It never approves or sends anything: a person still approves every
payment under the normal authorisation policy (typed amount, second approver, cooling-off, authenticator code, limits).
If the vault balance is too low or a rule blocks the draft, nothing is created and the reason is shown on the schedule.
If the app was off for several periods, one draft is created for the current due date and the missed ones are skipped (never a flood)."""
import calendar
import datetime as dt
import time

from . import authz, clock, db, ledger, security
from .money import fmt

FREQUENCIES = {"WEEKLY": "Every week", "FORTNIGHTLY": "Every 2 weeks", "MONTHLY": "Every month", "QUARTERLY": "Every 3 months", "YEARLY": "Every year"}
ACTOR = "agent:Scheduler"


def today():
    return dt.datetime.now(clock.tz()[0]).date()


def _add_months(d, n):
    m = d.month - 1 + n
    y, mo = d.year + m // 12, m % 12 + 1
    return dt.date(y, mo, min(d.day, calendar.monthrange(y, mo)[1]))


def occurrence(start, frequency, k):
    """The k-th payment date (k=0 is the start date). Monthly dates keep the start day, clamped to short months."""
    if frequency == "WEEKLY":
        return start + dt.timedelta(weeks=k)
    if frequency == "FORTNIGHTLY":
        return start + dt.timedelta(weeks=2 * k)
    return _add_months(start, {"MONTHLY": 1, "QUARTERLY": 3, "YEARLY": 12}[frequency] * k)


def _d(s):
    return dt.date.fromisoformat(s) if s else None


def upcoming(rec, n=5):
    start, out, k = _d(rec["start_date"]), [], rec["k"]
    end = _d(rec["end_date"])
    left = (rec["max_count"] - rec["drafted"]) if rec["max_count"] else n
    while len(out) < min(n, left):
        d = occurrence(start, rec["frequency"], k)
        if end and d > end:
            break
        out.append(d); k += 1
    return out


def create(payee_name, method, bsb, account, payid, biller_code, crn, amount_cents, purpose, category, invoice_ref,
           frequency, start_date, actor, *, end_date=None, max_count=0):
    authz.require(actor, "schedule")
    if frequency not in FREQUENCIES:
        raise ValueError("Choose how often the payment repeats.")
    if amount_cents <= 0:
        raise ValueError("Amount must be greater than zero.")
    if not (payee_name or "").strip():
        raise ValueError("Enter the payee's account name.")
    if start_date < today():
        raise ValueError("The first payment date cannot be in the past.")
    if end_date and end_date < start_date:
        raise ValueError("The end date is before the first payment date.")
    if max_count < 0:
        raise ValueError("Number of payments cannot be negative.")
    method = method or security.infer_method(bsb, account, payid, biller_code)
    if method == "BPAY" and not crn:
        crn, account = account, ""
    d = security.normalise_destination(method, bsb, account, payid, biller_code, crn)  # same format checks as a manual release
    rid = db.x("INSERT INTO recurring(mode,payee_name,method,bsb,account,payid,biller_code,crn,amount_cents,purpose,category,invoice_ref,frequency,"
               "start_date,k,next_due,end_date,max_count,created_by,created_ts) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,?,?,?,?,?)",
               (ledger.mode(), payee_name.strip(), d["method"], d["bsb"], d["account"], d["payid"], d["biller_code"], d["crn"], amount_cents,
                (purpose or "").strip(), category, (invoice_ref or "").strip(), frequency, start_date.isoformat(), start_date.isoformat(),
                end_date.isoformat() if end_date else "", int(max_count), actor, time.time()))
    security.audit(actor, "SCHEDULE_CREATED", f"#{rid} {payee_name} {fmt(amount_cents)} {frequency} from {start_date}")
    return rid


def set_status(rec_id, status, actor):
    authz.require(actor, "schedule")
    r = db.q1("SELECT * FROM recurring WHERE id=?", (rec_id,))
    if not r or status not in ("ACTIVE", "PAUSED", "ENDED"):
        raise ValueError("Unknown schedule.")
    if r["status"] == "ENDED":
        raise ValueError("This schedule has ended.")
    k, nd = r["k"], r["next_due"]
    if status == "ACTIVE" and r["status"] == "PAUSED":  # resume without catching up on what was missed
        start = _d(r["start_date"])
        while occurrence(start, r["frequency"], k) < today():
            k += 1
        nd = occurrence(start, r["frequency"], k).isoformat()
    db.x("UPDATE recurring SET status=?, k=?, next_due=? WHERE id=?", (status, k, nd, rec_id))
    security.audit(actor, "SCHEDULE_" + status, f"#{rec_id}")


def due(m=None):
    t = today().isoformat()
    return db.q("SELECT * FROM recurring WHERE mode=? AND status='ACTIVE' AND next_due<=? ORDER BY next_due", (m or ledger.mode(), t))


def generate_due():
    """Turn every due schedule into a draft release (safe to call often and from several places; each due date is claimed once)."""
    made = []
    for rec in due():
        start, end = _d(rec["start_date"]), _d(rec["end_date"])
        k_new = rec["k"] + 1
        while occurrence(start, rec["frequency"], k_new) <= today():  # skip missed periods rather than flood
            k_new += 1
        nd = occurrence(start, rec["frequency"], k_new)
        drafted = rec["drafted"] + 1
        ended = bool((end and nd > end) or (rec["max_count"] and drafted >= rec["max_count"]))
        with db.conn() as c:  # claim this due date atomically so two callers cannot both draft it
            n = c.execute("UPDATE recurring SET k=?, next_due=?, drafted=?, status=? WHERE id=? AND k=? AND status='ACTIVE'",
                          (k_new, nd.isoformat(), drafted, "ENDED" if ended else "ACTIVE", rec["id"], rec["k"])).rowcount
        if n != 1:
            continue
        try:
            rid = ledger.create_release(rec["payee_name"], rec["bsb"], rec["account"], rec["payid"], rec["amount_cents"], rec["purpose"], ACTOR,
                                        confirmed_warnings=True, method=rec["method"], biller_code=rec["biller_code"], category=rec["category"],
                                        crn=rec["crn"], invoice_ref=rec["invoice_ref"])
        except ValueError as e:  # blocked (for example not enough in the vault): undo the claim and show why
            db.x("UPDATE recurring SET k=?, next_due=?, drafted=?, status='ACTIVE', last_error=? WHERE id=?",
                 (rec["k"], rec["next_due"], rec["drafted"], f"{time.strftime('%Y-%m-%d %H:%M')}: {e}"[:300], rec["id"]))
            continue
        skipped = k_new - rec["k"] - 1
        db.x("UPDATE releases SET schedule_id=? WHERE id=?", (rec["id"], rid))
        db.x("UPDATE recurring SET last_release_id=?, last_error='' WHERE id=?", (rid, rec["id"]))
        security.audit(ACTOR, "SCHEDULE_DRAFTED", f"schedule #{rec['id']} -> release #{rid}" + (f" ({skipped} missed period(s) skipped)" if skipped else ""))
        made.append(rid)
    return made


def all_schedules(m=None):
    return db.q("SELECT * FROM recurring WHERE mode=? ORDER BY CASE status WHEN 'ACTIVE' THEN 0 WHEN 'PAUSED' THEN 1 ELSE 2 END, next_due", (m or ledger.mode(),))
