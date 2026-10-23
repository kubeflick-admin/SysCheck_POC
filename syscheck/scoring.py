"""Health scoring: raw telemetry -> per-subsystem scores -> letter grade.

Each subsystem scorer returns a :class:`ComponentScore` with a 0–100
value, a status in {pass, warn, fail, skip}, and a one-line note aimed at
a non-technical buyer. The overall score is the weighted average of the
non-skipped components (weights renormalise when something is skipped),
mapped to a letter via ``config.GRADE_BANDS``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from . import config


@dataclass
class ComponentScore:
    key: str
    label: str
    score: float             # 0–100
    status: str              # pass | warn | fail | skip
    note: str = ""
    details: List[str] = field(default_factory=list)


def _band_from_status(score: float) -> str:
    if score >= 85:
        return "pass"
    if score >= 60:
        return "warn"
    return "fail"


# ─────────────────────────────────────────────────────────────────────
# Subsystem scorers
# ─────────────────────────────────────────────────────────────────────
def score_storage(data: Dict[str, object]) -> ComponentScore:
    drives = data.get("drives", []) if data else []
    if not drives:
        return ComponentScore("storage", "Storage", 50, "skip",
                              "No drive telemetry available.")

    worst = 100.0
    notes: List[str] = []
    details: List[str] = []
    for d in drives:
        drive_score = 100.0
        model = d.get("model", "Drive")
        health = d.get("health", "Unknown")
        if health == "FAILING":
            drive_score = 0.0
            notes.append(f"{model} reports imminent failure.")
        elif health in ("Unhealthy",):
            drive_score = min(drive_score, 20.0)
            notes.append(f"{model} health is Unhealthy.")
        elif health in ("Warning",):
            drive_score = min(drive_score, 60.0)

        poh = d.get("power_on_hours")
        if poh is not None:
            if poh >= config.POH_FAIL_HOURS:
                drive_score = min(drive_score, 45.0)
                notes.append(f"{model} has very high usage ({poh:,} h).")
            elif poh >= config.POH_WARN_HOURS:
                drive_score = min(drive_score, 70.0)
            details.append(f"{model}: {poh:,} power-on hours")

        tbw = d.get("host_writes_tb")
        if tbw is not None:
            if tbw >= config.TBW_FAIL_TB:
                drive_score = min(drive_score, 45.0)
                notes.append(f"{model} heavily written ({tbw:.0f} TB).")
            elif tbw >= config.TBW_WARN_TB:
                drive_score = min(drive_score, 70.0)
            details.append(f"{model}: {tbw:.1f} TB written")

        worst = min(worst, drive_score)

    if not data.get("smart_readable", False):
        notes.append("Run as Administrator for full S.M.A.R.T. detail.")
        worst = min(worst, 80.0)  # cap: we can't fully verify wear

    note = " ".join(notes) if notes else "Drive health looks good."
    return ComponentScore("storage", "Storage", worst,
                          _band_from_status(worst), note, details)


def score_battery(data: Dict[str, object]) -> ComponentScore:
    if not data or not data.get("present"):
        return ComponentScore("battery", "Battery", 0, "skip",
                              "No battery present (excluded from grade).")

    wear = data.get("wear_pct")
    cycles = data.get("cycles")
    score = 100.0
    notes: List[str] = []
    details: List[str] = []

    if wear is not None:
        details.append(f"Wear level: {wear:.0f}%")
        if wear >= config.BATTERY_WEAR_FAIL_PCT:
            score = min(score, 35.0)
            notes.append(f"Battery badly worn ({wear:.0f}%).")
        elif wear >= config.BATTERY_WEAR_WARN_PCT:
            score = min(score, 65.0)
            notes.append(f"Battery moderately worn ({wear:.0f}%).")
        else:
            score = min(score, 100.0 - wear)  # gentle linear penalty

    if cycles is not None:
        details.append(f"Charge cycles: {cycles}")
        if cycles >= config.BATTERY_CYCLE_FAIL:
            score = min(score, 45.0)
        elif cycles >= config.BATTERY_CYCLE_WARN:
            score = min(score, 70.0)

    # Live discharge test (time + load for a 1% drop, off AC).
    disc = data.get("discharge") if isinstance(data, dict) else None
    if isinstance(disc, dict) and disc.get("ran") and disc.get("seconds_per_pct"):
        spp = disc["seconds_per_pct"]
        kind = "measured" if disc.get("measured") else "estimated"
        line = f"Discharge: {spp:.0f}s per 1% ({kind})"
        if disc.get("avg_cpu_pct") is not None:
            line += f" at {disc['avg_cpu_pct']:.0f}% CPU"
        details.append(line)
        drain = disc.get("drain_w")
        if drain is not None:
            details.append(f"Average draw: ~{drain:.1f} W")
            if drain >= config.BATTERY_DRAIN_FAIL_W:
                score = min(score, 45.0)
                notes.append(f"Very high power draw ({drain:.0f} W) off mains.")
            elif drain >= config.BATTERY_DRAIN_WARN_W:
                score = min(score, 70.0)
                notes.append(f"Elevated power draw ({drain:.0f} W) off mains.")

    note = " ".join(notes) if notes else "Battery holds a healthy charge."
    return ComponentScore("battery", "Battery", score,
                          _band_from_status(score), note, details)


def score_thermal(data: Dict[str, object]) -> ComponentScore:
    if not data:
        return ComponentScore("thermal", "Thermals", 50, "skip",
                              "Thermal test not run.")

    score = 100.0
    notes: List[str] = []
    details: List[str] = []
    peak = data.get("peak_temp_c")

    if not data.get("temp_available"):
        notes.append("No temperature sensor readable on this machine.")
        score = min(score, 75.0)
    elif peak is not None:
        details.append(f"Peak temperature: {peak:.0f} °C")
        if peak >= config.THERMAL_CRITICAL_C:
            score = min(score, 30.0)
            notes.append(f"Overheated to {peak:.0f} °C under load.")
        elif peak >= config.THERMAL_WARN_C:
            score = min(score, 70.0)
            notes.append(f"Runs warm ({peak:.0f} °C under load).")

    if data.get("throttled"):
        score = min(score, 60.0)
        notes.append("CPU throttled its clock under sustained load.")
        if data.get("peak_freq_mhz") and data.get("min_freq_mhz"):
            details.append(f"Clock: {data['peak_freq_mhz']} → "
                           f"{data['min_freq_mhz']} MHz under load")

    note = " ".join(notes) if notes else "Cooling held up under load."
    return ComponentScore("thermal", "Thermals", score,
                          _band_from_status(score), note, details)


def score_camera(data: Dict[str, object]) -> ComponentScore:
    if not data or data.get("skipped"):
        return ComponentScore("camera", "Webcam", 50, "skip",
                              data.get("reason", "Camera test skipped.") if data
                              else "Camera test skipped.")
    final = data.get("final_score", 0.0)
    details = [f"Focus score: {final:.0f} "
               f"(threshold {config.CAMERA_BLUR_THRESHOLD:.0f})"]
    if data.get("blurry"):
        score, note = 55.0, "Webcam image stayed soft even after cleaning."
    else:
        score, note = 100.0, "Webcam produces a sharp image."
    if data.get("improved"):
        details.append("Clarity improved after cleaning the lens.")
    return ComponentScore("camera", "Webcam", score,
                          _band_from_status(score), note, details)


def score_keyboard(data: Dict[str, object]) -> ComponentScore:
    if not data or data.get("skipped"):
        return ComponentScore("keyboard", "Keyboard", 50, "skip",
                              "Keyboard test skipped.")
    coverage = data.get("coverage_pct", 0.0)
    dead = data.get("dead_or_missed", [])
    details = [f"Keys actuated: {data.get('pressed', 0)}/{data.get('total', 0)}"]
    if dead:
        details.append("Unconfirmed: " + ", ".join(dead[:12])
                       + (" …" if len(dead) > 12 else ""))
    score = float(coverage)
    if coverage >= 100:
        note = "Every key registered."
    elif coverage >= 90:
        note = "Almost all keys registered."
    else:
        note = f"{len(dead)} key(s) never registered — check for dead keys."
    return ComponentScore("keyboard", "Keyboard", score,
                          _band_from_status(score), note, details)


def score_touchpad(data: Dict[str, object]) -> ComponentScore:
    if not data or data.get("skipped"):
        return ComponentScore("touchpad", "Touchpad", 50, "skip",
                              "Touchpad test skipped.")
    coverage = float(data.get("coverage_pct", 0.0))
    missing = data.get("missing_gestures", []) or []
    details = [f"Pad coverage: {coverage:.0f}% ({data.get('cells', '?')} tiles)"]
    if missing:
        pretty = ", ".join(m.replace("_", " ") for m in missing)
        details.append(f"Not demonstrated: {pretty}")

    # Coverage sets the score; an un-demonstrated gesture caps it to caution.
    score = coverage
    if coverage >= config.TOUCHPAD_COVERAGE_WARN_PCT and not missing:
        note = "Cursor reached the whole pad; all gestures worked."
    elif coverage >= config.TOUCHPAD_COVERAGE_FAIL_PCT:
        score = min(score, 70.0)
        note = ("Pad mostly responsive"
                + (f" — {len(missing)} gesture(s) unconfirmed." if missing
                   else ", but some areas were hard to reach."))
    else:
        note = "Large areas of the pad never registered the cursor."
    return ComponentScore("touchpad", "Touchpad", score,
                          _band_from_status(score), note, details)


def score_ports(data: Dict[str, object]) -> ComponentScore:
    if not data or data.get("skipped"):
        return ComponentScore("ports", "Ports", 50, "skip",
                              "Ports test skipped.")
    connects = int(data.get("connects", 0))
    disconnects = int(data.get("disconnects", 0))
    displays = data.get("displays", []) or []
    details = [f"USB connect events: {connects}",
               f"USB disconnect events: {disconnects}"]
    if displays:
        details.append("Display outputs: " + ", ".join(displays))

    if data.get("verified"):
        score, note = 100.0, "USB ports responded to plug/unplug events."
    elif connects or disconnects:
        score, note = 70.0, ("Some USB activity seen, but no full "
                             "plug-and-unplug cycle was completed.")
    else:
        score, note = 55.0, ("No USB activity detected — ports could not be "
                             "verified (try plugging a device in).")
    return ComponentScore("ports", "Ports", score,
                          _band_from_status(score), note, details)


# ─────────────────────────────────────────────────────────────────────
# Aggregation
# ─────────────────────────────────────────────────────────────────────
def compute_overall(components: List[ComponentScore]) -> Dict[str, object]:
    """Weighted average of non-skipped components -> letter grade."""
    weights = config.SCORE_WEIGHTS
    total_w = 0.0
    acc = 0.0
    for c in components:
        if c.status == "skip":
            continue
        w = weights.get(c.key, 0.0)
        total_w += w
        acc += w * c.score

    overall = (acc / total_w) if total_w > 0 else 0.0

    grade = "F"
    for letter, cutoff in config.GRADE_BANDS:
        if overall >= cutoff:
            grade = letter
            break

    # Any hard failure drags the grade down to at most C.
    if any(c.status == "fail" for c in components) and grade in ("A", "B"):
        grade = "C"

    return {"score": round(overall, 1), "grade": grade}


def build_report(results: Dict[str, object]) -> Dict[str, object]:
    """Top-level entry: produce components + overall from a results dict."""
    components = [
        score_storage(results.get("storage", {})),
        score_battery(results.get("battery", {})),
        score_thermal(results.get("thermal", {})),
        score_camera(results.get("camera", {})),
        score_keyboard(results.get("keyboard", {})),
        score_touchpad(results.get("touchpad", {})),
        score_ports(results.get("ports", {})),
    ]
    overall = compute_overall(components)
    return {"components": components, "overall": overall}
