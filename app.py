"""AI Orchestra Platform: Streamlit entry point."""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import streamlit as st

from core import db, security, agents, runner, secrets, ledger

import os as _os
ASSETS = _os.path.join(_os.path.dirname(__file__), "assets")
_icon = _os.path.join(ASSETS, "favicon-64.png")
st.set_page_config(page_title="AI Orchestra", page_icon=_icon if _os.path.exists(_icon) else "🎼", layout="wide")
import ui
ui.inject_css()


@st.cache_resource(show_spinner=False)
def _boot():
    """Start-up work (tables, saved keys, agent list, background runner). Runs once per server process, not on every click."""
    db.init()
    secrets.load_into_env()
    agents.seed_agents()
    runner.ensure_started()
    return True


_boot()


def login_screen():
    _logo = _os.path.join(ASSETS, "logo.webp")
    if _os.path.exists(_logo):
        _a, _b, _c = st.columns([1, 1, 1])
        _b.image(_logo, width=300)
        st.markdown('<div class="hero" style="padding-top:.5rem"><p>Agents, vault and gold on one command centre</p></div>', unsafe_allow_html=True)
    else:
        st.markdown('<div class="hero"><div class="logo">🎼</div><h1>AI Orchestra</h1>'
                    '<p>Agents, vault and gold on one command centre</p></div>', unsafe_allow_html=True)
    _l, mid, _r = st.columns([1, 1.3, 1])
    with mid:
        if not security.has_users():
            st.subheader("Create the first admin account")
            with st.form("setup"):
                u = st.text_input("Username")
                p = st.text_input("Password (10+ characters)", type="password")
                p2 = st.text_input("Repeat password", type="password")
                if st.form_submit_button("Create admin", type="primary"):
                    if p != p2:
                        st.error("Passwords do not match.")
                    else:
                        try:
                            security.create_user(u, p, "admin")
                            st.success("Admin created. Sign in below.")
                            st.rerun()
                        except Exception as e:
                            st.error(str(e))
            return
        with st.form("login"):
            u = st.text_input("Username")
            p = st.text_input("Password", type="password")
            code = st.text_input("Authenticator code (only if you turned it on)", max_chars=8)
            if st.form_submit_button("Sign in", type="primary"):
                if security.is_locked(u):
                    st.error("Too many failed attempts. This username is locked for 15 minutes.")
                    return
                user = security.verify_user(u, p)
                if user and security.totp_secret_of(user["username"]) and not security.totp_check(user["username"], code):
                    st.error("Authenticator code missing or wrong. Wait for the next code if you just used one.")
                elif user:
                    st.session_state["user"] = {"username": user["username"], "role": user["role"]}
                    security.audit(user["username"], "LOGIN", "")
                    st.rerun()
                else:
                    st.error("Wrong username or password.")


if "user" not in st.session_state:
    login_screen()
    st.stop()

pages = [
    st.Page("views/command_centre.py", title="Command Centre", icon="🎛️", default=True),
    st.Page("views/agent_hub.py", title="Agent Hub", icon="💬"),
    st.Page("views/vault_dashboard.py", title="Vault Dashboard", icon="📊"),
    st.Page("views/vault_terminal.py", title="Vault E-Terminal", icon="🏦"),
    st.Page("views/gold_vault.py", title="Gold Vault", icon="🪙"),
    st.Page("views/accounting.py", title="Accounting", icon="🧮"),
    st.Page("views/projects.py", title="Projects and Builds", icon="💻"),
    st.Page("views/jobs.py", title="Autonomous Jobs", icon="⏱️"),
    st.Page("views/security_settings.py", title="Security and Settings", icon="🛡️"),
]
_ROLE_PAGES = {
    "admin": None,
    "approver": ("Command Centre", "Vault Dashboard", "Vault E-Terminal", "Accounting"),
    "operator": ("Command Centre", "Agent Hub", "Vault Dashboard", "Vault E-Terminal", "Accounting"),
    "viewer": ("Command Centre", "Vault Dashboard", "Accounting"),
}
_allowed = _ROLE_PAGES.get(st.session_state["user"]["role"], ("Command Centre",))
if _allowed:
    pages = [p for p in pages if p.title in _allowed]
nav = st.navigation(pages)
with st.sidebar:
    _small = _os.path.join(ASSETS, "logo-small.webp")
    if _os.path.exists(_small):
        st.image(_small, width=110)
    st.markdown('<div class="brand">AI Orchestra</div>' + ui.mode_badge_html(), unsafe_allow_html=True)
    if ledger.mode() == "LIVE":
        st.caption("Live books: real records.")
    st.caption(f"Signed in as **{st.session_state['user']['username']}**")
    if st.button("Sign out"):
        security.audit(st.session_state["user"]["username"], "LOGOUT", "")
        st.session_state.clear()
        st.rerun()
nav.run()
