"""Shared test setup.

Qt runs on the offscreen platform so the view tests work headless (WSL,
CI). WMI is absent off Windows, so every collector already degrades to
"unavailable"; tests that need hardware readings fake them explicitly.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
