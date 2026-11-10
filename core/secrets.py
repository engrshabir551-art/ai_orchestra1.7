"""API keys entered in the app are saved to data/secrets.env on this computer (never in the database or audit log).

Honest limits: the file is plain text, protected only by your computer's file permissions. Anyone who can read
the data folder can read the key. Use a key with a spend limit, and keep the folder private.
"""
import os
import re

from . import db

PATH = os.environ.get("ORCHESTRA_SECRETS", os.path.join(os.path.dirname(db.DB_PATH), "secrets.env"))
ALLOWED = {"ANTHROPIC_API_KEY": "sk-ant-", "OPENROUTER_API_KEY": "sk-or-", "FIGMA_TOKEN": "", "PROVIDER_API_KEY": "", "PROVIDER_API_SECRET": ""}
_SAFE = re.compile(r"^[A-Za-z0-9_\-\.]{8,300}$")


def _read() -> dict:
    out = {}
    try:
        with open(PATH, encoding="utf-8") as f:
            for line in f:
                if "=" in line:
                    k, v = line.rstrip("\r\n").split("=", 1)
                    if k in ALLOWED:
                        out[k] = v
    except FileNotFoundError:
        pass
    return out


def _write(d: dict):
    os.makedirs(os.path.dirname(PATH), exist_ok=True)
    tmp = PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for k, v in d.items():
            f.write(f"{k}={v}\n")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, PATH)


def load_into_env():
    """Call at startup. A key already set in the real environment wins over the saved file."""
    for k, v in _read().items():
        os.environ.setdefault(k, v)


def save(name: str, value: str):
    if name not in ALLOWED:
        raise ValueError("Unknown key name.")
    value = (value or "").strip()
    if not _SAFE.match(value):
        raise ValueError("That does not look like a valid key (letters, numbers, - _ . only; no spaces).")
    if ALLOWED[name] and not value.startswith(ALLOWED[name]):
        raise ValueError(f"This key should start with {ALLOWED[name]}")
    d = _read(); d[name] = value
    _write(d)
    os.environ[name] = value


def delete(name: str):
    d = _read(); d.pop(name, None)
    _write(d)
    os.environ.pop(name, None)


def source(name: str):
    if name in _read():
        return "saved file" if _read()[name] == os.environ.get(name) else "environment"
    return "environment" if os.environ.get(name) else None


def mask(v: str) -> str:
    return (v[:7] + "..." + v[-4:]) if v and len(v) > 14 else "set"


def check_anthropic():
    """Make a tiny real call to confirm the key works. Returns (ok, message)."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return False, "No key set."
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=key)
        client.messages.create(model=os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5-5"), max_tokens=8,
                               messages=[{"role": "user", "content": "Reply with OK"}])
        return True, "Key works."
    except Exception as e:
        name = type(e).__name__
        if "Authentication" in name or "PermissionDenied" in name:
            return False, "Anthropic rejected this key. Check it was copied fully and has credit."
        return False, f"Could not verify ({name}): {str(e)[:200]}"


def check_openrouter():
    """Ask OpenRouter about this key (free call, spends no credit). Returns (ok, message)."""
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        return False, "No key set."
    try:
        import requests
        r = requests.get("https://openrouter.ai/api/v1/auth/key", headers={"Authorization": f"Bearer {key}"}, timeout=20)
        if r.status_code == 401:
            return False, "OpenRouter rejected this key. Check it was copied fully."
        r.raise_for_status()
        d = r.json().get("data", {})
        lim = d.get("limit_remaining")
        extra = f" Credit remaining: ${lim:,.2f}." if isinstance(lim, (int, float)) else ""
        return True, "Key works." + extra
    except Exception as e:
        return False, f"Could not verify ({type(e).__name__}): {str(e)[:200]}"
