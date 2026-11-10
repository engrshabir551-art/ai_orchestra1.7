"""Vault ledger and the 'Vault Payment Plt' release workflow.

Rules that keep the numbers honest:
* The ledger only ever holds money that was recorded as a deposit. Nothing creates balance on its own.
* There are two balances. The RESERVE holds funds you record as received. The VAULT is the spending balance: it is funded only
  by a manual move from the reserve, and releases (payments) can only draw on the vault.
* The reserve volume (ceiling) is a cap on total holdings (reserve + vault). It does not add money.
* The reserve ceiling is a cap on the balance you allow the vault to hold. It does not add money.
* In LIVE mode a deposit counts as available only after you mark it verified against your bank statement.
* TEST mode keeps separate books and is always labelled, so test figures can never be mistaken for real funds.
"""
import time

from . import authz, db, security
from .money import fmt, to_cents

REFERENCE = "Vault Payment Plt"
WALLET = "VAULT"
RESERVE = "RESERVE"
DEFAULT_CEILING = 0  # starts at AUD 0.00. You set the ceiling (for example 1,000,000,000) before recording deposits


def mode() -> str:
    return db.get_setting("mode", "TEST")


def ceiling_cents() -> int:
    return int(db.get_setting("ceiling_cents", str(DEFAULT_CEILING)))


def set_ceiling(cents: int, actor: str):
    authz.require(actor, "change_volume")
    if cents < 0:
        raise ValueError("Ceiling cannot be negative.")
    old = ceiling_cents()
    db.set_setting("ceiling_cents", cents)
    security.audit(actor, "CEILING_CHANGED", f"{old} -> {cents}")


def _sum(m, wallet, verified_only=False):
    cond = " AND (amount_cents<0 OR verified=1)" if verified_only else ""
    return db.q1(f"SELECT COALESCE(SUM(amount_cents),0) s FROM ledger WHERE mode=? AND wallet=?{cond}", (m, wallet))["s"]


def balance_cents(m=None) -> int:
    """Vault balance (the spending balance)."""
    return _sum(m or mode(), WALLET)


def available_cents(m=None) -> int:
    """Vault funds that can be released. Moves from the reserve are verified, so this is the vault balance except old unverified entries."""
    return _sum(m or mode(), WALLET, True)


def reserve_cents(m=None) -> int:
    """Reserve balance: funds recorded as received and not yet moved to the vault."""
    return _sum(m or mode(), RESERVE)


def reserve_available_cents(m=None) -> int:
    """Reserve funds that can be moved to the vault: verified deposits only."""
    return _sum(m or mode(), RESERVE, True)


def total_cents(m=None) -> int:
    m = m or mode()
    return reserve_cents(m) + balance_cents(m)


def move_to_vault(amount_cents: int, actor: str, note: str = "", bank_ref: str = ""):
    """Manual move: reserve balance -> vault balance. Bookkeeping inside the app; see README for matching it at your bank."""
    authz.require(actor, "move_funds")
    return _move(amount_cents, actor, note, bank_ref, to_vault=True)


def move_to_reserve(amount_cents: int, actor: str, note: str = "", bank_ref: str = ""):
    """Manual move: unused vault balance -> back to the reserve."""
    authz.require(actor, "move_funds")
    return _move(amount_cents, actor, note, bank_ref, to_vault=False)


def _move(amount_cents, actor, note, bank_ref, to_vault):
    if amount_cents <= 0:
        raise ValueError("Amount must be greater than zero.")
    m = mode()
    src, dst = (RESERVE, WALLET) if to_vault else (WALLET, RESERVE)
    k_out, k_in = ("TO_VAULT", "FROM_RESERVE") if to_vault else ("TO_RESERVE", "FROM_VAULT")
    label = "reserve" if to_vault else "vault"
    ts = time.time()
    ref = (bank_ref or "").strip() or REFERENCE
    with db.conn() as c:  # one transaction: both entries or neither
        have = c.execute("SELECT COALESCE(SUM(amount_cents),0) s FROM ledger WHERE mode=? AND wallet=? AND (amount_cents<0 OR verified=1)",
                         (m, src)).fetchone()["s"]
        if amount_cents > have:
            raise ValueError(f"Not enough verified {label} balance. Available: {fmt(have)}."
                             + (" Verify your deposits first." if to_vault and reserve_cents(m) > have else ""))
        memo = (note or "").strip() or ("Moved from reserve to vault" if to_vault else "Returned from vault to reserve")
        c.execute("INSERT INTO ledger(ts,mode,wallet,kind,amount_cents,ref,memo,verified) VALUES(?,?,?,?,?,?,?,1)", (ts, m, src, k_out, -amount_cents, ref, memo))
        c.execute("INSERT INTO ledger(ts,mode,wallet,kind,amount_cents,ref,memo,verified) VALUES(?,?,?,?,?,?,?,1)", (ts, m, dst, k_in, amount_cents, ref, memo))
    security.audit(actor, "FUNDS_MOVED", f"{m} {fmt(amount_cents)} {'reserve -> vault' if to_vault else 'vault -> reserve'} {memo}")


def unverified_cents(m=None) -> int:
    m = m or mode()
    return (reserve_cents(m) - reserve_available_cents(m)) + (balance_cents(m) - available_cents(m))


def record_deposit(amount_cents: int, source_ref: str, memo: str, actor: str, verified: bool = None):
    """Record real money that has arrived in the reserve account. source_ref is the bank/provider reference."""
    authz.require(actor, "record_deposit")
    if amount_cents <= 0:
        raise ValueError("Deposit must be greater than zero.")
    m = mode()
    if ceiling_cents() <= 0:
        raise ValueError("Set the reserve ceiling first (Vault Terminal > Reserve ceiling tab).")
    if not source_ref.strip():
        raise ValueError("A bank or provider reference is required for every deposit.")
    if total_cents(m) + amount_cents > ceiling_cents():
        raise ValueError(f"Deposit would take the vault above the reserve ceiling of {fmt(ceiling_cents())}. Raise the ceiling first.")
    if verified is None:
        verified = (m == "TEST")
    lid = db.x(
        "INSERT INTO ledger(ts,mode,wallet,kind,amount_cents,ref,memo,verified) VALUES(?,?,?,?,?,?,?,?)",
        (time.time(), m, RESERVE, "DEPOSIT", amount_cents, source_ref.strip(), memo, 1 if verified else 0))
    security.audit(actor, "DEPOSIT_RECORDED", f"{m} {fmt(amount_cents)} ref={source_ref} verified={verified}")
    return lid


def add_reserve_balance(amount_cents: int, actor: str, note: str = "", bank_ref: str = "", *, move_to_vault_too: bool = False,
                        raise_ceiling: bool = False):
    """Manual entry of the funds you hold, owner-confirmed. Optionally raise the reserve volume to fit and move the same amount to the vault.

    This is bookkeeping of money that is in your own bank or provider account. It does not create money."""
    authz.require(actor, "record_deposit")
    if amount_cents <= 0:
        raise ValueError("Enter an amount greater than zero.")
    m = mode()
    need = total_cents(m) + amount_cents
    if need > ceiling_cents():
        if not raise_ceiling:
            raise ValueError(f"That would take the holdings above the reserve volume of {fmt(ceiling_cents())}. "
                             "Tick 'raise the reserve volume to fit' or increase the volume first.")
        set_ceiling(need, actor)
    ref = (bank_ref or "").strip() or f"MANUAL-{int(time.time())}"
    memo = ((note or "").strip() + " [manual entry, owner confirmed funds are held]").strip()
    lid = record_deposit(amount_cents, ref, memo, actor, verified=True)
    if move_to_vault_too:
        move_to_vault(amount_cents, actor, note or "Funded vault from reserve", ref)
    return lid


def verify_deposit(ledger_id: int, actor: str):
    authz.require(actor, "verify_deposit")
    row = db.q1("SELECT * FROM ledger WHERE id=? AND kind='DEPOSIT'", (ledger_id,))
    if not row:
        raise ValueError("Deposit not found.")
    db.x("UPDATE ledger SET verified=1 WHERE id=?", (ledger_id,))
    security.audit(actor, "DEPOSIT_VERIFIED", f"id={ledger_id} {fmt(row['amount_cents'])}")


# ---------- releases ----------
CATEGORIES = ["Bills", "Housing", "Living", "Family", "Investment", "Business", "Tax", "Gold", "Other"]


def policy() -> dict:
    g = db.get_setting
    return {
        "max_release_cents": int(g("max_release_cents", "0")),
        "daily_limit_cents": int(g("daily_limit_cents", "0")),
        "monthly_limit_cents": int(g("monthly_limit_cents", "0")),
        "large_release_cents": int(g("large_release_cents", "10000000")),
        "confirm_cents": int(g("confirm_cents", "1000000")),      # type the amount to confirm at or above (AUD 10,000 default)
        "dual_control_cents": int(g("dual_control_cents", "0")),  # a second approver at or above
        "cooling_cents": int(g("cooling_cents", "0")),            # cooling-off applies at or above (0 = every release if minutes > 0)
        "cooling_minutes": int(g("cooling_minutes", "0")),
        "payee_mode": g("payee_mode", "open"),
    }


def requirements(r: dict, actor: str) -> dict:
    """What approving this release needs, in plain terms, for the UI and for enforcement."""
    p = policy()
    amt = r["amount_cents"]
    approvers = db.q1("SELECT COUNT(*) n FROM users WHERE role IN('admin','approver')")["n"]
    release_at = 0.0
    if p["cooling_minutes"] > 0 and (p["cooling_cents"] == 0 or amt >= p["cooling_cents"]):
        release_at = r["ts"] + p["cooling_minutes"] * 60
    return {
        "typed_amount": p["confirm_cents"] > 0 and amt >= p["confirm_cents"],
        "second_approver": p["dual_control_cents"] > 0 and amt >= p["dual_control_cents"] and approvers > 1,
        "cooling_until": release_at if release_at > time.time() else 0.0,
        "totp": bool(security.totp_secret_of(actor)),
    }


def create_release(payee_name, bsb, account, payid, amount_cents, memo, actor, confirmed_warnings=False, *,
                   method=None, biller_code="", category="", crn="", invoice_ref=""):
    """Draft a release. For BPAY pass the biller code in biller_code and the reference number in account."""
    authz.require(actor, "draft")
    m = mode()
    if (method or security.infer_method(bsb, account, payid, biller_code)) == "BPAY" and not crn:
        crn, account = account, ""  # BPAY: the reference number may arrive in `account`
    errors, warnings = security.check_release(payee_name, bsb, account, payid, amount_cents, available_cents(m), m,
                                              method=method, biller_code=biller_code, crn=crn)
    if errors:
        raise ValueError(" ".join(errors))
    if warnings and not confirmed_warnings:
        raise ValueError("CONFIRM: " + " ".join(warnings))
    d = security.normalise_destination(method, bsb, account, payid, biller_code, crn)
    rid = db.x(
        "INSERT INTO releases(ts,mode,status,payee_name,bsb,account,payid,amount_cents,reference,memo,created_by,method,biller_code,category,crn,invoice_ref) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (time.time(), m, "DRAFT", payee_name.strip(), d["bsb"], d["account"], d["payid"], amount_cents, REFERENCE, memo, actor,
         d["method"], d["biller_code"], category, d["crn"], (invoice_ref or "").strip()[:60]))
    security.audit(actor, "RELEASE_DRAFTED", f"#{rid} {m} {d['method']} {fmt(amount_cents)} to {payee_name.strip()}")
    return rid


def approve_release(release_id: int, actor: str, *, typed_amount=None, totp_code=None):
    """Commit funds. Enforces the approval policy: role, typed amount, cooling-off, second approver and authenticator code."""
    r = db.q1("SELECT * FROM releases WHERE id=?", (release_id,))
    if not r or r["status"] != "DRAFT":
        raise ValueError("Only a draft release can be approved.")
    if actor.startswith("agent:"):
        raise authz.NotAuthorised("Agents can never approve releases.")
    authz.require(actor, "approve")
    m = r["mode"]
    if m != mode():
        raise ValueError(f"This release was drafted in {m} mode. Switch back to {m} mode or cancel it.")
    req = requirements(r, actor)
    if req["second_approver"] and r["created_by"] == actor:
        raise ValueError("Dual control: a different approver must approve releases at or above the dual-control amount.")
    if req["cooling_until"]:
        raise ValueError("Cooling-off: this release can be approved after " + time.strftime("%H:%M", time.localtime(req["cooling_until"])) + ".")
    if req["typed_amount"]:
        try:
            ok = to_cents(typed_amount or "") == r["amount_cents"]
        except ValueError:
            ok = False
        if not ok:
            raise ValueError("Type the exact release amount to confirm.")
    if req["totp"] and not security.totp_check(actor, totp_code or ""):
        raise ValueError("Authenticator code is missing, wrong or already used.")
    if r["amount_cents"] > available_cents(m):
        raise ValueError("Insufficient available verified balance.")
    ts = time.time()
    db.x("INSERT INTO ledger(ts,mode,wallet,kind,amount_cents,ref,release_id,memo,verified) VALUES(?,?,?,?,?,?,?,?,1)",
         (ts, m, WALLET, "RELEASE", -r["amount_cents"], REFERENCE, release_id, f"{REFERENCE}: {r['payee_name']}"))
    db.x("UPDATE releases SET status='APPROVED', approved_by=?, approved_ts=? WHERE id=?", (actor, ts, release_id))
    d = {k: r[k] or "" for k in ("bsb", "account", "payid", "biller_code", "crn")}
    d["method"] = r["method"] or "BANK"
    if not security.payee_known(d):
        db.x("INSERT INTO payees(name,bsb,account,payid,first_seen,method,biller_code,crn,category) VALUES(?,?,?,?,?,?,?,?,?)",
             (r["payee_name"], d["bsb"], d["account"], d["payid"], ts, d["method"], d["biller_code"], d["crn"], r.get("category") or ""))
    security.audit(actor, "RELEASE_APPROVED", f"#{release_id} {m} {d['method']} {fmt(r['amount_cents'])} to {r['payee_name']}")


def add_payee(name, bsb, account, payid, actor, *, method=None, biller_code="", crn="", category=""):
    authz.require(actor, "manage_payees")
    if (method or security.infer_method(bsb, account, payid, biller_code)) == "BPAY" and not crn:
        crn, account = account, ""
    d = security.normalise_destination(method, bsb, account, payid, biller_code, crn)
    if not name or len(name.strip()) < 2:
        raise ValueError("Payee name is required.")
    if security.payee_known(d):
        raise ValueError("That payee is already saved.")
    db.x("INSERT INTO payees(name,bsb,account,payid,first_seen,method,biller_code,crn,category) VALUES(?,?,?,?,?,?,?,?,?)",
         (name.strip(), d["bsb"], d["account"], d["payid"], time.time(), d["method"], d["biller_code"], d["crn"], category))
    security.audit(actor, "PAYEE_ADDED", f"{d['method']} {name.strip()}")


def destination_text(r: dict) -> str:
    m = r.get("method") or "BANK"
    if m == "BANK":
        return f"BSB {r['bsb']} · Account {r['account']}"
    if m == "BPAY":
        return f"BPAY biller {r['biller_code']} · Ref {r.get('crn') or r['account']}"
    return f"PayID {r['payid']}"


def _check_time(ts, r, label, floor):
    """A recorded time must be real: not in the future and not before the step that came first."""
    if ts is None:
        return time.time()
    if ts > time.time() + 120:
        raise ValueError(f"{label} cannot be in the future.")
    if floor and ts < floor - 60:
        raise ValueError(f"{label} cannot be before the earlier step ({time.strftime('%Y-%m-%d %H:%M', time.localtime(floor))}).")
    return ts


def mark_exported(release_ids, channel: str, batch_id: str, actor: str, *, sent_ts=None):
    """Record that releases left our account (file uploaded or sent from the bank). sent_ts = the real time, default now."""
    authz.require(actor, "export")
    for rid in release_ids:
        r = db.q1("SELECT * FROM releases WHERE id=?", (rid,))
        if not r or r["status"] != "APPROVED":
            continue
        t = _check_time(sent_ts, r, "The sent time", r["approved_ts"] or r["ts"])
        db.x("UPDATE releases SET status='EXPORTED', channel=?, batch_id=?, sent_ts=? WHERE id=? AND status='APPROVED'", (channel, batch_id, t, rid))
    security.audit(actor, "RELEASES_EXPORTED", f"{channel} batch={batch_id} ids={list(release_ids)}")


def mark_settled(release_id: int, actor: str, *, received_ts=None, bank_ref: str = "", sent_ts=None):
    """Record that the recipient received the funds. received_ts = the real time (from the bank statement), default now."""
    authz.require(actor, "settle")
    r = db.q1("SELECT * FROM releases WHERE id=?", (release_id,))
    if not r or r["status"] not in ("APPROVED", "EXPORTED"):
        raise ValueError("Only an approved or sent release can be marked as received.")
    sent = r["sent_ts"]
    if sent_ts is not None and r["status"] == "APPROVED":
        sent = _check_time(sent_ts, r, "The sent time", r["approved_ts"] or r["ts"])
    t = _check_time(received_ts, r, "The received time", sent or r["approved_ts"] or r["ts"])
    db.x("UPDATE releases SET status='SETTLED', settled_ts=?, sent_ts=COALESCE(?, sent_ts), bank_ref=COALESCE(NULLIF(?, ''), bank_ref) WHERE id=?",
         (t, sent, (bank_ref or "").strip()[:60], release_id))
    security.audit(actor, "RELEASE_SETTLED", f"#{release_id} received {time.strftime('%Y-%m-%d %H:%M', time.localtime(t))} bank_ref={bank_ref}")


def cancel_release(release_id: int, actor: str):
    authz.require(actor, "cancel")
    r = db.q1("SELECT * FROM releases WHERE id=?", (release_id,))
    if not r or r["status"] in ("SETTLED", "CANCELLED"):
        raise ValueError("This release cannot be cancelled.")
    if r["status"] in ("APPROVED", "EXPORTED"):
        db.x("INSERT INTO ledger(ts,mode,wallet,kind,amount_cents,ref,release_id,memo,verified) VALUES(?,?,?,?,?,?,?,?,1)",
             (time.time(), r["mode"], WALLET, "REVERSAL", r["amount_cents"], REFERENCE, release_id, f"Reversal of release #{release_id}"))
    db.x("UPDATE releases SET status='CANCELLED' WHERE id=?", (release_id,))
    security.audit(actor, "RELEASE_CANCELLED", f"#{release_id} was {r['status']}")


def releases(status=None, m=None):
    m = m or mode()
    if status:
        return db.q("SELECT * FROM releases WHERE mode=? AND status=? ORDER BY id DESC", (m, status))
    return db.q("SELECT * FROM releases WHERE mode=? ORDER BY id DESC", (m,))


def ledger_rows(m=None, limit=500):
    m = m or mode()
    return db.q("SELECT * FROM ledger WHERE mode=? ORDER BY id DESC LIMIT ?", (m, limit))


def summary() -> dict:
    m = mode()
    return {
        "mode": m,
        "ceiling_cents": ceiling_cents(),
        "balance_cents": balance_cents(m),  # vault balance
        "vault_cents": balance_cents(m),
        "reserve_cents": reserve_cents(m),
        "reserve_available_cents": reserve_available_cents(m),
        "total_cents": total_cents(m),
        "available_cents": available_cents(m),
        "unverified_cents": unverified_cents(m),
        "pending_drafts": len(releases("DRAFT", m)),
        "headroom_cents": max(0, ceiling_cents() - total_cents(m)),
    }


STEPS = ["Drafted", "Approved (funds committed)", "Sent to bank", "Paid"]


def step_index(status: str) -> int:
    return {"DRAFT": 0, "APPROVED": 1, "EXPORTED": 2, "SETTLED": 3}.get(status, -1)


def action_queue() -> dict:
    """Everything waiting on you, in the order a transfer moves: approve -> send -> confirm paid, plus deposits to verify."""
    m = mode()
    return {
        "approve": releases("DRAFT", m),
        "send": releases("APPROVED", m),
        "confirm": releases("EXPORTED", m),
        "verify": [r for r in ledger_rows(m) if r["kind"] == "DEPOSIT" and not r["verified"]],
    }
