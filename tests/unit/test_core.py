import core
import i18n


def setup_function(function):
    i18n.set_language("en")


def test_window_labels():
    assert core.window_label(10080) == "Weekly"
    assert core.window_label(300) == "5 h window"
    assert core.window_label(2880) == "2-day window"
    assert core.window_label(45) == "45 min window"
    assert core.window_label(None) == "Quota"
    i18n.set_language("fr")
    assert core.window_label(10080) == "Hebdomadaire"


def test_reset_and_credit_formatting():
    assert core.format_reset(None) == ""
    assert core.format_credits("1250") == "1,250.00"
    assert core.format_credits("n/a") == "n/a"


def test_rewrite_instruction_follows_the_dictation_language():
    english = core.build_rewrite_instruction("um hello there", "en")
    french = core.build_rewrite_instruction("euh bonjour", "fr")
    assert "dictation editor" in english and "um hello there" in english
    assert "éditeur de dictée" in french and "euh bonjour" in french


def test_quotas_are_optional(monkeypatch):
    monkeypatch.setattr(core.Path, "home", lambda: core.Path("Z:/nowhere"))
    cards = core.read_quotas("")
    assert cards[0]["name"] == "Codex" and "not configured" in cards[0]["status"]


def test_quota_snapshot_is_parsed(monkeypatch):
    limits = {"planType": "pro",
              "primary": {"usedPercent": 38.4, "windowDurationMins": 300, "resetsAt": 5000},
              "secondary": {"usedPercent": 61, "windowDurationMins": 10080, "resetsAt": 9000},
              "credits": {"hasCredits": True, "balance": "12.5"}}
    snapshot = {"now": 1000, "accounts": [{"name": "A", "metrics": {"last_success_at": 990, "limits": {"rateLimits": limits}}}]}

    class Reply:
        def raise_for_status(self):
            pass

        def json(self):
            return snapshot

    monkeypatch.setattr(core.requests, "get", lambda *a, **k: Reply())
    monkeypatch.setattr(core.Path, "home", lambda: core.Path("Z:/nowhere"))
    card, = core.read_quotas("http://127.0.0.1:1/x")
    assert card["name"] == "Codex Pro" and card["status"] == "ok"
    assert [w["used"] for w in card["windows"]] == [38, 61]
    assert card["extra_credits"] == "12.5"


def test_unreachable_quota_service_is_reported(monkeypatch):
    def boom(*a, **k):
        raise core.requests.ConnectionError()

    monkeypatch.setattr(core.requests, "get", boom)
    monkeypatch.setattr(core.Path, "home", lambda: core.Path("Z:/nowhere"))
    assert core.read_quotas("http://127.0.0.1:1/x")[0]["status"] == "Quota service unavailable"


def test_rewrite_is_skipped_without_a_codex_profile(monkeypatch, tmp_path):
    monkeypatch.setattr(core, "codex_command", lambda: "codex")
    assert not core.rewrite_enabled({"home": str(tmp_path / "missing")})
    assert core.rewrite_enabled({"home": str(tmp_path)})
    assert not core.rewrite_enabled({"home": str(tmp_path), "rewrite": "off"})
    monkeypatch.setattr(core, "codex_command", lambda: None)
    assert not core.rewrite_enabled({"home": str(tmp_path)})


def test_rewrite_command_only_accepts_safe_options(monkeypatch, tmp_path):
    captured = {}

    class Done:
        returncode, stdout, stderr = 0, "  final prompt  ", ""

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["env"] = kwargs["env"]
        return Done()

    monkeypatch.setattr(core, "codex_command", lambda: "codex")
    monkeypatch.setattr(core.subprocess, "run", fake_run)
    config = {"home": str(tmp_path), "model": "gpt-x; rm -rf /", "reasoning": "extreme", "auth_store": "keyring"}
    assert core.rewrite_with_codex("hello", "en", config) == "final prompt"
    command = captured["command"]
    assert "-m" not in command and not any("reasoning" in part for part in command)
    assert 'cli_auth_credentials_store="keyring"' in command
    assert captured["env"]["CODEX_HOME"] == str(tmp_path)
    assert "OPENAI_API_KEY" not in captured["env"]
    core.rewrite_with_codex("hello", "en", {"home": str(tmp_path), "model": "gpt-5", "reasoning": "high"})
    assert "gpt-5" in captured["command"] and 'model_reasoning_effort="high"' in captured["command"]
