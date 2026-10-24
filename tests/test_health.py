"""Drive Health % and estimated remaining life, on hand-built drives and
the smartctl fixtures. The default weights in ``config`` are assumed."""

import json
from pathlib import Path

import pytest

from syscheck import config
from syscheck.diskhealth.health import (compute_health,
                                        estimate_remaining_life, life_words,
                                        vendor_health)
from syscheck.telemetry.smart_sources import smartctl
from syscheck.telemetry.smart_sources.schema import new_drive

FIXTURES = Path(__file__).parent / "fixtures" / "smartctl"


def _fixture(name):
    report = json.loads((FIXTURES / f"{name}.json").read_text("utf-8"))
    return smartctl.parse_device(report)


def _attr(attr_id, raw=0, value=100, thresh=0, name=None):
    return {"id": attr_id, "name": name or f"Attr_{attr_id}",
            "value": value, "worst": value, "thresh": thresh, "raw": raw,
            "raw_string": str(raw), "when_failed": None, "prefailure": None}


def _ata(*attrs, kind="SATA SSD", **fields):
    """A smartctl-read ATA drive with this attribute table."""
    fields.setdefault("health_flag", "OK")
    return new_drive(kind=kind, source="smartctl",
                     raw={"attributes": list(attrs)}, **fields)


def _pct(drive):
    return compute_health(drive).health_pct


# ── edge cases ─────────────────────────────────────────────────────────
def test_nothing_readable_has_no_health():
    result = compute_health(new_drive())
    assert result.health_pct is None
    assert result.method == "none"
    assert result.reasons == []


def test_failing_flag_alone_caps_health():
    result = compute_health(new_drive(health_flag="FAILING"))
    assert result.health_pct == config.HEALTH_CAP_FAILING_PCT
    assert result.method == "status"
    assert result.worst.severity == "fail"
    assert "Windows reports" in result.worst.text


def test_failing_flag_caps_even_clean_counters():
    assert _pct(_ata(_attr(5), health_flag="FAILING")) == 10


def test_windows_warning_caps_health():
    drive = new_drive(source="reliability_counter", health_flag="Warning",
                      uncorrectable_errors=0)
    assert _pct(drive) == config.HEALTH_CAP_WARNING_PCT


def test_clean_table_is_100_with_no_reasons():
    result = compute_health(_ata(_attr(5), _attr(197), _attr(198)))
    assert result.health_pct == 100
    assert result.reasons == []


def test_wear_beyond_100_percent_clamps_to_zero():
    assert _pct(_ata(_attr(5), percentage_used=150)) == 0


# ── data method: multiplicative penalties ──────────────────────────────
def test_two_reallocated_sectors_cost_two_percent():
    assert _pct(_ata(_attr(5, raw=2))) == 98


def test_problems_multiply():
    # 0.98 (2 reallocated) x 0.90 (5 pending) = 0.882
    result = compute_health(_ata(_attr(5, raw=2), _attr(197, raw=5)))
    assert result.health_pct == 88
    assert result.worst.text.startswith("5 unstable areas")


def test_max_loss_caps_each_attribute():
    assert _pct(_ata(_attr(5, raw=10_000))) == 30          # 1 - 0.70
    assert _pct(_ata(_attr(5, raw=10_000), _attr(197, raw=10_000))) == 9


def test_hdd_only_attributes_skip_ssds():
    spin = _attr(10, raw=3, value=100, thresh=97)
    assert _pct(_ata(spin, kind="SATA SSD")) == 100
    assert _pct(_ata(spin, kind="HDD")) == 85                # 3 x 5%


def test_packed_command_timeouts_count_the_low_word():
    timeouts = _attr(188, raw=(7 << 32) | (7 << 16) | 50)
    assert _pct(_ata(timeouts, kind="HDD")) == 95            # 50 x 0.1%


def test_crc_errors_are_advice_not_damage():
    result = compute_health(_fixture("hdd_crc_only"))
    assert result.health_pct == 100
    assert result.worst.severity == "info"
    assert "connector" in result.worst.text


def test_windows_uncorrected_errors_count_without_a_table():
    drive = new_drive(source="reliability_counter", kind="NVMe SSD",
                      health_flag="OK", uncorrectable_errors=2,
                      percentage_used=5)
    assert _pct(drive) == 94                                 # 0.94, wear cap 95


# ── caps ───────────────────────────────────────────────────────────────
def test_value_at_threshold_caps_at_10_percent():
    spin_up = _attr(3, value=20, thresh=21, name="Spin_Up_Time")
    result = compute_health(_ata(spin_up, kind="HDD"))
    assert result.health_pct == 10
    assert "Spin Up Time" in result.worst.text


def test_hot_temperature_attribute_is_not_a_failure():
    hot = _attr(194, raw=62, value=38, thresh=40, name="Temperature_Celsius")
    assert _pct(_ata(hot, kind="HDD")) == 100


def test_ssd_wear_caps_health():
    result = compute_health(_ata(_attr(5), percentage_used=12))
    assert result.health_pct == 88
    assert result.worst.text == "12% of rated life used, plenty left."
    assert result.worst.severity == "info"


def test_nvme_spare_below_threshold_caps_at_20_percent():
    drive = new_drive(kind="NVMe SSD", source="smartctl", health_flag="OK",
                      media_errors=0, percentage_used=3,
                      available_spare_pct=5, available_spare_threshold_pct=10)
    assert _pct(drive) == config.HEALTH_CAP_SPARE_PCT


# ── smartctl fixtures ──────────────────────────────────────────────────
def test_healthy_nvme():
    result = compute_health(_fixture("nvme_healthy"))
    assert result.health_pct == 98                           # 2% used
    assert [r.severity for r in result.reasons] == ["info"]


def test_worn_nvme_is_critical():
    result = compute_health(_fixture("nvme_worn"))
    assert result.health_pct == 3                            # 97% used
    assert result.worst.severity == "fail"
    assert "critical warning" in result.worst.text
    texts = " ".join(r.text for r in result.reasons)
    assert "spare space" in texts and "3 data integrity errors" in texts


def test_sata_ssd_with_bad_sectors():
    # 0.98 (2 reallocated) x 0.90 (5 pending) x 0.88 (4 uncorrectable)
    result = compute_health(_fixture("sata_ssd_bad_sectors"))
    assert result.health_pct == 78
    assert result.method == "data"
    assert result.vendor_pct == 99


# ── vendor method ──────────────────────────────────────────────────────
def test_vendor_method_scales_value_against_threshold():
    health, lowest = vendor_health(_ata(_attr(5, value=50, thresh=10)))
    assert health == pytest.approx(40 / 90)
    assert lowest["id"] == 5


@pytest.mark.parametrize("value, expected", [
    (150, 0.75),          # starts at 200
    (240, 240 / 253),     # starts at 253
])
def test_vendor_method_uses_200_and_253_starting_values(value, expected):
    health, _ = vendor_health(_ata(_attr(5, value=value)))
    assert health == pytest.approx(expected)


def test_vendor_method_survives_threshold_equal_to_start():
    health, _ = vendor_health(_ata(_attr(5, value=100, thresh=100)))
    assert health == 0.0


def test_vendor_method_ignores_attributes_without_health_meaning():
    seek = _attr(7, value=60, thresh=45, name="Seek_Error_Rate")
    assert vendor_health(_ata(seek, kind="HDD")) == (None, None)


def test_main_health_is_the_lower_of_the_two_methods():
    result = compute_health(_ata(_attr(5, value=50, thresh=10)))
    assert result.health_pct == 44
    assert result.method == "vendor"
    assert "Manufacturer-based health: 44%" in result.technical()


# ── remaining life ─────────────────────────────────────────────────────
def test_new_hdd_is_capped_at_1000_days():
    life = estimate_remaining_life(new_drive(kind="HDD", power_on_hours=0),
                                   100)
    assert life.hours == config.LIFE_CAP_DAYS * 24
    assert life.text == "more than 1000 days"


def test_hdd_life_scales_with_health_squared():
    drive = new_drive(kind="HDD", power_on_hours=20_000, temperature_c=38)
    life = estimate_remaining_life(drive, 70)
    assert life.hours == pytest.approx((43_800 - 20_000) * 0.49)
    assert life.basis == "age"


def test_heat_shortens_life():
    drive = new_drive(kind="HDD", power_on_hours=20_000,
                      temperature_max_c=60)
    life = estimate_remaining_life(drive, 70)
    assert life.hours == pytest.approx((43_800 - 20_000 - 4_380) * 0.49)


def test_worn_ssd_uses_the_lower_wear_estimate():
    # Age formula: (52,560 - 10,000) x 0.25 = 10,640 h; at 200 h per 1%
    # of wear, the 50% left lasts 10,000 h.
    drive = new_drive(kind="SATA SSD", power_on_hours=10_000,
                      percentage_used=50)
    life = estimate_remaining_life(drive, 50)
    assert life.hours == pytest.approx(10_000)
    assert life.basis == "wear"


@pytest.mark.parametrize("drive, health", [
    (new_drive(kind="HDD"), 100),                        # no power-on hours
    (new_drive(kind="HDD", power_on_hours=100), None),   # no health
])
def test_life_is_unknown_without_inputs(drive, health):
    life = estimate_remaining_life(drive, health)
    assert life.hours is None
    assert life.text == "unknown"


def test_estimated_power_on_time_makes_life_rough():
    drive = new_drive(kind="HDD", power_on_hours=100,
                      raw={"power_on_estimated": True})
    assert estimate_remaining_life(drive, 100).rough


@pytest.mark.parametrize("days, text", [
    (0, "less than 3 months — replace soon"),
    (89, "less than 3 months — replace soon"),
    (91, "about 3 months of typical use"),
    (427, "about 14 months of typical use"),
    (800, "more than 2 years"),
    (1000, "more than 1000 days"),
])
def test_life_wording(days, text):
    assert life_words(days) == text
