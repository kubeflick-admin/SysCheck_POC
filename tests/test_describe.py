"""Plain-English drive wording and display formatting."""

import json
from pathlib import Path

import pytest

from syscheck.diskhealth.describe import (ATA_COLUMNS, NVME_COLUMNS,
                                          UNSAFE_SHUTDOWNS_TEXT, assess,
                                          condition, data_words, detail_rows,
                                          no_table_reason, on_time_words,
                                          size_words, smart_columns,
                                          smart_rows, temperature_words,
                                          unsafe_shutdowns_note)
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
    assert temperature_words(38, None) == "38 °C now · max not reported"
    assert temperature_words(None, 61) == "61 °C max"
    assert temperature_words(None, None) == "n/a"


def test_detail_rows_say_when_no_max_temperature_is_reported():
    nvme = dict(detail_rows(_fixture("nvme_healthy")))
    sata = dict(detail_rows(_fixture("sata_ssd_bad_sectors")))
    assert nvme["Temperature"] == "38 °C now · max not reported"
    assert sata["Temperature"] == "36 °C now · 61 °C max"


@pytest.mark.parametrize("count, note", [
    (56, UNSAFE_SHUTDOWNS_TEXT), (0, UNSAFE_SHUTDOWNS_TEXT), (None, None),
])
def test_unsafe_shutdowns_are_explained_when_counted(count, note):
    assert unsafe_shutdowns_note(new_drive(unsafe_shutdowns=count)) == note
    assert UNSAFE_SHUTDOWNS_TEXT.count(".") == 1          # one sentence


def test_detail_rows_can_hide_the_serial():
    drive = _fixture("nvme_healthy")
    shown = dict(detail_rows(drive))
    hidden = dict(detail_rows(drive, hide_serial=True))
    assert shown["Serial"] == "S4EVNX0N123456A"
    assert hidden["Serial"] == "Hidden"
    assert shown["Power-on time"] == "4,321 h — about 6 months switched on"
    assert shown["Read by"] == "smartctl (S.M.A.R.T.)"


# ── S.M.A.R.T. table ───────────────────────────────────────────────────
def _rows(drive):
    return {r.id: r for r in smart_rows(drive)}


def test_ata_table_flags_the_damage_counters():
    drive = _fixture("sata_ssd_bad_sectors")
    assert smart_columns(drive) == ATA_COLUMNS
    rows = _rows(drive)
    assert len(rows) == 11
    for attr_id in ("5", "187", "197"):
        assert (rows[attr_id].status, rows[attr_id].severity) == (
            "Warning", "warn")
    assert rows["5"].name == "Reallocated Sector Ct"
    assert (rows["5"].value, rows["5"].worst, rows["5"].thresh) == (
        "99", "99", "10")
    assert rows["194"].raw == "36 (Min/Max 18/61)"      # smartctl's own text
    assert rows["5"].cells(ATA_COLUMNS)[:3] == ("5", "Reallocated Sector Ct",
                                                "99")
    assert rows["9"].status == "OK"


def test_ata_table_marks_cable_errors_and_ignores_harmless_raws():
    rows = _rows(_fixture("hdd_crc_only"))
    assert (rows["199"].status, rows["199"].severity) == ("Check cable",
                                                          "info")
    assert rows["188"].status == "OK"                    # "0 0 0"
    assert rows["7"].status == "OK"                      # seek rate raw
    assert rows["241"].worst == "253"


def test_ata_table_marks_failing_and_hot_attributes():
    drive = new_drive(kind="HDD", raw={"attributes": [
        {"id": 5, "name": "Reallocated_Sector_Ct", "value": 30, "worst": 30,
         "thresh": 36, "raw": 900},
        {"id": 194, "name": "Temperature_Celsius", "value": 30, "worst": 30,
         "thresh": 40, "raw": 70},
    ]})
    rows = _rows(drive)
    assert (rows["5"].status, rows["5"].severity) == ("FAILING", "fail")
    assert rows["5"].raw == "900"                        # no raw_string
    assert (rows["194"].status, rows["194"].severity) == ("Too hot", "warn")


def test_nvme_table_is_name_value_status_from_the_health_log():
    drive = _fixture("nvme_healthy")
    assert smart_columns(drive) == NVME_COLUMNS == ("NAME", "VALUE", "STATUS")
    rows = {r.name: r for r in smart_rows(drive)}
    assert rows["Critical warning"].cells(NVME_COLUMNS) == (
        "Critical warning", "0x00", "OK")
    assert rows["Data units written"].value == "23,456,789 (12.0 TB)"
    assert rows["Available spare threshold"].value == "10%"
    assert {r.status for r in rows.values()} == {"OK"}


def test_worn_nvme_table_flags_each_problem():
    rows = {r.name: r for r in smart_rows(_fixture("nvme_worn"))}
    assert rows["Critical warning"].status == "FAILING"
    assert rows["Available spare"].status == "FAILING"
    assert rows["Percentage used"].status == "Warning"
    assert rows["Media and data integrity errors"].status == "Warning"


def test_windows_counters_have_no_table_and_say_why():
    drive = new_drive(source="reliability_counter", percentage_used=3)
    assert smart_rows(drive) == []
    assert "Administrator" in no_table_reason(drive)
