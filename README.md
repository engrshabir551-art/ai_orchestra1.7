# AI Orchestra Platform (Streamlit)

A personal agent system: 19 agents, the Vault E-Terminal (AUD wallet with manual transfers), a gold register, accounting, build tools and 6/12/24-hour autonomous jobs. Built for your own use with your own money, not for customers or third parties.

## Run
Windows: double-click `run_windows.bat`. Other systems: `pip install -r requirements.txt` then `streamlit run app.py`.
Add an AI key in the app: **Security and Settings > AI keys**. You need ONE of: an **OpenRouter** key (sk-or-..., many models, one bill) or an **Anthropic** key (sk-ant-...). Step-by-step instructions for creating each key are on that page.

**Create an OpenRouter key:** openrouter.ai > sign in > Credits (add credit) > Keys > Create Key > name it, set a credit limit > copy it (shown once) > paste in the app > Save key > Test key. Then choose a model ID from openrouter.ai/models on the same page.
Phone / Play Store / hosting: see `PLAYSTORE_GUIDE.md`.
First launch asks you to create the admin account. Data lives in `data/orchestra.db` (back it up).

## Quick start in the Vault E-Terminal
1. **Set your reserve volume** (the most the vault may hold, e.g. 1,000,000,000). Commas are fine.
2. **Record the funds you hold** on the Deposits tab. In live mode tick "This money is already in my bank account".
3. **Manual release** to a PayID, or BSB + account number + account name. Approve it, then export the bank file or send the PayID from your bank app.

## How real money works here
* The vault **records and controls** money. It never creates it. Balance only comes from deposits you record
  with a bank/provider reference. The **reserve ceiling** starts at AUD 0.00. You set it (for example 1,000,000,000) before recording deposits. It is a cap.
* **TEST mode** (default) has separate books and is labelled everywhere. **LIVE mode** counts a deposit as
  available only after you verify it against your bank statement.
* Release = draft -> you re-enter your password to approve (funds committed in ledger) -> **export** -> you mark paid
  after the bank confirms. Every transfer carries the reference **Vault Payment Plt**.
* **BSB + account + name:** the app builds a standard **ABA direct-entry file**. Upload it in your bank's business
  internet banking and the bank moves the money. **PayID:** listed for you to send from your bank app/provider.
* No card numbers or CVCs are collected or stored.
* Agents can read the vault and **draft** releases. No agent tool can approve, export or send money.

## Go-live checklist
1. Use your own bank account that supports bulk direct credit (ABA files) in online banking, and get your APCA user ID from the bank. Ask the bank to raise your daily online payment limit if needed.
2. Security and Settings: set limits and dual control. Vault Terminal > Export: enter your bank file details.
3. Fund the reserve account. Switch to LIVE. Record each deposit, then mark it verified against the statement.
4. Do a small first release, upload its ABA file, confirm the bank result, then mark paid.

## Gold
Add bars only if a custodian holds them (custodian + certificate reference required). Value = fine oz x USD/oz,
converted at USD/AUD. "Fetch live prices" uses gold-api.com and frankfurter.app; if unreachable, enter prices manually.

## Limits to know
* Autonomous jobs run in a background thread of the Streamlit server: keep the server running (VPS/always-on PC).
* Memory is a searchable database table (keyword search), not a vector database.
* Direct bank-API payouts are not built in (they need your bank's or provider's own API access). ABA file export and PayID instructions are the supported routes.
* Back up `data/`, put the app behind HTTPS (e.g. a reverse proxy) before exposing it beyond your own network.

## Tests
`python -m pytest tests` (26 tests: ledger, limits, ABA format, audit chain, gold, agents, Streamlit pages).

## Vault E-Terminal: payment methods, authorisation and finance

**Payment methods** (all use the reference "Vault Payment Plt"): bank transfer (BSB + account number + account name), PayID by email, PayID by mobile, PayID by ABN, and BPAY (biller code + reference). Bank transfers export as an ABA file for your bank's bulk-pay upload. PayID and BPAY appear as an instruction list to send from your bank app or provider, then you mark them sent and paid. Direct provider API sending needs a provider account and is added when you have sandbox credentials.

**Roles**: admin (everything), approver (approve, export, settle, cancel), operator (draft releases, record funds), viewer (dashboards). Agents can only draft.

**Authorisation policy** (Security and Settings): maximum per release, daily and 30-day limits, type-the-amount confirmation, second approver, cooling-off period, saved-payees-only mode, and optional authenticator-app two-factor (turn on under Vault Dashboard > Authorisation). All rules are enforced in the ledger, not just the screens.

**Vault Dashboard**: balance and reserve utilisation, cash-flow charts, payments by method, category and payee, limit usage, pending approvals, budgets, activity feed. **Accounting** adds a running-balance statement, payee ledger and reconciliation.

## Connecting a real payment provider
Security and Settings > Payment provider: choose the provider, sandbox or live, source account ID, and paste the API key/secret. Keys are stored in `data/secrets.env`, not the database. When a connector for that provider is installed (written from the provider's docs and tested in its sandbox), approved releases show a **Send now** button; each release can be sent only once, and a failed send leaves it approved with nothing sent. Without a provider the app stays in manual mode (ABA file and PayID/BPAY instructions). The connector interface is `core/providers.py` (`Adapter.check` and `Adapter.send`).

## Reserve balance and vault balance
Funds you record on the Deposits tab go into the **reserve balance**. Payments are made only from the **vault balance**. Move funds yourself on Vault E-Terminal > **Reserve to vault** (amount, optional note and bank reference, your password), and move unused funds back with Vault -> Reserve. Only verified reserve funds can be moved. The reserve volume (ceiling) caps total holdings (reserve + vault). Admin and approver roles can move funds. The move is bookkeeping inside the app: if your reserve and spending money sit in different bank or provider accounts, make the matching transfer there and put its reference in the move.

## Deploying from GitHub
Keep the repository private. `.gitignore` already excludes `data/`, `secrets.env` and `*.db`. Streamlit Cloud erases local files on restart, so for real use run the app where the `data/` folder persists (a VPS or a host with a disk; see the Dockerfile).

## Quick flow: reserve balance -> vault -> payments
Vault E-Terminal > **Reserve to vault**: type the reserve balance you hold (tick "also move into the vault" to do both in one step), then use **Manual release** to pay by BSB + account number, PayID (email, mobile, ABN) or BPAY from the vault balance.

## Receipts, tracking and file formats
Every accepted payment has a receipt (number VPP-T/L-000001) with the purpose, bank reference and the timeline: instruction created, accepted (approved), left our account (sent), received by the recipient, and the time each step took. Record the real sent and received times from your bank statement on the release (Releases > Record when it was sent and received). Receipts download as HTML (print to PDF) or text; Receipts and tracking lists everything not yet received and flags payments taking longer than usual. Times show in the time zone set in Security and Settings (default Australia/Sydney).
Bank files: ABA (every line 120 characters, code 50 credit, totals and counts checked before download) and ISO 20022 pain.001.001.03 XML. Amounts are AUD with at most 2 decimals; anything else is rejected, never rounded.

## Scheduled payments
Vault E-Terminal > Scheduled payments: set a payment to repeat weekly, fortnightly, monthly, quarterly or yearly (optional end date or number of payments). When due, the app creates a draft only; a person approves it. If the vault is short the draft is not created and the reason is shown, then it tries again. Missed periods are skipped, never paid in a batch. See `TESTING.md` for the full test plan.
