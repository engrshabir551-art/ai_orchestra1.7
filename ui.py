import datetime as dt

import streamlit as st

from core import authz, ledger, security


def user():
    return st.session_state.get("user")


def username():
    u = user()
    return u["username"] if u else "anonymous"


def ts(t):
    """Time in the chosen display time zone (Security and Settings), with the zone name."""
    from core import clock
    return clock.fmt(t)


def mode_badge_html() -> str:
    if ledger.mode() == "LIVE":
        return '<span class="pill pill-live"><i></i>Live</span>'
    return ('<div class="aibot"><svg viewBox="0 0 64 64" aria-hidden="true">'
            '<line x1="32" y1="6" x2="32" y2="15" stroke="#35d0ff" stroke-width="3" stroke-linecap="round"/>'
            '<circle class="ant" cx="32" cy="6" r="4" fill="#ffd479"/>'
            '<rect x="10" y="15" width="44" height="36" rx="11" fill="#13306f" stroke="#35d0ff" stroke-width="3"/>'
            '<rect x="4" y="28" width="6" height="11" rx="3" fill="#35d0ff"/><rect x="54" y="28" width="6" height="11" rx="3" fill="#35d0ff"/>'
            '<ellipse class="eye" cx="23" cy="31" rx="5" ry="6" fill="#b9ffd9"/><ellipse class="eye" cx="41" cy="31" rx="5" ry="6" fill="#b9ffd9"/>'
            '<rect x="22" y="42" width="20" height="4" rx="2" fill="#35d0ff"/></svg>'
            '<div class="t">AI agents online<span class="dots"><i></i><i></i><i></i></span></div></div>')


def mode_banner():
    """Kept for the pages; the mode pill now lives in the sidebar so pages stay clean."""
    return None


def guard(admin: bool = False, cap: str = None, any_cap=None):
    """Stop the page unless someone is signed in. admin=True needs the admin role; cap needs that capability."""
    u = user()
    if not u:
        st.error("Please sign in first.")
        st.stop()
    if admin and u.get("role") != "admin":
        st.warning("This page is for admin users.")
        st.stop()
    if any_cap and not any(authz.role_can(u.get("role"), c) for c in any_cap):
        st.warning("This page is for admin, approver or operator users.")
        st.stop()
    if cap and not authz.role_can(u.get("role"), cap):
        st.warning(f"Your role ({u.get('role')}) does not include: {authz.CAP_LABELS.get(cap, cap).lower()}.")
        st.stop()


def can(cap: str) -> bool:
    u = user()
    return bool(u) and authz.role_can(u.get("role"), cap)


def password_ok(key: str, label: str = "Re-enter your password to confirm") -> bool:
    pw = st.text_input(label, type="password", key=key)
    if not pw:
        return False
    if security.verify_user(username(), pw):
        return True
    st.error("Password incorrect.")
    return False


def sections(names, key):
    """Tab-style switcher that renders ONLY the chosen section. (st.tabs runs every tab's code on every click, which is slow.)"""
    if st.session_state.get(key) not in names:
        st.session_state[key] = names[0]
    return st.radio("Section", names, key=key, horizontal=True, label_visibility="collapsed")


def show_df(df, **kw):
    """st.dataframe that works on old Streamlit (use_container_width) and new (width='stretch')."""
    try:
        st.dataframe(df, width="stretch", **kw)
    except TypeError:
        st.dataframe(df, use_container_width=True, **kw)


CSS = """
<style>
html, body, [class*="css"], .stApp { font-family: system-ui, -apple-system, 'Segoe UI', Roboto, Arial, sans-serif; }

/* ---- deep-blue background (static: fast on weak devices) ---- */
.stApp {
  background:
    radial-gradient(1200px 700px at 12% -10%, rgba(52,110,255,.28), transparent 60%),
    radial-gradient(900px 600px at 95% 10%, rgba(0,190,255,.16), transparent 55%),
    linear-gradient(135deg, #040B22 0%, #07112B 45%, #0A1B4A 100%);
  background-attachment: fixed;
}
[data-testid="stAppViewContainer"], [data-testid="stMain"], [data-testid="stSidebar"] { position: relative; z-index: 1; }
[data-testid="stHeader"] { background: transparent; }
footer, #MainMenu, .stDeployButton, [data-testid="stStatusWidget"] { visibility: hidden; }

/* ---- sidebar ---- */
[data-testid="stSidebar"] {
  background: linear-gradient(180deg, rgba(8,20,58,.96), rgba(5,12,36,.98));
  border-right: 1px solid rgba(110,150,255,.15);
}
.brand { font-weight: 800; font-size: 1.35rem; letter-spacing: .3px; padding: .2rem 0 .6rem 0;
  background: linear-gradient(90deg, #7fb0ff, #35d0ff, #ffd479); -webkit-background-clip: text; background-clip: text; color: transparent;
}

/* ---- headings ---- */
h1 { font-weight: 800 !important; letter-spacing: -.5px;
  background: linear-gradient(90deg, #ffffff 0%, #9cc2ff 60%, #ffd479 100%); -webkit-background-clip: text; background-clip: text; color: transparent; }
h2, h3 { font-weight: 700 !important; color: #dfe9ff; }
@keyframes fadeUp { from { opacity: 0; transform: translateY(12px); } to { opacity: 1; transform: none; } }

/* ---- metric cards ---- */
[data-testid="stMetric"] {
  background: linear-gradient(150deg, rgba(40,82,190,.42), rgba(10,26,70,.62));
  border: 1px solid rgba(130,170,255,.22); border-radius: 18px; padding: 18px 20px;
  box-shadow: 0 10px 30px rgba(0,0,0,.35), inset 0 1px 0 rgba(255,255,255,.06); transition: border-color .2s ease; overflow: hidden;
}
[data-testid="stMetric"]:hover { border-color: rgba(120,190,255,.6); box-shadow: 0 18px 40px rgba(20,80,255,.30); }
[data-testid="stMetricLabel"] p { color: #9db8f5 !important; font-weight: 600; text-transform: uppercase; font-size: .72rem !important; letter-spacing: .8px; }
[data-testid="stMetricValue"] { font-weight: 800; color: #ffffff; font-size: 1.4rem; }
[data-testid="stMetricValue"] > div { white-space: normal !important; overflow: visible !important; text-overflow: clip !important; line-height: 1.2; }
[data-testid="stMainBlockContainer"] { padding-top: 2.5rem; }

/* ---- buttons ---- */
.stButton > button, .stDownloadButton > button, [data-testid="stFormSubmitButton"] > button {
  border-radius: 12px; border: 1px solid rgba(130,170,255,.35); background: rgba(30,60,140,.45); color: #eaf1ff; font-weight: 600;
  transition: transform .18s ease, box-shadow .18s ease, background .18s ease;
}
.stButton > button:hover, .stDownloadButton > button:hover, [data-testid="stFormSubmitButton"] > button:hover {
  transform: translateY(-2px); box-shadow: 0 8px 22px rgba(60,130,255,.40); border-color: #7fb0ff; color: #fff; }
.stButton > button[kind="primary"], [data-testid="stFormSubmitButton"] > button[kind="primary"] {
  background: linear-gradient(90deg, #2f6bff, #00b7ff); border: none; color: #fff; box-shadow: 0 6px 20px rgba(0,140,255,.35); }

/* ---- tabs, expanders, forms, inputs ---- */
[data-baseweb="tab-list"] { gap: 6px; }
[data-baseweb="tab"] { border-radius: 10px 10px 0 0; transition: background .2s; }
[data-baseweb="tab"]:hover { background: rgba(80,130,255,.15); }
[data-testid="stExpander"], [data-testid="stForm"] {
  background: rgba(14,32,82,.55); border: 1px solid rgba(120,160,255,.18) !important; border-radius: 16px; }
[data-testid="stExpander"]:hover { border-color: rgba(120,180,255,.45) !important; }
input, textarea, [data-baseweb="select"] > div { background-color: rgba(8,20,58,.75) !important; border-radius: 10px !important; }
input:focus, textarea:focus { box-shadow: 0 0 0 2px rgba(76,141,255,.55) !important; }
[data-testid="stDataFrame"] { border-radius: 14px; overflow: hidden; border: 1px solid rgba(120,160,255,.2); }
[data-testid="stAlert"] { border-radius: 14px; }
.stProgress > div > div > div > div { background: linear-gradient(90deg, #2f6bff, #35d0ff, #ffd479); }

/* ---- mode pill ---- */
.pill-row { display: flex; justify-content: flex-end; margin-bottom: -2.2rem; }
.pill { display: inline-flex; align-items: center; gap: 7px; font-size: .75rem; font-weight: 700; padding: 5px 12px; border-radius: 999px;
  border: 1px solid rgba(255,255,255,.15); background: rgba(10,26,70,.7); color: #cfe0ff; }
.pill i { width: 8px; height: 8px; border-radius: 50%; display: inline-block; }
.pill-live { color: #b9ffd9; } .pill-live i { background: #21e08a; animation: pulse 1.8s infinite; }
.pill-test { color: #ffe3a6; } .pill-test i { background: #ffb938; animation: pulse 2.4s infinite; }
/* ---- animated AI bot (transform/opacity only, cheap) ---- */
.aibot { display: flex; align-items: center; gap: 10px; margin: 6px 0 2px 0; }
.aibot svg { width: 46px; height: 46px; flex: none; animation: botbob 3s ease-in-out infinite; filter: drop-shadow(0 0 8px rgba(80,160,255,.7)); }
.aibot .eye { transform-origin: center; transform-box: fill-box; animation: botblink 4s infinite; }
.aibot .ant { animation: botping 1.6s ease-in-out infinite; }
.aibot .t { font-size: .78rem; font-weight: 700; color: #b9ffd9; line-height: 1.2; }
.aibot .t span { display: block; font-weight: 500; color: #9db8f5; }
.aibot .dots i { display: inline-block; width: 5px; height: 5px; margin-right: 3px; border-radius: 50%; background: #35d0ff; animation: botdot 1.2s infinite; }
.aibot .dots i:nth-child(2) { animation-delay: .2s; } .aibot .dots i:nth-child(3) { animation-delay: .4s; }
@keyframes botbob { 0%,100% { transform: translateY(0); } 50% { transform: translateY(-4px); } }
@keyframes botblink { 0%,92%,100% { transform: scaleY(1); } 96% { transform: scaleY(.1); } }
@keyframes botping { 0%,100% { opacity: 1; } 50% { opacity: .25; } }
@keyframes botdot { 0%,100% { opacity: .2; } 50% { opacity: 1; } }
@keyframes pulse { 0% { box-shadow: 0 0 0 0 rgba(120,255,190,.6); } 70% { box-shadow: 0 0 0 9px rgba(120,255,190,0); } 100% { box-shadow: 0 0 0 0 rgba(120,255,190,0); } }

/* ---- login hero ---- */
.hero { text-align: center; padding: 3.5rem 0 1rem 0; }
.hero .logo { font-size: 4rem; display: inline-block; animation: bob 3.2s ease-in-out infinite; filter: drop-shadow(0 0 24px rgba(80,160,255,.8)); }
@keyframes bob { 0%,100% { transform: translateY(0) rotate(-3deg); } 50% { transform: translateY(-12px) rotate(3deg); } }
.hero h1 { font-size: 3rem; margin: .3rem 0 0 0; }
.hero p { color: #9db8f5; font-size: 1.05rem; margin-top: .2rem; }
@media (prefers-reduced-motion: reduce) { * { animation: none !important; transition: none !important; } }
</style>
"""


def inject_css():
    st.markdown(CSS, unsafe_allow_html=True)
