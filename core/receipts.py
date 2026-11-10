"""Payment receipts and tracking: the timeline of a payment from acceptance to the recipient receiving the funds."""
import html
import time

from . import clock, db, ledger, security
from .money import fmt

# How long each method normally takes to arrive. Used only to flag a payment as "taking longer than usual".
TYPICAL_SECONDS = {"PAYID_EMAIL": 30 * 60, "PAYID_MOBILE": 30 * 60, "PAYID_ABN": 30 * 60, "BANK": 2 * 86400, "BPAY": 3 * 86400}
TYPICAL_TEXT = {"PAYID_EMAIL": "usually within minutes", "PAYID_MOBILE": "usually within minutes", "PAYID_ABN": "usually within minutes",
                "BANK": "usually the next business day", "BPAY": "usually 1 to 2 business days"}


def number(r) -> str:
    return f"VPP-{'L' if r['mode'] == 'LIVE' else 'T'}-{int(r['id']):06d}"


def state(r) -> str:
    return {"DRAFT": "Draft, not approved", "APPROVED": "Accepted, not yet sent", "EXPORTED": "Sent, awaiting receipt",
            "SETTLED": "Received by recipient", "CANCELLED": "Cancelled"}.get(r["status"], r["status"])


def timeline(r) -> dict:
    created, accepted, sent, received = r["ts"], r.get("approved_ts"), r.get("sent_ts"), r.get("settled_ts")
    d = lambda a, b: (b - a) if (a and b) else None
    now = time.time()
    out = {"created": created, "accepted": accepted, "sent": sent, "received": received,
           "accept_to_sent": d(accepted, sent), "sent_to_received": d(sent, received), "accept_to_received": d(accepted, received),
           "waiting": None, "late": False}
    if r["status"] == "EXPORTED" and sent:
        out["waiting"] = now - sent
    elif r["status"] == "APPROVED" and accepted:
        out["waiting"] = now - accepted
    if out["waiting"] is not None and r["status"] in ("EXPORTED", "APPROVED"):
        out["late"] = out["waiting"] > TYPICAL_SECONDS.get(r.get("method") or "BANK", 2 * 86400)
    return out


def destination(r) -> str:
    return ledger.destination_text(r)


def _rows(r):
    t = timeline(r)
    who = lambda u: f" by {u}" if u else ""
    z = lambda x: clock.fmt(x, True) if x else "Not recorded"
    return [
        ("Receipt number", number(r)),
        ("Status", state(r)),
        ("Payee", r["payee_name"]),
        ("Payment method", security.METHODS.get(r.get("method") or "BANK", "")),
        ("Sent to", destination(r)),
        ("Amount", fmt(r["amount_cents"])),
        ("Currency", "AUD (Australian dollars)"),
        ("Payment reference", r["reference"]),
        ("Payment purpose", r.get("memo") or "-"),
        ("Category", r.get("category") or "-"),
        ("Invoice reference", r.get("invoice_ref") or "-"),
        ("Bank / provider reference", r.get("bank_ref") or r.get("batch_id") or "-"),
        ("Sent via", r.get("channel") or "-"),
        ("1. Instruction created", z(t["created"]) + who(r.get("created_by"))),
        ("2. Accepted (approved)", z(t["accepted"]) + who(r.get("approved_by"))),
        ("3. Left our account (sent)", z(t["sent"])),
        ("4. Received by recipient", z(t["received"])),
        ("Time from accepted to sent", clock.duration(t["accept_to_sent"]) or "-"),
        ("Time from sent to received", clock.duration(t["sent_to_received"]) or "-"),
        ("Total, accepted to received", clock.duration(t["accept_to_received"]) or "-"),
    ]


def text(r) -> str:
    _, zone = clock.tz()
    w = max(len(k) for k, _ in _rows(r))
    lines = ["VAULT PAYMENT PLT, PAYMENT RECEIPT", "=" * 60] + [f"{k.ljust(w)} : {v}" for k, v in _rows(r)]
    lines += ["", f"Times are shown in {zone}.", "This receipt records the payment instruction in your Vault records. Confirm the credit on your bank statement."]
    return "\n".join(lines) + "\n"


def html_doc(r) -> str:
    _, zone = clock.tz()
    rows = "".join(f"<tr><th>{html.escape(k)}</th><td>{html.escape(str(v))}</td></tr>" for k, v in _rows(r))
    tag = "TEST BOOKS: practice record" if r["mode"] != "LIVE" else "LIVE"
    done = r["status"] == "SETTLED"
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>Receipt {number(r)}</title>
<style>body{{font-family:system-ui,Segoe UI,Arial,sans-serif;max-width:720px;margin:32px auto;color:#0b1b3a;padding:0 16px}}
h1{{font-size:22px;margin:0}}.sub{{color:#51618a;margin:4px 0 16px}}.badge{{display:inline-block;padding:3px 10px;border-radius:99px;font-size:12px;
background:{'#d9f7e6' if done else '#fff3d6'};color:{'#0a6b35' if done else '#8a5a00'};font-weight:600}}
table{{border-collapse:collapse;width:100%;margin-top:12px}}th,td{{text-align:left;padding:8px 10px;border-bottom:1px solid #dde4f3;font-size:14px;vertical-align:top}}
th{{width:36%;color:#51618a;font-weight:600}}.amt td{{font-size:18px;font-weight:700}}.foot{{color:#51618a;font-size:12px;margin-top:18px}}
@media print{{body{{margin:0}}}}</style></head><body>
<h1>Vault Payment Plt, payment receipt</h1><div class="sub">{html.escape(tag)} &middot; <span class="badge">{html.escape(state(r))}</span></div>
<table>{rows}</table>
<p class="foot">Times are shown in {html.escape(zone)}. This receipt records the payment instruction in your Vault records.
Confirm the credit on your bank statement. To save as PDF, open this file and choose Print, then Save as PDF.</p></body></html>"""


def register(m=None):
    """One row per accepted payment, for the tracking table and CSV."""
    rows = []
    for r in db.q("SELECT * FROM releases WHERE mode=? AND status!='DRAFT' ORDER BY id DESC", (m or ledger.mode(),)):
        t = timeline(r)
        rows.append({"Receipt": number(r), "Payee": r["payee_name"], "Method": security.METHODS.get(r.get("method") or "BANK", ""),
                     "Amount AUD": f"{r['amount_cents'] / 100:.2f}", "Purpose": r.get("memo") or "", "Status": state(r),
                     "Accepted": clock.fmt(t["accepted"]), "Sent": clock.fmt(t["sent"]), "Received": clock.fmt(t["received"]),
                     "Accepted to sent": clock.duration(t["accept_to_sent"]), "Sent to received": clock.duration(t["sent_to_received"]),
                     "Total": clock.duration(t["accept_to_received"]),
                     "Waiting": clock.duration(t["waiting"]) if r["status"] in ("EXPORTED", "APPROVED") else "",
                     "Longer than usual": "yes" if t["late"] else "", "Bank reference": r.get("bank_ref") or r.get("batch_id") or "",
                     "_id": r["id"]})
    return rows


def awaiting(m=None):
    """Accepted or sent payments the recipient has not received yet, oldest first."""
    out = []
    for r in db.q("SELECT * FROM releases WHERE mode=? AND status IN ('APPROVED','EXPORTED') ORDER BY COALESCE(sent_ts,approved_ts,ts)", (m or ledger.mode(),)):
        out.append((r, timeline(r)))
    return out
