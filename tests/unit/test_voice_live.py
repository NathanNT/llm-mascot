import numpy as np
import pytest

pytest.importorskip("sounddevice")        # the speech stack is not installed in CI
pytest.importorskip("faster_whisper")
import voice  # noqa: E402


def make_recorder(seen):
    return voice.Recorder(lambda s: None, lambda t, l: None, lambda m: None, on_partial=seen.append, get_live=lambda: True)


def test_the_preview_joins_finished_pieces_with_the_words_spoken_since():
    seen = []
    recorder = make_recorder(seen)
    recorder._piece = lambda audio, language, config, fallback=None: "et la suite"
    state = {"busy": True, "stopped": False}
    recorder._preview(np.zeros(20000, dtype=np.float32), "fr", {}, ["Bonjour,"], state)
    assert seen == ["Bonjour, et la suite"] and state["busy"] is False


def test_a_preview_never_falls_back_to_a_slow_model_and_is_dropped_once_recording_stopped():
    seen = []
    recorder = make_recorder(seen)
    used = []

    def fake_piece(audio, language, config, fallback=None):
        used.append(fallback)
        return "mots"

    recorder._piece = fake_piece
    recorder._preview(np.zeros(20000, dtype=np.float32), "fr", {}, [], {"busy": True, "stopped": True})
    assert used == [False] and seen == []                      # no CPU fallback; and nothing shown after the recording ended


def test_a_failing_preview_is_silent_and_frees_the_slot():
    seen = []
    recorder = make_recorder(seen)

    def broken(*args, **kwargs):
        raise RuntimeError("gpu busy")

    recorder._piece = broken
    state = {"busy": True, "stopped": False}
    recorder._preview(np.zeros(20000, dtype=np.float32), "fr", {}, [], state)
    assert seen == [] and state["busy"] is False


def test_a_long_dictation_is_cut_into_pieces_while_recording_and_nothing_is_lost(monkeypatch):
    """A fake microphone delivers three minutes of 'speech with pauses' in a few seconds; every piece is transcribed on the way."""
    import threading
    import time

    rng = np.random.default_rng(11)
    sound = (rng.standard_normal(16000 * 180) * 0.1).astype(np.float32)
    for start in range(7, 180, 7):
        sound[start * 16000: int((start + 0.6) * 16000)] = 0

    delivered = threading.Event()

    class FakeMicrophone:
        def __init__(self, samplerate, channels, dtype, callback):
            self.callback = callback

        def start(self):
            def feed():
                for begin in range(0, len(sound), 1600):
                    self.callback(sound[begin:begin + 1600].reshape(-1, 1), 1600, None, None)
                    time.sleep(0.0005)
                delivered.set()
            threading.Thread(target=feed, daemon=True).start()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(voice.sd, "InputStream", FakeMicrophone)
    monkeypatch.setattr(voice.transcribe, "is_gpu", lambda config: True)
    results, done = [], threading.Event()
    recorder = voice.Recorder(lambda s: None, lambda text, language: (results.append(text), done.set()), lambda m: (results.append("ERROR " + m), done.set()))
    sent = []

    def fake_piece(audio, language, config, fallback=None):
        sent.append(len(audio))
        return f"p{len(sent)}"

    recorder._piece = fake_piece
    recorder.start("fr")
    assert delivered.wait(15)
    time.sleep(0.4)
    recorder.stop()
    assert done.wait(10), "the dictation finished"
    assert not results[0].startswith("ERROR"), results
    assert len(sent) > 10 and all(n <= 13 * 16000 for n in sent), sent
    assert sum(sent) == len(sound), "every sample of the recording went to the speech model exactly once"
