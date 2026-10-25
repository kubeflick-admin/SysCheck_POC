"""Thermal evaluation heuristics and early-stop handling."""

from syscheck import config
from syscheck.scoring import score_thermal
from syscheck.telemetry import thermal

BASE_MHZ = 2600.0


def _ramp(start, end, n):
    return [start + (end - start) * i / (n - 1) for i in range(n)]


def test_turbo_falling_back_to_base_is_not_throttling():
    # 4.5 GHz boost for the first 20 s, then a steady base clock.
    freqs = [4500.0] * 40 + [BASE_MHZ] * 50
    result = thermal._evaluate(_ramp(55, 78, 90), freqs, BASE_MHZ)
    assert result["throttled"] is False
    assert result["sustained_freq_mhz"] == 2600


def test_sustained_clock_below_base_is_throttling():
    freqs = [4500.0] * 40 + [1800.0] * 50
    result = thermal._evaluate(_ramp(55, 88, 90), freqs, BASE_MHZ)
    assert result["throttled"] is True
    assert score_thermal({**result, "completed": True}).status == "warn"


def test_one_off_dip_at_the_end_is_ignored():
    freqs = [BASE_MHZ] * 85 + [1200.0] * 2 + [BASE_MHZ] * 3
    assert thermal._evaluate(_ramp(55, 75, 90), freqs, BASE_MHZ)["throttled"] \
        is False


def test_sensor_that_never_moves_is_treated_as_unavailable():
    result = thermal._evaluate([27.8] * 90, [BASE_MHZ] * 90, BASE_MHZ)
    assert result["temp_stuck"] is True
    assert result["temp_available"] is False
    assert result["peak_temp_c"] is None
    assert score_thermal({**result, "completed": True}).status == "inconclusive"


def test_overheating_is_flagged():
    temps = _ramp(60, config.THERMAL_CRITICAL_C + 3, 90)
    result = thermal._evaluate(temps, [BASE_MHZ] * 90, BASE_MHZ)
    assert result["overheated"] is True
    assert score_thermal({**result, "completed": True}).status == "fail"


def test_stopping_early_marks_the_run_incomplete():
    result = thermal.run_thermal_stress(should_stop=lambda: True)
    assert result["completed"] is False
    assert score_thermal(result).status == "skip"
