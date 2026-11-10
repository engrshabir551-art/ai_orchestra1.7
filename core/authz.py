"""Roles and capabilities. Every money-moving function in ledger.py checks these, not just the screens."""
from . import db

ROLES = ["admin", "approver", "operator", "viewer"]

CAPS = {
    "admin": {"view", "draft", "record_deposit", "verify_deposit", "move_funds", "approve", "export", "settle", "cancel", "manage_payees",
              "manage_budgets", "change_volume", "manage_users", "manage_limits", "manage_gold", "manage_keys", "run_agents", "switch_mode", "schedule"},
    "approver": {"view", "approve", "verify_deposit", "move_funds", "schedule", "export", "settle", "cancel", "run_agents"},
    "operator": {"view", "draft", "record_deposit", "run_agents", "schedule"},
    "viewer": {"view"},
    "agent": {"view", "draft"},  # AI agents: read and draft only, never approve or send
}

CAP_LABELS = {
    "view": "View dashboards and reports", "draft": "Create release drafts", "record_deposit": "Record funds received", "move_funds": "Move funds between the reserve and the vault",
    "verify_deposit": "Verify deposits against the bank statement", "approve": "Approve releases (commit funds)",
    "export": "Export bank file / send payment instructions", "settle": "Confirm a payment as paid", "cancel": "Cancel releases and return funds",
    "manage_payees": "Add or remove approved payees", "manage_budgets": "Set category budgets", "change_volume": "Change the reserve volume",
    "manage_users": "Add users and change roles", "manage_limits": "Change limits and approval policy", "manage_gold": "Add, move and sell gold",
    "manage_keys": "Manage API keys", "run_agents": "Use agents and jobs", "switch_mode": "Switch between test and live books", "schedule": "Set up and manage scheduled payments",
}


class NotAuthorised(ValueError):
    """A ValueError subclass so existing screens show the message without special handling."""


def role_of(actor: str):
    if actor and actor.startswith("agent:"):
        return "agent"
    u = db.q1("SELECT role FROM users WHERE username=?", (actor,))
    return u["role"] if u else None


def can(actor: str, cap: str) -> bool:
    return cap in CAPS.get(role_of(actor) or "", set())


def role_can(role: str, cap: str) -> bool:
    return cap in CAPS.get(role, set())


def require(actor: str, cap: str):
    if not can(actor, cap):
        raise NotAuthorised(f"Not authorised: your role ({role_of(actor) or 'unknown'}) cannot {CAP_LABELS.get(cap, cap).lower()}.")


def approver_count() -> int:
    return db.q1("SELECT COUNT(*) n FROM users WHERE role IN ('admin','approver')")["n"]
