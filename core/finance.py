"""Read-only finance reporting for the Vault E-Terminal: flows, breakdowns, statements, budgets, reconciliation."""
import datetime as dt
import time

from . import db, ledger, security
from .money import fmt


def _day(ts):
    return dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


def _since(days):
    return time.time() - days * 86400


def daily_flows(days=30, m=None):
    """Per day: funds in, released out, running balance. Zero-filled so charts never have gaps."""
    m = m or ledger.mode()
    rows = db.q("SELECT ts,amount_cents,kind,verified FROM ledger WHERE mode=? AND wallet=? ORDER BY ts", (m, ledger.WALLET))
    start = dt.date.today() - dt.timedelta(days=days - 1)
    run = sum(r["amount_cents"] for r in rows if _day(r["ts"]) < start.isoformat() and r["verified"])
    by = {}
    for r in rows:
        d = _day(r["ts"])
        if d < start.isoformat():
            continue
        e = by.setdefault(d, [0, 0])
        if r["amount_cents"] > 0:
            e[0] += r["amount_cents"]
        else:
            e[1] += -r["amount_cents"]
    out = []
    for i in range(days):
        d = (start + dt.timedelta(days=i)).isoformat()
        i_, o_ = by.get(d, [0, 0])
        run += i_ - o_
        out.append({"date": d, "in_cents": i_, "out_cents": o_, "balance_cents": run})
    return out


def _releases(days, m, statuses=("APPROVED", "EXPORTED", "SETTLED")):
    ph = ",".join("?" * len(statuses))
    return db.q(f"SELECT * FROM releases WHERE mode=? AND status IN ({ph}) AND COALESCE(approved_ts,ts)>=?",
                (m, *statuses, _since(days)))


def period_summary(days=30, m=None):
    m = m or ledger.mode()
    rel = _releases(days, m)
    dep = db.q("SELECT amount_cents FROM ledger WHERE mode=? AND kind='DEPOSIT' AND ts>=?", (m, _since(days)))
    out = sum(r["amount_cents"] for r in rel)
    return {"in_cents": sum(d["amount_cents"] for d in dep), "out_cents": out, "count": len(rel),
            "avg_cents": out // len(rel) if rel else 0, "largest_cents": max([r["amount_cents"] for r in rel], default=0)}


def by_method(days=30, m=None):
    agg = {}
    for r in _releases(days, m or ledger.mode()):
        k = security.METHODS.get(r.get("method") or "BANK", r.get("method") or "BANK")
        e = agg.setdefault(k, {"method": k, "count": 0, "amount_cents": 0})
        e["count"] += 1; e["amount_cents"] += r["amount_cents"]
    return sorted(agg.values(), key=lambda e: -e["amount_cents"])


def top_payees(days=90, m=None, n=10):
    agg = {}
    for r in _releases(days, m or ledger.mode()):
        e = agg.setdefault(r["payee_name"], {"payee": r["payee_name"], "count": 0, "amount_cents": 0})
        e["count"] += 1; e["amount_cents"] += r["amount_cents"]
    return sorted(agg.values(), key=lambda e: -e["amount_cents"])[:n]


def by_category(days=30, m=None):
    agg = {}
    for r in _releases(days, m or ledger.mode()):
        k = r.get("category") or "Uncategorised"
        agg[k] = agg.get(k, 0) + r["amount_cents"]
    return [{"category": k, "amount_cents": v} for k, v in sorted(agg.items(), key=lambda kv: -kv[1])]


def status_counts(m=None):
    m = m or ledger.mode()
    out = {s: 0 for s in ("DRAFT", "APPROVED", "EXPORTED", "SETTLED", "CANCELLED")}
    for r in db.q("SELECT status, COUNT(*) n, COALESCE(SUM(amount_cents),0) c FROM releases WHERE mode=? GROUP BY status", (m,)):
        out[r["status"]] = r["n"]
    return out


def statement(days=30, m=None):
    """Bank-style statement with a running balance (verified entries only move the balance)."""
    m = m or ledger.mode()
    rows = db.q("SELECT * FROM ledger WHERE mode=? ORDER BY ts, id", (m,))
    bal, out = 0, []  # running TOTAL holdings (reserve + vault); moves between the two net to zero
    for r in rows:
        if r["verified"]:
            bal += r["amount_cents"]
        out.append({"Wallet": r["wallet"].title(), "Date": dt.datetime.fromtimestamp(r["ts"]).strftime("%Y-%m-%d %H:%M"), "Type": r["kind"], "Reference": r["ref"] or "",
                    "Detail": r["memo"] or "", "Credit": r["amount_cents"] if r["amount_cents"] > 0 else 0,
                    "Debit": -r["amount_cents"] if r["amount_cents"] < 0 else 0, "Balance": bal, "Verified": bool(r["verified"])})
    cutoff = dt.datetime.fromtimestamp(_since(days)).strftime("%Y-%m-%d")
    return [e for e in out if e["Date"][:10] >= cutoff]


def payee_ledger(m=None):
    agg = {}
    for r in db.q("SELECT * FROM releases WHERE mode=? AND status!='CANCELLED'", (m or ledger.mode(),)):
        e = agg.setdefault(r["payee_name"], {"Payee": r["payee_name"], "Payments": 0, "Paid": 0, "Pending": 0})
        e["Payments"] += 1
        e["Paid" if r["status"] == "SETTLED" else "Pending"] += r["amount_cents"]
    return sorted(agg.values(), key=lambda e: -(e["Paid"] + e["Pending"]))


def reconcile(m=None):
    """Compare the book balance with what the bank says. Unverified deposits and unsettled releases explain differences."""
    m = m or ledger.mode()
    book = ledger.total_cents(m)  # reserve + vault
    unverified = sum(r["amount_cents"] for r in db.q("SELECT amount_cents FROM ledger WHERE mode=? AND kind='DEPOSIT' AND verified=0", (m,)))
    in_flight = sum(r["amount_cents"] for r in db.q("SELECT amount_cents FROM releases WHERE mode=? AND status IN ('APPROVED','EXPORTED')", (m,)))
    return {"reserve_cents": ledger.reserve_cents(m), "vault_cents": ledger.balance_cents(m), "book_cents": book, "unverified_cents": unverified, "in_flight_cents": in_flight,
            "expected_bank_cents": book + in_flight}  # approved/exported money has left the book but not yet the bank


# ---- budgets ----
def set_budget(category, monthly_cents, actor):
    from . import authz
    authz.require(actor, "manage_budgets")
    if monthly_cents < 0:
        raise ValueError("Budget cannot be negative.")
    db.x("INSERT INTO budgets(category,monthly_cents) VALUES(?,?) ON CONFLICT(category) DO UPDATE SET monthly_cents=excluded.monthly_cents",
         (category, int(monthly_cents)))
    security.audit(actor, "BUDGET_SET", f"{category} {fmt(int(monthly_cents))}")


def budget_usage(m=None):
    used = {c["category"]: c["amount_cents"] for c in by_category(30, m)}
    rows = []
    for b in db.q("SELECT * FROM budgets ORDER BY category"):
        u = used.get(b["category"], 0)
        rows.append({"category": b["category"], "budget_cents": b["monthly_cents"], "used_cents": u,
                     "pct": (u / b["monthly_cents"] * 100) if b["monthly_cents"] else 0.0})
    return rows


def activity(n=20):
    return db.q("SELECT ts,actor,action,detail FROM audit ORDER BY id DESC LIMIT ?", (n,))
