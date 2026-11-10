"""Users, password hashing, tamper-evident audit log and payee/limit checks (the Security agent's rules)."""
import base64
import hashlib
import hmac
import os
import re
import struct
import time

from . import authz, db
from .money import fmt


def _hash(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 240_000).hex()


ROLES = tuple(authz.ROLES)


def create_user(username: str, password: str, role: str = "admin"):
    if role not in ROLES:
        raise ValueError(f"Role must be one of {ROLES}.")
    if len(password) < 10:
        raise ValueError("Password must be at least 10 characters.")
    salt = os.urandom(16).hex()
    db.x("INSERT INTO users(username,salt,pw_hash,role) VALUES(?,?,?,?)", (username.strip(), salt, _hash(password, salt), role))
    audit(username, "USER_CREATED", f"role={role}")


MAX_FAILS, LOCK_SECONDS = 5, 900


def is_locked(username: str) -> bool:
    """5 failed sign-ins in 15 minutes (since the last good sign-in) locks that username for the rest of the window."""
    name = username.strip()
    ok = db.q1("SELECT COALESCE(MAX(ts),0) t FROM audit WHERE actor=? AND action='LOGIN'", (name,))["t"]
    since = max(ok, time.time() - LOCK_SECONDS)
    n = db.q1("SELECT COUNT(*) n FROM audit WHERE actor=? AND action='LOGIN_FAILED' AND ts>?", (name, since))["n"]
    return n >= MAX_FAILS


def verify_user(username: str, password: str):
    if is_locked(username):
        return None
    u = db.q1("SELECT * FROM users WHERE username=?", (username.strip(),))
    if not u:
        return None
    if hmac.compare_digest(u["pw_hash"], _hash(password, u["salt"])):
        return u
    audit(username, "LOGIN_FAILED", "")
    return None


def has_users() -> bool:
    return db.q1("SELECT COUNT(*) n FROM users")["n"] > 0


def audit(actor: str, action: str, detail: str = ""):
    last = db.q1("SELECT hash FROM audit ORDER BY id DESC LIMIT 1")
    prev = last["hash"] if last else "GENESIS"
    ts = time.time()
    h = hashlib.sha256(f"{prev}|{ts}|{actor}|{action}|{detail}".encode()).hexdigest()
    db.x("INSERT INTO audit(ts,actor,action,detail,prev_hash,hash) VALUES(?,?,?,?,?,?)", (ts, actor, action, detail, prev, h))


def verify_audit_chain():
    """Return (ok, broken_at_id). Any edited or deleted row breaks the chain."""
    prev = "GENESIS"
    for r in db.q("SELECT * FROM audit ORDER BY id"):
        h = hashlib.sha256(f"{prev}|{r['ts']}|{r['actor']}|{r['action']}|{r['detail']}".encode()).hexdigest()
        if r["prev_hash"] != prev or r["hash"] != h:
            return False, r["id"]
        prev = r["hash"]
    return True, None


# ---------- roles (capabilities live in core/authz.py) ----------
role_of = authz.role_of


# ---------- payment methods ----------
METHODS = {
    "BANK": "Bank transfer (BSB + account number)",
    "PAYID_EMAIL": "PayID: email address",
    "PAYID_MOBILE": "PayID: mobile phone",
    "PAYID_ABN": "PayID: ABN",
    "BPAY": "BPAY (biller code + reference)",
}
_EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


def norm_bsb(bsb: str) -> str:
    d = re.sub(r"\D", "", bsb or "")
    if len(d) != 6:
        raise ValueError("BSB must be 6 digits.")
    return f"{d[:3]}-{d[3:]}"


def norm_account(acc: str) -> str:
    d = re.sub(r"\D", "", acc or "")
    if not 6 <= len(d) <= 9:
        raise ValueError("Account number must be 6 to 9 digits.")
    return d


def abn_valid(abn: str) -> bool:
    d = [int(c) for c in re.sub(r"\D", "", abn or "")]
    if len(d) != 11:
        return False
    d[0] -= 1
    return sum(w * x for w, x in zip((10, 1, 3, 5, 7, 9, 11, 13, 15, 17, 19), d)) % 89 == 0


def payid_type(pid: str):
    p = (pid or "").strip()
    if _EMAIL.fullmatch(p):
        return "EMAIL"
    d = re.sub(r"[\s\-()]", "", p)
    if re.fullmatch(r"(\+?61|0)4\d{8}", d):
        return "MOBILE"
    if re.fullmatch(r"\d{11}", d) and abn_valid(d):
        return "ABN"
    return None


def norm_payid(pid: str) -> str:
    t = payid_type(pid)
    p = (pid or "").strip()
    if t == "EMAIL":
        return p.lower()
    d = re.sub(r"\D", "", p)
    if t == "MOBILE":
        return "0" + d[2:] if d.startswith("61") else d
    if t == "ABN":
        return d
    raise ValueError("PayID must be an email address, an Australian mobile number, or a valid 11-digit ABN.")


def norm_biller(b: str) -> str:
    d = re.sub(r"\D", "", b or "")
    if not 3 <= len(d) <= 6:
        raise ValueError("BPAY biller code must be 3 to 6 digits.")
    return d


def norm_crn(c: str) -> str:
    d = re.sub(r"\D", "", c or "")
    if not 2 <= len(d) <= 21:
        raise ValueError("BPAY reference number must be 2 to 21 digits.")
    return d


def infer_method(bsb, account, payid, biller_code):
    if biller_code:
        return "BPAY"
    if bsb or (account and not payid):
        return "BANK"
    t = payid_type(payid) if payid else None
    if t:
        return "PAYID_" + t
    if payid:
        raise ValueError("PayID must be an email address, an Australian mobile number, or a valid 11-digit ABN.")
    raise ValueError("Enter a BSB and account number, a PayID, or a BPAY biller code and reference.")


def normalise_destination(method, bsb, account, payid, biller_code, crn=""):
    """Validate and normalise where the money goes. Returns dict(method,bsb,account,payid,biller_code,crn)."""
    method = method or infer_method(bsb, account, payid, biller_code)
    if method not in METHODS:
        raise ValueError("Unknown payment method.")
    out = {"method": method, "bsb": "", "account": "", "payid": "", "biller_code": "", "crn": ""}
    if method == "BANK":
        out["bsb"], out["account"] = norm_bsb(bsb), norm_account(account)
    elif method.startswith("PAYID_"):
        want = method.split("_", 1)[1]
        if payid_type(payid) != want:
            raise ValueError({"EMAIL": "Enter a valid PayID email address.", "MOBILE": "Enter a valid Australian mobile number (04xx xxx xxx).",
                              "ABN": "Enter a valid 11-digit ABN."}[want])
        out["payid"] = norm_payid(payid)
    else:
        out["biller_code"], out["crn"] = norm_biller(biller_code), norm_crn(crn)
    return out


def payee_known(d: dict) -> bool:
    return bool(db.q1("SELECT 1 FROM payees WHERE method=? AND COALESCE(bsb,'')=? AND COALESCE(account,'')=? AND COALESCE(payid,'')=? "
                      "AND COALESCE(biller_code,'')=? AND COALESCE(crn,'')=?",
                      (d["method"], d["bsb"], d["account"], d["payid"], d["biller_code"], d.get("crn", ""))))


def _used(mode, seconds):
    return db.q1("SELECT COALESCE(SUM(amount_cents),0) s FROM releases WHERE mode=? AND status IN('APPROVED','EXPORTED','SETTLED') AND approved_ts>?",
                 (mode, time.time() - seconds))["s"]


def limits_used(mode):
    return {"daily": _used(mode, 86400), "monthly": _used(mode, 30 * 86400)}


# ---------- release checks (Security agent rules) ----------
def check_release(payee_name, bsb, account, payid, amount_cents, available_cents, mode, *, method=None, biller_code="", crn=""):
    """Returns (errors, warnings). Errors block the release, warnings need explicit confirmation."""
    errors, warnings = [], []
    if not payee_name or len(payee_name.strip()) < 2:
        errors.append("Payee name is required.")
    if amount_cents <= 0:
        errors.append("Amount must be greater than zero.")
    if amount_cents > available_cents:
        errors.append(f"Amount exceeds the vault balance available to release ({fmt(available_cents)}). Move funds from the reserve to the vault first.")
    dest = None
    try:
        dest = normalise_destination(method, bsb, account, payid, biller_code, crn)
    except ValueError as e:
        errors.append(str(e))
    g = db.get_setting
    maxr = int(g("max_release_cents", "0"))
    if maxr > 0 and amount_cents > maxr:
        errors.append(f"Amount is above your single-release maximum of {fmt(maxr)}.")
    used = limits_used(mode)
    daily, monthly = int(g("daily_limit_cents", "0")), int(g("monthly_limit_cents", "0"))
    if daily > 0 and used["daily"] + amount_cents > daily:
        errors.append(f"Daily release limit {fmt(daily)} would be exceeded (already {fmt(used['daily'])} in 24h).")
    if monthly > 0 and used["monthly"] + amount_cents > monthly:
        errors.append(f"30-day release limit {fmt(monthly)} would be exceeded (already {fmt(used['monthly'])} in 30 days).")
    if dest and not payee_known(dest):
        if g("payee_mode", "open") == "saved_only":
            errors.append("Payee mode is 'saved payees only'. Add this payee in Security and Settings first.")
        else:
            warnings.append("New payee: you have not paid this destination before. Check the details with the payee.")
    large = int(g("large_release_cents", "10000000"))
    if amount_cents >= large:
        warnings.append(f"Large release (at or above {fmt(large)}). Check the amount carefully.")
    if payee_name and db.q1("SELECT 1 FROM releases WHERE amount_cents=? AND payee_name=? AND ts>? AND status!='CANCELLED'",
                            (amount_cents, payee_name.strip(), time.time() - 3600)):
        warnings.append("An identical release to this payee was created in the last hour.")
    return errors, warnings


# ---------- TOTP (authenticator app) second factor, RFC 6238, no extra libraries ----------
def totp_new_secret() -> str:
    return base64.b32encode(os.urandom(20)).decode().rstrip("=")


def _hotp(secret: str, counter: int, digits: int = 6) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    h = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    o = h[-1] & 15
    return str((struct.unpack(">I", h[o:o + 4])[0] & 0x7FFFFFFF) % 10 ** digits).zfill(digits)


def totp_code(secret: str, t: float = None) -> str:
    return _hotp(secret, int((t if t is not None else time.time()) // 30))


def totp_uri(username: str, secret: str) -> str:
    return f"otpauth://totp/AI%20Orchestra:{username}?secret={secret}&issuer=AI%20Orchestra"


def totp_secret_of(username: str) -> str:
    u = db.q1("SELECT totp_secret FROM users WHERE username=?", (username,))
    return (u["totp_secret"] or "") if u else ""


def totp_check(username: str, code: str, now: float = None) -> bool:
    """Verify a code (30s steps, one step of drift allowed). A code can only be used once."""
    secret = totp_secret_of(username)
    code = re.sub(r"\D", "", code or "")
    if not secret or len(code) != 6:
        return False
    now = now if now is not None else time.time()
    cur = int(now // 30)
    last = int(db.get_setting(f"totp_last_{username}", "0"))
    for step in (cur - 1, cur, cur + 1):
        if step > last and hmac.compare_digest(_hotp(secret, step), code):
            db.set_setting(f"totp_last_{username}", step)
            return True
    return False


def enable_totp(username: str, secret: str, code: str):
    tmp = db.q1("SELECT 1 FROM users WHERE username=?", (username,))
    if not tmp:
        raise ValueError("Unknown user.")
    if not any(hmac.compare_digest(_hotp(secret, s_), re.sub(r"\D", "", code or "").zfill(6)) for s_ in
               (int(time.time() // 30) - 1, int(time.time() // 30), int(time.time() // 30) + 1)):
        raise ValueError("That code is not correct. Check the time on your phone and try again.")
    db.x("UPDATE users SET totp_secret=? WHERE username=?", (secret, username))
    audit(username, "MFA_ENABLED", "")


def disable_totp(username: str):
    db.x("UPDATE users SET totp_secret='' WHERE username=?", (username,))
    audit(username, "MFA_DISABLED", "")
