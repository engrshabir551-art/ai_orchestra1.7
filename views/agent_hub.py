import streamlit as st

import ui
from core import agents, db, security

ui.guard(admin=True)
st.title("Agent Hub")
all_agents = agents.list_agents()
names = [a["name"] for a in all_agents if a["enabled"]]

st.subheader("Talk to an agent")
choice = st.selectbox("Agent", ["Director (auto-route)"] + names)
text = st.text_area("Your request", height=120, placeholder="e.g. Draft a release of 2,500.00 to Jane Citizen, BSB 062-000, account 12345678")
if st.button("Run", type="primary", disabled=not text.strip()):
    target = agents.route(text) if choice.startswith("Director") else choice
    st.caption(f"Handled by: **{target}**")
    with st.spinner("Agent working..."):
        st.markdown(agents.run_agent(target, text))

st.divider()
st.subheader("Add an agent")
st.caption("Add the extra agents you plan during the week. They sync through shared memory and tasks, and run with only the tools you tick.")
with st.form("newagent"):
    n = st.text_input("Agent name")
    g = st.text_input("Group", value="Custom")
    role = st.text_input("Role (one sentence)")
    prompt = st.text_area("Extra instructions (optional)")
    tools = st.multiselect("Tools", agents.SAFE_TOOLS, default=["recall", "remember", "create_task"])
    if st.form_submit_button("Add agent"):
        try:
            agents.add_custom_agent(n, g, role, prompt, tools)
            security.audit(ui.username(), "AGENT_ADDED", n)
            st.success(f"Added {n}.")
            st.rerun()
        except Exception as e:
            st.error(str(e))

st.subheader("Enable or disable")
for a in all_agents:
    c1, c2 = st.columns([4, 1])
    c1.write(f"**{a['name']}** · {a['grp']} · tools: {a['tools']}")
    on = c2.toggle("On", value=bool(a["enabled"]), key=f"en{a['id']}")
    if on != bool(a["enabled"]):
        db.x("UPDATE agents SET enabled=? WHERE id=?", (1 if on else 0, a["id"]))
        security.audit(ui.username(), "AGENT_TOGGLED", f"{a['name']} -> {on}")
        st.rerun()
