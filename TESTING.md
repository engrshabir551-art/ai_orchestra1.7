# Real test plan (about 30 minutes)

Use **Test books** first (sidebar badge). Nothing here moves real money. Each step says what you should see.

1. **Sign in** as admin. Vault E-Terminal opens.
2. **Reserve volume** (bottom section "Reserve volume"): set 1,000,000. The "Room under ceiling" card shows 1,000,000.00.
3. **Reserve to vault**: type 50,000 as the reserve balance, tick the confirmation, keep "Also move into the vault". Vault balance shows 50,000.00, reserve 0.00.
4. **Pay by each method** (Manual release): Bank transfer (BSB + account), PayID email, PayID mobile, PayID ABN, BPAY. Use "Check a payee's details" first. Wrong formats must show a clear error.
5. **Approve** each in Releases (password, typed amount if shown). The vault balance drops by each amount.
6. **Send**: PayID/BPAY from the instruction list; bank transfers via Export (ABA or ISO 20022 file, format-checked).
7. **Record times**: on each release open "Record when it was sent and received", enter the times and a bank reference, press "Mark as received by recipient".
8. **Receipts and tracking**: every payment has a receipt with the 4-step timeline and transit times. Download the HTML and text receipts.
9. **Scheduled payments**: create a payment starting today. A draft appears in Releases marked "Scheduled payment #1". Approve it. Pause and resume the schedule.
10. **Limits**: Security and Settings > Authorisation policy. Set a per-release limit and try to exceed it: the draft must be refused.
11. **Roles**: add an operator and a viewer. The operator can draft but not approve; the viewer sees dashboards only.
12. **Two-factor**: Vault Dashboard > Authorisation > Authenticator app. Turn it on; sign-in and approvals then ask for the code.
13. **Accounting**: statement, journal, payee ledger and reconciliation agree with what you did.

**Going live:** follow the go-live checklist in Security and Settings > Payment provider. First live payment: AUD 1 to your own account.
