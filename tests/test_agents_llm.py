import os, sys, tempfile, types
ROOT = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, ROOT)
os.environ["ANTHROPIC_API_KEY"] = "test"
import anthropic
from core import db, agents, ledger, runner, security
from core.money import to_cents



class Block:
    def __init__(self, **k): self.__dict__.update(k)


class FakeMessages:
    def __init__(self): self.calls = 0
    def create(self, **kw):
        self.calls += 1
        if self.calls % 2 == 1:
            return types.SimpleNamespace(stop_reason="tool_use", content=[Block(type="tool_use", id=f"t{self.calls}", name="vault_summary", input={})])
        last = kw["messages"][-1]["content"][0]["content"]
        return types.SimpleNamespace(stop_reason="end_turn", content=[Block(type="text", text="Summary seen: " + last[:300])])


class FakeClient:
    def __init__(self, *a, **k): self.messages = FakeMessages()


def test_tool_loop_and_memory(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setattr(anthropic, "Anthropic", FakeClient)
    if not db.q1("SELECT 1 FROM users WHERE username='u'"):
        security.create_user("u", "passwordpassword", "admin")
    ledger.record_deposit(to_cents("100"), "R", "", "u")
    out = agents.run_agent("Treasury", "What is the balance?")
    assert "Summary seen" in out and "AUD 100.00" in out
    assert db.q("SELECT * FROM memory WHERE agent='Treasury'")


def test_runner_tick_and_finish(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setattr(anthropic, "Anthropic", FakeClient)
    jid = runner.start_job("Autonomous runner", "watch the vault", "report balance", 6, 1, "u")
    job = db.q1("SELECT * FROM jobs WHERE id=?", (jid,))
    runner.tick(job)
    assert len(runner.checkpoints(jid)) == 1
    db.x("UPDATE jobs SET ends=? WHERE id=?", (0, jid))
    runner.finish(db.q1("SELECT * FROM jobs WHERE id=?", (jid,)))
    assert db.q1("SELECT status FROM jobs WHERE id=?", (jid,))["status"] == "COMPLETED"


def test_disabled_agent_refuses():
    db.x("UPDATE agents SET enabled=0 WHERE name='Treasury'")
    assert "not available" in agents.run_agent("Treasury", "x")


def test_openrouter_tool_loop(monkeypatch):
    import requests
    from core import agents as ag
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-test")
    db.set_setting("llm_provider", "auto")
    assert ag.provider() == "openrouter" and ag.has_key()
    calls = []

    class R:
        def __init__(self, data): self.status_code, self._d, self.text = 200, data, ""
        def json(self): return self._d

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append(json)
        assert url.endswith("/chat/completions") and headers["Authorization"] == "Bearer sk-or-v1-test"
        if len(calls) == 1:
            assert json["tools"][0]["type"] == "function" and json["messages"][0]["role"] == "system"
            return R({"choices": [{"message": {"content": None, "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "vault_summary", "arguments": "{}"}}]}}]})
        tool_msg = json["messages"][-1]
        assert tool_msg["role"] == "tool" and tool_msg["tool_call_id"] == "c1" and "mode" in tool_msg["content"]
        return R({"choices": [{"message": {"content": "All good via OpenRouter"}}]})

    monkeypatch.setattr(requests, "post", fake_post)
    assert ag.run_agent("Wallet agent", "status?") == "All good via OpenRouter"


def test_openrouter_error_is_friendly(monkeypatch):
    import requests
    from core import agents as ag
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-test")

    class R:
        status_code, text = 402, "insufficient credits"
    monkeypatch.setattr(requests, "post", lambda *a, **k: R())
    out = ag.run_agent("Wallet agent", "x")
    assert "error" in out.lower() and "insufficient credits" in out


def test_provider_choice(monkeypatch):
    from core import agents as ag
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-x"); monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-x")
    db.set_setting("llm_provider", "auto"); assert ag.provider() == "anthropic"
    db.set_setting("llm_provider", "openrouter"); assert ag.provider() == "openrouter"
    monkeypatch.delenv("OPENROUTER_API_KEY"); assert ag.provider() is None
    db.set_setting("llm_provider", "auto")
