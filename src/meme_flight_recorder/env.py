"""Load ``.env`` into the process environment.

The repository ships a ``.env.example`` listing ``HELIUS_API_KEY`` and friends,
but nothing ever read the resulting ``.env``. Every provider calls ``os.getenv``
directly, so a correctly filled-in file silently had no effect and the only
symptom was providers reporting missing credentials.

Real environment variables always win over file contents. A value exported in
the shell or injected by a container orchestrator is more specific than a
checked-out file, and silently overriding it would be surprising.
"""

from __future__ import annotations

import os
from pathlib import Path

_loaded = False


def load_env(path: str | Path | None = None, override: bool = False) -> bool:
    """Populate ``os.environ`` from a dotenv file. Returns True if one was read.

    Idempotent: repeated calls are cheap no-ops, so entry points may call it
    defensively without coordinating.
    """
    global _loaded
    if _loaded and path is None:
        return True

    env_path = Path(path or os.getenv("MFR_ENV_FILE", ".env"))
    if not env_path.is_file():
        return False

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if not key:
            continue
        if override or key not in os.environ:
            os.environ[key] = value

    if path is None:
        _loaded = True
    return True
