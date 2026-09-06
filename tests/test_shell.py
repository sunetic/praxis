from __future__ import annotations

from app.core.shell import resolve_login_shell


def test_resolve_login_shell_uses_configured_executable(monkeypatch):
    monkeypatch.setenv("SHELL", "/bin/sh")

    assert resolve_login_shell() == "/bin/sh"


def test_resolve_login_shell_falls_back_when_host_shell_is_missing(monkeypatch):
    monkeypatch.setenv("SHELL", "/host-only/bin/zsh")

    assert resolve_login_shell() in {"/bin/bash", "/bin/sh"}
