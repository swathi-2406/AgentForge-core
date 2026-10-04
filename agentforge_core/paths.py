"""Where the repo lives. AGENTFORGE_ROOT overrides it (tests use this)."""

import os
from pathlib import Path


def get_root() -> Path:
    env = os.getenv("AGENTFORGE_ROOT")
    return Path(env).resolve() if env else Path(__file__).resolve().parents[1]