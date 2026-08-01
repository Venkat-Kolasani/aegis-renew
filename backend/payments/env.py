"""Load local ignored env files for payment configuration."""

from __future__ import annotations

import os
from pathlib import Path


def load_local_env() -> None:
    """Populate os.environ from the repo-root .env when keys are unset."""
    env_path = Path(__file__).resolve().parents[2] / ".env"
    if not env_path.is_file():
        return

    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value
