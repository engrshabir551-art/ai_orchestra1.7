import pandas as pd
import streamlit as st

import ui
from core import finance, ledger
from core.money import fmt

ui.guard(cap="view")
st.title("Accounting")
rows = ledger.ledger_rows(limit=5000)
if not rows:
    st.info("No ledger entries yet. Record a deposit on the Vault E-Terminal page.")
    st.stop()
df = pd.DataFrame(rows)
df["date"] = pd.to_datetime(df["ts"], unit="s")
df["AUD"] = df["amount_cents"] / 100
df["month"] = df["date"].dt.strftime("%Y-%m")

c = st.columns(3)
c[0].metric("Total deposits", fmt(int(df[df.kind == "DEPOSIT"].amount_cents.sum())))
c[1].metric("Total released", fmt(int(-df[df.kind == "RELEASE"].amount_cents.sum())))
c[2].metric("Reversals", fmt(int(df[df.kind == "REVERSAL"].amount_cents.sum())))
st.caption("Moves between the reserve and the vault are internal and net to zero in total holdings.")

_NAMES = ['Monthly summary', 'Statement', 'Journal and releases', 'Payee ledger', 'Reconciliation']
_sec = ui.sections(_NAMES, "sec_accounting")

if _sec == 'Monthly summary':
    piv = df.pivot_table(index="month", columns="kind", values="AUD", aggfunc="sum", fill_value=0)
    piv["Net movement"] = piv.sum(axis=1)
    ui.show_df(piv.style.format("{:,.2f}"))
    st.bar_chart(piv.drop(columns=["Net movement"]))

if _sec == 'Statement':
    days = st.radio("Period", [30, 90, 365], horizontal=True, format_func=lambda d: f"Last {d} days", key="stmt_days")
    stmt = pd.DataFrame(finance.statement(days))
    if stmt.empty:
        st.info("No entries in this period.")
    else:
        for col in ("Credit", "Debit", "Balance"):
            stmt[col] = stmt[col] / 100
        ui.show_df(stmt, hide_index=True)
        st.download_button("Download statement (CSV)", stmt.to_csv(index=False), file_name="vault_statement.csv", mime="text/csv")

if _sec == 'Journal and releases':
    st.subheader("Journal")
    view = df[["id", "date", "wallet", "kind", "AUD", "ref", "release_id", "memo", "verified"]].rename(columns={"ref": "reference"})
    ui.show_df(view, hide_index=True)
    st.download_button("Download journal (CSV)", view.to_csv(index=False), file_name="vault_journal.csv", mime="text/csv")
    st.subheader("Release register")
    rel = pd.DataFrame(ledger.releases())
    if not rel.empty:
        rel["AUD"] = rel["amount_cents"] / 100
        cols = [c_ for c_ in ["id", "status", "method", "payee_name", "bsb", "account", "payid", "biller_code", "crn", "category", "AUD", "reference", "channel", "batch_id"] if c_ in rel]
        rel = rel[cols]
        ui.show_df(rel, hide_index=True)
        st.download_button("Download releases (CSV)", rel.to_csv(index=False), file_name="vault_releases.csv", mime="text/csv")

if _sec == 'Payee ledger':
    pl = finance.payee_ledger()
    if pl:
        out = pd.DataFrame(pl)
        out["Paid"] = out["Paid"] / 100
        out["Pending"] = out["Pending"] / 100
        ui.show_df(out, hide_index=True)
    else:
        st.info("No payments yet.")

if _sec == 'Reconciliation':
    r = finance.reconcile()
    a, b, c2, d = st.columns(4)
    a.metric("Book balance", fmt(r["book_cents"]))
    b.metric("Approved, not yet paid", fmt(r["in_flight_cents"]))
    c2.metric("Expected in bank", fmt(r["expected_bank_cents"]))
    d.metric("Unverified deposits", fmt(r["unverified_cents"]))
    st.caption("Compare the expected bank balance with your bank statement. A difference means a deposit is unverified, a payment is still in flight, or something was not recorded.")
