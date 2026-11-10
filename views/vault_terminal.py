import datetime as dt
import io
import csv
import time

import pandas as pd
import streamlit as st

import ui
from core import aba, authz, clock, db, ledger, providers, receipts, recurring, security
from core.money import fmt, to_cents

ui.guard(any_cap=["draft", "approve", "export", "record_deposit"])
ui.mode_banner()
st.title("Vault E-Terminal")
st.caption(f"Reference on every manual transfer: **{ledger.REFERENCE}**")
me = ui.username()
s = ledger.summary()

c = st.columns(3) + st.columns(3)
c[0].metric("Reserve volume (ceiling)", fmt(s["ceiling_cents"]))
c[1].metric("Reserve balance", fmt(s["reserve_cents"]))
c[2].metric("Vault balance", fmt(s["vault_cents"]))
c[3].metric("Available to release", fmt(s["available_cents"]))
c[4].metric("Unverified deposits", fmt(s["unverified_cents"]))
c[5].metric("Room under ceiling", fmt(s["headroom_cents"]))

def volume_form(first: bool):
    """One form: amount, password (only when changing), and a visible button."""
    with st.form("volume_form"):
        new = st.text_input("Reserve volume (AUD)", value="" if first else f"{s['ceiling_cents'] // 100:,}",
                            placeholder="e.g. 1,000,000,000", help="The most this vault is allowed to hold. You can change it any time.")
        pw = "" if first else st.text_input("Your password (needed to change an existing volume)", type="password")
        if st.form_submit_button("Set reserve volume" if first else "Change reserve volume", type="primary"):
            try:
                c2 = to_cents(new)
                if c2 <= 0:
                    raise ValueError("Enter an amount greater than zero.")
                if not first and not security.verify_user(me, pw):
                    raise ValueError("Password incorrect.")
                if c2 < s["total_cents"]:
                    raise ValueError(f"The volume cannot be lower than the current holdings ({fmt(s['total_cents'])}: reserve plus vault).")
                ledger.set_ceiling(c2, me)
                st.toast(f"Reserve volume set to {fmt(c2)}")
                st.rerun()
            except ValueError as e:
                st.error(str(e))


has_funds = any(r["kind"] == "DEPOSIT" for r in ledger.ledger_rows())
has_moved = any(r["kind"] == "FROM_RESERVE" for r in ledger.ledger_rows()) or s["vault_cents"] > 0
steps = [(s["ceiling_cents"] > 0, "1. Set your reserve volume"), (has_funds, "2. Add your reserve balance"),
         (has_moved, "3. Move funds to the vault"), (bool(ledger.releases()), "4. Make your first transfer")]
st.markdown("  ·  ".join(("✅ " if ok else "⬜ ") + label for ok, label in steps))
if s["ceiling_cents"] == 0:
    st.subheader("Step 1: set your reserve volume")
    st.caption("This is the most the vault may hold, for example 1,000,000,000. Type it with or without commas.")
    volume_form(first=True)

_made = recurring.generate_due()  # due scheduled payments become drafts (a person still approves them)
if _made:
    st.info(f"{len(_made)} scheduled payment(s) came due and were drafted. Approve them in Releases.")
_NAMES = ['Reserve to vault', 'Manual release', 'Releases', 'Scheduled payments', 'Receipts and tracking', 'Deposits', 'Export direct credit', 'Reserve volume']
_sec = ui.sections(_NAMES, "sec_vault_terminal")

# ---------------- manual release ----------------
if _sec == 'Manual release':
    st.subheader("Create a Vault Payment Plt release")
    st.caption("Choose how the payee gets paid. A draft does not move money. It is approved in the Releases tab under your authorisation policy.")
    if not ui.can("draft"):
        st.info("Your role cannot create releases.")
    else:
        saved = db.q("SELECT * FROM payees ORDER BY name")

        def _dest(p):
            m_ = p.get("method") or "BANK"
            return (f"{p['bsb']} {p['account']}" if m_ == "BANK" else f"BPAY {p.get('biller_code', '')}" if m_ == "BPAY" else p["payid"])

        labels = {f"{p['name']} · {_dest(p)}": p for p in saved}

        def _fill():
            p = labels.get(st.session_state.get("saved_payee"))
            st.session_state["rel_method"] = (p.get("method") or "BANK") if p else "BANK"
            st.session_state["rel_name"] = p["name"] if p else ""
            st.session_state["rel_bsb"] = p["bsb"] if p else ""
            st.session_state["rel_acct"] = p["account"] if p else ""
            st.session_state["rel_payid"] = (p["payid"] or "") if p else ""
            st.session_state["rel_biller"] = (p.get("biller_code") or "") if p else ""
            st.session_state["rel_crn"] = (p.get("crn") or "") if p else ""

        pol = ledger.policy()
        if s["available_cents"] == 0:
            st.warning("The vault balance is zero. Payments are made from the vault, so move funds from the reserve first (Reserve to vault tab).")
        else:
            st.caption(f"Available to release from the vault: **{fmt(s['available_cents'])}**")
        if pol["payee_mode"] == "saved_only":
            st.warning("Saved-payees-only is on: you can only pay people who are already in the payee book.")
        st.selectbox("Saved payee (optional)", ["New payee"] + list(labels), key="saved_payee", on_change=_fill,
                     help="People you have paid before. Choosing one fills the details below.")
        with st.expander("Check a payee's details (format checker)"):
            st.caption("Type the details and see exactly how the app reads them. Nothing is saved.")
            ck_m = st.selectbox("Method", list(security.METHODS), format_func=security.METHODS.get, key="ck_m")
            k1, k2 = st.columns(2)
            ck_a = k1.text_input({"BANK": "BSB", "BPAY": "Biller code"}.get(ck_m, "PayID (email, mobile or ABN)"), key="ck_a")
            ck_b = k2.text_input({"BANK": "Account number", "BPAY": "Reference number"}.get(ck_m, "(not used)"), key="ck_b", disabled=ck_m not in ("BANK", "BPAY"))
            if ck_a or ck_b:
                try:
                    d_ = security.normalise_destination(ck_m, ck_a if ck_m == "BANK" else "", ck_b if ck_m == "BANK" else "", ck_a if ck_m.startswith("PAYID") else "",
                                                         ck_a if ck_m == "BPAY" else "", ck_b if ck_m == "BPAY" else "")
                    st.success("Format is valid: " + ledger.destination_text({**d_, "payid": d_["payid"], "method": d_["method"]}))
                except ValueError as e:
                    st.error(str(e))
            st.markdown("**Accepted formats**  \n"
                        "• BSB: 6 digits, shown as 062-000 · Account number: 6 to 9 digits  \n"
                        "• PayID email: name@example.com (stored in lower case) · PayID mobile: 04xx xxx xxx or +61 4xx xxx xxx (stored as 04xxxxxxxx)  \n"
                        "• PayID ABN: 11 digits that pass the ABN checksum  \n"
                        "• BPAY: biller code 3 to 6 digits, reference 2 to 21 digits  \n"
                        "• Amount: Australian dollars, up to 2 decimals (10.50). 10.505 is rejected, never rounded.")
        method = st.radio("Payment method", list(security.METHODS), format_func=security.METHODS.get, key="rel_method", horizontal=True)
        with st.form("release"):
            name = st.text_input("Payee full account name", key="rel_name")
            bsb = acct = payid = biller = crn = ""
            if method == "BANK":
                a_, b_ = st.columns(2)
                bsb = a_.text_input("BSB (e.g. 062-000)", key="rel_bsb")
                acct = b_.text_input("Account number", key="rel_acct")
            elif method == "BPAY":
                a_, b_ = st.columns(2)
                biller = a_.text_input("Biller code (3 to 6 digits)", key="rel_biller")
                crn = b_.text_input("Customer reference number", key="rel_crn")
            else:
                hint = {"PAYID_EMAIL": "name@example.com", "PAYID_MOBILE": "04xx xxx xxx", "PAYID_ABN": "11 digit ABN"}[method]
                payid = st.text_input("PayID " + {"PAYID_EMAIL": "email address", "PAYID_MOBILE": "mobile number", "PAYID_ABN": "ABN"}[method],
                                      key="rel_payid", placeholder=hint)
            a_, b_ = st.columns(2)
            amt = a_.text_input("Amount (AUD)", placeholder="0.00")
            cat = b_.selectbox("Category", [""] + ledger.CATEGORIES, format_func=lambda c_: c_ or "Uncategorised")
            memo = st.text_input("Payment purpose (what this payment is for, shown on the receipt)", placeholder="e.g. Invoice 42, plumbing work")
            inv = st.text_input("Invoice or order reference (optional)")
            confirm = st.checkbox("I have checked the payee details and any warnings shown")
            go = st.form_submit_button("Create draft")
        if go:
            try:
                cents = to_cents(amt)
                rid = ledger.create_release(name, bsb, acct, payid, cents, memo, me, confirmed_warnings=confirm,
                                            method=method, biller_code=biller, category=cat, crn=crn, invoice_ref=inv)
                st.success(f"Draft #{rid} created. Go to the Releases tab to approve it.")
            except ValueError as e:
                msg = str(e)
                (st.warning if msg.startswith("CONFIRM") else st.error)(msg + (" Tick the confirmation box and submit again." if msg.startswith("CONFIRM") else ""))

# ---------------- releases ----------------
if _sec == 'Releases':
    rows_all = ledger.releases()
    if not rows_all:
        st.info("No releases yet.")
    f1, f2 = st.columns([2, 1])
    status_f = f1.selectbox("Show", ["Waiting for action", "All", "DRAFT", "APPROVED", "EXPORTED", "SETTLED", "CANCELLED"], key="rel_filter")
    limit_n = f2.selectbox("Rows", [20, 50, 100], key="rel_limit")
    if status_f == "Waiting for action":
        rows = [r for r in rows_all if r["status"] in ("DRAFT", "APPROVED", "EXPORTED")]
    elif status_f == "All":
        rows = rows_all
    else:
        rows = [r for r in rows_all if r["status"] == status_f]
    st.caption(f"Showing {min(len(rows), limit_n)} of {len(rows)}. Older items stay in Accounting > Journal and releases.")
    rows = rows[:limit_n]
    prov_ready = providers.status()[0] == "ready"
    for r in rows:
        _flag = " · ⚠ NOT SENT TO BANK YET" if r["status"] == "APPROVED" else (" · sent, not yet received" if r["status"] == "EXPORTED" else "")
        head = f"#{r['id']} · {r['status']} · {fmt(r['amount_cents'])} · {r['payee_name']} · {r['reference']}{_flag}"
        with st.expander(head, expanded=(r["status"] == "DRAFT")):
            idx = ledger.step_index(r["status"])
            if idx >= 0:
                st.markdown("  →  ".join(("✅ " if i <= idx else "⬜ ") + s_ for i, s_ in enumerate(ledger.STEPS)))
            else:
                st.markdown("🚫 Cancelled")
            st.write(f"**{security.METHODS.get(r.get('method') or 'BANK')}** · {ledger.destination_text(r)}" + (f" · {r['category']}" if r.get("category") else ""))
            if r.get("schedule_id"):
                st.caption(f"🔁 Scheduled payment #{r['schedule_id']}")
            if r.get("memo"):
                st.write(f"Purpose: **{r['memo']}**" + (f" · Invoice {r['invoice_ref']}" if r.get("invoice_ref") else ""))
            if r["status"] != "DRAFT":
                tl = receipts.timeline(r)
                st.caption(f"{receipts.number(r)} · accepted {ui.ts(tl['accepted']) or '-'} · sent {ui.ts(tl['sent']) or 'not recorded'} · received {ui.ts(tl['received']) or 'not yet'}"
                           + (f" · transit {clock.duration(tl['sent_to_received'])}" if tl["sent_to_received"] is not None else "")
                           + (f" · waiting {clock.duration(tl['waiting'])}" if tl["waiting"] is not None else "")
                           + (" · ⚠ longer than usual" if tl["late"] else ""))
                d1, d2 = st.columns(2)
                d1.download_button("Receipt (HTML, print to PDF)", receipts.html_doc(r), file_name=f"{receipts.number(r)}.html", mime="text/html", key=f"rh{r['id']}")
                d2.download_button("Receipt (text)", receipts.text(r), file_name=f"{receipts.number(r)}.txt", mime="text/plain", key=f"rt{r['id']}")
            st.write(f"Created {ui.ts(r['ts'])} by {r['created_by']}. {('Approved by ' + r['approved_by'] + ' ' + ui.ts(r['approved_ts'])) if r['approved_by'] else ''}")
            if r["status"] == "DRAFT":
                req = ledger.requirements(r, me)
                checks = ["Password re-entry"]
                if req["typed_amount"]:
                    checks.append("type the exact amount")
                if req["second_approver"]:
                    checks.append(f"a different approver than {r['created_by']}")
                if req["totp"]:
                    checks.append("authenticator code")
                if req["cooling_until"]:
                    checks.append(f"cooling-off until {ui.ts(req['cooling_until'])}")
                st.caption("Needed to approve: " + " · ".join(checks))
                if not ui.can("approve"):
                    st.info("Your role cannot approve. An approver or admin must approve this draft.")
                elif req["second_approver"] and r["created_by"] == me:
                    st.warning("Dual control: another approver must approve this one.")
                elif req["cooling_until"]:
                    st.warning(f"Cooling-off period: approval opens at {ui.ts(req['cooling_until'])}.")
                elif ui.password_ok(f"pw{r['id']}", "Re-enter your password to approve"):
                    typed = st.text_input(f"Type the amount ({fmt(r['amount_cents'])}) to confirm", key=f"ty{r['id']}") if req["typed_amount"] else None
                    code = st.text_input("Authenticator code (6 digits)", key=f"tp{r['id']}") if req["totp"] else None
                    if st.button("Approve and commit funds", key=f"ap{r['id']}", type="primary"):
                        try:
                            ledger.approve_release(r["id"], me, typed_amount=typed, totp_code=code)
                            st.rerun()
                        except ValueError as e:
                            st.error(str(e))
                if st.button("Cancel draft", key=f"cd{r['id']}"):
                    try:
                        ledger.cancel_release(r["id"], me); st.rerun()
                    except ValueError as e:
                        st.error(str(e))
            elif r["status"] in ("APPROVED", "EXPORTED"):
                if r["status"] == "APPROVED":
                    st.error("⚠ NOT SENT TO YOUR BANK YET. Approval only commits funds in this app. No money has left any bank account. "
                             "Send it from your bank app or provider (details below), or connect a provider, then press 'Mark as sent'.")
                    st.markdown("**Copy these into your bank app**")
                    st.code(f"Pay to:     {r['payee_name']}\n"
                            f"Method:     {security.METHODS.get(r.get('method') or 'BANK')}\n"
                            f"Details:    {ledger.destination_text(r)}\n"
                            f"Amount:     {fmt(r['amount_cents'])}\n"
                            f"Reference:  {r['reference']}", language=None)
                else:
                    st.warning("Marked as sent. It is only complete when the recipient has received it. Check the bank statement, then press 'Mark as received by recipient'.")
                if r["status"] == "APPROVED" and prov_ready and ui.can("export"):
                    if st.button(f"Send now via {providers.PROVIDERS[providers.config()['provider']]}", key=f"pv{r['id']}", type="primary"):
                        try:
                            ref = providers.send_release(r["id"], me)
                            st.success(f"Sent. Provider reference {ref}."); st.rerun()
                        except ValueError as e:
                            st.error(str(e))
                if ui.can("settle") or ui.can("export"):
                    with st.expander("Record when it was sent and received (use the bank statement times)"):
                        st.caption(f"Times are in {clock.tz()[1]}. Leave 'use the current time' ticked to record right now.")
                        now_ = dt.datetime.now(clock.tz()[0])
                        use_now = st.checkbox("Use the current time", value=True, key=f"un{r['id']}")
                        s1, s2 = st.columns(2)
                        sd = s1.date_input("Sent date", now_.date(), key=f"sd{r['id']}", disabled=use_now)
                        stm = s2.time_input("Sent time", now_.time().replace(second=0, microsecond=0), key=f"st{r['id']}", disabled=use_now)
                        r1, r2 = st.columns(2)
                        rd = r1.date_input("Received date", now_.date(), key=f"rd{r['id']}", disabled=use_now)
                        rtm = r2.time_input("Received time", now_.time().replace(second=0, microsecond=0), key=f"rt_{r['id']}", disabled=use_now)
                        bref = st.text_input("Bank or provider transaction reference (from the statement)", key=f"br{r['id']}")
                        b1, b2 = st.columns(2)
                        if r["status"] == "APPROVED" and b1.button("Mark as sent", key=f"ms{r['id']}"):
                            try:
                                ledger.mark_exported([r["id"]], "MANUAL", dt.datetime.now().strftime("%Y%m%d%H%M%S"), me,
                                                     sent_ts=None if use_now else clock.to_epoch(sd, stm)); st.rerun()
                            except ValueError as e:
                                st.error(str(e))
                        if b2.button("Mark as received by recipient", key=f"sp{r['id']}", type="primary"):
                            try:
                                ledger.mark_settled(r["id"], me, received_ts=None if use_now else clock.to_epoch(rd, rtm), bank_ref=bref,
                                                    sent_ts=None if (use_now or r["status"] != "APPROVED") else clock.to_epoch(sd, stm)); st.rerun()
                            except ValueError as e:
                                st.error(str(e))
                if st.button("Cancel and return funds", key=f"cx{r['id']}"):
                    ledger.cancel_release(r["id"], me); st.rerun()

# ---------------- scheduled payments ----------------
if _sec == 'Scheduled payments':
    st.subheader("Scheduled payments")
    st.caption("Set a payment to repeat (rent, subscriptions, regular transfers). When it falls due the app creates a **draft** in Releases. "
               "It never approves or sends by itself: a person approves every payment under your authorisation policy, and the vault must hold enough.")
    if ui.can("schedule"):
        smethod = st.radio("Payment method", list(security.METHODS), format_func=security.METHODS.get, key="sc_method", horizontal=True)
        with st.form("sched_form", clear_on_submit=True):
            sname = st.text_input("Payee full account name", key="sc_name")
            sbsb = sacct = spid = sbill = scrn = ""
            if smethod == "BANK":
                a_, b_ = st.columns(2)
                sbsb = a_.text_input("BSB (e.g. 062-000)", key="sc_bsb"); sacct = b_.text_input("Account number", key="sc_acct")
            elif smethod == "BPAY":
                a_, b_ = st.columns(2)
                sbill = a_.text_input("Biller code", key="sc_bill"); scrn = b_.text_input("Customer reference number", key="sc_crn")
            else:
                spid = st.text_input({"PAYID_EMAIL": "PayID email address", "PAYID_MOBILE": "PayID mobile number", "PAYID_ABN": "PayID ABN"}[smethod], key="sc_pid")
            a_, b_, c_ = st.columns(3)
            samt = a_.text_input("Amount each time (AUD)", key="sc_amt", placeholder="0.00")
            sfreq = b_.selectbox("Repeats", list(recurring.FREQUENCIES), format_func=recurring.FREQUENCIES.get, index=2, key="sc_freq")
            sstart = c_.date_input("First payment date", recurring.today(), key="sc_start")
            a_, b_, c_ = st.columns(3)
            spurp = a_.text_input("Payment purpose (on the receipt)", key="sc_purp", placeholder="e.g. Monthly rent")
            scat = b_.selectbox("Category", [""] + ledger.CATEGORIES, format_func=lambda c: c or "Uncategorised", key="sc_cat")
            sinv = c_.text_input("Invoice or order reference (optional)", key="sc_inv")
            a_, b_ = st.columns(2)
            smax = a_.number_input("Stop after this many payments (0 = no limit)", min_value=0, max_value=1000, value=0, key="sc_max")
            use_end = b_.checkbox("Stop on a date", key="sc_useend")
            send_ = b_.date_input("Last payment date", recurring.today(), key="sc_end", disabled=not use_end)
            go_s = st.form_submit_button("Create schedule", type="primary")
        if go_s:
            try:
                rid_ = recurring.create(sname, smethod, sbsb, sacct, spid, sbill, scrn, to_cents(samt), spurp, scat, sinv, sfreq, sstart, me,
                                        end_date=send_ if use_end else None, max_count=int(smax))
                st.success(f"Schedule #{rid_} created. First draft appears on {sstart}."); st.rerun()
            except ValueError as e:
                st.error(str(e))
    else:
        st.info("Your role cannot create schedules.")
    scheds = recurring.all_schedules()
    if not scheds:
        st.info("No scheduled payments yet.")
    for sc in scheds:
        icon = {"ACTIVE": "🟢", "PAUSED": "⏸", "ENDED": "⏹"}[sc["status"]]
        with st.expander(f"{icon} #{sc['id']} · {sc['payee_name']} · {fmt(sc['amount_cents'])} · {recurring.FREQUENCIES[sc['frequency']].lower()} · {sc['status'].title()}"):
            st.write(f"**{security.METHODS.get(sc['method'])}** · {ledger.destination_text(sc)}" + (f" · {sc['category']}" if sc["category"] else ""))
            if sc["purpose"]:
                st.write(f"Purpose: **{sc['purpose']}**")
            ups = recurring.upcoming(sc, 5) if sc["status"] != "ENDED" else []
            st.caption(f"Drafted so far: {sc['drafted']}" + (f" of {sc['max_count']}" if sc["max_count"] else "")
                       + (f" · next: {', '.join(d.strftime('%d %b %Y') for d in ups)}" if ups else "")
                       + (f" · last draft: release #{sc['last_release_id']}" if sc["last_release_id"] else ""))
            if sc["last_error"]:
                st.warning("Could not draft the last due payment: " + sc["last_error"] + " It will try again; top up the vault or fix the cause.")
            if ui.can("schedule") and sc["status"] != "ENDED":
                b1, b2 = st.columns(2)
                if sc["status"] == "ACTIVE" and b1.button("Pause", key=f"scp{sc['id']}"):
                    recurring.set_status(sc["id"], "PAUSED", me); st.rerun()
                if sc["status"] == "PAUSED" and b1.button("Resume", key=f"scr{sc['id']}"):
                    recurring.set_status(sc["id"], "ACTIVE", me); st.rerun()
                if b2.button("End schedule", key=f"sce{sc['id']}"):
                    recurring.set_status(sc["id"], "ENDED", me); st.rerun()

# ---------------- receipts and tracking ----------------
if _sec == 'Receipts and tracking':
    st.subheader("Receipts and tracking")
    st.caption(f"From the moment a payment is accepted to the moment the recipient has the funds. Times are in {clock.tz()[1]}. "
               "A payment counts as received when you record it from your bank statement, or when your provider connection confirms it.")
    wait = receipts.awaiting()
    w1, w2, w3 = st.columns(3)
    w1.metric("Accepted, not yet sent", sum(1 for r_, _ in wait if r_["status"] == "APPROVED"))
    w2.metric("Sent, awaiting receipt", sum(1 for r_, _ in wait if r_["status"] == "EXPORTED"))
    w3.metric("Longer than usual", sum(1 for _, t_ in wait if t_["late"]))
    if wait:
        st.markdown("**Not yet received**")
        ui.show_df(pd.DataFrame([{"Receipt": receipts.number(r_), "Payee": r_["payee_name"], "Method": security.METHODS.get(r_.get("method") or "BANK"),
                                  "Amount": fmt(r_["amount_cents"]), "Status": receipts.state(r_), "Waiting": clock.duration(t_["waiting"]),
                                  "Normally": receipts.TYPICAL_TEXT.get(r_.get("method") or "BANK", ""), "Check": "⚠ longer than usual" if t_["late"] else ""}
                                 for r_, t_ in wait]), hide_index=True)
        st.caption("If a payment is taking longer than usual, check your bank or provider for the transaction. The app cannot move the money itself; the bank or provider does.")
    reg = receipts.register()
    if not reg:
        st.info("No accepted payments yet. Receipts appear here once a release is approved.")
    else:
        df_ = pd.DataFrame(reg)
        ui.show_df(df_.drop(columns=["_id"]), hide_index=True)
        st.download_button("Download all receipts (CSV)", df_.drop(columns=["_id"]).to_csv(index=False), file_name="vault_receipts.csv", mime="text/csv")
        pick = st.selectbox("Open a receipt", [f"{x['Receipt']} · {x['Payee']} · {x['Status']}" for x in reg], key="rc_pick")
        rid_ = reg[[f"{x['Receipt']} · {x['Payee']} · {x['Status']}" for x in reg].index(pick)]["_id"]
        rr = db.q1("SELECT * FROM releases WHERE id=?", (rid_,))
        st.code(receipts.text(rr), language=None)
        c1_, c2_ = st.columns(2)
        c1_.download_button("Download receipt (HTML, print to PDF)", receipts.html_doc(rr), file_name=f"{receipts.number(rr)}.html", mime="text/html", key="rc_h")
        c2_.download_button("Download receipt (text)", receipts.text(rr), file_name=f"{receipts.number(rr)}.txt", mime="text/plain", key="rc_t")

# ---------------- deposits ----------------
if _sec == 'Deposits':
    st.subheader("Record the funds you hold")
    st.caption("Recorded funds go into the **reserve balance**. Move what you want to spend into the vault on the Reserve to vault tab.")
    live = ledger.mode() == "LIVE"
    st.caption("Type the amount that is in your account. The bank or provider reference is how you match it to your statement" + (" (needed in live mode)." if live else " (optional in test mode)."))
    if s["ceiling_cents"] == 0:
        st.warning("Set your reserve volume first (Step 1 above).")
    with st.form("dep"):
        amt = st.text_input("Funds amount (AUD)", placeholder="e.g. 250,000.00")
        ref = st.text_input("Bank or provider reference" + ("" if live else " (optional)"))
        memo = st.text_input("Note (optional)")
        attest = st.checkbox("This money is already in my bank account, mark it verified now") if live else True
        if st.form_submit_button("Record funds", type="primary"):
            try:
                cents = to_cents(amt)
                r_ = ref.strip() or (f"TEST-{int(time.time())}" if not live else "")
                ledger.record_deposit(cents, r_, memo + (" [owner confirmed funds in account]" if live and attest else ""), me, verified=bool(attest))
                st.toast(f"Recorded {fmt(cents)}" + ("" if attest else ". Verify it below before releasing."))
                st.rerun()
            except ValueError as e:
                st.error(str(e))
    deps = [r for r in ledger.ledger_rows() if r["kind"] == "DEPOSIT"]
    for d in deps:
        c1, c2, c3 = st.columns([3, 2, 2])
        c1.write(f"{ui.ts(d['ts'])} · **{fmt(d['amount_cents'])}** · ref {d['ref']}")
        c2.write("✅ verified" if d["verified"] else "⏳ unverified")
        if not d["verified"] and c3.button("Mark verified", key=f"vf{d['id']}"):
            ledger.verify_deposit(d["id"], me); st.rerun()

# ---------------- reserve to vault ----------------
if _sec == 'Reserve to vault':
    st.subheader("Fund the vault")
    st.caption("Three steps: (1) reserve volume is the ceiling, (2) type the amount you hold as your reserve balance, (3) move what you want to spend into "
               "the vault. Payments (BSB and account number, PayID, BPAY) are then sent from the vault balance.")
    st.caption(f"Movable now (verified reserve funds): **{fmt(s['reserve_available_cents'])}**")
    if s["ceiling_cents"] and s["reserve_cents"] == 0 and s["vault_cents"] == 0:
        st.info("The reserve volume is only the ceiling. There is nothing to move yet. Type your reserve balance below first.")
    if ui.can("record_deposit"):
        st.markdown("**Add reserve balance (manual)**")
        with st.form("add_reserve", clear_on_submit=True):
            amt0 = st.text_input("Reserve balance amount (AUD)", placeholder="e.g. 250,000.00")
            n0, r0 = st.columns(2)
            note0 = n0.text_input("Note (optional)", key="ar_note")
            ref0 = r0.text_input("Bank or provider reference (optional)", key="ar_ref")
            also = st.checkbox("Also move this amount into the vault now", value=True)
            fit = st.checkbox("Raise the reserve volume to fit if needed") if ui.can("change_volume") else False
            own = st.checkbox("I confirm this money is held in my own bank or provider account")
            go0 = st.form_submit_button("Add reserve balance", type="primary")
        if go0:
            try:
                if not own:
                    raise ValueError("Tick the confirmation box: only record money you actually hold.")
                if also and not ui.can("move_funds"):
                    raise ValueError("Your role cannot move funds to the vault. Untick that option.")
                cents0 = to_cents(amt0)
                ledger.add_reserve_balance(cents0, me, note0, ref0, move_to_vault_too=also, raise_ceiling=fit)
                st.success(f"Added {fmt(cents0)} to the reserve" + (" and moved it to the vault." if also else "."))
                st.rerun()
            except ValueError as e:
                st.error(str(e))
    st.markdown("**Move between reserve and vault**")
    if not ui.can("move_funds"):
        st.info("Your role cannot move funds. An admin or approver can.")
    else:
        direction = st.radio("Direction", ["Reserve → Vault", "Vault → Reserve"], horizontal=True, key="mv_dir")
        to_vault = direction.startswith("Reserve")
        limit_c = s["reserve_available_cents"] if to_vault else s["available_cents"]
        pw_ok = ui.password_ok("pw_move", "Your password (needed to move funds)")
        with st.form("move_form", clear_on_submit=True):
            amt = st.text_input("Amount to move (AUD)", placeholder="e.g. 50,000.00", help=f"Up to {fmt(limit_c)}")
            note = st.text_input("Note (optional)", placeholder="e.g. Weekly payments budget")
            bref = st.text_input("Bank or provider transfer reference (optional)", help="If this move is also made between two accounts at your bank or provider, put that reference here to match it later.")
            all_ = st.checkbox("Move everything available")
            go_ = st.form_submit_button("Move funds", type="primary")
        if go_:
            if not pw_ok:
                st.error("Enter your password above the form, then press Move funds again.")
            else:
                try:
                    cents = limit_c if all_ else to_cents(amt)
                    (ledger.move_to_vault if to_vault else ledger.move_to_reserve)(cents, me, note, bref)
                    st.success(f"Moved {fmt(cents)} {'to the vault' if to_vault else 'back to the reserve'}.")
                    st.rerun()
                except ValueError as e:
                    st.error(str(e))
    moves = [r for r in ledger.ledger_rows() if r["kind"] in ("FROM_RESERVE", "FROM_VAULT")]
    if moves:
        st.markdown("**Recent moves**")
        ui.show_df(pd.DataFrame([{"When": ui.ts(r["ts"]), "Direction": "Reserve → Vault" if r["kind"] == "FROM_RESERVE" else "Vault → Reserve",
                                  "Amount": fmt(r["amount_cents"]), "Reference": r["ref"], "Note": r["memo"]} for r in moves[:30]]), hide_index=True)

# ---------------- export ----------------
if _sec == 'Export direct credit':
    _ps, _pm = providers.status()
    (st.success if _ps == "ready" else st.info)("Payment provider: " + _pm + ("" if _ps == "ready" else " Add provider credentials in Security and Settings to enable one-click sending."))
    st.subheader("Send approved releases as a bank direct-credit file (ABA)")
    with st.expander("File formats used (coded structure)"):
        st.markdown("""
**ABA (Cemtext / direct entry).** Plain text, every line exactly 120 characters. Line 1 is the type 0 header (user name, APCA ID, date).
Each payment is a type 1 line: BSB (7), account (9), transaction code **50** (externally initiated credit), amount in **cents, 10 digits**, payee name (32), reference (18).
A balancing type 1 debit (code 13) matches your own account. The last line is type 7: net, credit and debit totals and the record count. The app checks all of this before it gives you the file.
Limit: AUD 99,999,999.99 per payment and per file, because the amount field is 10 digits of cents.

**ISO 20022 pain.001.001.03.** The international XML message for customer credit transfers: group header (message ID, count, control sum),
payment information (your account and bank), then one credit transfer per payee with amount, currency AUD, payee bank (BSB as code AUBSB) and account, and the purpose text.

**PayID and BPAY** are sent from your bank app or a provider. The app lists exactly what to enter. Currency is AUD only; no currency conversion is applied to transfers.
""")
    st.caption("Upload the file in your bank's business internet banking (bulk payments / direct entry). Your bank moves the money. PayID and BPAY payments cannot be in an ABA file, so they are listed for you to send from your bank app.")
    with st.expander("Your bank file details", expanded=not db.get_setting("aba_own_bsb")):
        with st.form("abacfg"):
            g = lambda k, d="": db.get_setting(k, d)
            bank = st.text_input("Bank abbreviation (3 letters, e.g. CBA, WBC, NAB, ANZ)", g("aba_bank", "CBA"))
            uname = st.text_input("Account name (user name on the file, max 26 chars)", g("aba_user"))
            apca = st.text_input("APCA user ID (6 digits, from your bank)", g("aba_apca"))
            desc = st.text_input("File description (max 12 chars)", g("aba_desc", "VAULTPAY"))
            obsb = st.text_input("Your account BSB", g("aba_own_bsb"))
            oacc = st.text_input("Your account number", g("aba_own_acc"))
            rem = st.text_input("Remitter name (max 16 chars)", g("aba_rem"))
            if st.form_submit_button("Save"):
                try:
                    security.norm_bsb(obsb); security.norm_account(oacc)
                    if not apca.isdigit() or len(apca) > 6:
                        raise ValueError("APCA user ID must be up to 6 digits.")
                    for k, v in dict(aba_bank=bank, aba_user=uname, aba_apca=apca, aba_desc=desc, aba_own_bsb=obsb, aba_own_acc=oacc, aba_rem=rem).items():
                        db.set_setting(k, v)
                    security.audit(me, "ABA_SETTINGS_SAVED", "")
                    st.success("Saved.")
                except ValueError as e:
                    st.error(str(e))
    sheet = [r for r in ledger.releases() if r["status"] in ("APPROVED", "EXPORTED")]
    if sheet:
        out = io.StringIO(); w = csv.writer(out)
        w.writerow(["Release", "Method", "Payee name", "BSB", "Account", "PayID", "BPAY biller", "BPAY ref", "Amount AUD", "Reference", "Status"])
        for r in sheet:
            w.writerow([r["id"], r.get("method") or "BANK", r["payee_name"], r["bsb"], r["account"], r["payid"], r.get("biller_code") or "", r.get("crn") or "", f"{r['amount_cents'] / 100:.2f}", r["reference"], r["status"]])
        st.download_button("Download payment instruction sheet (CSV)", out.getvalue(), file_name="vault_payment_plt_instructions.csv", mime="text/csv")
    ready = ledger.releases("APPROVED")
    bank_ready = [r for r in ready if r["bsb"] and r["account"]]
    payid_ready = [r for r in ready if (r.get("method") or "BANK") != "BANK" and not (r["bsb"] and r["account"])]
    if bank_ready:
        st.write(f"**{len(bank_ready)}** approved bank-account release(s), total **{fmt(sum(r['amount_cents'] for r in bank_ready))}**")
        fmt_choice = st.radio("File format", ["ABA (Australian direct entry)", "ISO 20022 pain.001 (XML)"], horizontal=True, key="exp_fmt")
        if st.button("Build file"):
            try:
                batch = dt.datetime.now().strftime("%Y%m%d%H%M%S")
                if fmt_choice.startswith("ABA"):
                    text = aba.build_aba(bank_ready, bank_abbrev=db.get_setting("aba_bank", "CBA"), user_name=db.get_setting("aba_user", ""),
                                         apca_id=db.get_setting("aba_apca", "0"), description=db.get_setting("aba_desc", "VAULTPAY"),
                                         own_bsb=db.get_setting("aba_own_bsb", ""), own_account=db.get_setting("aba_own_acc", ""),
                                         remitter=db.get_setting("aba_rem", ""))
                    problems = aba.validate_aba(text)
                    if problems:
                        raise ValueError("The file failed its own format check and was not created: " + " ".join(problems))
                    name_, mime_ = f"vault_payment_plt_{batch}.aba", "text/plain"
                else:
                    text = aba.build_pain001(bank_ready, debtor_name=db.get_setting("aba_user", "") or "VAULT", own_bsb=db.get_setting("aba_own_bsb", ""),
                                             own_account=db.get_setting("aba_own_acc", ""), msg_id=f"VPP{batch}")
                    name_, mime_ = f"vault_payment_plt_{batch}.xml", "application/xml"
                ledger.mark_exported([r["id"] for r in bank_ready], "ABA" if fmt_choice.startswith("ABA") else "ISO20022", batch, me)
                st.session_state["aba_file"] = (name_, text, mime_)
            except ValueError as e:
                st.error(str(e))
        if "aba_file" in st.session_state:
            fn, text, mime_ = st.session_state["aba_file"]
            st.success("File built and format-checked (every line 120 characters, totals and counts match)." if fn.endswith(".aba") else "File built (well-formed ISO 20022 pain.001.001.03). Confirm your bank accepts this layout.")
            st.download_button("Download " + fn, text, file_name=fn, mime=mime_)
    else:
        st.info("No approved bank-account releases waiting.")
    if payid_ready:
        st.write("**PayID and BPAY releases to send from your bank app or provider** (reference: Vault Payment Plt):")
        ui.show_df(pd.DataFrame([{"Release": r["id"], "Payee": r["payee_name"], "Method": security.METHODS.get(r.get("method") or "BANK"), "Send to": ledger.destination_text(r), "Amount": fmt(r["amount_cents"]), "Reference": r["reference"]} for r in payid_ready]), hide_index=True)
        if st.button("Mark these as sent"):
            ledger.mark_exported([r["id"] for r in payid_ready], "PAYID", dt.datetime.now().strftime("%Y%m%d%H%M%S"), me); st.rerun()

# ---------------- ceiling ----------------
if _sec == 'Reserve volume':
    st.subheader("Reserve volume")
    st.write(f"Current reserve volume: **{fmt(s['ceiling_cents'])}**. It is the most the vault may hold. The balance itself comes from the funds you record.")
    if s["ceiling_cents"] == 0:
        st.info("Set it in Step 1 at the top of this page.")
    else:
        volume_form(first=False)
