import io
import os
import zipfile

import streamlit as st

import ui
from core import agents

ui.guard(admin=True)
st.title("Projects and Builds")
st.caption("The App developer, Figma and Open-source agents write code into the workspace folder of this app. Review everything before you run or publish it.")
if not agents.has_key():
    st.warning("Add your Anthropic key on the Security and Settings page to let build agents write code.")

c1, c2 = st.columns([1, 2])
project = c1.text_input("Project name", "my-app")
agent = c1.selectbox("Agent", ["App developer", "Figma design agent", "Open-source integrator"])
brief = c2.text_area("Brief", height=150, placeholder="Describe the app or website. For Figma, include the file key (from the Figma URL) and set FIGMA_TOKEN.")
if st.button("Build", type="primary", disabled=not brief.strip()):
    with st.spinner("Agent building..."):
        out = agents.run_agent(agent, f"Project folder name: {project}\nBrief: {brief}\nWrite complete, runnable files with write_project_file, then summarise what you built and how to run it.", max_steps=12)
    st.markdown(out)

st.subheader("Files")
root = agents.WORKSPACE
projects = sorted(d for d in os.listdir(root) if os.path.isdir(os.path.join(root, d))) if os.path.isdir(root) else []
if not projects:
    st.info("No projects yet.")
for pr in projects:
    with st.expander(pr):
        files = []
        for d, _, fs in os.walk(os.path.join(root, pr)):
            files += [os.path.join(d, f) for f in fs]
        for f in files:
            rel = os.path.relpath(f, os.path.join(root, pr))
            if st.checkbox(rel, key=f"{pr}{rel}"):
                st.code(open(f, encoding="utf-8", errors="replace").read()[:20000])
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            for f in files:
                z.write(f, os.path.relpath(f, os.path.join(root, pr)))
        st.download_button("Download project (zip)", buf.getvalue(), file_name=f"{pr}.zip", key=f"z{pr}")
