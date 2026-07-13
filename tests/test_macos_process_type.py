from __future__ import annotations

from dbaide.desktop import process_type


class _FakeFunction:
    def __init__(self, result=0):
        self.result = result
        self.calls = []

    def __call__(self, *args):
        self.calls.append(args)
        return self.result


class _FakeApplicationServices:
    def __init__(self):
        self.GetCurrentProcess = _FakeFunction()
        self.TransformProcessType = _FakeFunction()


def test_macos_headless_process_transforms_to_ui_element(monkeypatch):
    services = _FakeApplicationServices()
    monkeypatch.setattr(process_type.sys, "platform", "darwin")
    monkeypatch.setattr(process_type.ctypes, "CDLL", lambda _path: services)

    assert process_type.hide_headless_process_from_dock() is True
    assert len(services.GetCurrentProcess.calls) == 1
    assert services.TransformProcessType.calls[0][1] == 4


def test_macos_gui_process_transforms_to_foreground(monkeypatch):
    services = _FakeApplicationServices()
    monkeypatch.setattr(process_type.sys, "platform", "darwin")
    monkeypatch.setattr(process_type.ctypes, "CDLL", lambda _path: services)

    assert process_type.show_gui_process_in_dock() is True
    assert services.TransformProcessType.calls[0][1] == 1


def test_non_macos_does_not_load_application_services(monkeypatch):
    monkeypatch.setattr(process_type.sys, "platform", "linux")
    monkeypatch.setattr(
        process_type.ctypes,
        "CDLL",
        lambda _path: (_ for _ in ()).throw(AssertionError("must not load")),
    )
    assert process_type.hide_headless_process_from_dock() is True


def test_launcher_hides_process_before_cli_dispatch(monkeypatch):
    from dbaide import cli
    from dbaide.desktop import launcher

    calls = []
    monkeypatch.setattr(
        process_type,
        "hide_headless_process_from_dock",
        lambda: calls.append("hide"),
    )
    monkeypatch.setattr(cli, "main", lambda argv: calls.append(("cli", argv)) or 7)

    assert launcher._run_headless_cli(["mcp-daemon", "status"]) == 7
    assert calls == ["hide", ("cli", ["mcp-daemon", "status"])]


def test_macos_bundle_starts_as_ui_element():
    from pathlib import Path

    spec = Path("packaging/pyinstaller/dbaide-gui.spec").read_text(encoding="utf-8")
    assert '"LSUIElement": True' in spec
