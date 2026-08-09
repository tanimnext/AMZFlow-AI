"""Single source of truth for product identity.

Every template, page title, footer, and default preview string reads from here,
so a rebrand is one edit instead of the twenty scattered literals this replaced.
"""

from __future__ import annotations

try:
    from .runtime_support import version as _runtime_version
except ImportError:
    from runtime_support import version as _runtime_version


BRAND = {
    "name": "AmzFlow AI",
    "short_name": "AmzFlow",
    "version": f"v{_runtime_version()}",
    "tagline": "AI Video Engine for Amazon Affiliates",
    # Directory name under ~/Library/Application Support (or %APPDATA%).
    # Changing this requires a migration step in scripts/migrate_secrets.py --
    # the previous name is kept as LEGACY_DATA_DIR_NAME in secure_paths.py.
    "data_dir_name": "AmzFlow AI",
}

PREVIEW_TEXT = (
    f"Welcome to {BRAND['name']}. This is how I sound with these settings."
)
