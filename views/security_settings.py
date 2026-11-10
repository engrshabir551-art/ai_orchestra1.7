import os

import pandas as pd
import streamlit as st

import ui
from core import agents, clock, db, ledger, providers, security
from core import secrets
from core.money import fmt, to_cents

ui.guard(admin=True)
st.title("Security and Settings")
me = ui.username()
is_admin = ui.user()["role"] == "admin"

st.subheader("Audit log integrity")
ok, bad = security.verify_audit_chain()
_ = st.success("Audit chain verified: no entries have been edited or removed.") if ok else st.error(f"Audit chain BROKEN at entry #{bad}. Records were changed or deleted.")

st.subheader("Test books and Live books")
m = ledger.mode()
with st.expander("What is the Test books area?", expanded=False):
    st.markdown("""
The app keeps **two separate sets of books**.

- **Test books** are for practice and set-up. They have their own reserve, vault, payments and receipts. Nothing in them is a real bank record, so you can try every step safely.
- **Live books** are for your real money. You record funds you actually hold, verify them against your bank statement, and every receipt is a real record.

The badge at the left of every page shows which one you are in. Switching does not move any money; it only changes which set of books you are looking at and writing to.
Switch to Live below when the bank or provider is set up and you are ready for a small first payment.
""")
st.write(f"Current mode: **{m}**. TEST and LIVE keep completely separate books.")
if is_admin:
    if m == "TEST":
        st.caption("Before going LIVE: use your own bank account, fund it, then record each real deposit here and verify it against your bank statement. Your first live payment should be a small one.")
        typed = st.text_input("Type LIVE to switch to LIVE mode")
        if ui.password_ok("pwmode") and st.button("Switch to LIVE") and typed == "LIVE":
            db.set_setting("mode", "LIVE"); security.audit(me, "MODE_CHANGED", "TEST -> LIVE"); st.rerun()
    elif st.button("Switch back to TEST"):
        db.set_setting("mode", "TEST"); security.audit(me, "MODE_CHANGED", "LIVE -> TEST"); st.rerun()

st.subheader("Time zone")
_zs = list(clock.COMMON)
_cur = clock.tz()[1]
if _cur not in _zs:
    _zs.append(_cur)
if is_admin:
    _z = st.selectbox("Show all dates and times in", _zs, index=_zs.index(_cur),
                      help="Receipts and timelines use this zone. Times are stored in UTC, so changing it never alters a record.")
    if _z != _cur:
        db.set_setting("timezone", _z); security.audit(me, "TIMEZONE_CHANGED", f"{_cur} -> {_z}"); st.rerun()
else:
    st.write(f"Times are shown in **{_cur}**.")

st.subheader("Access: closed system")
st.markdown("""
This is a **closed, private system**. There is no public sign-up and no subscription inside the app.
- Only an admin can create users, and every user signs in with a password (and an authenticator code if switched on).
- Roles limit what each person can do. Five wrong passwords lock the account for 15 minutes.
- Every action is written to a tamper-evident audit log (see the top of this page).

**To keep the whole site private,** host it where only you can reach it: a private GitHub repository, and a private server or paid host (not a public link).
The included `Caddyfile` can restrict access to your own IP addresses. Running costs are the host, AI usage (OpenRouter or Anthropic), and any payment provider fees.
""")

st.subheader("Authorisation policy")
st.caption("These rules are enforced in the ledger itself, so they apply to the screens, agents and every user. Use 0 to turn a rule off.")
pol = ledger.policy()
_a = lambda k: f"{pol[k] // 100:,}"
with st.form("limits"):
    c1, c2, c3 = st.columns(3)
    mx = c1.text_input("Maximum per release (AUD)", _a("max_release_cents"))
    daily = c2.text_input("Daily limit (AUD)", _a("daily_limit_cents"))
    month = c3.text_input("30-day limit (AUD)", _a("monthly_limit_cents"))
    c4, c5, c6 = st.columns(3)
    large = c4.text_input("Large-release warning at or above (AUD)", _a("large_release_cents"))
    typed = c5.text_input("Type the amount to approve at or above (AUD)", _a("confirm_cents"))
    dual = c6.text_input("Second approver at or above (AUD)", _a("dual_control_cents"))
    c7, c8, c9 = st.columns(3)
    cool_min = c7.number_input("Cooling-off minutes", min_value=0, max_value=10080, value=int(pol["cooling_minutes"]))
    cool_amt = c8.text_input("Cooling-off applies at or above (AUD, 0 = all)", _a("cooling_cents"))
    pm = c9.selectbox("Payees", ["open", "saved_only"], index=0 if pol["payee_mode"] == "open" else 1,
                      format_func=lambda v: "Open (new payees need a confirmation tick)" if v == "open" else "Saved payees only")
    if st.form_submit_button("Save policy", type="primary") and is_admin:
        try:
            for k, v in dict(max_release_cents=mx, daily_limit_cents=daily, monthly_limit_cents=month, large_release_cents=large,
                             confirm_cents=typed, dual_control_cents=dual, cooling_cents=cool_amt).items():
                db.set_setting(k, to_cents(v) if str(v).strip() not in ("", "0") else 0)
            db.set_setting("cooling_minutes", int(cool_min)); db.set_setting("payee_mode", pm)
            security.audit(me, "POLICY_CHANGED", f"max={mx} daily={daily} month={month} typed={typed} dual={dual} cool={cool_min}m/{cool_amt} payees={pm}")
            st.success("Policy saved."); st.rerun()
        except ValueError as e:
            st.error(str(e))

st.subheader("Users and roles")
st.caption("Admin: everything. Approver: approves and sends releases. Operator: drafts releases and records funds. Viewer: dashboards only.")
ui.show_df(pd.DataFrame(db.q("SELECT username, role, CASE WHEN COALESCE(totp_secret,'')!='' THEN 'on' ELSE 'off' END AS authenticator FROM users")), hide_index=True)
if is_admin:
    with st.form("newuser"):
        u = st.text_input("New username"); p = st.text_input("Password (10+ chars)", type="password"); r = st.selectbox("Role", list(security.ROLES))
        if st.form_submit_button("Add user"):
            try:
                security.create_user(u, p, r); security.audit(me, "USER_ADDED", f"{u} as {r}"); st.success("User added."); st.rerun()
            except Exception as e:
                st.error(str(e))
    others = [x["username"] for x in db.q("SELECT username FROM users") if x["username"] != me]
    if others:
        with st.form("rolechange"):
            tu = st.selectbox("Change role for", others); tr = st.selectbox("New role", list(security.ROLES), key="newrole")
            if st.form_submit_button("Change role"):
                db.x("UPDATE users SET role=? WHERE username=?", (tr, tu)); security.audit(me, "ROLE_CHANGED", f"{tu} -> {tr}"); st.rerun()

st.subheader("Payee book")
payees = db.q("SELECT * FROM payees ORDER BY name")
if payees:
    ui.show_df(pd.DataFrame([{"Name": p_["name"], "Method": security.METHODS.get(p_.get("method") or "BANK"), "Details": ledger.destination_text(p_), "Category": p_.get("category") or ""} for p_ in payees]), hide_index=True)
if is_admin:
    with st.form("addpayee", clear_on_submit=True):
        pm_ = st.selectbox("Method", list(security.METHODS), format_func=security.METHODS.get, key="pb_method")
        pn = st.text_input("Payee account name")
        d1, d2 = st.columns(2)
        pbsb = d1.text_input("BSB (bank transfer)"); pacct = d2.text_input("Account number (bank transfer)")
        ppid = st.text_input("PayID (email, mobile or ABN)")
        d3, d4 = st.columns(2)
        pbill = d3.text_input("BPAY biller code"); pcrn = d4.text_input("BPAY reference")
        if st.form_submit_button("Save payee"):
            try:
                ledger.add_payee(pn, pbsb, pacct, ppid, me, method=pm_, biller_code=pbill, crn=pcrn); st.success("Payee saved."); st.rerun()
            except ValueError as e:
                st.error(str(e))

st.subheader("Payment provider")
st.caption("This is where the real-money connection goes. The owner opens and funds a payments provider account from his bank. Once the provider's sandbox credentials are entered here, approved releases can be sent from the app. Until then the app stays in manual mode.")
pc = providers.config()
pst, pmsg = providers.status()
{"ready": st.success, "manual": st.info}.get(pst, st.warning)(pmsg)
if is_admin:
    with st.form("provider_cfg"):
        keys = list(providers.PROVIDERS)
        pv_ = st.selectbox("Provider", keys, index=keys.index(pc["provider"]) if pc["provider"] in keys else 0, format_func=providers.PROVIDERS.get)
        env_ = st.radio("Environment", ["sandbox", "live"], index=0 if pc["environment"] == "sandbox" else 1, horizontal=True,
                        help="Use sandbox first. Switch to live only after the provider approves the account. TEST mode needs sandbox, LIVE mode needs live.")
        acc_ = st.text_input("Source account ID (the provider account payments are sent from)", pc["account_id"])
        url_ = st.text_input("API base URL (from the provider's documentation, optional)", pc["base_url"])
        if st.form_submit_button("Save provider settings", type="primary"):
            try:
                providers.save_config(pv_, env_, acc_, url_, me); st.rerun()
            except ValueError as e:
                st.error(str(e))
    for kname, label in [("PROVIDER_API_KEY", "Provider API key / client ID"), ("PROVIDER_API_SECRET", "Provider API secret (if the provider gives one)")]:
        cur = os.environ.get(kname)
        st.markdown(f"**{label}**: " + (f"✅ set (`{secrets.mask(cur)}`)" if cur else "❌ not set"))
        with st.form(f"form_{kname}", clear_on_submit=True):
            val = st.text_input(f"Paste {label}", type="password", key=f"in_{kname}")
            if st.form_submit_button("Save"):
                try:
                    secrets.save(kname, val); security.audit(me, "API_KEY_SAVED", kname); st.rerun()
                except ValueError as e:
                    st.error(str(e))
        if cur and st.button("Remove", key=f"rm_{kname}"):
            secrets.delete(kname); security.audit(me, "API_KEY_REMOVED", kname); st.rerun()
    if pst == "ready" and st.button("Test provider connection"):
        ok_, m_ = providers.test_connection(me)
        (st.success if ok_ else st.error)(m_)
with st.expander("Go-live checklist"):
    st.markdown("""
1. Owner chooses a payments provider and opens an account (ID, ABN or entity details, source of funds).
2. Owner gets **sandbox** API credentials from the provider and sends them with the API documentation.
3. Enter them above. The connector is installed and tested in sandbox in TEST mode.
4. Provider approves the live account. Switch the provider to **live**, enter live credentials, switch the app to LIVE.
5. Fund the provider account from the bank. Record the deposit and verify it against the statement.
6. First live payment: AUD 1 to the owner's own account. Check it arrives, then increase gradually.
""")

st.subheader("AI keys")
st.caption("Paste a key and press Save. It is stored in a file on this computer (data/secrets.env), never in the database or audit log, and is shown masked. Use a key with a spend limit and keep the data folder private. You only need ONE of the two AI keys.")
import os
from core import secrets

with st.expander("How to create an OpenRouter API key (one key, many AI models)"):
    st.markdown("""
1. Go to **openrouter.ai** and sign in (Google, GitHub or email).
2. Open **Credits** and add credit (for example USD 10). Paid models need credit.
3. Open **Keys** (openrouter.ai/keys) and press **Create Key**.
4. Give it a name such as *AI Orchestra*, and set a **credit limit** so it can never overspend.
5. Copy the key now (it starts with `sk-or-`). It is shown only once.
6. Paste it below and press **Save key**, then **Test key**.
7. Choose the model below. Browse model names at openrouter.ai/models and copy the model ID (for example `anthropic/claude-sonnet-4.5`). Pick one that supports tools.
""")
with st.expander("How to create an Anthropic (Claude) API key"):
    st.markdown("""
1. Go to **console.anthropic.com** and sign in.
2. Open **Billing** and add credit.
3. Open **API Keys** and press **Create Key**. Name it *AI Orchestra*.
4. Copy the key (it starts with `sk-ant-`). It is shown only once.
5. Paste it below and press **Save key**, then **Test key**.
""")

KEYS = [("OPENROUTER_API_KEY", "OpenRouter API key", "Starts with sk-or-. Create at openrouter.ai/keys."),
        ("ANTHROPIC_API_KEY", "Anthropic (Claude) API key", "Starts with sk-ant-. Create at console.anthropic.com > API Keys."),
        ("FIGMA_TOKEN", "Figma personal access token (optional)", "Figma > Settings > Security > Personal access tokens.")]
for kname, label, hint in KEYS:
    cur = os.environ.get(kname)
    st.markdown(f"**{label}**: " + (f"✅ set (`{secrets.mask(cur)}`, from {secrets.source(kname)})" if cur else "❌ not set"))
    if is_admin:
        with st.form(f"form_{kname}", clear_on_submit=True):
            val = st.text_input(f"Paste {label}", type="password", help=hint, key=f"in_{kname}")
            if st.form_submit_button("Save key"):
                try:
                    secrets.save(kname, val)
                    security.audit(me, "API_KEY_SAVED", kname)
                    st.success("Saved. It is active now."); st.rerun()
                except ValueError as e:
                    st.error(str(e))
        c1, c2 = st.columns(2)
        if kname in ("ANTHROPIC_API_KEY", "OPENROUTER_API_KEY") and cur and c1.button("Test key", key=f"test_{kname}"):
            with st.spinner("Checking..."):
                ok, msg = secrets.check_anthropic() if kname == "ANTHROPIC_API_KEY" else secrets.check_openrouter()
            (st.success if ok else st.error)(msg)
        if cur and c2.button("Remove key", key=f"rm_{kname}"):
            secrets.delete(kname); security.audit(me, "API_KEY_REMOVED", kname); st.rerun()

st.markdown("**Which AI service should the agents use?**")
if is_admin:
    with st.form("llm"):
        opts = ["auto", "openrouter", "anthropic"]
        pv = st.selectbox("Service", opts, index=opts.index(db.get_setting("llm_provider", "auto")),
                          help="Auto uses Anthropic if its key is set, otherwise OpenRouter.")
        om = st.text_input("OpenRouter model ID", db.get_setting("openrouter_model", agents.DEFAULT_OR_MODEL),
                           help="Copy from openrouter.ai/models. Must support tool calling.")
        if st.form_submit_button("Save AI settings"):
            db.set_setting("llm_provider", pv); db.set_setting("openrouter_model", om.strip() or agents.DEFAULT_OR_MODEL)
            security.audit(me, "LLM_SETTINGS", f"{pv} {om}"); st.success("Saved.")
st.write(f"Active service now: **{agents.provider() or 'none (add a key)'}** · Anthropic model `{agents.MODEL}` · OpenRouter model `{db.get_setting('openrouter_model', agents.DEFAULT_OR_MODEL)}`")

st.subheader("Audit log")
ui.show_df(pd.DataFrame([{**r, "ts": ui.ts(r["ts"])} for r in db.q("SELECT ts,actor,action,detail FROM audit ORDER BY id DESC LIMIT 300")]), hide_index=True)
