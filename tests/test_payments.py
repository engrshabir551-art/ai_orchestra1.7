import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import pytest
from core import db, ledger, security, finance, authz
from core.money import to_cents


@pytest.fixture(autouse=True)
def fresh():
    for ext in ("", "-wal", "-shm"):
        if os.path.exists(db.DB_PATH + ext):
            os.remove(db.DB_PATH + ext)
    db.init()
    db.set_setting("mode", "TEST"); db.set_setting("ceiling_cents", str(10**11))
    for n, r in [("adm", "admin"), ("app", "approver"), ("op", "operator"), ("vw", "viewer")]:
        security.create_user(n, "passwordpassword", r)
    ledger.record_deposit(to_cents("100000"), "R", "", "adm")
    ledger.move_to_vault(to_cents("100000"), "adm")
    yield


def draft(method, actor="adm", amt="50", **kw):
    return ledger.create_release("Jane Citizen", kw.get("bsb", ""), kw.get("account", ""), kw.get("payid", ""),
                                 to_cents(amt), "", actor, confirmed_warnings=True, method=method,
                                 biller_code=kw.get("biller", ""), crn=kw.get("crn", ""), category="Bills")


def test_all_methods_draft_and_approve():
    ids = [draft("BANK", bsb="062-000", account="12345678"), draft("PAYID_EMAIL", payid="jane@example.com"),
           draft("PAYID_MOBILE", payid="0412 345 678"), draft("PAYID_ABN", payid="51 824 753 556"),
           draft("BPAY", biller="12345", crn="987654321")]
    for i in ids:
        ledger.approve_release(i, "adm")
    assert {r["method"] for r in ledger.releases()} == {"BANK", "PAYID_EMAIL", "PAYID_MOBILE", "PAYID_ABN", "BPAY"}
    assert ledger.balance_cents() == to_cents("100000") - 5 * to_cents("50")
    assert any(r["payid"] == "0412345678" for r in ledger.releases())


@pytest.mark.parametrize("method,kw", [("PAYID_EMAIL", {"payid": "nope"}), ("PAYID_MOBILE", {"payid": "123"}),
                                       ("PAYID_ABN", {"payid": "11111111111"}), ("BPAY", {"biller": "1", "crn": "x"}),
                                       ("BANK", {"bsb": "12", "account": "1"})])
def test_bad_details_rejected(method, kw):
    with pytest.raises(ValueError):
        draft(method, **kw)


def test_roles():
    with pytest.raises(authz.NotAuthorised):
        draft("PAYID_EMAIL", actor="vw", payid="a@b.co")
    rid = draft("PAYID_EMAIL", actor="op", payid="a@b.co")
    with pytest.raises(authz.NotAuthorised):
        ledger.approve_release(rid, "op")
    with pytest.raises(authz.NotAuthorised):
        ledger.approve_release(rid, "agent:Treasury")
    ledger.approve_release(rid, "app")


def test_typed_amount_and_dual_control_and_limits():
    db.set_setting("confirm_cents", to_cents("100"))
    rid = draft("PAYID_EMAIL", payid="a@b.co", amt="500")
    with pytest.raises(ValueError, match="amount"):
        ledger.approve_release(rid, "adm")
    with pytest.raises(ValueError):
        ledger.approve_release(rid, "adm", typed_amount="499")
    db.set_setting("dual_control_cents", to_cents("100"))
    with pytest.raises(ValueError, match="[Dd]ual"):
        ledger.approve_release(rid, "adm", typed_amount="500")
    ledger.approve_release(rid, "app", typed_amount="500")
    db.set_setting("max_release_cents", to_cents("100"))
    with pytest.raises(ValueError):
        draft("PAYID_EMAIL", payid="a@b.co", amt="101")
    db.set_setting("max_release_cents", 0); db.set_setting("monthly_limit_cents", to_cents("600"))
    with pytest.raises(ValueError):
        draft("PAYID_EMAIL", payid="a@b.co", amt="200")


def test_cooling_off_and_saved_only():
    db.set_setting("cooling_minutes", 30)
    rid = draft("PAYID_EMAIL", payid="a@b.co")
    with pytest.raises(ValueError, match="[Cc]ooling"):
        ledger.approve_release(rid, "adm")
    db.x("UPDATE releases SET ts=ts-4000")
    ledger.approve_release(rid, "adm")
    db.set_setting("payee_mode", "saved_only")
    with pytest.raises(ValueError):
        draft("PAYID_EMAIL", payid="new@b.co")
    draft("PAYID_EMAIL", payid="a@b.co")  # saved after approval


def test_totp():
    assert security.totp_code("GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ", 59)[-6:] == "287082"
    sec = security.totp_new_secret()
    security.enable_totp("adm", sec, security.totp_code(sec))
    rid = draft("PAYID_EMAIL", payid="a@b.co")
    with pytest.raises(ValueError, match="[Aa]uthenticator"):
        ledger.approve_release(rid, "adm")
    db.set_setting("totp_last_adm", "0")
    ledger.approve_release(rid, "adm", totp_code=security.totp_code(sec))
    with pytest.raises(ValueError):  # replay
        security.totp_check("adm", security.totp_code(sec)) or (_ for _ in ()).throw(ValueError("replay"))


def test_finance_reports():
    ledger.approve_release(draft("BPAY", biller="12345", crn="99"), "adm")
    assert finance.period_summary()["count"] == 1
    assert finance.by_method()[0]["method"].startswith("BPAY")
    assert finance.by_category()[0]["category"] == "Bills"
    finance.set_budget("Bills", to_cents("100"), "adm")
    assert finance.budget_usage()[0]["pct"] == 50.0
    assert finance.reconcile()["in_flight_cents"] == to_cents("50")
    with pytest.raises(authz.NotAuthorised):
        finance.set_budget("Bills", 1, "op")


def test_provider_states_and_send(monkeypatch):
    from core import providers, secrets
    monkeypatch.delenv("PROVIDER_API_KEY", raising=False)
    assert providers.status()[0] == "manual"
    providers.save_config("zepto", "sandbox", "ACC1", "", "adm")
    assert providers.status()[0] == "missing_credentials"
    monkeypatch.setenv("PROVIDER_API_KEY", "key-12345678")
    assert providers.status()[0] == "no_adapter"
    sent = []

    @providers.register("zepto")
    class Fake(providers.Adapter):
        name = "zepto"
        def check(self): return True, "ok"
        def send(self, r): sent.append(r["id"]); return "PAY-1"
    try:
        assert providers.status()[0] == "ready"
        rid = draft("PAYID_EMAIL", payid="a@b.co")
        ledger.approve_release(rid, "adm")
        with pytest.raises(authz.NotAuthorised):
            providers.send_release(rid, "op")
        assert providers.send_release(rid, "adm") == "PAY-1"
        with pytest.raises(ValueError):
            providers.send_release(rid, "adm")  # no double send
        assert sent == [rid] and ledger.releases("EXPORTED")[0]["batch_id"] == "PAY-1"
        db.set_setting("provider_env", "live")
        assert providers.status()[0] == "missing_credentials"  # TEST mode needs sandbox
    finally:
        providers.ADAPTERS.pop("zepto", None)


def test_provider_failure_sends_nothing(monkeypatch):
    from core import providers
    monkeypatch.setenv("PROVIDER_API_KEY", "key-12345678")
    providers.save_config("monoova", "sandbox", "A", "", "adm")

    @providers.register("monoova")
    class Bad(providers.Adapter):
        def check(self): return True, ""
        def send(self, r): raise RuntimeError("declined")
    try:
        rid = draft("PAYID_EMAIL", payid="a@b.co"); ledger.approve_release(rid, "adm")
        with pytest.raises(ValueError, match="Nothing was sent"):
            providers.send_release(rid, "adm")
        assert ledger.releases("APPROVED")  # still approved, retry possible
    finally:
        providers.ADAPTERS.pop("monoova", None)


# ---------------- receipts, tracking, formats ----------------
def _paid(method="PAYID_EMAIL", **kw):
    rid = draft(method, payid=kw.get("payid", "a@b.co"), **{k: v for k, v in kw.items() if k != "payid"})
    ledger.approve_release(rid, "adm")
    db.x("UPDATE releases SET approved_ts=approved_ts-7200, ts=ts-7300 WHERE id=?", (rid,))
    return rid


def test_receipt_timeline_and_durations():
    from core import receipts
    rid = _paid()
    now = time.time()
    ledger.mark_exported([rid], "PAYID", "B1", "adm", sent_ts=now - 3600)
    ledger.mark_settled(rid, "adm", received_ts=now - 3000, bank_ref="NPP-99")
    r = db.q1("SELECT * FROM releases WHERE id=?", (rid,))
    t = receipts.timeline(r)
    assert round(t["sent_to_received"]) == 600 and round(t["accept_to_sent"]) == 3600 and round(t["accept_to_received"]) == 4200
    txt = receipts.text(r)
    for needle in ("VPP-T-", "Vault Payment Plt", "Received by recipient", "NPP-99", "10 min", "1 h 10 min", "Australia/Sydney"):
        assert needle in txt, needle
    assert "<table>" in receipts.html_doc(r)


def test_received_time_rules():
    rid = _paid()
    now = time.time()
    with pytest.raises(ValueError, match="future"):
        ledger.mark_settled(rid, "adm", received_ts=now + 99999)
    with pytest.raises(ValueError, match="before"):
        ledger.mark_settled(rid, "adm", received_ts=now - 10**6)
    with pytest.raises(authz.NotAuthorised):
        ledger.mark_settled(rid, "op")
    ledger.mark_settled(rid, "adm")
    with pytest.raises(ValueError):
        ledger.mark_settled(rid, "adm")  # cannot be received twice


def test_awaiting_and_late_flag():
    from core import receipts
    rid = _paid("PAYID_EMAIL")
    ledger.mark_exported([rid], "PAYID", "B", "adm", sent_ts=time.time() - 7200)  # PayID normally minutes
    (r, t), = receipts.awaiting()
    assert t["late"] and t["waiting"] > 7000
    assert receipts.register()[0]["Longer than usual"] == "yes"


def test_amount_is_never_rounded_silently():
    for bad in ("10.005", "0.001", "1.234,5"):
        with pytest.raises(ValueError):
            to_cents(bad)
    assert to_cents("10.50") == 1050 and to_cents("1,234,567.89") == 123456789 and to_cents("A$5") == 500


def test_payid_and_account_formats():
    for ok in ("jane.doe+x@example.com.au", "0412 345 678", "+61 412 345 678", "61412345678", "51 824 753 556"):
        assert security.norm_payid(ok)
    assert security.norm_payid("+61 412 345 678") == "0412345678" and security.norm_payid("Jane@Example.COM") == "jane@example.com"
    for bad in ("jane@", "0312345678", "041234567", "12345678901", "a b@c.com"):
        with pytest.raises(ValueError):
            security.norm_payid(bad)
    assert security.norm_bsb("062000") == "062-000" and security.norm_bsb("062 000") == "062-000"
    for bad in ("12345", "0620000", "abc"):
        with pytest.raises(ValueError):
            security.norm_bsb(bad)
    assert security.norm_account("1234-5678") == "12345678"
    for bad in ("12345", "1234567890"):
        with pytest.raises(ValueError):
            security.norm_account(bad)


def _bank_release(i, cents):
    return {"id": i, "bsb": "062-000", "account": "12345678", "amount_cents": cents, "payee_name": f"Payee {i}",
            "reference": "Vault Payment Plt", "memo": "Invoice"}


def test_aba_layout_codes_and_validation():
    from core import aba
    rows = [_bank_release(1, 123456), _bank_release(2, 100)]
    t = aba.build_aba(rows, bank_abbrev="CBA", user_name="Vault", apca_id="123456", description="PAY", own_bsb="062-001", own_account="87654321", remitter="Vault")
    lines = t.rstrip("\r\n").split("\r\n")
    assert all(len(x) == 120 for x in lines) and lines[0][0] == "0" and lines[-1].startswith("7999-999")
    assert lines[1][18:20] == "50" and lines[1][20:30] == "0000123456" and lines[-2][18:20] == "13"
    assert lines[-1][30:40] == "0000123556" and lines[-1][40:50] == "0000123556" and lines[-1][74:80] == "000003"
    assert aba.validate_aba(t) == []
    assert aba.validate_aba(t.replace("0000123456", "0000999999", 1))  # tampered amount is caught
    assert aba.validate_aba(t.replace("\r\n7", "\r\n 7"))  # broken layout is caught


def test_aba_refuses_amounts_that_do_not_fit():
    from core import aba
    with pytest.raises(ValueError, match="99,999,999.99"):
        aba.build_aba([_bank_release(1, 10_000_000_000)], bank_abbrev="CBA", user_name="V", apca_id="1", description="P", own_bsb="062-001", own_account="87654321", remitter="V")
    with pytest.raises(ValueError, match="file total"):
        aba.build_aba([_bank_release(1, 9_000_000_000), _bank_release(2, 9_000_000_000)], bank_abbrev="CBA", user_name="V", apca_id="1", description="P", own_bsb="062-001", own_account="87654321", remitter="V")


def test_pain001_is_wellformed_with_correct_totals():
    import xml.etree.ElementTree as ET
    from core import aba
    x = aba.build_pain001([_bank_release(7, 123456), _bank_release(8, 5)], debtor_name="Vault & Co", own_bsb="062-001", own_account="87654321", msg_id="M1")
    root = ET.fromstring(x)
    ns = {"d": "urn:iso:std:iso:20022:tech:xsd:pain.001.001.03"}
    assert root.find(".//d:GrpHdr/d:NbOfTxs", ns).text == "2" and root.find(".//d:GrpHdr/d:CtrlSum", ns).text == "1234.61"
    amts = [e.text for e in root.findall(".//d:InstdAmt", ns)]
    assert amts == ["1234.56", "0.05"] and all(e.get("Ccy") == "AUD" for e in root.findall(".//d:InstdAmt", ns))
    assert root.find(".//d:CdtrAgt//d:MmbId", ns).text == "062000" and root.find(".//d:CdtrAgt//d:ClrSysId/d:Cd", ns).text == "AUBSB"


# ---------------- scheduled payments ----------------
def _sched(freq="MONTHLY", start=None, **kw):
    import datetime as dt
    from core import recurring
    return recurring.create("Landlord Pty", "PAYID_EMAIL", "", "", "rent@example.com", "", "", to_cents("100"), "Monthly rent", "Bills", "",
                            freq, start or recurring.today(), "adm", **kw)


def test_schedule_drafts_but_never_approves():
    from core import recurring
    ledger.move_to_vault(to_cents("1000"), "adm") if ledger.reserve_available_cents() else None
    sid = _sched()
    made = recurring.generate_due()
    assert len(made) == 1
    r = db.q1("SELECT * FROM releases WHERE id=?", (made[0],))
    assert r["status"] == "DRAFT" and r["created_by"] == "agent:Scheduler" and r["schedule_id"] == sid and r["memo"] == "Monthly rent"
    assert recurring.generate_due() == []  # the same due date is never drafted twice
    with pytest.raises(authz.NotAuthorised):
        ledger.approve_release(made[0], "agent:Scheduler")
    ledger.approve_release(made[0], "adm")


def test_schedule_dates_and_limits():
    import datetime as dt
    from core import recurring
    s = dt.date(2026, 1, 31)
    assert recurring.occurrence(s, "MONTHLY", 1) == dt.date(2026, 2, 28) and recurring.occurrence(s, "MONTHLY", 2) == dt.date(2026, 3, 31)
    assert recurring.occurrence(s, "WEEKLY", 2) == dt.date(2026, 2, 14) and recurring.occurrence(s, "FORTNIGHTLY", 1) == dt.date(2026, 2, 14)
    assert recurring.occurrence(dt.date(2026, 2, 28), "YEARLY", 2) == dt.date(2028, 2, 28)
    sid = _sched("WEEKLY", max_count=1)
    recurring.generate_due()
    assert db.q1("SELECT status FROM recurring WHERE id=?", (sid,))["status"] == "ENDED"  # stopped after the set number


def test_schedule_blocked_by_low_vault_and_retries():
    from core import recurring
    db.x("DELETE FROM ledger WHERE wallet='VAULT'")  # empty vault
    sid = _sched()
    assert recurring.generate_due() == []
    rec = db.q1("SELECT * FROM recurring WHERE id=?", (sid,))
    assert rec["status"] == "ACTIVE" and rec["drafted"] == 0 and "vault" in rec["last_error"].lower()
    ledger.add_reserve_balance(to_cents("500"), "adm", move_to_vault_too=True)
    assert len(recurring.generate_due()) == 1  # drafts as soon as the vault is funded
    assert db.q1("SELECT last_error FROM recurring WHERE id=?", (sid,))["last_error"] == ""


def test_schedule_skips_missed_periods_and_validates():
    import datetime as dt
    from core import recurring
    ledger.add_reserve_balance(to_cents("500"), "adm", move_to_vault_too=True)
    sid = _sched("WEEKLY")
    db.x("UPDATE recurring SET start_date=?, next_due=? WHERE id=?", ((recurring.today() - dt.timedelta(weeks=5)).isoformat(),) * 2 + (sid,))
    assert len(recurring.generate_due()) == 1  # one draft, not five
    assert db.q1("SELECT next_due FROM recurring WHERE id=?", (sid,))["next_due"] > recurring.today().isoformat()
    with pytest.raises(ValueError, match="past"):
        _sched(start=recurring.today() - dt.timedelta(days=1))
    with pytest.raises(ValueError):
        recurring.create("X", "PAYID_EMAIL", "", "", "not-an-email", "", "", 100, "", "", "", "MONTHLY", recurring.today(), "adm")
    with pytest.raises(authz.NotAuthorised):
        recurring.create("X", "PAYID_EMAIL", "", "", "a@b.co", "", "", 100, "", "", "", "MONTHLY", recurring.today(), "vw")
    recurring.set_status(sid, "PAUSED", "adm")
    assert recurring.due() == []
