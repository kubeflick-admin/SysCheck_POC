"""Scoring rules: per-component statuses and the overall grade."""

import pytest

from syscheck import config
from syscheck.scoring import (ComponentScore, build_report, compute_overall,
                              score_battery, score_camera, score_keyboard,
                              score_ports, score_thermal)


def _comp(key, score, status):
    return ComponentScore(key, key.title(), score, status)


# ── overall grade ──────────────────────────────────────────────────────
def test_overall_is_weighted_average_of_measured_components():
    overall = compute_overall([_comp("storage", 100, "pass"),
                               _comp("battery", 50, "fail")])
    # Equal weights (0.20 each) -> plain mean.
    assert overall["score"] == 75.0


def test_any_fail_caps_grade_at_c():
    comps = [_comp(k, 100, "pass") for k in config.SCORE_WEIGHTS]
    comps[0] = _comp(comps[0].key, 50, "fail")
    assert compute_overall(comps)["grade"] == "C"


def test_skip_and_inconclusive_are_left_out_of_the_grade():
    comps = [_comp("storage", 100, "pass"),
             _comp("ports", 0, "inconclusive"),
             _comp("camera", 0, "skip")]
    assert compute_overall(comps) == {"score": 100.0, "grade": "A"}


def test_nothing_measured_gives_na_not_f():
    comps = [_comp("ports", 0, "inconclusive"), _comp("camera", 0, "skip")]
    assert compute_overall(comps) == {"score": None, "grade": "N/A"}


def test_empty_results_give_na():
    assert build_report({})["overall"]["grade"] == "N/A"


def test_ports_without_a_usb_device_do_not_cap_a_good_laptop():
    results = {
        "battery": {"present": True, "wear_pct": 5.0, "cycles": 100},
        "thermal": {"completed": True, "temp_available": True,
                    "peak_temp_c": 70.0, "throttled": False},
        "keyboard": {"coverage_pct": 100.0, "pressed": 77, "total": 77,
                     "dead_or_missed": []},
        "ports": {"connects": 0, "disconnects": 0, "verified": False},
    }
    report = build_report(results)
    ports = next(c for c in report["components"] if c.key == "ports")
    assert ports.status == "inconclusive"
    assert report["overall"]["grade"] == "A"


# ── battery ────────────────────────────────────────────────────────────
@pytest.mark.parametrize("wear, status", [
    (0.0, "pass"),
    (config.BATTERY_WEAR_WARN_PCT - 0.1, "pass"),   # was "warn" from 15%
    (config.BATTERY_WEAR_WARN_PCT, "warn"),
    (config.BATTERY_WEAR_FAIL_PCT, "fail"),
])
def test_battery_wear_bands_match_config(wear, status):
    assert score_battery({"present": True, "wear_pct": wear}).status == status


def test_battery_fitted_but_unreadable_is_inconclusive():
    comp = score_battery({"present": True, "wear_pct": None, "cycles": None})
    assert comp.status == "inconclusive"


def test_no_battery_is_skipped():
    assert score_battery({"present": False}).status == "skip"


def test_battery_draw_without_capacity_is_still_scored():
    # Firmware rate path on a pack whose capacity is unknown.
    disc = {"ran": True, "method": "rate", "drain_w": 70.0,
            "seconds_per_pct": None}
    comp = score_battery({"present": True, "discharge": disc})
    assert comp.status == "fail"
    assert any("70.0 W" in d for d in comp.details)


# ── thermal ────────────────────────────────────────────────────────────
def test_thermal_stopped_early_is_skipped_not_passed():
    data = {"completed": False, "temp_available": True,
            "peak_temp_c": 50.0, "throttled": False}
    assert score_thermal(data).status == "skip"


def test_thermal_without_sensor_or_throttle_is_inconclusive():
    data = {"completed": True, "temp_available": False, "throttled": False}
    assert score_thermal(data).status == "inconclusive"


def test_thermal_without_sensor_but_throttled_is_warn():
    data = {"completed": True, "temp_available": False, "throttled": True,
            "base_freq_mhz": 2600, "sustained_freq_mhz": 1800}
    comp = score_thermal(data)
    assert comp.status == "warn"
    assert "1800 MHz sustained vs 2600 MHz base" in comp.details[0]


def test_thermal_overheat_fails():
    data = {"completed": True, "temp_available": True,
            "peak_temp_c": config.THERMAL_CRITICAL_C + 1, "throttled": False}
    assert score_thermal(data).status == "fail"


# ── keyboard / camera / ports ──────────────────────────────────────────
@pytest.mark.parametrize("coverage, status", [
    (100.0, "pass"),
    (95.0, "warn"),          # a few dark keys: worth a re-test
    (80.0, "fail"),          # several dead keys
])
def test_keyboard_any_dark_key_is_not_a_pass(coverage, status):
    data = {"coverage_pct": coverage, "dead_or_missed": ["Q"] * 3}
    assert score_keyboard(data).status == status


def test_camera_that_will_not_open_is_inconclusive():
    comp = score_camera({"unavailable": True, "reason": "device busy"})
    assert comp.status == "inconclusive"
    assert comp.details == ["device busy"]


def test_blurry_camera_is_caution_not_failure():
    data = {"final_score": 20.0, "blurry": True, "improved": False}
    assert score_camera(data).status == "warn"


def test_ports_full_cycle_passes():
    data = {"connects": 1, "disconnects": 1, "verified": True}
    assert score_ports(data).status == "pass"
