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
