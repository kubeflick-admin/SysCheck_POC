"""Plain-English drive wording and display formatting."""

import json
from pathlib import Path

import pytest

from syscheck.diskhealth.describe import (assess, condition, data_words,
                                          detail_rows, on_time_words,
                                          size_words, temperature_words)
from syscheck.telemetry.smart_sources import smartctl
from syscheck.telemetry.smart_sources.schema import new_drive

FIXTURES = Path(__file__).parent / "fixtures" / "smartctl"


def _fixture(name):
    report = json.loads((FIXTURES / f"{name}.json").read_text("utf-8"))
    return smartctl.parse_device(report)


# ── sentences ──────────────────────────────────────────────────────────
def test_lightly_used_ssd_reads_as_perfect():
    drive = new_drive(kind="NVMe SSD", source="smartctl", health_flag="OK",
                      media_errors=0, percentage_used=1)
    assert assess(drive).summary == (
        "The SSD is in perfect condition. No problems found. "
        "1% of rated life used, plenty left.")


def test_cable_errors_are_not_perfect_but_still_good():
    a = assess(_fixture("hdd_crc_only"))
    assert a.health.health_pct == 100
    assert a.summary.startswith("The hard drive is in good condition. "
                                "37 data transfer errors")


def test_damaged_drive_lists_its_worst_problems_first():
    summary = assess(_fixture("sata_ssd_bad_sectors")).summary
    assert summary.startswith("The SSD shows wear or problems.")
    assert summary.index("4 read errors") < summary.index("5 unstable areas")
    assert "2 damaged areas" in summary


def test_unreadable_drive_says_how_to_fix_it():
    assert "Run SysCheck as Administrator" in assess(new_drive()).summary


@pytest.mark.parametrize("pct, word", [
    (None, "UNKNOWN"), (100, "EXCELLENT"), (95, "EXCELLENT"), (94, "GOOD"),
    (85, "GOOD"), (60, "FAIR"), (30, "POOR"), (29, "CRITICAL"), (0, "CRITICAL"),
])
def test_condition_words(pct, word):
    assert condition(pct) == word


# ── values ─────────────────────────────────────────────────────────────
@pytest.mark.parametrize("hours, text", [
    (None, "n/a"),
    (1, "1 hour switched on"),
    (30, "30 hours switched on"),
    (240, "about 10 days switched on"),
    (4_321, "about 6 months switched on"),
    (20_000, "about 2.3 years switched on"),
])
def test_power_on_time_in_plain_words(hours, text):
    assert on_time_words(hours) == text


@pytest.mark.parametrize("gb, text", [
    (500.1, "500 GB"), (256.06, "256 GB"), (1000.2, "1 TB"),
    (2000.4, "2 TB"), (1500.3, "1.5 TB"), (None, "n/a"),
])
def test_size_words(gb, text):
    assert size_words(gb) == text


@pytest.mark.parametrize("tb, text", [
    (0.85, "850 GB"), (12.0, "12.0 TB"), (148.48, "148.5 TB"), (None, "n/a"),
])
def test_data_words(tb, text):
    assert data_words(tb) == text


def test_temperature_words():
    assert temperature_words(38, 61) == "38 °C now · 61 °C max"
    assert temperature_words(38, None) == "38 °C"
    assert temperature_words(None, None) == "n/a"


def test_detail_rows_can_hide_the_serial():
    drive = _fixture("nvme_healthy")
    shown = dict(detail_rows(drive))
    hidden = dict(detail_rows(drive, hide_serial=True))
    assert shown["Serial"] == "S4EVNX0N123456A"
    assert hidden["Serial"] == "Hidden"
    assert shown["Power-on time"] == "4,321 h — about 6 months switched on"
    assert shown["Read by"] == "smartctl (S.M.A.R.T.)"
