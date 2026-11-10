"""Agent registry, tools and the Claude tool-use loop.

Safety design: agents can READ the vault and DRAFT releases, but there is no tool that approves, exports or
sends money. Only a signed-in human can approve (password re-entry) on the Vault E-Terminal page.
"""
import json
import os
import re
import time

import requests

from . import db, ledger, gold, security
from .money import fmt, to_cents

MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5-5")
WORKSPACE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "workspace")

BASE_RULES = (
    "You are one agent inside the AI Orchestra platform. Be concrete and brief. Never claim to have done something "
    "you did not do with a tool. You cannot approve, send or move money: you may only draft releases for a human to "
    "review. Never invent balances, prices, bank details or gold holdings: read them with your tools. "
    "Money is in AUD. Use the label 'Vault Payment Plt' for vault transfers."
)

# name, group, role, tools
_SPECS = [
    ("Director", "Command and control", "Sets goals, splits work and decides which agent handles each request.", "vault_summary,list_tasks,create_task,recall,remember,gold_valuation"),
    ("Conductor", "Command and control", "Routes tasks between agents, tracks status and resolves conflicts.", "list_tasks,create_task,recall,remember"),
    ("Security guard", "Command and control", "Reviews agent activity and the audit log for anything outside policy.", "audit_tail,list_releases,vault_summary,recall,remember"),
    ("Treasury", "Finance and treasury", "Cash position, reserve ceiling, funding needs and liquidity planning.", "vault_summary,list_releases,gold_valuation,recall,remember"),
    ("Accounting and finance", "Finance and treasury", "Journals, reconciliation, invoices, reporting and tax summaries.", "vault_summary,list_releases,ledger_tail,recall,remember"),
    ("Portfolio management", "Finance and treasury", "Tracks holdings (cash and gold), allocation and performance.", "vault_summary,gold_valuation,recall,remember"),
    ("Wallet agent", "Finance and treasury", "Wallet balances, deposits awaiting verification and ledger questions.", "vault_summary,ledger_tail,list_releases,recall,remember"),
    ("Vault terminal fund manager", "Finance and treasury", "Prepares Vault Payment Plt release drafts from your instructions and checks them before you approve.", "vault_summary,list_releases,draft_release,ledger_tail,recall,remember"),
    ("Commodity vault", "Finance and treasury", "Gold register, bar movements, sales records and AUD valuation.", "gold_valuation,gold_register,recall,remember"),
    ("Entrepreneur", "Business and growth", "Opportunity discovery, business models and go-to-market ideas.", "recall,remember,create_task"),
    ("Business developer", "Business and growth", "Plans to create, scale and grow a business: strategy, pricing, partnerships.", "recall,remember,create_task"),
    ("Research analyst", "Business and growth", "Structured research briefs with clear assumptions and open questions.", "recall,remember,create_task"),
    ("Multi-management facilitator", "Business and growth", "Coordinates several projects, owners and deadlines and reports status.", "list_tasks,create_task,recall,remember"),
    ("App developer", "Build and technology", "Designs and writes app and website code into the workspace folder.", "write_project_file,list_project_files,recall,remember"),
    ("Figma design agent", "Build and technology", "Reads Figma files and turns designs into specs and UI code.", "figma_get_file,write_project_file,recall,remember"),
    ("Open-source integrator", "Build and technology", "Selects and wires open-source libraries and tools for a project.", "write_project_file,list_project_files,recall,remember"),
    ("Program task executor", "Build and technology", "Breaks a goal into tasks and works through them in order.", "list_tasks,create_task,recall,remember"),
    ("Autonomous runner", "Autonomy", "Runs long jobs (6, 12 or 24 hours) toward a target with checkpoints.", "list_tasks,create_task,recall,remember,vault_summary"),
    ("Task and role target manager", "Autonomy", "Defines roles, targets and success measures for agents and jobs.", "list_tasks,create_task,recall,remember"),
]


def seed_agents():
    for name, grp, role, tools in _SPECS:
        if not db.q1("SELECT 1 FROM agents WHERE name=?", (name,)):
            db.x("INSERT INTO agents(name,grp,role,prompt,tools,enabled,custom) VALUES(?,?,?,?,?,1,0)", (name, grp, role, "", tools))


SAFE_TOOLS = ["vault_summary", "list_releases", "ledger_tail", "gold_valuation", "gold_register", "recall", "remember",
              "create_task", "list_tasks", "audit_tail", "write_project_file", "list_project_files", "figma_get_file", "draft_release"]


def add_custom_agent(name, grp, role, prompt, tools):
    name = name.strip()
    if not name or not role.strip():
        raise ValueError("Name and role are required.")
    bad = [t for t in tools if t not in SAFE_TOOLS]
    if bad:
        raise ValueError(f"Unknown tools: {bad}")
    db.x("INSERT INTO agents(name,grp,role,prompt,tools,enabled,custom) VALUES(?,?,?,?,?,1,1)", (name, grp or "Custom", role, prompt, ",".join(tools)))


def list_agents():
    return db.q("SELECT * FROM agents ORDER BY custom, id")


def get_agent(name):
    return db.q1("SELECT * FROM agents WHERE name=?", (name,))


# ---------- tool schemas ----------
def _s(name, desc, props=None, req=None):
    return {"name": name, "description": desc, "input_schema": {"type": "object", "properties": props or {}, "required": req or []}}


TOOL_DEFS = {
    "vault_summary": _s("vault_summary", "Current vault mode, reserve ceiling, balance, available verified balance and pending drafts (AUD)."),
    "list_releases": _s("list_releases", "Recent Vault Payment Plt releases and their status.", {"status": {"type": "string"}}),
    "ledger_tail": _s("ledger_tail", "Most recent ledger entries.", {"n": {"type": "integer"}}),
    "gold_valuation": _s("gold_valuation", "Held gold fine ounces and value in USD and AUD using the latest saved prices."),
    "gold_register": _s("gold_register", "List gold bars with status, location and custodian."),
    "recall": _s("recall", "Search shared memory for text.", {"query": {"type": "string"}}, ["query"]),
    "remember": _s("remember", "Save a durable fact or decision to shared memory.", {"content": {"type": "string"}}, ["content"]),
    "create_task": _s("create_task", "Create a task for an agent.", {"agent": {"type": "string"}, "title": {"type": "string"}}, ["agent", "title"]),
    "list_tasks": _s("list_tasks", "List open tasks."),
    "audit_tail": _s("audit_tail", "Most recent audit log entries.", {"n": {"type": "integer"}}),
    "draft_release": _s("draft_release", "Create a DRAFT Vault Payment Plt release for human approval. Cannot approve or send.",
                        {"payee_name": {"type": "string"}, "method": {"type": "string", "description": "BANK, PAYID_EMAIL, PAYID_MOBILE, PAYID_ABN or BPAY"},
                         "bsb": {"type": "string"}, "account": {"type": "string"}, "payid": {"type": "string", "description": "email, mobile or ABN"},
                         "biller_code": {"type": "string"}, "crn": {"type": "string", "description": "BPAY reference"},
                         "category": {"type": "string"}, "amount_aud": {"type": "string"}, "memo": {"type": "string"}},
                        ["payee_name", "amount_aud"]),
    "write_project_file": _s("write_project_file", "Write a text file inside workspace/<project>/<path>.",
                             {"project": {"type": "string"}, "path": {"type": "string"}, "content": {"type": "string"}}, ["project", "path", "content"]),
    "list_project_files": _s("list_project_files", "List files in a project folder.", {"project": {"type": "string"}}, ["project"]),
    "figma_get_file": _s("figma_get_file", "Fetch a Figma file's structure (needs FIGMA_TOKEN).", {"file_key": {"type": "string"}}, ["file_key"]),
}


def _safe_path(project, path):
    proj = re.sub(r"[^A-Za-z0-9_\-]", "_", project)[:60] or "project"
    root = os.path.realpath(os.path.join(WORKSPACE, proj))
    full = os.path.realpath(os.path.join(root, path))
    if not full.startswith(root + os.sep):
        raise ValueError("Path escapes the project folder.")
    return root, full


def run_tool(agent_name, name, args):
    actor = f"agent:{agent_name}"
    try:
        if name == "vault_summary":
            s = ledger.summary()
            return {k: (fmt(v) if k.endswith("_cents") else v) for k, v in s.items()}
        if name == "list_releases":
            rows = ledger.releases(args.get("status") or None)[:15]
            return [{"id": r["id"], "status": r["status"], "payee": r["payee_name"], "amount": fmt(r["amount_cents"]), "reference": r["reference"]} for r in rows]
        if name == "ledger_tail":
            rows = ledger.ledger_rows(limit=min(int(args.get("n", 10)), 50))
            return [{"id": r["id"], "kind": r["kind"], "amount": fmt(r["amount_cents"]), "ref": r["ref"], "verified": bool(r["verified"])} for r in rows]
        if name == "gold_valuation":
            v = gold.valuation()
            return {"held_gross_kg": round(gold.held_gross_kg(), 3), "fine_oz": round(v["fine_oz"], 3), "usd": v["usd"],
                    "aud": fmt(v["aud_cents"]) if v["aud_cents"] is not None else None,
                    "price_source": v["price"]["source"] if v["price"] else "NO PRICE SAVED: ask the user to refresh prices"}
        if name == "gold_register":
            return [{k: b[k] for k in ("serial", "weight_g", "purity", "location", "custodian", "status", "cert_ref")} for b in gold.bars()]
        if name == "recall":
            like = f"%{args.get('query', '')}%"
            return db.q("SELECT ts,agent,content FROM memory WHERE content LIKE ? ORDER BY id DESC LIMIT 8", (like,))
        if name == "remember":
            db.x("INSERT INTO memory(ts,agent,kind,content) VALUES(?,?,?,?)", (time.time(), agent_name, "fact", args["content"][:2000]))
            return "saved"
        if name == "create_task":
            tid = db.x("INSERT INTO tasks(ts,agent,title,status) VALUES(?,?,?,'OPEN')", (time.time(), args["agent"], args["title"][:300]))
            return f"task #{tid} created"
        if name == "list_tasks":
            return db.q("SELECT id,agent,title,status FROM tasks WHERE status!='DONE' ORDER BY id DESC LIMIT 20")
        if name == "audit_tail":
            return db.q("SELECT ts,actor,action,detail FROM audit ORDER BY id DESC LIMIT ?", (min(int(args.get("n", 15)), 50),))
        if name == "draft_release":
            rid = ledger.create_release(args["payee_name"], args.get("bsb", ""), args.get("account", ""), args.get("payid", ""),
                                        to_cents(args["amount_aud"]), args.get("memo", ""), actor, confirmed_warnings=False,
                                        method=args.get("method") or None, biller_code=args.get("biller_code", ""),
                                        category=args.get("category", ""), crn=args.get("crn", ""))
            return f"DRAFT #{rid} created. A human must approve it on the Vault E-Terminal page. You cannot approve it."
        if name == "write_project_file":
            _, full = _safe_path(args["project"], args["path"])
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "w", encoding="utf-8") as f:
                f.write(args["content"])
            security.audit(actor, "PROJECT_FILE_WRITTEN", f"{args['project']}/{args['path']}")
            return f"written {args['project']}/{args['path']}"
        if name == "list_project_files":
            root, _ = _safe_path(args["project"], ".")
            out = []
            for d, _, fs in os.walk(root):
                out += [os.path.relpath(os.path.join(d, f), root) for f in fs]
            return out[:200]
        if name == "figma_get_file":
            tok = os.environ.get("FIGMA_TOKEN")
            if not tok:
                return "FIGMA_TOKEN is not set."
            import requests
            r = requests.get(f"https://api.figma.com/v1/files/{args['file_key']}?depth=2", headers={"X-Figma-Token": tok}, timeout=20)
            return r.text[:6000] if r.ok else f"Figma error {r.status_code}"
    except Exception as e:  # return the error to the model so it can correct itself
        return f"ERROR: {e}"
    return "unknown tool"


OPENROUTER_URL = "https://openrouter.ai/api/v1"
DEFAULT_OR_MODEL = "anthropic/claude-sonnet-4.5"


def provider():
    """Which AI service to use: 'anthropic', 'openrouter' or None. Setting 'auto' prefers Anthropic when both keys exist."""
    pref = db.get_setting("llm_provider", "auto")
    a, o = bool(os.environ.get("ANTHROPIC_API_KEY")), bool(os.environ.get("OPENROUTER_API_KEY"))
    if pref == "anthropic":
        return "anthropic" if a else None
    if pref == "openrouter":
        return "openrouter" if o else None
    return "anthropic" if a else ("openrouter" if o else None)


def has_key() -> bool:
    return provider() is not None


def _context(agent):
    mem = db.q("SELECT agent,content FROM memory WHERE agent=? OR kind='fact' ORDER BY id DESC LIMIT 10", (agent["name"],))
    mem_txt = "\n".join(f"- [{m['agent']}] {m['content']}" for m in reversed(mem)) or "(no memory yet)"
    s = ledger.summary()
    return (f"{BASE_RULES}\n\nYour name: {agent['name']}\nYour role: {agent['role']}\n{agent['prompt'] or ''}\n\n"
            f"Platform state: mode={s['mode']}. Use tools for exact figures.\n\nShared memory:\n{mem_txt}")


def _run_anthropic(agent, user_text, system, max_steps):
    import anthropic
    client = anthropic.Anthropic()
    tools = [TOOL_DEFS[t] for t in agent["tools"].split(",") if t in TOOL_DEFS]
    msgs = [{"role": "user", "content": user_text}]
    for _ in range(max_steps):
        resp = client.messages.create(model=MODEL, max_tokens=2000, system=system, tools=tools, messages=msgs)
        if resp.stop_reason != "tool_use":
            return "".join(b.text for b in resp.content if b.type == "text").strip()
        msgs.append({"role": "assistant", "content": resp.content})
        results = []
        for b in resp.content:
            if b.type == "tool_use":
                out = run_tool(agent["name"], b.name, b.input if isinstance(b.input, dict) else {})
                results.append({"type": "tool_result", "tool_use_id": b.id, "content": json.dumps(out, default=str)[:8000]})
        msgs.append({"role": "user", "content": results})
    return None


def _run_openrouter(agent, user_text, system, max_steps):
    key = os.environ["OPENROUTER_API_KEY"]
    model = db.get_setting("openrouter_model", DEFAULT_OR_MODEL)
    tools = [{"type": "function", "function": {"name": d["name"], "description": d["description"], "parameters": d["input_schema"]}}
             for d in (TOOL_DEFS[t] for t in agent["tools"].split(",") if t in TOOL_DEFS)]
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json", "X-Title": "AI Orchestra"}
    msgs = [{"role": "system", "content": system}, {"role": "user", "content": user_text}]
    for _ in range(max_steps):
        body = {"model": model, "messages": msgs, "max_tokens": 2000}
        if tools:
            body["tools"] = tools
        r = requests.post(f"{OPENROUTER_URL}/chat/completions", headers=headers, json=body, timeout=120)
        if r.status_code != 200:
            raise RuntimeError(f"OpenRouter error {r.status_code}: {r.text[:300]}")
        msg = r.json()["choices"][0]["message"]
        calls = msg.get("tool_calls")
        if not calls:
            return (msg.get("content") or "").strip()
        msgs.append({"role": "assistant", "content": msg.get("content"), "tool_calls": calls})
        for c in calls:
            try:
                args = json.loads(c["function"].get("arguments") or "{}")
            except ValueError:
                args = {}
            out = run_tool(agent["name"], c["function"]["name"], args if isinstance(args, dict) else {})
            msgs.append({"role": "tool", "tool_call_id": c["id"], "content": json.dumps(out, default=str)[:8000]})
    return None


NO_KEY_MSG = ("No AI key is set yet, so this agent cannot think. Add an Anthropic or OpenRouter key on the Security and Settings page. "
              "(The vault, ledger, gold register and releases work without it.)")


def run_agent(agent_name: str, user_text: str, max_steps: int = 6) -> str:
    agent = get_agent(agent_name)
    if not agent or not agent["enabled"]:
        return f"Agent '{agent_name}' is not available."
    prov = provider()
    if not prov:
        return NO_KEY_MSG
    system = _context(agent)
    try:
        text = (_run_anthropic if prov == "anthropic" else _run_openrouter)(agent, user_text, system, max_steps)
    except Exception as e:
        security.audit(f"agent:{agent_name}", "AGENT_ERROR", f"{prov}: {type(e).__name__}")
        return f"The AI service ({prov}) returned an error: {str(e)[:300]}"
    if text is None:
        return "Stopped after the maximum number of tool steps. Ask again with a narrower request."
    db.x("INSERT INTO memory(ts,agent,kind,content) VALUES(?,?,?,?)", (time.time(), agent_name, "run", f"Q: {user_text[:200]} | A: {text[:400]}"))
    security.audit(f"agent:{agent_name}", "AGENT_RUN", f"{prov}: {user_text[:100]}")
    return text


_ROUTES = [
    (("release", "pay", "transfer", "bsb", "payid", "direct credit"), "Vault terminal fund manager"),
    (("gold", "bar", "kg", "ounce", "bullion"), "Commodity vault"),
    (("ledger", "journal", "invoice", "tax", "reconcil"), "Accounting and finance"),
    (("balance", "ceiling", "cash", "liquidity"), "Treasury"),
    (("figma", "design"), "Figma design agent"),
    (("app", "website", "code", "build"), "App developer"),
    (("research", "market", "compare"), "Research analyst"),
    (("security", "audit", "risk"), "Security guard"),
    (("grow", "scale", "business"), "Business developer"),
]


def route(text: str) -> str:
    """Director routing. Keyword based so it works offline and is predictable."""
    t = text.lower()
    for keys, agent in _ROUTES:
        if any(k in t for k in keys):
            return agent
    return "Program task executor"
