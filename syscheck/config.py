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
# Shown on the start screen and at the top of the report when the app
# isn't elevated (thermal zones and most drive data need Administrator).
ADMIN_BANNER = "Run as Administrator for temperatures and full drive details."

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
# Drive Health % (syscheck/diskhealth/health.py)
# ─────────────────────────────────────────────────────────────────────
# Health starts at 100%. Every health-relevant count takes a share away,
# loss = min(weight * count, max_loss), and the shares multiply: two
# different problems hurt more than one of the same size, and no single
# attribute can push health below 1 - max_loss. Our own starting numbers.
#   ATA attribute id: (loss per count, max loss, drives it applies to)
#   "all" = HDD and SSD; "hdd" = hard drives (and drives of unknown kind)
HEALTH_PENALTIES = {
    5: (0.01, 0.70, "all"),      # reallocated sectors
    196: (0.005, 0.50, "hdd"),   # reallocation events
    197: (0.02, 0.70, "all"),    # current pending sectors
    198: (0.03, 0.70, "all"),    # offline uncorrectable sectors
    187: (0.03, 0.60, "all"),    # reported uncorrectable errors
    188: (0.001, 0.20, "hdd"),   # command timeouts
    10: (0.05, 0.50, "hdd"),     # spin retries
    189: (0.001, 0.20, "hdd"),   # high fly writes
    184: (0.05, 0.50, "all"),    # end-to-end (internal data path) errors
}
# ATA 199 (UDMA CRC errors) costs nothing: it points at the cable or
# connector, not the drive, so it is reported as advice only.
HEALTH_NVME_MEDIA_PENALTY = (0.03, 0.70)        # NVMe media / integrity errors
# Windows' uncorrected read + write errors (reliability counters, which
# have no attribute table) count like reported uncorrectable errors.
HEALTH_WINDOWS_UNCORRECTED_PENALTY = (0.03, 0.60)

# Caps applied after the penalties: health is at most this many percent.
HEALTH_CAP_FAILING_PCT = 10      # drive or Windows says failing / NVMe critical warning
HEALTH_CAP_THRESHOLD_PCT = 10    # a normalised value at or below its threshold
HEALTH_CAP_SPARE_PCT = 20        # NVMe available spare below its threshold
HEALTH_CAP_WARNING_PCT = 60      # Windows HealthStatus "Warning"
# Temperature attributes crossing their threshold mean "hot right now",
# not damage, so they don't trigger the threshold cap.
HEALTH_THRESHOLD_IGNORE_IDS = (190, 194)
SSD_WEAR_WARN_PCT = 80           # rated life used before wear becomes a warning

# Condition word shown next to a Health % (first band it reaches). The
# bar colour follows the score bands: green >= 85, amber >= 60, else red.
HEALTH_WORDS = [
    (95, "EXCELLENT"),
    (85, "GOOD"),
    (60, "FAIR"),
    (30, "POOR"),
    (0, "CRITICAL"),
]

# ─────────────────────────────────────────────────────────────────────
# Estimated remaining drive life
# ─────────────────────────────────────────────────────────────────────
# Powered-on hours a healthy drive is expected to last (5 and 6 years).
EXPECTED_LIFE_HOURS = {"HDD": 43_800, "SSD": 52_560}
LIFE_CAP_DAYS = 1000             # estimates are capped and shown as "more than"
LIFE_HOURS_PER_DAY = 8           # assumed use when converting to calendar time
# A drive that has run this hot (now or lifetime max) loses a share of
# its expected life.
LIFE_HOT_C = {"HDD": 55, "SSD": 75}
LIFE_HEAT_PENALTY_FRAC = 0.10
# Raw power-on values implying more than ~15 years are re-read as minutes
# or half-minutes (Layer C only; smartctl knows each drive's unit).
POWER_ON_MAX_PLAUSIBLE_HOURS = 131_400

# ─────────────────────────────────────────────────────────────────────
# Storage data collection (smartctl / Windows S.M.A.R.T. sources)
# ─────────────────────────────────────────────────────────────────────
SMARTCTL_TIMEOUT_S = 10          # per call, so a hung drive can't stall the scan
# Sizes within this fraction count as the same drive when matching sources
# by model + size (Win32_DiskDrive rounds size down to whole cylinders).
DRIVE_SIZE_MATCH_TOLERANCE = 0.01

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
# Performance & hang check (syscheck/perf, telemetry/performance.py)
# ─────────────────────────────────────────────────────────────────────
PERF_IDLE_S = 30                 # idle load + idle stutter share this window
PERF_CPU_TEST_S = 10             # each of the one-core and all-core tests
PERF_LOAD_STUTTER_S = 20         # stutter test under full CPU load
PERF_MEMORY_TEST_MB = 256        # copy-bandwidth array (shrinks if memory is short)
PERF_DISK_TEST_MB = 256          # the one temporary file, in %TEMP%
PERF_DISK_CHUNK_MB = 4           # sequential read / write block
PERF_DISK_RANDOM_S = 5           # 4 KB random reads for this long
PERF_DISK_PHASE_MAX_S = 30       # a very slow drive stops each phase early
PERF_DISK_MIN_FREE_MB = 1024     # skip the drive test with less free space than this

# Stutter test: a 10 ms timer loop; longer gaps between wake-ups are felt.
PERF_TICK_MS = 10
PERF_STUTTER_MS = 50             # gap over this: a stutter
PERF_FREEZE_MS = 250             # gap over this: a freeze

# Reference values: the reference laptop (Core Ultra 7 265H, 32 GB DDR5,
# SK hynix PC801 NVMe) scores PERF_REFERENCE_POINTS on each measurement;
# every halving costs PERF_POINTS_PER_HALVING and every doubling adds as
# much, up to 100. cpu_single / cpu_multi / memory were measured on that
# laptop in WSL2, median of 5 runs. WSL2 saw 6 of its 16 cores, so
# cpu_multi is provisional: replace it with the all-core figure from a
# Windows run (Performance card > Technical details). The drive values
# are typical for a PCIe 4 NVMe SSD read one block at a time.
PERF_REFERENCE = {
    "cpu_single": 210.0,         # zlib work units / s on one core
    "cpu_multi": 690.0,          # zlib work units / s on every core (provisional)
    "memory": 23.5,              # GB/s copy bandwidth (bytes read + written)
    "seq_read": 3000.0,          # MB/s, 4 MB blocks, unbuffered
    "seq_write": 2000.0,         # MB/s, 4 MB blocks, write-through
    "rand_read": 10_000.0,       # 4 KB random reads / s, one at a time
}
PERF_REFERENCE_POINTS = 90
PERF_POINTS_PER_HALVING = 25

# How the sub-scores combine. Everyday speed is mostly one core, and the
# drive's random reads matter most for how quickly things open.
PERF_CPU_WEIGHTS = {"single": 0.6, "multi": 0.4}
PERF_DRIVE_WEIGHTS = {"rand_read": 0.6, "seq_read": 0.2, "seq_write": 0.2}
PERF_WEIGHTS = {"cpu": 0.30, "memory": 0.15, "drive": 0.35,
                "responsiveness": 0.20}
# Installed memory caps the memory sub-score: (at least GB, cap).
PERF_RAM_CAPS = [(16, 100), (8, 75), (4, 45), (0, 25)]
# Responsiveness starts at 100; each stutter / freeze takes this many
# points (up to the max). Gaps under full load count this fraction.
PERF_STUTTER_PENALTY = 5
PERF_STUTTER_MAX_LOSS = 40
PERF_FREEZE_PENALTY = 20
PERF_FREEZE_MAX_LOSS = 80
PERF_LOAD_GAP_FACTOR = 0.5

# Performance score -> label and status (first band it reaches).
PERF_LABELS = [
    (80, "Fast", "pass"),
    (60, "Good", "pass"),
    (40, "OK for basic use", "warn"),
    (0, "Slow", "fail"),
]

# Typical-use guide: the lowest of the "needs" sub-scores must reach
# "ok" for OK, "slow" for Slow, else Not suitable. Less memory than
# "ram_gb" caps it at Slow (under half: Not suitable); "gpu" needs a
# separate graphics card for better than Slow.
PERF_USE_GUIDE = {
    "Browsing": {"needs": ("cpu", "memory", "drive"), "ok": 30, "slow": 0,
                 "ram_gb": 4},
    "Office": {"needs": ("cpu", "memory", "drive"), "ok": 35, "slow": 0,
               "ram_gb": 4},
    "Video calls": {"needs": ("cpu", "memory", "responsiveness"), "ok": 50,
                    "slow": 25, "ram_gb": 8},
    "Light photo editing": {"needs": ("cpu", "memory", "drive"), "ok": 60,
                            "slow": 40, "ram_gb": 8},
    "Gaming": {"needs": ("cpu", "memory"), "ok": 75, "slow": 50,
               "ram_gb": 16, "gpu": True},
}

# "Why it might hang" thresholds.
PERF_IDLE_CPU_WARN_PCT = 15      # CPU busy at idle
PERF_IDLE_RAM_WARN_PCT = 80      # memory used at idle
PERF_IDLE_DISK_WARN_PCT = 50     # drive busy at idle
PERF_LOW_RAM_GB = 4              # this much memory or less is too little
PERF_COMMIT_WARN_PCT = 90        # memory reserved by programs vs the limit
PERF_STARTUP_WARN = 15           # programs starting with Windows
PERF_DISK_FREE_WARN_PCT = 10     # free space on the system drive
PERF_HDD_IOPS = 400              # random reads / s below this: hard-disk slow
PERF_SLOW_DRIVE_IOPS = 2000      # ... below this: a slow SSD
PERF_IDLE_STUTTER_WARN = 5       # stutters at idle worth mentioning
PERF_HANG_EVENTS_WARN = 3        # "stopped responding" events in the history
PERF_CRASH_EVENTS_WARN = 3       # program crashes in the history
PERF_SHUTDOWN_EVENTS_WARN = 2    # unexpected shutdowns in the history
PERF_SSD_UPGRADE = "about £40"   # what an SSD upgrade costs, for the advice
# Most likely cause first: higher sorts earlier.
PERF_HANG_PRIORITY = {
    "drive_health": 100, "hdd": 95, "disk_errors": 90, "bsod": 85,
    "freezes": 80, "ram": 75, "low_ram": 74, "thermal": 70, "stutter": 68,
    "slow_drive": 65, "disk_full": 60, "idle_cpu": 55, "commit": 52,
    "idle_disk": 50, "startup": 45, "app_hangs": 40, "app_crashes": 35,
    "shutdowns": 30, "power_saver": 25,
}

# Event-log history and the "reinstalled recently" check.
PERF_HISTORY_DAYS = 30
PERF_REINSTALL_DAYS = 14         # logs younger than this can't vouch for the past
PERF_EVENT_MAX = 2000            # events read per log

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
    "performance": 0.15,
}
