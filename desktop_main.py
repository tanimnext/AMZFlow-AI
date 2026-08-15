"""AmzFlow AI portable desktop entry point."""

from __future__ import annotations

import os
import runpy
import sys
from pathlib import Path


def _resource_root() -> Path:
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)).resolve()


def _prepare_imports() -> Path:
    root = _resource_root()
    for path in (root / "web_app", root / "app_files", root):
        value = str(path)
        if value not in sys.path:
            sys.path.insert(0, value)
    os.environ["AMZFLOW_DESKTOP"] = "1"
    return root


def main() -> int:
    root = _prepare_imports()
    if len(sys.argv) > 1 and sys.argv[1] == "--render-worker":
        script = root / "app_files" / "amazon_video_maker.py"
        sys.argv = [str(script), *sys.argv[2:]]
        runpy.run_path(str(script), run_name="__main__")
        return 0

    from update_manager import maybe_start_update

    if maybe_start_update():
        return 0

    import app as application

    # The packaged app starts here, not through app.py's __main__ block, so
    # the settings migration has to be invoked on this path too -- otherwise
    # it would only ever run for people launching from source.
    try:
        application.migrate_superseded_defaults()
    except Exception as exc:  # never block startup over a settings tidy-up
        print(f"[SETTINGS][WARN] Default migration skipped: {exc}")

    host, port = "127.0.0.1", 7503
    application.open_browser_after_start(f"http://{host}:{port}")
    application.app.run(debug=False, host=host, port=port, use_reloader=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
