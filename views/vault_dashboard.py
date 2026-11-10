import pandas as pd
import streamlit as st

import ui
from core import authz, clock, db, finance, ledger, providers, receipts, recurring, security
from core.money import fmt

ui.guard(cap="view")
st.title("Vault Dashboard")
st.caption("Your own funds, your own controls. Every figure is from the ledger and shows zero until you record funds.")
me = ui.username()
s = ledger.summary()
pol = ledger.policy()
days = st.radio("Period", [7, 30, 90], index=1, horizontal=True, format_func=lambda d: f"Last {d} days")
ps = finance.period_summary(days)

c = st.columns(4) + st.columns(4)
c[0].metric("Reserve balance", fmt(s["reserve_cents"]))
c[1].metric("Vault balance", fmt(s["vault_cents"]))
c[2].metric("Available to release", fmt(s["available_cents"]))
c[3].metric("Total holdings", fmt(s["total_cents"]))
c[4].metric("Reserve volume", fmt(s["ceiling_cents"]))
c[5].metric("Funds in", fmt(ps["in_cents"]))
c[6].metric("Released", fmt(ps["out_cents"]))
c[7].metric("Payments", f"{ps['count']}")
if s["ceiling_cents"]:
    used = min(1.0, s["total_cents"] / s["ceiling_cents"])
    st.progress(used, text=f"Reserve utilisation {used * 100:.4f}% of {fmt(s['ceiling_cents'])}  ·  vault holds {fmt(s['vault_cents'])}, reserve holds {fmt(s['reserve_cents'])}")

_ps, _pm = providers.status()
st.caption(("🟢 " if _ps == "ready" else "⚪ ") + "Payment provider: " + _pm)
_NAMES = ['Cash flow', 'Payments', 'Authorisation', 'Budgets', 'Activity']
_sec = ui.sections(_NAMES, "sec_vault_dashboard")

if _sec == 'Cash flow':
    flows = finance.daily_flows(days)
    df = pd.DataFrame(flows).set_index("date")
    st.markdown("**Vault balance**")
    st.line_chart(df[["balance_cents"]].div(100).rename(columns={"balance_cents": "Vault balance (AUD)"}))
    st.markdown("**Moved into the vault and released, per day**")
    st.bar_chart(df[["in_cents", "out_cents"]].div(100).rename(columns={"in_cents": "Moved into vault", "out_cents": "Released"}))
    r = finance.reconcile()
    a, b, c2 = st.columns(3)
    a.metric("Book balance (reserve + vault)", fmt(r["book_cents"]))
    b.metric("Approved, not yet paid", fmt(r["in_flight_cents"]))
    c2.metric("Expected in bank", fmt(r["expected_bank_cents"]))

if _sec == 'Payments':
    a, b = st.columns(2)
    with a:
        st.markdown("**By payment method**")
        m = finance.by_method(days)
        if m:
            ui.show_df(pd.DataFrame([{"Method": e["method"], "Payments": e["count"], "Total": fmt(e["amount_cents"])} for e in m]), hide_index=True)
            st.bar_chart(pd.DataFrame(m).set_index("method")[["amount_cents"]].div(100).rename(columns={"amount_cents": "AUD"}))
        else:
            st.info("No payments in this period.")
    with b:
        st.markdown("**By category**")
        cats = finance.by_category(days)
        if cats:
            st.bar_chart(pd.DataFrame(cats).set_index("category")[["amount_cents"]].div(100).rename(columns={"amount_cents": "AUD"}))
        else:
            st.info("No categorised payments yet.")
    st.markdown("**Top payees**")
    tp = finance.top_payees(max(days, 90))
    if tp:
        ui.show_df(pd.DataFrame([{"Payee": e["payee"], "Payments": e["count"], "Total": fmt(e["amount_cents"])} for e in tp]), hide_index=True)
    wait = receipts.awaiting()
    ww = [t_["waiting"] for _, t_ in wait if t_["waiting"] is not None]
    k1, k2, k3 = st.columns(3)
    k1.metric("Sent, awaiting receipt", sum(1 for r_, _ in wait if r_["status"] == "EXPORTED"))
    k2.metric("Longest wait", clock.duration(max(ww)) if ww else "0 s")
    k3.metric("Longer than usual", sum(1 for _, t_ in wait if t_["late"]))
    st.caption("Details and receipts: Vault E-Terminal > Receipts and tracking.")
    _sc = [x for x in recurring.all_schedules() if x["status"] == "ACTIVE"]
    _soon = [x for x in _sc if x["next_due"] <= (recurring.today() + __import__("datetime").timedelta(days=7)).isoformat()]
    q1, q2 = st.columns(2)
    q1.metric("Active scheduled payments", len(_sc))
    q2.metric("Due in the next 7 days", len(_soon))
    sc = finance.status_counts()
    st.markdown("**Release pipeline**")
    cols = st.columns(5)
    for col, k in zip(cols, ["DRAFT", "APPROVED", "EXPORTED", "SETTLED", "CANCELLED"]):
        col.metric(k.title(), sc[k])

if _sec == 'Authorisation':
    st.markdown("**Authorisation policy**")
    off = lambda v: fmt(v) if v else "Off"
    ui.show_df(pd.DataFrame([
        {"Control": "Maximum per release", "Setting": off(pol["max_release_cents"])},
        {"Control": "Daily limit", "Setting": off(pol["daily_limit_cents"])},
        {"Control": "30-day limit", "Setting": off(pol["monthly_limit_cents"])},
        {"Control": "Type the amount to confirm at or above", "Setting": off(pol["confirm_cents"])},
        {"Control": "Second approver at or above", "Setting": off(pol["dual_control_cents"])},
        {"Control": "Cooling-off", "Setting": f"{pol['cooling_minutes']} min" if pol["cooling_minutes"] else "Off"},
        {"Control": "Payee mode", "Setting": "Saved payees only" if pol["payee_mode"] == "saved_only" else "Open (new payees need a confirmation tick)"},
    ]), hide_index=True)
    st.markdown("**Limit usage**")
    lu = security.limits_used(ledger.mode())
    for label, key, lim in [("Today", "daily", "daily_limit_cents"), ("Last 30 days", "monthly", "monthly_limit_cents")]:
        used_ = lu.get(key, 0)
        if pol[lim]:
            st.progress(min(1.0, used_ / pol[lim]), text=f"{label}: {fmt(used_)} of {fmt(pol[lim])}")
        else:
            st.write(f"{label}: {fmt(used_)} (no limit set)")
    st.markdown("**Your access**")
    st.write(f"Role: **{ui.user().get('role')}**  ·  Authenticator: **{'on' if security.totp_secret_of(me) else 'off'}**")
    st.caption(", ".join(authz.CAP_LABELS[k] for k in sorted(authz.CAPS.get(ui.user().get("role"), set())) if k in authz.CAP_LABELS))
    with st.expander("Authenticator app (two-factor) for your account"):
        if security.totp_secret_of(me):
            st.success("Authenticator is on. It is asked at sign-in and when approving releases.")
            if ui.password_ok("pw_mfa_off", "Re-enter your password to turn it off") and st.button("Turn authenticator off"):
                security.disable_totp(me); st.rerun()
        else:
            sec = st.session_state.setdefault("mfa_secret", security.totp_new_secret())
            st.write("Add this key to Google Authenticator, Microsoft Authenticator or Authy (enter the key manually), then type the 6-digit code to confirm.")
            st.code(sec)
            st.caption(security.totp_uri(me, sec))
            with st.form("mfa_on"):
                code = st.text_input("6-digit code from the app")
                if st.form_submit_button("Turn authenticator on"):
                    try:
                        security.enable_totp(me, sec, code); st.session_state.pop("mfa_secret", None); st.rerun()
                    except ValueError as e:
                        st.error(str(e))
    st.markdown("**Waiting for approval**")
    drafts = ledger.releases("DRAFT")
    if drafts:
        ui.show_df(pd.DataFrame([{"Release": r["id"], "Payee": r["payee_name"], "Amount": fmt(r["amount_cents"]), "Created by": r["created_by"],
                                  "Method": security.METHODS.get(r.get("method") or "BANK")} for r in drafts]), hide_index=True)
        st.caption("Approve them in Vault E-Terminal, Releases tab.")
    else:
        st.info("Nothing is waiting for approval.")
    ok, bad = security.verify_audit_chain()
    st.write("Audit log integrity: " + ("✅ intact" if ok else f"❌ broken at entry {bad}"))

if _sec == 'Budgets':
    bu = finance.budget_usage()
    if bu:
        for b_ in bu:
            st.progress(min(1.0, b_["pct"] / 100), text=f"{b_['category']}: {fmt(b_['used_cents'])} of {fmt(b_['budget_cents'])} ({b_['pct']:.0f}%)")
    else:
        st.info("No budgets yet. Set one below.")
    if ui.can("manage_budgets"):
        from core.money import to_cents
        with st.form("budget"):
            cat = st.selectbox("Category", ledger.CATEGORIES)
            amt = st.text_input("Monthly budget (AUD)", placeholder="0.00")
            if st.form_submit_button("Save budget"):
                try:
                    finance.set_budget(cat, to_cents(amt), me); st.rerun()
                except ValueError as e:
                    st.error(str(e))

if _sec == 'Activity':
    act = finance.activity(30)
    if act:
        ui.show_df(pd.DataFrame([{"When": ui.ts(a_["ts"]), "Who": a_["actor"], "Action": a_["action"], "Detail": a_["detail"]} for a_ in act]), hide_index=True)
    else:
        st.info("No activity yet.")
