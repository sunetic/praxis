from __future__ import annotations

import os
import shutil


def resolve_login_shell() -> str:
    """Return an executable shell, tolerating host-only ``SHELL`` values.

    Container processes commonly inherit values such as ``/bin/zsh`` from a
    macOS host even though that path does not exist in the Linux image.  Prefer
    the configured shell when it is executable, then fall back to shells that
    are normally available in the runtime image.
    """
    configured = str(os.environ.get("SHELL") or "").strip()
    candidates = [configured, "/bin/bash", "/bin/sh"]
    for candidate in candidates:
        if not candidate:
            continue
        resolved = shutil.which(candidate)
        if resolved and os.access(resolved, os.X_OK):
            return resolved
    return "sh"
