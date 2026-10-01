import numpy as np

import voice


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
