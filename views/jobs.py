import time

import pandas as pd
import streamlit as st

import ui
from core import agents, db, runner

ui.guard(admin=True)
st.title("Autonomous Jobs")
st.caption("A job gives an agent an objective and a target. It works on a schedule (a 'tick'), saves a checkpoint each time and reports at the end. Jobs run inside this server, so keep the app running for 12 and 24 hour jobs.")
if not agents.has_key():
    st.warning("Add your Anthropic key on the Security and Settings page so agents can do the work in each tick.")

with st.form("job"):
    names = [a["name"] for a in agents.list_agents() if a["enabled"]]
    ag = st.selectbox("Agent", names, index=names.index("Autonomous runner") if "Autonomous runner" in names else 0)
    obj = st.text_area("Objective")
    tgt = st.text_input("Target (how will we know it worked?)")
    c = st.columns(2)
    hrs = c[0].selectbox("Duration (hours)", [6, 12, 24])
    tick = c[1].number_input("Tick every (minutes)", 1, 720, 60)
    if st.form_submit_button("Start job"):
        try:
            jid = runner.start_job(ag, obj, tgt, hrs, tick, ui.username())
            st.success(f"Job #{jid} started."); st.rerun()
        except ValueError as e:
            st.error(str(e))

st.button("Refresh")
for j in db.q("SELECT * FROM jobs ORDER BY id DESC LIMIT 30"):
    done = min(1.0, (time.time() - j["ts"]) / max(1, j["ends"] - j["ts"])) if j["status"] == "RUNNING" else 1.0
    with st.expander(f"#{j['id']} · {j['agent']} · {j['hours']:g}h · {j['status']} · {j['objective'][:60]}", expanded=j["status"] == "RUNNING"):
        st.progress(done, text=f"Ends {ui.ts(j['ends'])}")
        st.write(f"**Target:** {j['target']}")
        cps = runner.checkpoints(j["id"])
        for cp in cps[-10:]:
            st.write(f"`{ui.ts(cp['ts'])}` {cp['note']}")
        if j["report"]:
            st.markdown("**Final report**"); st.write(j["report"])
        if j["status"] == "RUNNING" and st.button("Stop job", key=f"stop{j['id']}"):
            runner.stop_job(j["id"], ui.username()); st.rerun()
