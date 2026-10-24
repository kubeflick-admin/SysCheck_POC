"""Central configuration: thresholds, palette, and tunable constants.

Keeping every magic number in one place makes the scoring logic auditable
and lets you re-tune the tool for a particular fleet of laptops without
hunting through the codebase.
"""

from __future__ import annotations

# ─────────────────────────────────────────────────────────────────────
# Branding
# ─────────────────────────────────────────────────────────────────────
APP_TITLE = "SysCheck // Laptop Diagnostic Console"
APP_TAGLINE = "SECOND-HAND HARDWARE INTEGRITY SCANNER"

# ─────────────────────────────────────────────────────────────────────
# Cyber-HUD palette  (hex strings, consumed by theme.py + widgets)
# ─────────────────────────────────────────────────────────────────────
COL_BG = "#0D0D0D"          # deep black background
COL_PANEL = "#141617"       # slightly raised panel
COL_PANEL_HI = "#1C1F21"    # hover / selected panel
COL_GRID = "#2A2E31"        # hairlines / borders

COL_BLUE = "#00E5FF"        # electric blue  — primary accent
COL_GREEN = "#39FF14"       # matrix green   — pass / good
COL_AMBER = "#FFB000"       # amber          — warning / caution
COL_RED = "#FF3B3B"         # red            — fail / critical
COL_TEXT = "#D6E4E8"        # default text
COL_TEXT_DIM = "#6C7A80"    # secondary text

MONO_FONT = "Consolas"      # falls back to Courier New / monospace in QSS

# ─────────────────────────────────────────────────────────────────────
# Thermal stress test
# ─────────────────────────────────────────────────────────────────────
THERMAL_DURATION_S = 45          # length of the synthetic load (30–60s)
THERMAL_SAMPLE_HZ = 2            # temperature samples per second
THERMAL_CRITICAL_C = 90.0        # hard fail above this package temp
THERMAL_WARN_C = 80.0            # caution band
THERMAL_THROTTLE_DROP_MHZ = 300  # sustained clock this far below base => throttling
THERMAL_SUSTAIN_WINDOW_S = 10    # tail of the run that gives the sustained clock
THERMAL_STUCK_DELTA_C = 1.0      # less movement under full load => dead sensor

# ─────────────────────────────────────────────────────────────────────
# Camera clarity
# ─────────────────────────────────────────────────────────────────────
CAMERA_INDEX = 0                 # cv2.VideoCapture index
CAMERA_BLUR_THRESHOLD = 90.0     # Laplacian variance below => "blurry"
CAMERA_IMPROVE_DELTA = 15.0      # variance gain counted as a real improvement

# ─────────────────────────────────────────────────────────────────────
# Storage health scoring (heuristics for consumer laptops)
# ─────────────────────────────────────────────────────────────────────
POH_WARN_HOURS = 15000           # ~1.7 years powered on
POH_FAIL_HOURS = 30000           # ~3.4 years powered on
TBW_WARN_TB = 100.0              # host writes caution (typical SSD endurance)
TBW_FAIL_TB = 250.0

# ─────────────────────────────────────────────────────────────────────
# Battery health scoring
# ─────────────────────────────────────────────────────────────────────
BATTERY_WEAR_WARN_PCT = 20.0     # wear level caution
BATTERY_WEAR_FAIL_PCT = 40.0     # wear level fail
BATTERY_CYCLE_WARN = 500
BATTERY_CYCLE_FAIL = 1000

# ─────────────────────────────────────────────────────────────────────
# Battery discharge test (runs on battery only — prompts to unplug AC)
# ─────────────────────────────────────────────────────────────────────
BATTERY_DISCHARGE_DROP_PCT = 1        # whole-percent step we time (1%)
# Timing a full step can take two percent-intervals (~180 s each at
# 10 W on a 50 Wh pack), so allow for both before falling back.
BATTERY_DISCHARGE_TIMEOUT_S = 360     # give up waiting for a full step after this
BATTERY_DISCHARGE_SAMPLE_HZ = 1       # percent / load samples per second
BATTERY_RATE_WINDOW_S = 20            # average the firmware discharge rate over this
# Derived drain (watts) above which we flag an unusually thirsty battery.
BATTERY_DRAIN_WARN_W = 35.0
BATTERY_DRAIN_FAIL_W = 60.0

# ─────────────────────────────────────────────────────────────────────
# Keyboard continuity test
# ─────────────────────────────────────────────────────────────────────
KEYBOARD_COVERAGE_FAIL_PCT = 90.0   # below this => several dead keys

# ─────────────────────────────────────────────────────────────────────
# Touchpad continuity test
# ─────────────────────────────────────────────────────────────────────
TOUCHPAD_GRID_COLS = 10          # coverage grid resolution
TOUCHPAD_GRID_ROWS = 6
TOUCHPAD_COVERAGE_WARN_PCT = 85.0   # below this => cursor can't reach areas
TOUCHPAD_COVERAGE_FAIL_PCT = 60.0

# ─────────────────────────────────────────────────────────────────────
# Ports test (live USB arrival / removal detection)
# ─────────────────────────────────────────────────────────────────────
PORTS_POLL_HZ = 2                # USB enumeration polls per second

# ─────────────────────────────────────────────────────────────────────
# Overall score bands (weighted 0–100 → letter grade)
# ─────────────────────────────────────────────────────────────────────
GRADE_BANDS = [
    ("A", 85),   # >= 85
    ("B", 70),   # >= 70
    ("C", 50),   # >= 50
    ("F", 0),    # otherwise
]

# Relative weight each subsystem contributes to the final score.
SCORE_WEIGHTS = {
    "storage": 0.20,
    "battery": 0.20,
    "thermal": 0.18,
    "camera": 0.12,
    "keyboard": 0.10,
    "touchpad": 0.10,
    "ports": 0.10,
}
