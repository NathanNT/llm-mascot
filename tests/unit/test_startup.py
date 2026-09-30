import pytest

winreg = pytest.importorskip("winreg")

import startup


@pytest.fixture
def fake_registry(monkeypatch):
    store = {}

    class Key:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(startup.winreg, "OpenKey", lambda *a, **k: Key())
    monkeypatch.setattr(startup.winreg, "CreateKey", lambda *a, **k: Key())
    monkeypatch.setattr(startup.winreg, "SetValueEx", lambda key, name, reserved, kind, value: store.__setitem__(name, value))

    def query(key, name):
        if name not in store:
            raise OSError
        return store[name], 1

    def delete(key, name):
        if name not in store:
            raise OSError
        del store[name]

    monkeypatch.setattr(startup.winreg, "QueryValueEx", query)
    monkeypatch.setattr(startup.winreg, "DeleteValue", delete)
    monkeypatch.setattr(startup, "startup_shortcuts", lambda: [])
    return store


def test_enable_and_disable_round_trip(fake_registry):
    assert not startup.is_enabled()
    startup.set_enabled(True)
    assert startup.is_enabled() and "rover.py" in fake_registry[startup.VALUE_NAME]
    startup.set_enabled(False)
    assert not startup.is_enabled()


def test_disabling_removes_startup_shortcuts_too(fake_registry, monkeypatch, tmp_path):
    shortcut = tmp_path / "Rover.lnk"
    shortcut.write_text("x")
    monkeypatch.setattr(startup, "startup_shortcuts", lambda: [shortcut] if shortcut.exists() else [])
    assert startup.is_enabled()
    startup.set_enabled(False)
    assert not shortcut.exists() and not startup.is_enabled()
