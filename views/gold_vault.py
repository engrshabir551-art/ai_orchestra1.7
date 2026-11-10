import pandas as pd
import streamlit as st

import ui
from core import db, gold
from core.money import fmt, to_cents

ui.guard(admin=True)
ui.mode_banner()
st.title("Gold Vault")
st.caption("Register of gold held by a custodian. Every bar needs a custodian and a certificate or bar-list reference. The register records holdings, it cannot create gold.")
me = ui.username()

v = gold.valuation()
c = st.columns(2) + st.columns(2)
c[0].metric("Gross weight held", f"{gold.held_gross_kg():,.3f} kg")
c[1].metric("Fine gold", f"{v['fine_oz']:,.2f} oz")
c[2].metric("Value (USD)", f"USD {v['usd']:,.2f}" if v["usd"] is not None else "Add prices")
c[3].metric("Value (AUD)", fmt(v["aud_cents"]) if v["aud_cents"] is not None else "Add prices")
st.progress(min(1.0, gold.held_gross_kg() / 50), text=f"{gold.held_gross_kg():.3f} kg recorded of the 50 kg opening target")

st.subheader("Prices")
p = v["price"]
if p:
    st.caption(f"Latest: USD {p['usd_per_oz']:,.2f}/oz × {p['usd_aud']:.4f} AUD per USD · {p['source']} · {ui.ts(p['ts'])}")
b1, b2 = st.columns(2)
with b1:
    if st.button("Fetch live prices"):
        try:
            gold.fetch_live_prices(); st.rerun()
        except Exception as e:
            st.error(f"Live price fetch failed ({e}). Enter prices manually.")
with b2:
    with st.form("manualprice"):
        g1 = st.number_input("Gold USD per troy oz", min_value=0.0, value=0.0, format="%.2f")
        g2 = st.number_input("AUD per 1 USD", min_value=0.0, value=0.0, format="%.4f")
        if st.form_submit_button("Save manual prices"):
            try:
                gold.save_price(g1, g2, f"manual entry by {me}"); st.rerun()
            except ValueError as e:
                st.error(str(e))

_NAMES = ['Register', 'Add bars', 'Move', 'Sell', 'History']
_sec = ui.sections(_NAMES, "sec_gold_vault")
if _sec == 'Register':
    bars = gold.bars()
    _ = ui.show_df(pd.DataFrame(bars).drop(columns=["id", "added_ts"], errors="ignore"), hide_index=True) if bars else st.info("No bars recorded yet.")
if _sec == 'Add bars':
    st.caption("For a 50 kg opening position you could add 4 bars of 12.5 kg. Only add bars your custodian really holds for you.")
    with st.form("addbars"):
        n = st.number_input("Number of bars", 1, 200, 4)
        kg = st.number_input("Weight of each bar (kg)", 0.001, 1000.0, 12.5, format="%.3f")
        pur = st.number_input("Purity (0.9999 = 99.99%)", 0.5, 1.0, 0.9999, format="%.4f")
        pre = st.text_input("Serial prefix", "BAR")
        loc = st.text_input("Location (vault / city)")
        cust = st.text_input("Custodian name")
        cert = st.text_input("Certificate or bar-list reference")
        if st.form_submit_button("Add to register"):
            try:
                start = len(gold.bars()) + 1
                for i in range(int(n)):
                    gold.add_bar(f"{pre}-{start + i:04d}", kg * 1000, pur, loc, cust, cert, me)
                st.success(f"Added {int(n)} bar(s)."); st.rerun()
            except Exception as e:
                st.error(str(e))
held = gold.bars("HELD")
opts = {f"{b['serial']} · {b['weight_g'] / 1000:.3f} kg · {b['location']}": b["id"] for b in held}
if _sec == 'Move':
    if opts:
        pick = st.selectbox("Bar", list(opts), key="mv")
        to = st.text_input("Move to (vault / location)")
        note = st.text_input("Note", key="mvn")
        if st.button("Record move"):
            try:
                gold.move_bar(opts[pick], to, note, me); st.rerun()
            except ValueError as e:
                st.error(str(e))
    else:
        st.info("No held bars.")
if _sec == 'Sell':
    if opts:
        pick = st.selectbox("Bar", list(opts), key="sl")
        buyer = st.text_input("Buyer")
        price = st.text_input("Sale price (AUD)")
        note = st.text_input("Note", key="sln")
        if ui.password_ok("pwsell", "Re-enter your password to record the sale"):
            if st.button("Record sale"):
                try:
                    gold.sell_bar(opts[pick], buyer, to_cents(price), note, me); st.rerun()
                except ValueError as e:
                    st.error(str(e))
    else:
        st.info("No held bars.")
if _sec == 'History':
    h = db.q("SELECT m.ts,b.serial,m.kind,m.to_location,m.buyer,m.price_aud_cents,m.note FROM gold_moves m JOIN gold_bars b ON b.id=m.bar_id ORDER BY m.id DESC")
    for r in h:
        pc = r.pop("price_aud_cents"); r["ts"] = ui.ts(r["ts"]); r["price"] = fmt(pc) if pc else ""
    _ = ui.show_df(pd.DataFrame(h), hide_index=True) if h else st.info("No movements yet.")
