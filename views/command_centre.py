import pandas as pd
import streamlit as st

import ui
from core import agents, db, gold, ledger
from core.money import fmt

ui.guard()
ui.mode_banner()
st.title("Command Centre")
s = ledger.summary()
v = gold.valuation()
c = st.columns(3) + st.columns(3)
c[0].metric("Reserve ceiling", fmt(s["ceiling_cents"]))
c[1].metric("Reserve balance", fmt(s["reserve_cents"]))
c[2].metric("Vault balance", fmt(s["vault_cents"]))
c[3].metric("Available to release", fmt(s["available_cents"]))
c[4].metric("Gold held", f"{gold.held_gross_kg():,.3f} kg")
c[5].metric("Gold value (AUD)", fmt(v["aud_cents"]) if v["aud_cents"] is not None else "Add prices")
if s["unverified_cents"]:
    st.info(f"{fmt(s['unverified_cents'])} of deposits are recorded but not yet verified, so they cannot be released.")
if s["pending_drafts"]:
    st.warning(f"{s['pending_drafts']} release draft(s) waiting for your approval on the Vault E-Terminal page.")

st.subheader("Transfer actions waiting on you")
aq = ledger.action_queue()
qa = st.columns(4)
qa[0].metric("1. Approve drafts", len(aq["approve"]))
qa[1].metric("2. Send to bank", len(aq["send"]))
qa[2].metric("3. Confirm paid", len(aq["confirm"]))
qa[3].metric("Deposits to verify", len(aq["verify"]))
if any(len(v) for v in aq.values()):
    st.caption("Open the Vault E-Terminal page to act on these. Order: approve, send the bank file or PayID, then confirm paid once your bank shows it.")
else:
    st.caption("Nothing waiting. Create a release on the Vault E-Terminal page when you want to pay someone.")

st.subheader("Agents")
rows = agents.list_agents()
df = pd.DataFrame([{"Agent": a["name"], "Group": a["grp"], "Role": a["role"], "Enabled": bool(a["enabled"]), "Custom": bool(a["custom"])} for a in rows])
ui.show_df(df, hide_index=True)
st.caption("AI key status: " + ("connected" if agents.has_key() else "no Anthropic key yet: add it on the Security and Settings page (the vault works without it)"))

l, r = st.columns(2)
with l:
    st.subheader("Open tasks")
    t = db.q("SELECT id,agent,title,status FROM tasks WHERE status!='DONE' ORDER BY id DESC LIMIT 15")
    _ = ui.show_df(pd.DataFrame(t), hide_index=True) if t else st.caption("No open tasks.")
with r:
    st.subheader("Running jobs")
    j = db.q("SELECT id,agent,objective,hours,status FROM jobs WHERE status='RUNNING'")
    _ = ui.show_df(pd.DataFrame(j), hide_index=True) if j else st.caption("No jobs running.")
