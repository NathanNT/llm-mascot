import io
import wave

import numpy as np
import pytest

import secrets_store
import transcribe


def audio(seconds=1.0):
    return (np.sin(np.linspace(0, 440 * 6.28 * seconds, int(16000 * seconds))) * 0.3).astype("float32")


def test_key_round_trips_through_dpapi_and_is_not_clear_text():
    blob = secrets_store.protect("sk-secret-123")
    assert blob.startswith("dpapi:") and "secret" not in blob
    assert secrets_store.unprotect(blob) == "sk-secret-123"
    assert secrets_store.unprotect("sk-plain") == "" and secrets_store.unprotect("dpapi:@@@") == ""
    assert secrets_store.protect("") == ""


def test_wav_is_16khz_mono_pcm16():
    data = transcribe.wav_bytes(audio(0.5))
    with wave.open(io.BytesIO(data)) as handle:
        assert (handle.getnchannels(), handle.getsampwidth(), handle.getframerate()) == (1, 2, 16000)
        assert handle.getnframes() == 8000


def test_base_urls_must_be_https_unless_local():
    assert transcribe.safe_base_url("https://api.example.com/v1/") == "https://api.example.com/v1"
    assert transcribe.safe_base_url("http://localhost:8000/v1") == "http://localhost:8000/v1"
    assert transcribe.safe_base_url("http://api.example.com/v1") == ""
    assert transcribe.safe_base_url("https://user:pass@api.example.com") == ""
    assert transcribe.safe_base_url("ftp://x") == "" and transcribe.safe_base_url("") == ""


def test_key_comes_from_the_store_first_then_the_environment(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    config = {"engine": "openai"}
    assert transcribe.resolve(config)["key"] == "" and transcribe.key_source(config) == ""
    monkeypatch.setenv("OPENAI_API_KEY", "from-env")
    assert transcribe.resolve(config)["key"] == "from-env" and transcribe.key_source(config) == "environment"
    config["api_key"] = secrets_store.protect("from-store")
    assert transcribe.resolve(config)["key"] == "from-store" and transcribe.key_source(config) == "stored"


def test_presets_default_to_the_fast_models():
    assert transcribe.resolve({"engine": "openai", "api_key": ""})["model"] == "gpt-4o-mini-transcribe"
    assert transcribe.resolve({"engine": "groq"})["model"] == "whisper-large-v3-turbo"
    assert transcribe.resolve({"engine": "openai", "model": "whisper-1"})["model"] == "whisper-1"
    assert transcribe.resolve({"engine": "openai", "model": "x; calc"})["model"] == ""


class Reply:
    def __init__(self, status=200, body=None):
        self.status_code, self._body = status, body if body is not None else {"text": " hello there "}
        self.ok = status < 400

    def json(self):
        return self._body


def test_remote_request_shape(monkeypatch):
    seen = {}

    def fake_post(url, headers, files, data, timeout):
        seen.update(url=url, headers=headers, files=files, data=data)
        return Reply()

    monkeypatch.setattr(transcribe.requests, "post", fake_post)
    config = {"engine": "groq", "api_key": secrets_store.protect("gsk-abc")}
    assert transcribe.transcribe_remote(audio(), "fr", config) == "hello there"
    assert seen["url"] == "https://api.groq.com/openai/v1/audio/transcriptions"
    assert seen["headers"] == {"Authorization": "Bearer gsk-abc"}
    assert seen["data"]["model"] == "whisper-large-v3-turbo" and seen["data"]["language"] == "fr"
    name, payload, kind = seen["files"]["file"]
    assert name.endswith(".wav") and payload[:4] == b"RIFF" and kind == "audio/wav"


@pytest.mark.parametrize("status,recoverable", [(401, False), (404, False), (429, True), (503, True)])
def test_http_errors_become_readable_messages(monkeypatch, status, recoverable):
    monkeypatch.setattr(transcribe.requests, "post", lambda *a, **k: Reply(status, {"error": {"message": "nope"}}))
    with pytest.raises(transcribe.TranscribeError) as caught:
        transcribe.transcribe_remote(audio(), "en", {"engine": "openai", "api_key": secrets_store.protect("sk-abc")})
    assert caught.value.recoverable is recoverable and "sk-abc" not in str(caught.value)


def test_missing_key_is_reported_before_any_request(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(transcribe.requests, "post", lambda *a, **k: pytest.fail("no request without a key"))
    with pytest.raises(transcribe.TranscribeError):
        transcribe.transcribe_remote(audio(), "en", {"engine": "openai"})


def test_local_fallback_only_when_the_failure_is_recoverable(monkeypatch):
    local = lambda samples, language: "from the local model"
    config = {"engine": "openai", "api_key": secrets_store.protect("sk-abc")}

    def unreachable(*a, **k):
        raise transcribe.requests.ConnectionError()

    monkeypatch.setattr(transcribe.requests, "post", unreachable)
    assert transcribe.run(audio(), "en", config, local, fallback=True) == "from the local model"
    with pytest.raises(transcribe.TranscribeError):
        transcribe.run(audio(), "en", config, local, fallback=False)
    monkeypatch.setattr(transcribe.requests, "post", lambda *a, **k: Reply(401, {}))
    with pytest.raises(transcribe.TranscribeError):                   # a rejected key is never hidden by a fallback
        transcribe.run(audio(), "en", config, local, fallback=True)
    assert transcribe.run(audio(), "en", {"engine": "local"}, local, fallback=False) == "from the local model"


def test_connection_test_uses_the_free_models_endpoint(monkeypatch):
    calls = []
    monkeypatch.setattr(transcribe.requests, "get", lambda url, headers, timeout: calls.append(url) or Reply(200, {}))
    ok, message = transcribe.test_connection({"engine": "openai", "api_key": secrets_store.protect("sk-abc")})
    assert ok and calls == ["https://api.openai.com/v1/models"]
    monkeypatch.setattr(transcribe.requests, "get", lambda *a, **k: Reply(401, {}))
    assert transcribe.test_connection({"engine": "openai", "api_key": secrets_store.protect("sk-abc")})[0] is False
