"""Battery collector and discharge test, driven by a scripted fake battery.

The fake clock replaces ``time`` inside the battery module, so a six-minute
discharge replays in milliseconds.
"""

from types import SimpleNamespace

import pytest

from syscheck.scoring import score_battery
from syscheck.telemetry import battery

FULL_MWH = 50_000            # 50 Wh pack
SECONDS_PER_PCT = 180.0      # 1% (500 mWh) every 180 s  ==  10 W draw


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def monotonic(self):
        return self.t

    def sleep(self, seconds):
        self.t += seconds


@pytest.fixture
def clock(monkeypatch):
    clk = FakeClock()
    monkeypatch.setattr(battery, "time", clk)
    monkeypatch.setattr(battery.psutil, "cpu_percent", lambda interval=None: 12.0)
    return clk


def _discharging(clock, start_charge, plugged_until=0.0, secsleft=-1):
    """psutil-style readings: Windows reports whole percents."""
    def reading():
        on_ac = clock.t < plugged_until
        drained = max(0.0, clock.t - plugged_until) / SECONDS_PER_PCT
        return SimpleNamespace(percent=int(start_charge - drained),
                               power_plugged=on_ac, secsleft=secsleft)
    return reading


def test_percent_method_times_a_full_step_not_the_partial_first_one(
        clock, monkeypatch):
    # Unplugged at 57.1%: the first whole-percent drop comes after only
    # ~18 s. Timing from the unplug used to report ~100 W for a 10 W laptop.
    monkeypatch.setattr(battery, "_battery_reading",
                        _discharging(clock, start_charge=57.1))
    result = battery.run_discharge_test(full_mwh=FULL_MWH)

    assert result["method"] == "percent"
    assert result["measured"] is True
    assert result["seconds_per_pct"] == pytest.approx(SECONDS_PER_PCT, abs=2)
    assert result["drain_w"] == pytest.approx(10.0, abs=0.2)
    assert result["avg_cpu_pct"] == 12.0

    health = {"present": True, "wear_pct": 10.0, "discharge": result}
    assert score_battery(health).status == "pass"


def test_waits_for_unplug_before_timing(clock, monkeypatch):
    samples = []
    monkeypatch.setattr(battery, "_battery_reading",
                        _discharging(clock, start_charge=80.5,
                                     plugged_until=30.0))
    result = battery.run_discharge_test(
        full_mwh=FULL_MWH, on_sample=lambda *s: samples.append(s))

    assert samples[0][0] == "wait"
    assert any(s[0] == "drain" for s in samples)
    assert result["start_pct"] == 80
    assert result["drain_w"] == pytest.approx(10.0, abs=0.2)


def test_rate_method_uses_firmware_discharge_rate(clock, monkeypatch):
    monkeypatch.setattr(battery, "_battery_reading",
                        _discharging(clock, start_charge=57.9))
    monkeypatch.setattr(battery, "_discharge_rate_mw", lambda conn: 9_500)
    result = battery.run_discharge_test(full_mwh=FULL_MWH)

    assert result["method"] == "rate"
    assert result["drain_w"] == 9.5
    assert result["seconds_per_pct"] == pytest.approx(189.5, abs=0.1)
    assert clock.t < 30                       # no waiting for percent ticks


def test_reconnecting_ac_voids_the_reading(clock, monkeypatch):
    base = _discharging(clock, start_charge=57.1)

    def reading():
        r = base()
        r.power_plugged = clock.t >= 5
        return r

    monkeypatch.setattr(battery, "_battery_reading", reading)
    result = battery.run_discharge_test(full_mwh=FULL_MWH)

    assert result["ran"] is True
    assert result["method"] is None
    assert result["drain_w"] is None
    assert "reconnected" in result["reason"]


def test_falls_back_to_os_estimate_when_charge_never_drops(clock, monkeypatch):
    monkeypatch.setattr(
        battery, "_battery_reading",
        lambda: SimpleNamespace(percent=57, power_plugged=False,
                                secsleft=10_000))
    result = battery.run_discharge_test(full_mwh=FULL_MWH)

    assert result["method"] == "estimate"
    assert result["estimated"] is True
    assert result["seconds_per_pct"] == pytest.approx(10_000 / 57, abs=0.1)


def test_no_battery_skips_the_test(clock, monkeypatch):
    monkeypatch.setattr(battery, "_battery_reading", lambda: None)
    result = battery.run_discharge_test()
    assert result["ran"] is False
    assert result["reason"] == "No battery present."


def test_battery_seen_by_os_but_without_capacity_is_present(monkeypatch):
    empty = {"design_mwh": None, "full_mwh": None, "cycles": None}
    monkeypatch.setattr(battery, "_wmi_battery", lambda log: dict(empty))
    monkeypatch.setattr(battery, "_powercfg_report", lambda log: dict(empty))
    monkeypatch.setattr(battery, "_battery_reading",
                        lambda: SimpleNamespace(percent=50,
                                                power_plugged=True,
                                                secsleft=-2))
    data = battery.collect_battery_health()

    assert data["present"] is True
    assert data["wear_pct"] is None
    assert score_battery(data).status == "inconclusive"
