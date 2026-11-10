"""ABA (Cemtext / Direct Entry) file generator.

This is the standard Australian bulk direct-credit file. You upload it in your own bank's business
internet banking (or your payment provider's bulk-pay tool); the bank moves the money from your account.
Each record is exactly 120 characters.
"""
import datetime as dt

from . import security


def _fit(s, n, right=False, pad=" "):
    s = str(s)[:n]
    return s.rjust(n, pad) if right else s.ljust(n, pad)


def _clean(s):
    return "".join(ch if ch.isalnum() or ch in " -&./'" else " " for ch in (s or "")).upper()


MAX_ABA_CENTS = 9_999_999_999  # the ABA amount field is 10 digits (AUD 99,999,999.99 per line)


def build_aba(releases, *, bank_abbrev, user_name, apca_id, description, own_bsb, own_account, remitter, balancing=True, when=None):
    when = when or dt.date.today()
    own_bsb = security.norm_bsb(own_bsb)
    own_account = security.norm_account(own_account)
    items = [r for r in releases if r["bsb"] and r["account"]]
    for r in items:
        if int(r["amount_cents"]) > MAX_ABA_CENTS:
            raise ValueError(f"Release #{r.get('id', '?')} is above the ABA limit of AUD 99,999,999.99 per payment. Split it, or use your bank's high-value channel.")
    if sum(int(r["amount_cents"]) for r in items) > MAX_ABA_CENTS:
        raise ValueError("The file total is above the ABA limit of AUD 99,999,999.99. Export fewer releases per file.")
    if not items:
        raise ValueError("No bank-account releases to export (PayID payments cannot go in an ABA file).")
    lines = []
    head = ("0" + " " * 17 + "01" + _fit(bank_abbrev.upper(), 3) + " " * 7 + _fit(_clean(user_name), 26)
            + _fit(apca_id, 6, right=True, pad="0") + _fit(_clean(description), 12) + when.strftime("%d%m%y") + " " * 40)
    lines.append(head)
    credit_total = 0
    for r in items:
        cents = int(r["amount_cents"])
        credit_total += cents
        lines.append("1" + _fit(r["bsb"], 7) + _fit(r["account"], 9, right=True) + " " + "50" + _fit(cents, 10, right=True, pad="0")
                     + _fit(_clean(r["payee_name"]), 32) + _fit(_clean(r["reference"]), 18) + _fit(own_bsb, 7)
                     + _fit(own_account, 9, right=True) + _fit(_clean(remitter), 16) + "0" * 8)
    debit_total = 0
    if balancing:
        debit_total = credit_total
        lines.append("1" + _fit(own_bsb, 7) + _fit(own_account, 9, right=True) + " " + "13" + _fit(debit_total, 10, right=True, pad="0")
                     + _fit("VAULT PAYMENT PLT", 32) + _fit("BALANCING", 18) + _fit(own_bsb, 7) + _fit(own_account, 9, right=True)
                     + _fit(_clean(remitter), 16) + "0" * 8)
    count = len(lines) - 1
    lines.append("7" + "999-999" + " " * 12 + _fit(abs(credit_total - debit_total), 10, right=True, pad="0")
                 + _fit(credit_total, 10, right=True, pad="0") + _fit(debit_total, 10, right=True, pad="0") + " " * 24
                 + _fit(count, 6, right=True, pad="0") + " " * 40)
    for ln in lines:
        assert len(ln) == 120, f"ABA record is {len(ln)} chars: {ln!r}"
    return "\r\n".join(lines) + "\r\n"


def validate_aba(text: str):
    """Check an ABA file against the record layout. Returns a list of problems (empty means the structure is correct)."""
    problems = []
    lines = [ln for ln in text.replace("\r\n", "\n").split("\n") if ln != ""]
    if len(lines) < 3:
        return ["File needs a header (type 0), at least one payment (type 1) and a total (type 7)."]
    for i, ln in enumerate(lines, 1):
        if len(ln) != 120:
            problems.append(f"Line {i} is {len(ln)} characters, it must be exactly 120.")
    if problems:
        return problems
    if lines[0][0] != "0" or lines[0][18:20] != "01":
        problems.append("Line 1 must be a type 0 header with reel sequence 01.")
    if lines[-1][0] != "7" or lines[-1][1:8] != "999-999":
        problems.append("The last line must be a type 7 total starting 7 999-999.")
    credit = debit = n = 0
    for i, ln in enumerate(lines[1:-1], 2):
        if ln[0] != "1":
            problems.append(f"Line {i} must be a type 1 detail record."); continue
        n += 1
        if not (ln[1:8][:3].isdigit() and ln[4] == "-" and ln[5:8].isdigit()):
            problems.append(f"Line {i}: BSB must look like 062-000.")
        if not ln[8:17].strip().isdigit():
            problems.append(f"Line {i}: account number must be digits.")
        if not ln[20:30].isdigit():
            problems.append(f"Line {i}: amount must be 10 digits."); continue
        code, amt = ln[18:20], int(ln[20:30])
        if code == "50":
            credit += amt
        elif code == "13":
            debit += amt
        else:
            problems.append(f"Line {i}: unexpected transaction code {code} (expected 50 credit or 13 balancing debit).")
        if amt == 0:
            problems.append(f"Line {i}: amount is zero.")
    t = lines[-1]
    if t[20:30].isdigit() and t[30:40].isdigit() and t[40:50].isdigit() and t[74:80].isdigit():
        if int(t[30:40]) != credit:
            problems.append(f"Credit total {int(t[30:40])} does not match the lines ({credit}).")
        if int(t[40:50]) != debit:
            problems.append(f"Debit total {int(t[40:50])} does not match the lines ({debit}).")
        if int(t[20:30]) != abs(credit - debit):
            problems.append("Net total is wrong.")
        if int(t[74:80]) != n:
            problems.append(f"Record count {int(t[74:80])} does not match the {n} detail lines.")
    else:
        problems.append("The type 7 total record has non-numeric totals.")
    return problems


def build_pain001(releases, *, debtor_name, own_bsb, own_account, msg_id, when=None):
    """ISO 20022 pain.001.001.03 customer credit transfer file for bank-account (BSB) releases, in AUD.

    This is the generic message structure. Banks publish their own implementation guides, so confirm yours accepts it."""
    import datetime as _dt
    import xml.etree.ElementTree as ET
    when = when or _dt.datetime.now()
    own_bsb = security.norm_bsb(own_bsb).replace("-", "")
    own_account = security.norm_account(own_account)
    items = [r for r in releases if r["bsb"] and r["account"]]
    if not items:
        raise ValueError("No bank-account releases to export.")
    ns = "urn:iso:std:iso:20022:tech:xsd:pain.001.001.03"
    ET.register_namespace("", ns)
    q = lambda t: f"{{{ns}}}{t}"

    def sub(parent, tag, text=None):
        e = ET.SubElement(parent, q(tag))
        if text is not None:
            e.text = str(text)
        return e

    def agent(parent, tag, bsb):
        a = sub(sub(parent, tag), "FinInstnId")
        m = sub(a, "ClrSysMmbId")
        sub(sub(m, "ClrSysId"), "Cd", "AUBSB")
        sub(m, "MmbId", bsb)

    total = sum(int(r["amount_cents"]) for r in items)
    money = lambda c: f"{int(c) // 100}.{int(c) % 100:02d}"
    doc = ET.Element(q("Document"))
    root = sub(doc, "CstmrCdtTrfInitn")
    gh = sub(root, "GrpHdr")
    sub(gh, "MsgId", str(msg_id)[:35]); sub(gh, "CreDtTm", when.strftime("%Y-%m-%dT%H:%M:%S"))
    sub(gh, "NbOfTxs", len(items)); sub(gh, "CtrlSum", money(total)); sub(sub(gh, "InitgPty"), "Nm", _clean(debtor_name)[:70] or "VAULT")
    pi = sub(root, "PmtInf")
    sub(pi, "PmtInfId", str(msg_id)[:35]); sub(pi, "PmtMtd", "TRF"); sub(pi, "NbOfTxs", len(items)); sub(pi, "CtrlSum", money(total))
    sub(sub(sub(pi, "PmtTpInf"), "SvcLvl"), "Cd", "NURG")
    sub(pi, "ReqdExctnDt", when.strftime("%Y-%m-%d"))
    sub(sub(pi, "Dbtr"), "Nm", _clean(debtor_name)[:70] or "VAULT")
    da = sub(pi, "DbtrAcct"); sub(sub(sub(da, "Id"), "Othr"), "Id", own_account); sub(da, "Ccy", "AUD")
    agent(pi, "DbtrAgt", own_bsb)
    sub(pi, "ChrgBr", "SLEV")
    for r in items:
        tx = sub(pi, "CdtTrfTxInf")
        pid = sub(tx, "PmtId"); e2e = f"VPP{int(r['id']):08d}" if r.get("id") else "VPP"
        sub(pid, "InstrId", e2e); sub(pid, "EndToEndId", e2e)
        sub(sub(tx, "Amt"), "InstdAmt", money(r["amount_cents"])).set("Ccy", "AUD")
        agent(tx, "CdtrAgt", r["bsb"].replace("-", ""))
        sub(sub(tx, "Cdtr"), "Nm", _clean(r["payee_name"])[:70])
        sub(sub(sub(sub(tx, "CdtrAcct"), "Id"), "Othr"), "Id", r["account"])
        sub(sub(tx, "RmtInf"), "Ustrd", (r.get("memo") or r.get("reference") or "")[:140])
    ET.indent(doc)
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(doc, encoding="unicode") + "\n"
