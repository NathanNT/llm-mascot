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


# ---------------------------------------------------------------------------------------------- long recordings

def talking(seconds, rng, pause_every=7.0):
    """Noise standing for speech, with a short pause every few seconds."""
    sound = (rng.standard_normal(int(16000 * seconds)) * 0.1).astype(np.float32)
    for start in np.arange(pause_every, seconds, pause_every):
        sound[int(start * 16000): int((start + 0.6) * 16000)] = 0
    return sound


def test_cutting_ten_minutes_of_speech_as_it_arrives_loses_nothing_and_stays_cheap():
    import time
    sound = talking(600, np.random.default_rng(3))                      # ten minutes, fed 100 ms at a time like the microphone
    cutter, pieces = transcribe.PieceCutter(), []
    started = time.perf_counter()
    for begin in range(0, len(sound), 1600):
        pieces += cutter.feed([sound[begin:begin + 1600]])
    elapsed = time.perf_counter() - started
    assert sum(len(p) for p in pieces) == cutter.consumed and np.array_equal(np.concatenate([*pieces, cutter.pending]), sound)
    assert all(len(p) <= 13 * 16000 for p in pieces) and len(cutter.pending) <= 13 * 16000
    assert elapsed < 8, f"cutting must not slow down as the recording grows ({elapsed:.1f} s)"


def test_a_long_recording_without_any_pause_is_still_cut_into_windows():
    sound = (np.random.default_rng(4).standard_normal(16000 * 60) * 0.1).astype(np.float32)
    cutter, pieces = transcribe.PieceCutter(), []
    for begin in range(0, len(sound), 1600):
        pieces += cutter.feed([sound[begin:begin + 1600]])
    assert len(pieces) >= 4 and all(len(p) <= 13 * 16000 for p in pieces)


def test_a_service_gets_a_long_recording_in_pieces_and_silence_is_skipped(monkeypatch):
    sent = []

    def fake_remote(piece, language, config):
        sent.append(len(piece) / 16000)
        return f"part{len(sent)}"

    monkeypatch.setattr(transcribe, "transcribe_remote", fake_remote)
    monkeypatch.setattr(transcribe, "REMOTE_PIECE_SECONDS", 30.0)
    sound = talking(100, np.random.default_rng(5))
    sound[int(65 * 16000): int(95 * 16000)] = 0                               # a silent stretch of its own
    text = transcribe.transcribe_remote_long(sound, "fr", {})
    assert text.startswith("part1 part2") and len(sent) >= 2 and all(seconds <= 30 for seconds in sent)
    sent.clear()
    assert transcribe.transcribe_remote_long(talking(20, np.random.default_rng(6)), "fr", {}) == "part1" and len(sent) == 1


def test_a_silent_piece_does_not_sink_a_long_recording(monkeypatch):
    answers = iter([transcribe.TranscribeError("none", empty=True), "second"])

    def fake_remote(piece, language, config):
        answer = next(answers)
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(transcribe, "transcribe_remote", fake_remote)
    monkeypatch.setattr(transcribe, "REMOTE_PIECE_SECONDS", 20.0)
    assert transcribe.transcribe_remote_long(talking(35, np.random.default_rng(7)), "fr", {}) == "second"


def test_the_recorder_listens_for_half_an_hour():
    pytest.importorskip("sounddevice")
    pytest.importorskip("faster_whisper")
    import voice
    assert voice.MAX_SECONDS >= 30 * 60
