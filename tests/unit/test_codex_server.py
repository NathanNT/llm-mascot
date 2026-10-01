import os
import sys
from pathlib import Path

import pytest

import codex_server
import core

FAKE = Path(__file__).with_name("fake_codex_server.py")


@pytest.fixture
def running():
    server = codex_server.CodexServer()
    server.start([sys.executable, str(FAKE)], os.environ.copy(), Path.cwd(), "test")
    yield server
    server.stop()


def test_a_rewrite_reuses_the_running_server(running):
    assert running.running()
    assert running.rewrite("bonjour", model="gpt-x", tier="priority", cwd=".") == "BONJOUR"
    assert running.rewrite("encore", cwd=".") == "ENCORE"                       # a second conversation on the same process
    assert running.running()


def test_a_failed_turn_raises_and_the_server_stays_usable(running):
    with pytest.raises(codex_server.CodexServerError, match="boom"):
        running.rewrite("please FAIL", cwd=".")
    assert running.rewrite("ok", cwd=".") == "OK"


def test_a_dead_server_is_reported_not_waited_for(running):
    running.process.kill()
    running.process.wait()
    with pytest.raises(codex_server.CodexServerError):
        running.rewrite("hello", cwd=".")


def test_codex_exec_stays_the_fallback_when_the_server_cannot_run(monkeypatch, tmp_path):
    monkeypatch.setattr(core, "codex_command", lambda: "codex")
    monkeypatch.setattr(codex_server, "enabled", True)

    def refuse(*args, **kwargs):
        raise codex_server.CodexServerError("no server")

    monkeypatch.setattr(codex_server.server, "start", refuse)
    seen = {}

    class Done:
        returncode, stdout, stderr = 0, " from exec ", ""

    def fake_run(command, **kwargs):
        seen["command"] = command
        return Done()

    monkeypatch.setattr(core.subprocess, "run", fake_run)
    config = {"home": str(tmp_path), "model": "gpt-6-luna", "reasoning": "low", "auth_store": "", "tier": "priority"}
    assert core.rewrite_with_codex("bonjour", "fr", config) == "from exec"
    assert 'service_tier="priority"' in seen["command"]                        # Fast mode also applies to the fallback


def test_fast_mode_is_only_requested_when_chosen(monkeypatch, tmp_path):
    monkeypatch.setattr(core, "codex_command", lambda: "codex")
    monkeypatch.setattr(codex_server, "enabled", True)
    monkeypatch.setattr(codex_server.server, "start", lambda *a, **k: None)
    calls = []
    monkeypatch.setattr(codex_server.server, "rewrite", lambda prompt, model, effort, tier, cwd: calls.append((model, effort, tier)) or "ok")
    base = {"home": str(tmp_path), "model": "gpt-6-luna", "reasoning": "medium", "auth_store": ""}
    core.rewrite_with_codex("a", "en", {**base, "tier": ""})
    core.rewrite_with_codex("a", "en", {**base, "tier": "priority"})
    core.rewrite_with_codex("a", "en", {**base, "tier": "anything else"})
    assert calls == [("gpt-6-luna", "medium", ""), ("gpt-6-luna", "medium", "priority"), ("gpt-6-luna", "medium", "")]
