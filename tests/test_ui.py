import os, sys, tempfile
ROOT = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, ROOT)
os.environ.pop("ANTHROPIC_API_KEY", None)
from streamlit.testing.v1 import AppTest
from core import db, security, agents, ledger, gold
from core.money import to_cents

USER = {"username": "admin", "role": "admin"}


def page(name, section=None):
    """Open a page. Pages render only the chosen section (for speed), so tests pick the section they need."""
    at = AppTest.from_file(os.path.join(ROOT, "views", name), default_timeout=30)
    at.session_state["user"] = USER
    if section:
        at.session_state["sec_" + name[:-3]] = section
    at.run()
    assert not at.exception, (name, [e.value for e in at.exception])
    return at


def test_login_screen_and_login():
    at = AppTest.from_file(os.path.join(ROOT, "app.py"), default_timeout=30).run()
    assert not at.exception
    at.text_input[0].set_value("admin"); at.text_input[1].set_value("supersecret123")
    at.button[0].click().run()
    assert not at.exception, [e.value for e in at.exception]
    assert at.session_state["user"]["username"] == "admin"
    assert any("Command Centre" in t.value for t in at.title)


def test_all_pages_render_empty():
    for n in ["command_centre.py", "agent_hub.py", "vault_terminal.py", "gold_vault.py", "accounting.py", "projects.py", "jobs.py", "security_settings.py"]:
        page(n)


def test_vault_flow_through_ui():
    ledger.record_deposit(to_cents("5000"), "TESTREF", "", "admin")
    ledger.move_to_vault(to_cents("5000"), "admin")
    at = page("vault_terminal.py", "Manual release")
    assert any("5,000.00" in m.value for m in at.metric)
    # create draft through the form
    ti = {t.label: t for t in at.text_input}
    ti["Payee full account name"].set_value("Jane Citizen")
    ti["BSB (e.g. 062-000)"].set_value("062-000")
    ti["Account number"].set_value("12345678")
    ti["Amount (AUD)"].set_value("1250.00")
    [c for c in at.checkbox if "checked the payee" in c.label][0].check()
    [b for b in at.button if b.label == "Create draft"][0].click().run()
    assert not at.exception, [e.value for e in at.exception]
    drafts = ledger.releases("DRAFT")
    assert len(drafts) == 1 and drafts[0]["reference"] == "Vault Payment Plt" and drafts[0]["amount_cents"] == 125000
    at = page("vault_terminal.py", "Releases")
    pw = [t for t in at.text_input if t.label.startswith("Re-enter your password to approve")][0]
    pw.set_value("supersecret123").run()
    [b for b in at.button if b.label.startswith("Approve")][0].click().run()
    assert not at.exception
    assert ledger.balance_cents() == to_cents("3750")
    assert len(ledger.releases("APPROVED")) == 1


def test_wrong_password_blocks_approval():
    ledger.record_deposit(to_cents("100"), "R2", "", "admin")
    ledger.move_to_vault(to_cents("100"), "admin")
    rid = ledger.create_release("Jane Citizen", "062-000", "12345678", "", to_cents("10"), "", "admin", confirmed_warnings=True)
    at = page("vault_terminal.py", "Releases")
    pw = [t for t in at.text_input if t.label.startswith("Re-enter your password to approve")][0]
    pw.set_value("wrongwrong").run()
    assert not [b for b in at.button if b.label.startswith("Approve")]
    ledger.cancel_release(rid, "admin")


def test_gold_and_accounting_with_data():
    for i in range(4):
        gold.add_bar(f"BAR-{i}", 12500, 0.9999, "Perth", "Custodian", "CERT", "admin")
    gold.save_price(2000, 1.5, "test")
    at = page("gold_vault.py")
    assert any("50.000 kg" in m.value for m in at.metric)
    page("accounting.py"); page("command_centre.py"); page("security_settings.py")


def test_ceiling_change_ui():
    at = page("vault_terminal.py")
    assert any("1,000,000,000.00" in m.value for m in at.metric)


def test_settings_page_saves_key(monkeypatch, tmp_path):
    from core import secrets
    monkeypatch.setattr(secrets, "PATH", str(tmp_path / "s.env"))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    at = page("security_settings.py")
    ti = [t for t in at.text_input if t.label.startswith("Paste Anthropic")][0]
    ti.set_value("sk-ant-api03-abcdefghijklmnop")
    [b for b in at.button if b.label == "Save key"][1].click().run()
    assert not at.exception, [e.value for e in at.exception]
    assert os.environ["ANTHROPIC_API_KEY"].startswith("sk-ant-")
    secrets.delete("ANTHROPIC_API_KEY")


def test_pages_refuse_anonymous_and_viewers():
    import glob
    for f in glob.glob(os.path.join(ROOT, "views", "*.py")):
        at = AppTest.from_file(f, default_timeout=30).run()
        assert not at.exception, f
        assert at.error and "sign in" in at.error[0].value.lower(), f
        assert not at.metric and not at.button, f  # nothing actionable rendered
    # viewer role cannot open money/control pages
    for n in ["vault_terminal.py", "gold_vault.py", "jobs.py", "projects.py", "agent_hub.py", "security_settings.py"]:
        at = AppTest.from_file(os.path.join(ROOT, "views", n), default_timeout=30)
        at.session_state["user"] = {"username": "v", "role": "viewer"}
        at.run()
        assert at.warning and "admin" in at.warning[0].value.lower(), n
        assert not at.metric and not at.button, n


def test_settings_saves_openrouter_key(monkeypatch, tmp_path):
    from core import secrets
    monkeypatch.setattr(secrets, "PATH", str(tmp_path / "s2.env"))
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    at = page("security_settings.py")
    ti = [t for t in at.text_input if t.label.startswith("Paste OpenRouter")][0]
    ti.set_value("sk-or-v1-abcdefghijklmnop")
    [b for b in at.button if b.label == "Save key"][0].click().run()
    assert not at.exception, [e.value for e in at.exception]
    assert os.environ["OPENROUTER_API_KEY"].startswith("sk-or-")
    secrets.delete("OPENROUTER_API_KEY")


def test_saved_payee_fills_release_form():
    ledger.record_deposit(to_cents("100"), "R9", "", "admin")
    ledger.move_to_vault(to_cents("100"), "admin")
    rid = ledger.create_release("Sam Payee", "082-001", "99887766", "", to_cents("5"), "", "admin", confirmed_warnings=True)
    ledger.approve_release(rid, "admin")  # saves payee to the book
    at = page("vault_terminal.py", "Manual release")
    sb = [s for s in at.selectbox if s.label.startswith("Saved payee")][0]
    sb.select([o for o in sb.options if o.startswith('Sam Payee')][0]).run()
    assert not at.exception, [e.value for e in at.exception]
    ti = {t.label: t for t in at.text_input}
    assert ti["Payee full account name"].value == "Sam Payee" and ti["BSB (e.g. 062-000)"].value == "082-001" and ti["Account number"].value == "99887766"
    cc = page("command_centre.py")
    assert any(m.label.startswith("2. Send to bank") and int(m.value) >= 1 for m in cc.metric)


def test_no_bare_conditional_expression_statements():
    """A bare `st.x(...) if c else st.y(...)` line makes Streamlit print the returned object (internal docs) on the page."""
    import ast, glob
    for f in glob.glob(os.path.join(ROOT, "views", "*.py")) + [os.path.join(ROOT, "app.py")]:
        tree = ast.parse(open(f, encoding="utf-8").read())
        for node in ast.walk(tree):
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.IfExp):
                raise AssertionError(f"{os.path.basename(f)}:{node.lineno} bare conditional expression")


def test_first_time_volume_setup_and_deposit_through_ui():
    from core import db as _db
    _db.set_setting("ceiling_cents", "0")
    at = page("vault_terminal.py")
    assert any("Step 1" in s.value for s in at.subheader)
    ti = {t.label: t for t in at.text_input}
    ti["Reserve volume (AUD)"].set_value("1,000,000,000")
    [b for b in at.button if b.label == "Set reserve volume"][0].click().run()
    assert not at.exception, [e.value for e in at.exception]
    assert ledger.ceiling_cents() == 100_000_000_000
    # live mode deposit with owner confirmation becomes available immediately
    _db.set_setting("mode", "LIVE")
    try:
        at = page("vault_terminal.py", "Deposits")
        ti = {t.label: t for t in at.text_input}
        ti["Funds amount (AUD)"].set_value("2,000,000")
        ti["Bank or provider reference"].set_value("BANK-REF-1")
        [c for c in at.checkbox if "already in my bank account" in c.label][0].check()
        [b for b in at.button if b.label == "Record funds"][0].click().run()
        assert not at.exception, [e.value for e in at.exception]
        assert ledger.reserve_available_cents() == 200_000_000
        # changing an existing volume needs the password, and the button is visible without typing first
        at = page("vault_terminal.py", "Reserve volume")
        assert [b for b in at.button if b.label == "Change reserve volume"]
    finally:
        _db.set_setting("mode", "TEST")


def test_new_pages_render_for_each_role():
    from streamlit.testing.v1 import AppTest
    for role, pages in {"admin": ["vault_dashboard.py", "vault_terminal.py", "accounting.py", "security_settings.py"],
                        "approver": ["vault_dashboard.py", "vault_terminal.py", "accounting.py"],
                        "operator": ["vault_dashboard.py", "vault_terminal.py"], "viewer": ["vault_dashboard.py", "accounting.py"]}.items():
        for n in pages:
            at = AppTest.from_file(os.path.join(ROOT, "views", n), default_timeout=30)
            at.session_state["user"] = {"username": "admin", "role": role}
            at.run()
            assert not at.exception, (role, n, [e.value for e in at.exception])


def test_every_section_renders():
    secs = {"vault_terminal.py": ["Reserve to vault", "Manual release", "Releases", "Scheduled payments", "Receipts and tracking", "Deposits", "Export direct credit", "Reserve volume"],
            "vault_dashboard.py": ["Cash flow", "Payments", "Authorisation", "Budgets", "Activity"],
            "accounting.py": ["Monthly summary", "Statement", "Journal and releases", "Payee ledger", "Reconciliation"],
            "gold_vault.py": ["Register", "Add bars", "Move", "Sell", "History"]}
    for n, names in secs.items():
        for sec in names:
            page(n, sec)


def test_fund_vault_in_one_step_through_ui():
    """Client flow: type a reserve balance, move it to the vault, then pay from the vault."""
    from core import db as _db
    _db.set_setting("ceiling_cents", str(500_000 * 100))
    before = ledger.balance_cents()
    at = page("vault_terminal.py", "Reserve to vault")
    ti = {t.label: t for t in at.text_input}
    ti["Reserve balance amount (AUD)"].set_value("12,345.67")
    [c for c in at.checkbox if "own bank" in c.label][0].check()
    [b for b in at.button if b.label == "Add reserve balance"][0].click().run()
    assert not at.exception, [e.value for e in at.exception]
    assert ledger.balance_cents() - before == to_cents("12345.67")  # reserve -> vault happened in the same step
    # now the vault can pay by PayID
    rid = ledger.create_release("Sam Payee", "", "", "sam@example.com", to_cents("100"), "", "admin", confirmed_warnings=True, method="PAYID_EMAIL")
    ledger.approve_release(rid, "admin")
