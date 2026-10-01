import hashlib
import io
import json
import zipfile

import numpy as np
import pytest

import accel
import gpu
import transcribe


def fake_adapters(monkeypatch, payload):
    monkeypatch.setattr(gpu, "_powershell", lambda script: json.dumps(payload))


def test_adapters_are_classified_by_pci_vendor(monkeypatch):
    fake_adapters(monkeypatch, [
        {"Name": "AMD Radeon RX 6600 XT", "PNPDeviceID": "PCI\\VEN_1002&DEV_73FF", "DriverVersion": "32.0", "AdapterRAM": 4293918720},
        {"Name": "NVIDIA GeForce RTX 4070", "PNPDeviceID": "PCI\\VEN_10DE&DEV_2786", "DriverVersion": "31.0", "AdapterRAM": 4293918720},
        {"Name": "Intel(R) UHD Graphics 730", "PNPDeviceID": "PCI\\VEN_8086&DEV_4C8A", "DriverVersion": "30.0", "AdapterRAM": 1073741824},
        {"Name": "Microsoft Basic Render Driver", "PNPDeviceID": "ROOT\\BasicRender", "DriverVersion": "1", "AdapterRAM": 0},
    ])
    found = gpu.adapters()
    assert [a["vendor"] for a in found] == ["amd", "nvidia", "intel"]
    assert gpu.pick_best(found)["vendor"] in ("amd", "nvidia")             # a discrete card beats the integrated one


def test_a_single_adapter_is_returned_as_a_dict_by_powershell(monkeypatch):
    fake_adapters(monkeypatch, {"Name": "Intel(R) Arc A380", "PNPDeviceID": "PCI\\VEN_8086&DEV_56A5", "DriverVersion": "1", "AdapterRAM": 6})
    assert gpu.adapters()[0]["vendor"] == "intel"


def test_detect_reports_the_next_step(monkeypatch):
    fake_adapters(monkeypatch, [{"Name": "AMD Radeon RX 6600 XT", "PNPDeviceID": "PCI\\VEN_1002", "DriverVersion": "1", "AdapterRAM": 1}])
    monkeypatch.setattr(gpu, "loader_present", lambda: True)
    monkeypatch.setattr(gpu, "vulkan_drivers", lambda: ["C:\\amd-vulkan64.json"])
    assert gpu.detect()["vulkan_ready"] and gpu.detect()["advice"] == ""
    monkeypatch.setattr(gpu, "vulkan_drivers", lambda: [])
    result = gpu.detect()
    assert not result["vulkan_ready"] and result["advice"] == "install_driver" and "amd.com" in result["driver_page"]
    fake_adapters(monkeypatch, [])
    assert gpu.detect()["advice"] == "no_gpu"


# ---------------------------------------------------------------------------------------------- downloads

class Stream:
    def __init__(self, data):
        self.data = data
        self.headers = {"Content-Length": str(len(data))}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def raise_for_status(self):
        pass

    def iter_content(self, size):
        for start in range(0, len(self.data), 7):
            yield self.data[start:start + 7]


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setattr(accel, "RUNTIME_DIR", tmp_path / "runtime" / "whispercpp")
    monkeypatch.setattr(accel, "GGML_DIR", tmp_path / "ggml")
    monkeypatch.setattr(accel, "MANIFEST_FILE", tmp_path / "manifest.json")
    return tmp_path


def make_zip(files):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        for name, content in files.items():
            bundle.writestr(name, content)
    return buffer.getvalue()


def publish(sandbox, data):
    accel.MANIFEST_FILE.write_text(json.dumps({"whispercpp": {"version": "v1", "url": "https://example.test/r.zip",
                                                              "sha256": hashlib.sha256(data).hexdigest()}}))


def test_downloads_are_verified_before_they_are_kept(sandbox, monkeypatch):
    data = b"model bytes" * 50
    monkeypatch.setattr(accel.requests, "get", lambda *a, **k: Stream(data))
    target = sandbox / "model.bin"
    seen = []
    accel._download("https://x", target, hashlib.sha256(data).hexdigest(), lambda done, total: seen.append((done, total)))
    assert target.read_bytes() == data and seen[-1] == (len(data), len(data))
    bad = sandbox / "bad.bin"
    with pytest.raises(accel.AccelError):
        accel._download("https://x", bad, "0" * 64)
    assert not bad.exists() and not list(sandbox.glob("*.part"))


def test_runtime_install_needs_a_published_checksum(sandbox):
    assert not accel.runtime_published() and not accel.runtime_installed()
    with pytest.raises(accel.AccelError):
        accel.install_runtime()


def test_runtime_install_unpacks_the_verified_archive(sandbox, monkeypatch):
    data = make_zip({"whisper-server.exe": b"MZ", "BUILD.txt": b"built"})
    publish(sandbox, data)
    monkeypatch.setattr(accel.requests, "get", lambda *a, **k: Stream(data))
    accel.install_runtime()
    assert accel.runtime_exe().read_bytes() == b"MZ" and accel.runtime_installed()
    publish(sandbox, b"a newer build")                 # a different published checksum invalidates the installed copy
    assert not accel.runtime_installed()


def test_a_tampered_archive_is_rejected_and_never_unpacked(sandbox, monkeypatch):
    genuine = make_zip({"whisper-server.exe": b"MZ"})
    publish(sandbox, genuine)
    monkeypatch.setattr(accel.requests, "get", lambda *a, **k: Stream(make_zip({"whisper-server.exe": b"MALWARE"})))
    with pytest.raises(accel.AccelError):
        accel.install_runtime()
    assert not accel.runtime_exe().exists()


def test_archives_cannot_write_outside_their_folder(sandbox, monkeypatch):
    data = make_zip({"../evil.txt": b"x", "whisper-server.exe": b"MZ"})
    publish(sandbox, data)
    monkeypatch.setattr(accel.requests, "get", lambda *a, **k: Stream(data))
    with pytest.raises(accel.AccelError):
        accel.install_runtime()
    assert not (sandbox / "runtime" / "evil.txt").exists()


def test_model_download_checks_the_hugging_face_checksum(sandbox, monkeypatch):
    data = b"ggml" * 100
    digest = hashlib.sha256(data).hexdigest()

    class Listing:
        def json(self):
            return [{"path": "ggml-base.bin", "lfs": {"oid": digest}}]

    calls = {}

    def fake_get(url, **kwargs):
        calls[url] = True
        return Listing() if "api/models" in url else Stream(data)

    monkeypatch.setattr(accel.requests, "get", fake_get)
    accel.install_model("base")
    assert accel.model_installed("base") and accel.model_path("base").read_bytes() == data
    with pytest.raises(accel.AccelError):
        accel.install_model("not-a-model")


def test_models_map_to_ggml_files():
    assert accel.GGML_MODELS["base"][0] == "ggml-base.bin" and accel.GGML_MODELS["turbo"][0].startswith("ggml-large-v3-turbo")
    assert set(accel.GGML_MODELS) == {"tiny", "base", "small", "turbo", "medium", "large-v3"}


def test_server_refuses_to_start_without_its_files(sandbox):
    with pytest.raises(accel.AccelError):
        accel.server.ensure("base")


# ---------------------------------------------------------------------------------------------- fallback

def test_gpu_failures_fall_back_to_the_cpu_model(monkeypatch):
    def broken(*a, **k):
        raise accel.AccelError("no runtime")

    monkeypatch.setattr(accel, "transcribe_gpu", broken)
    local = lambda audio, language: "cpu text"
    silence = np.zeros(8000, dtype=np.float32)
    assert transcribe.run(silence, "en", {"engine": "gpu", "whisper_model": "base"}, local, fallback=True) == "cpu text"
    with pytest.raises(transcribe.TranscribeError):
        transcribe.run(silence, "en", {"engine": "gpu", "whisper_model": "base"}, local, fallback=False)
    monkeypatch.setattr(accel, "transcribe_gpu", lambda audio, language, model: f"gpu text ({model})")
    assert transcribe.run(silence, "fr", {"engine": "gpu", "whisper_model": "small"}, local, fallback=True) == "gpu text (small)"


def test_repetition_loops_are_detected_but_normal_speech_is_not():
    speech = "Ajoute une route POST sur slash api slash v2 dans le contrôleur Express, valide le payload avec Zod puis pousse l'événement dans la file Redis"
    assert not accel.looks_degenerate(speech, 12.0)
    assert accel.looks_degenerate("tu ne routes " * 60, 12.0)                          # a phrase repeated over and over
    assert accel.looks_degenerate(" ".join(f"mot{i}" for i in range(200)), 10.0)       # impossibly many words for the duration
    assert not accel.looks_degenerate("Bonjour.", 1.0)


def test_a_looping_gpu_answer_falls_back_to_the_cpu_model(monkeypatch):
    monkeypatch.setattr(accel.server, "transcribe", lambda audio, language, model: "la la la " * 80)
    silence = np.zeros(16000, dtype=np.float32)
    with pytest.raises(accel.AccelError):
        accel.transcribe_gpu(silence, "fr", "tiny")
    assert transcribe.run(silence, "fr", {"engine": "gpu", "whisper_model": "tiny"}, lambda a, l: "cpu answer", fallback=True) == "cpu answer"


def test_find_pause_cuts_inside_a_quiet_gap_after_enough_speech():
    rng = np.random.default_rng(1)
    speech = lambda s: (rng.standard_normal(int(16000 * s)) * 0.1).astype(np.float32)
    gap = np.zeros(int(16000 * 0.6), dtype=np.float32)
    audio = np.concatenate([speech(6), gap, speech(2)])
    cut = transcribe.find_pause(audio)
    assert cut and 6 * 16000 <= cut <= 6.6 * 16000
    assert transcribe.find_pause(np.concatenate([speech(2), gap, speech(2)])) is None      # too early: nothing worth sending yet
    assert transcribe.find_pause(speech(9)) is None                                          # no pause at all


def test_long_recordings_are_split_into_pieces_that_fit_the_gpu_window():
    audio = (np.random.default_rng(2).standard_normal(16000 * 40) * 0.1).astype(np.float32)
    audio[16000 * 12: 16000 * 12 + 4000] = 0                                   # one quiet moment near the limit
    pieces = transcribe.split_audio(audio)
    assert all(len(p) <= 13 * 16000 for p in pieces) and sum(len(p) for p in pieces) == len(audio)
    assert abs(len(pieces[0]) - 12.1 * 16000) < 0.3 * 16000                    # cut in the quiet gap, not mid-word
    assert len(transcribe.split_audio(audio[:16000 * 5])) == 1



# ---------------------------------------------------------------------------------------------- CPU model store

def test_a_half_downloaded_cpu_model_does_not_count(tmp_path):
    import modelstore
    folder = modelstore.cache_folder("small", tmp_path)
    (folder / "blobs").mkdir(parents=True)
    (folder / "blobs" / "abc.incomplete").write_bytes(b"x" * 10)
    assert not modelstore.model_downloaded("small", tmp_path)
    (folder / "snapshots" / "rev1").mkdir(parents=True)
    (folder / "snapshots" / "rev1" / "model.bin").write_bytes(b"model")
    assert modelstore.model_downloaded("small", tmp_path)
    assert not modelstore.model_downloaded("not-a-model", tmp_path)


def test_the_turbo_model_maps_to_its_own_repository(tmp_path):
    import modelstore
    assert modelstore.repo_for("turbo").endswith("faster-whisper-large-v3-turbo")
    assert modelstore.repo_for("base") == "Systran/faster-whisper-base"


def test_downloading_a_cpu_model_reports_progress_and_checks_the_result(tmp_path, monkeypatch):
    import modelstore
    monkeypatch.setattr(modelstore, "expected_bytes", lambda name: 1000)
    seen = []

    def fake_snapshot(repo, allow_patterns, cache_dir, tqdm_class):
        folder = modelstore.cache_folder("base", tmp_path)
        (folder / "blobs").mkdir(parents=True)
        (folder / "blobs" / "b").write_bytes(b"x" * 400)
        import time
        time.sleep(0.4)                                    # long enough for the progress watcher to see it
        (folder / "snapshots" / "r").mkdir(parents=True)
        (folder / "snapshots" / "r" / "model.bin").write_bytes(b"x")

    import huggingface_hub
    monkeypatch.setattr(huggingface_hub, "snapshot_download", fake_snapshot)
    modelstore.download_model("base", lambda done, total: seen.append((done, total)), root=tmp_path)
    assert seen[-1] == (1000, 1000) and any(0 < done < 1000 for done, _ in seen)
    assert modelstore.model_downloaded("base", tmp_path)
    monkeypatch.setattr(huggingface_hub, "snapshot_download", lambda *a, **k: None)      # a download that leaves nothing behind
    with pytest.raises(modelstore.ModelError):
        modelstore.download_model("small", root=tmp_path)
