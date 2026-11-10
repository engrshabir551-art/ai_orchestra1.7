"""Payment provider connection.

The app never holds money. A payments provider (an account the owner opens and funds from his bank) holds it and sends it.
This module is the single place where a provider plugs in:

  * "manual"  - built in. The app builds ABA files and PayID/BPAY instruction lists; the owner sends them from his bank.
  * any other - needs an adapter registered with @register("name") that implements check() and send(release).
                Credentials are entered in Security and Settings > Payment provider and stored in data/secrets.env.

Adapters are written against the provider's own documentation and tested in the provider's sandbox first.
"""
import os
import time

from . import authz, db, ledger, security
from .money import fmt

PROVIDERS = {"manual": "Manual (bank file and PayID instructions)", "zepto": "Zepto", "monoova": "Monoova",
             "airwallex": "Airwallex", "other": "Other provider"}
ADAPTERS = {}


def register(name):
    def deco(cls):
        ADAPTERS[name] = cls
        return cls
    return deco


class Adapter:
    """Implement this for each provider."""
    name = ""
    methods = ("BANK", "PAYID_EMAIL", "PAYID_MOBILE", "PAYID_ABN", "BPAY")  # what this provider can send

    def __init__(self, cfg):
        self.cfg = cfg

    def check(self):
        """Call the provider with the saved credentials. Return (ok, message)."""
        raise NotImplementedError

    def send(self, release: dict):
        """Send one approved release. Use release['id'] as the idempotency key. Return the provider's payment reference."""
        raise NotImplementedError


def config():
    g = db.get_setting
    return {"provider": g("provider", "manual"), "environment": g("provider_env", "sandbox"), "account_id": g("provider_account_id", ""),
            "base_url": g("provider_base_url", ""), "api_key": os.environ.get("PROVIDER_API_KEY", ""),
            "api_secret": os.environ.get("PROVIDER_API_SECRET", "")}


def save_config(provider, environment, account_id, base_url, actor):
    authz.require(actor, "manage_keys")
    if provider not in PROVIDERS:
        raise ValueError("Unknown provider.")
    if environment not in ("sandbox", "live"):
        raise ValueError("Environment must be sandbox or live.")
    db.set_setting("provider", provider); db.set_setting("provider_env", environment)
    db.set_setting("provider_account_id", account_id.strip()); db.set_setting("provider_base_url", base_url.strip())
    security.audit(actor, "PROVIDER_CONFIG", f"{provider} {environment} account={account_id.strip()[:6]}***")


def adapter():
    c = config()
    cls = ADAPTERS.get(c["provider"])
    return cls(c) if cls else None


def status():
    """('manual'|'missing_credentials'|'no_adapter'|'ready', message). Only 'ready' enables sending from the app."""
    c = config()
    if c["provider"] == "manual":
        return "manual", "Manual mode: the app builds bank files and instructions; you send them from your bank."
    if not c["api_key"]:
        return "missing_credentials", f"{PROVIDERS[c['provider']]} selected. Add the API key (and secret if given) to connect."
    if c["provider"] not in ADAPTERS:
        return "no_adapter", (f"Credentials saved for {PROVIDERS[c['provider']]}. The connector for this provider is installed once the "
                              "provider's sandbox access and API documentation are in hand.")
    if ledger.mode() == "LIVE" and c["environment"] != "live":
        return "missing_credentials", "The app is in LIVE mode but the provider is set to sandbox. Switch the provider to live when approved."
    if ledger.mode() == "TEST" and c["environment"] == "live":
        return "missing_credentials", "The provider is set to live but the app is in TEST mode. Use sandbox credentials in TEST mode."
    return "ready", f"Connected to {PROVIDERS[c['provider']]} ({c['environment']})."


def test_connection(actor):
    authz.require(actor, "manage_keys")
    st, msg = status()
    if st != "ready":
        return False, msg
    try:
        ok, m = adapter().check()
    except Exception as e:  # provider/network errors are shown, never raised into the page
        ok, m = False, f"Provider check failed: {e}"
    security.audit(actor, "PROVIDER_CHECK", f"ok={ok}")
    return ok, m


def send_release(release_id: int, actor: str):
    """Send one APPROVED release through the connected provider. Never sends twice: the release is claimed first; if the app dies mid-send it stays claimed until a person checks the provider."""
    authz.require(actor, "export")
    st, msg = status()
    if st != "ready":
        raise ValueError(msg)
    r = db.q1("SELECT * FROM releases WHERE id=?", (release_id,))
    if not r or r["status"] != "APPROVED":
        raise ValueError("Only an approved release that has not been sent can be sent.")
    if r["mode"] != ledger.mode():
        raise ValueError("This release belongs to the other mode.")
    ad = adapter()
    if (r.get("method") or "BANK") not in ad.methods:
        raise ValueError("This provider cannot send that payment method. Use the Export tab instead.")
    with db.conn() as c:  # claim atomically so a double click or second session cannot send twice
        n = c.execute("UPDATE releases SET channel='SENDING' WHERE id=? AND status='APPROVED' AND COALESCE(channel,'')=''", (release_id,)).rowcount
    if n != 1:
        raise ValueError("This release is already being sent or was sent. Check the provider before trying again.")
    try:
        ref = ad.send(r)
    except Exception as e:
        db.x("UPDATE releases SET channel='' WHERE id=?", (release_id,))
        security.audit(actor, "PROVIDER_SEND_FAILED", f"#{release_id} {e}")
        raise ValueError(f"The provider did not accept the payment: {e}. Nothing was sent.")
    db.x("UPDATE releases SET status='EXPORTED', channel=?, batch_id=?, bank_ref=?, sent_ts=? WHERE id=?",
         (f"API:{ad.name or config()['provider']}", str(ref), str(ref)[:60], time.time(), release_id))
    security.audit(actor, "PROVIDER_SENT", f"#{release_id} {fmt(r['amount_cents'])} ref={ref}")
    return ref
