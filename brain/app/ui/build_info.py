# brain/app/ui/build_info.py
"""Build stamp support.

The stamp is baked into the image at `docker compose build` time (see
brain/Dockerfile) as app/BUILD_STAMP:

    <12-char sha256 of app/ contents>.<UTC build time YYYYMMDD-HHMM>

Outside a built container (dev checkouts, pytest) the file is absent and the
stamp is "dev". The stamp is served at /api/ui/version, rendered into the
/ui/doctor page and every UI footer, and appended to static asset URLs so a
stale browser cache is impossible to miss and impossible to hit.
"""
from __future__ import annotations

from pathlib import Path

_STAMP_FILE = Path(__file__).resolve().parent.parent / "BUILD_STAMP"


def get_build_stamp() -> str:
    try:
        return _STAMP_FILE.read_text(encoding="utf-8").strip() or "dev"
    except OSError:
        return "dev"
