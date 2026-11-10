import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from core import aba, agents, db, gold, ledger, security  # noqa: E402
from core.money import to_cents, fmt  # noqa: E402


@pytest.fixture(autouse=True)
def fresh():
    for ext in ("", "-wal", "-shm"):
        if os.path.exists(db.DB_PATH + ext):
            os.remove(db.DB_PATH + ext)
    db.init()
    agents.seed_agents()
    db.set_setting("mode", "TEST"); db.set_setting("ceiling_cents", str(10**11))
    security.create_user("u", "passwordpassword", "admin")
    yield


def test_money():
    assert to_cents("1,234.56") == 123456
    assert to_cents("0.1") + to_cents("0.2") == 30
    assert fmt(100000000000) == "AUD 1,000,000,000.00"
    with pytest.raises(ValueError):
        to_cents("abc")


def test_no_balance_without_deposit():
    assert ledger.balance_cents() == 0
    with pytest.raises(ValueError):
        ledger.create_release("Jane Citizen", "062000", "12345678", "", 100, "", "u")


def test_deposit_ceiling_and_release_flow():
    ledger.set_ceiling(to_cents("1000"), "u")
    ledger.record_deposit(to_cents("600"), "BANKREF1", "", "u")
    with pytest.raises(ValueError):
        ledger.record_deposit(to_cents("500"), "BANKREF2", "", "u")  # over ceiling
    assert ledger.reserve_cents() == to_cents("600") and ledger.balance_cents() == 0
    with pytest.raises(ValueError, match="vault balance"):  # nothing released before funds are moved to the vault
        ledger.create_release("Jane Citizen", "062-000", "12345678", "", to_cents("250"), "", "u", confirmed_warnings=True)
    ledger.move_to_vault(to_cents("600"), "u")
    rid = ledger.create_release("Jane Citizen", "062-000", "12345678", "", to_cents("250"), "", "u", confirmed_warnings=True)
    ledger.approve_release(rid, "u")
    assert ledger.balance_cents() == to_cents("350")
    ledger.cancel_release(rid, "u")
    assert ledger.balance_cents() == to_cents("600")
    assert ledger.releases("CANCELLED")[0]["reference"] == "Vault Payment Plt"


def test_overdraw_blocked():
    ledger.record_deposit(to_cents("100"), "R", "", "u")
    ledger.move_to_vault(to_cents("100"), "u")
    with pytest.raises(ValueError):
        ledger.create_release("Jane Citizen", "062-000", "12345678", "", to_cents("100.01"), "", "u", confirmed_warnings=True)


def test_new_payee_needs_confirmation():
    ledger.record_deposit(to_cents("100"), "R", "", "u")
    ledger.move_to_vault(to_cents("100"), "u")
    with pytest.raises(ValueError, match="CONFIRM"):
        ledger.create_release("Jane Citizen", "062-000", "12345678", "", to_cents("10"), "", "u")


def test_live_deposit_unverified_cannot_be_released():
    db.set_setting("mode", "LIVE")
    lid = ledger.record_deposit(to_cents("500"), "BANK-ABC", "", "u")
    assert ledger.reserve_cents() == to_cents("500") and ledger.reserve_available_cents() == 0
    with pytest.raises(ValueError, match="verified"):
        ledger.move_to_vault(to_cents("5"), "u")  # unverified deposits cannot be moved or released
    with pytest.raises(ValueError):
        ledger.create_release("Jane Citizen", "062-000", "12345678", "", to_cents("5"), "", "u", confirmed_warnings=True)
    ledger.verify_deposit(lid, "u")
    ledger.move_to_vault(to_cents("500"), "u")
    assert ledger.available_cents() == to_cents("500")


def test_test_and_live_books_are_separate():
    ledger.record_deposit(to_cents("100"), "R", "", "u")
    ledger.move_to_vault(to_cents("100"), "u")
    db.set_setting("mode", "LIVE")
    assert ledger.balance_cents() == 0


def test_dual_control():
    security.create_user("a", "passwordpassword", "admin"); security.create_user("b", "passwordpassword", "admin")
    db.set_setting("dual_control_cents", to_cents("50"))
    ledger.record_deposit(to_cents("100"), "R", "", "a")
    ledger.move_to_vault(to_cents("100"), "a")
    rid = ledger.create_release("Jane Citizen", "062-000", "12345678", "", to_cents("60"), "", "a", confirmed_warnings=True)
    with pytest.raises(ValueError, match="Dual control"):
        ledger.approve_release(rid, "a")
    ledger.approve_release(rid, "b")


def test_daily_limit():
    db.set_setting("daily_limit_cents", to_cents("100"))
    ledger.record_deposit(to_cents("1000"), "R", "", "u")
    ledger.move_to_vault(to_cents("1000"), "u")
    r1 = ledger.create_release("Jane Citizen", "062-000", "12345678", "", to_cents("80"), "", "u", confirmed_warnings=True)
    ledger.approve_release(r1, "u")
    with pytest.raises(ValueError, match="Daily"):
        ledger.create_release("Jane Citizen", "062-000", "12345678", "", to_cents("30"), "", "u", confirmed_warnings=True)


def test_payee_validation():
    assert security.norm_bsb("062000") == "062-000"
    for bad in ("12345", "abcdef"):
        with pytest.raises(ValueError):
            security.norm_bsb(bad)
    assert security.norm_payid("A@B.com") == "a@b.com"
    with pytest.raises(ValueError):
        security.norm_payid("nonsense")


def test_audit_chain_detects_tampering():
    security.audit("u", "A", "1"); security.audit("u", "B", "2"); security.audit("u", "C", "3")
    assert security.verify_audit_chain()[0]
    db.x("UPDATE audit SET detail='changed' WHERE action='B'")
    assert not security.verify_audit_chain()[0]


def test_aba_file_format_and_totals():
    rels = [{"bsb": "062-000", "account": "12345678", "amount_cents": 150000, "payee_name": "Jane Citizen", "reference": "Vault Payment Plt"},
            {"bsb": "633-123", "account": "998877", "amount_cents": 2550, "payee_name": "Bob O'Neil & Co", "reference": "Vault Payment Plt"}]
    out = aba.build_aba(rels, bank_abbrev="CBA", user_name="Test Co", apca_id="123456", description="VAULTPAY",
                        own_bsb="062-000", own_account="87654321", remitter="Test Co")
    lines = out.split("\r\n")[:-1]
    assert all(len(x) == 120 for x in lines)
    assert lines[0][0] == "0" and lines[-1][0] == "7"
    assert lines[-1][20:30] == "0000000000"          # net total zero when balanced
    assert lines[-1][30:40] == "0000152550"          # credit total
    assert lines[-1][74:80] == "000003"              # 3 detail records
    assert "VAULT PAYMENT PLT" in lines[1]
    with pytest.raises(ValueError):
        aba.build_aba([{"bsb": "", "account": "", "amount_cents": 1, "payee_name": "x", "reference": "y"}], bank_abbrev="CBA", user_name="a", apca_id="1", description="d", own_bsb="062-000", own_account="12345678", remitter="r")


def test_gold_register_and_valuation():
    with pytest.raises(ValueError):
        gold.add_bar("B1", 12500, 0.9999, "Vault", "", "", "u")  # no custodian/cert
    for i in range(4):
        gold.add_bar(f"BAR-{i}", 12500, 1.0, "Perth", "Custodian Pty", "CERT-1", "u")
    assert gold.held_gross_kg() == 50
    assert gold.valuation()["aud_cents"] is None
    gold.save_price(2000.0, 1.5, "test")
    v = gold.valuation()
    assert abs(v["fine_oz"] - 50000 / 31.1034768) < 1e-6
    assert abs(v["usd"] - v["fine_oz"] * 2000) < 1e-6
    assert v["aud_cents"] == int(round(v["usd"] * 1.5 * 100))
    gold.sell_bar(1, "Buyer", to_cents("100000"), "", "u")
    assert gold.held_gross_kg() == 37.5


def test_agents_seeded_and_tools_safe():
    names = [a["name"] for a in agents.list_agents()]
    assert len(names) == 19 and "Vault terminal fund manager" in names
    assert not any("approve" in t or "export" in t for a in agents.list_agents() for t in a["tools"].split(","))
    agents.add_custom_agent("New A", "Custom", "Test", "", ["recall"])
    with pytest.raises(ValueError):
        agents.add_custom_agent("Bad", "Custom", "Test", "", ["approve_release"])


def test_agent_draft_cannot_bypass_warnings_or_balance():
    out = agents.run_tool("Vault terminal fund manager", "draft_release", {"payee_name": "Jane Citizen", "bsb": "062-000", "account": "12345678", "amount_aud": "10"})
    assert out.startswith("ERROR")  # no balance
    ledger.record_deposit(to_cents("100"), "R", "", "u")
    ledger.move_to_vault(to_cents("100"), "u")
    out = agents.run_tool("Vault terminal fund manager", "draft_release", {"payee_name": "Jane Citizen", "bsb": "062-000", "account": "12345678", "amount_aud": "10"})
    assert out.startswith("ERROR") and "CONFIRM" in out  # new payee warning blocks agents
    assert ledger.releases("DRAFT") == []


def test_project_path_escape_blocked():
    out = agents.run_tool("App developer", "write_project_file", {"project": "p", "path": "../../evil.txt", "content": "x"})
    assert out.startswith("ERROR")


def test_route():
    assert agents.route("pay Jane via PayID") == "Vault terminal fund manager"
    assert agents.route("how many kg of gold") == "Commodity vault"


def test_secrets_save_load_delete(tmp_path, monkeypatch):
    from core import secrets
    monkeypatch.setattr(secrets, "PATH", str(tmp_path / "s.env"))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(ValueError):
        secrets.save("ANTHROPIC_API_KEY", "bad key with spaces")
    with pytest.raises(ValueError):
        secrets.save("ANTHROPIC_API_KEY", "notanthropic-1234567890")
    secrets.save("ANTHROPIC_API_KEY", "sk-ant-api03-abcdefghijklmnop")
    assert os.environ["ANTHROPIC_API_KEY"].startswith("sk-ant-")
    assert "abcdefghij" not in secrets.mask(os.environ["ANTHROPIC_API_KEY"])
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    secrets.load_into_env()
    assert os.environ["ANTHROPIC_API_KEY"].endswith("mnop")
    assert not db.q("SELECT * FROM settings WHERE value LIKE '%sk-ant%'")  # never in the database
    secrets.delete("ANTHROPIC_API_KEY")
    assert "ANTHROPIC_API_KEY" not in os.environ and secrets._read() == {}


def test_login_lockout():
    security.create_user("locky", "correct-horse-battery", "admin")
    for _ in range(5):
        assert security.verify_user("locky", "wrong") is None
    assert security.is_locked("locky")
    assert security.verify_user("locky", "correct-horse-battery") is None  # locked even with right password
    db.x("UPDATE audit SET ts=ts-1000 WHERE actor='locky'")  # window passes
    assert security.verify_user("locky", "correct-horse-battery")


def test_new_vault_shows_zeros_and_needs_ceiling():
    db.set_setting("ceiling_cents", "0")
    s = ledger.summary()
    assert s["ceiling_cents"] == 0 and s["balance_cents"] == 0 and s["available_cents"] == 0
    with pytest.raises(ValueError, match="ceiling first"):
        ledger.record_deposit(100, "R", "", "u")
    v = gold.valuation()
    assert v["usd"] == 0 and v["aud_cents"] == 0


def test_action_queue_and_steps():
    ledger.record_deposit(to_cents("500"), "R", "", "u")
    ledger.move_to_vault(to_cents("500"), "u")
    r1 = ledger.create_release("Jane Citizen", "062-000", "12345678", "", to_cents("10"), "", "u", confirmed_warnings=True)
    r2 = ledger.create_release("Jane Citizen", "062-000", "12345678", "", to_cents("20"), "", "u", confirmed_warnings=True)
    ledger.approve_release(r2, "u")
    q = ledger.action_queue()
    assert [r["id"] for r in q["approve"]] == [r1] and [r["id"] for r in q["send"]] == [r2] and not q["confirm"]
    ledger.mark_exported([r2], "ABA", "B1", "u")
    assert [r["id"] for r in ledger.action_queue()["confirm"]] == [r2]
    assert ledger.step_index("DRAFT") == 0 and ledger.step_index("EXPORTED") == 2 and ledger.step_index("CANCELLED") == -1


def test_amount_parsing_variants():
    for txt in ("1,000,000,000", "AUD 1,000,000,000.00", "$1 000 000 000", "A$1000000000"):
        assert to_cents(txt) == 100_000_000_000
    assert to_cents("250,000.5") == 25_000_050



def test_reserve_to_vault_moves():
    from core import authz
    security.create_user("op1", "passwordpassword", "operator")
    ledger.set_ceiling(to_cents("1000"), "u")
    ledger.record_deposit(to_cents("800"), "R", "", "u")
    s = ledger.summary()
    assert (s["reserve_cents"], s["vault_cents"], s["total_cents"]) == (to_cents("800"), 0, to_cents("800"))
    with pytest.raises(authz.NotAuthorised):
        ledger.move_to_vault(to_cents("1"), "op1")
    with pytest.raises(ValueError):
        ledger.move_to_vault(0, "u")
    with pytest.raises(ValueError, match="Not enough"):
        ledger.move_to_vault(to_cents("801"), "u")
    ledger.move_to_vault(to_cents("300"), "u", note="weekly budget", bank_ref="TRF1")
    s = ledger.summary()
    assert (s["reserve_cents"], s["vault_cents"], s["total_cents"]) == (to_cents("500"), to_cents("300"), to_cents("800"))
    with pytest.raises(ValueError, match="Not enough"):
        ledger.move_to_reserve(to_cents("301"), "u")
    ledger.move_to_reserve(to_cents("100"), "u")
    assert ledger.balance_cents() == to_cents("200") and ledger.reserve_cents() == to_cents("600")
    assert s["headroom_cents"] == to_cents("200")
    with pytest.raises(ValueError):  # ceiling applies to total holdings
        ledger.record_deposit(to_cents("201"), "R2", "", "u")
    rid = ledger.create_release("Jane Citizen", "062-000", "12345678", "", to_cents("200"), "", "u", confirmed_warnings=True)
    with pytest.raises(ValueError):  # a release can never draw on the reserve
        ledger.create_release("Jane Citizen", "062-000", "12345678", "", to_cents("201"), "", "u", confirmed_warnings=True)
    ledger.approve_release(rid, "u")
    assert ledger.balance_cents() == 0 and ledger.reserve_cents() == to_cents("600")
    from core import finance
    assert finance.reconcile()["book_cents"] == to_cents("600")


def test_add_reserve_balance_manual():
    ledger.set_ceiling(to_cents("1000"), "u")
    with pytest.raises(ValueError, match="above the reserve volume"):
        ledger.add_reserve_balance(to_cents("1500"), "u")
    ledger.add_reserve_balance(to_cents("1500"), "u", move_to_vault_too=True, raise_ceiling=True)
    assert ledger.ceiling_cents() == to_cents("1500")
    assert ledger.balance_cents() == to_cents("1500") and ledger.reserve_cents() == 0
    ledger.add_reserve_balance(to_cents("100"), "u", raise_ceiling=True)  # reserve only
    assert ledger.reserve_available_cents() == to_cents("100")
    with pytest.raises(ValueError):
        ledger.add_reserve_balance(0, "u")
