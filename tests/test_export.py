"""The single-file HTML report (pure; no Qt)."""

import json
import re
from datetime import datetime
from pathlib import Path

import pytest

from syscheck.export import (computer_name, default_report_name,
                             render_html_report)
from syscheck.telemetry.smart_sources import smartctl

FIXTURES = Path(__file__).parent / "fixtures" / "smartctl"
WHEN = datetime(2026, 10, 24, 14, 30)


def _drive(name):
    report = json.loads((FIXTURES / f"{name}.json").read_text("utf-8"))
    return smartctl.parse_device(report)


def _results(*names):
    return {
        "specs": {"cpu": {"name": "Intel Core i5-8250U", "cores_physical": 4,
                          "cores_logical": 8},
                  "ram": {"total_gb": 8.0, "type": "DDR4", "speed_mhz": 2400},
                  "gpus": [{"name": "Intel UHD 620", "vram_mb": 1024}],
                  "board": {"board_mfr": "LENOVO", "board_model": "20L5",
                            "bios_version": "N24ET56W", "bios_date": "2019"}},
        "storage": {"drives": [_drive(n) for n in names],
                    "smart_readable": True},
        "battery": {"present": True, "wear_pct": 12.0, "cycles": 300},
        "ports": {"connects": 0, "disconnects": 0, "verified": False},
    }


def _render(results, **kw):
    kw.setdefault("computer", "LAPTOP-7Q2")
    return render_html_report(results, generated=WHEN, **kw)


# ── file name ──────────────────────────────────────────────────────────
@pytest.mark.parametrize("computer, name", [
    ("LAPTOP-7Q2", "SysCheck_LAPTOP-7Q2_2026-10-24.html"),
    ("Bob's PC", "SysCheck_Bob-s-PC_2026-10-24.html"),
    ('a\\b/c:d*e?f"g<h>i|j', "SysCheck_a-b-c-d-e-f-g-h-i-j_2026-10-24.html"),
    ("...", "SysCheck_PC_2026-10-24.html"),
])
def test_default_file_name_is_safe(computer, name):
    assert default_report_name(computer, WHEN) == name


def test_computer_name_prefers_windows_variable(monkeypatch):
    monkeypatch.setenv("COMPUTERNAME", "DESKTOP-ABC")
    assert computer_name() == "DESKTOP-ABC"
    monkeypatch.delenv("COMPUTERNAME")
    assert computer_name()                       # falls back to the hostname


# ── content ────────────────────────────────────────────────────────────
def test_report_has_grade_every_subsystem_and_specs():
    html = _render(_results("nvme_healthy"), admin=True)
    assert "LAPTOP-7Q2" in html and "2026-10-24 14:30" in html
    assert "RUN AS ADMINISTRATOR <b>Yes</b>" in html
    assert "GRADE A — EXCELLENT CONDITION" in html
    for title in ("STORAGE", "BATTERY", "THERMALS", "WEBCAM", "KEYBOARD",
                  "TOUCHPAD", "PORTS"):
        assert f"<h3>{title}</h3>" in html
    assert "NOT VERIFIED" in html                # ports with no USB device
    assert "Intel Core i5-8250U" in html


def test_report_has_each_drive_with_its_smart_table():
    html = _render(_results("nvme_healthy", "sata_ssd_bad_sectors"))
    assert "SAMSUNG SSD 860 EVO 500GB" in html
    assert 'style="width:78%"' in html           # health bar
    assert "FAIR" in html and "EXCELLENT" in html
    assert "Estimated remaining life".upper() in html
    # ATA table row for ID 5, flagged, and the NVMe health log.
    assert re.search(r"<td class='num'>5</td><td class='warn'>Reallocated "
                     r"Sector Ct</td>", html)
    assert "Data units written" in html
    assert "Manufacturer-based health: 99%" in html
    assert "S3Z2NB0K123456X" in html and "S4EVNX0N123456A" in html


def test_nvme_table_has_name_value_status_only():
    html = _render(_results("nvme_healthy"))
    assert ("<thead><tr><th>NAME</th><th>VALUE</th><th>STATUS</th></tr>"
            "</thead>") in html
    assert "<th>WORST</th>" not in html
    assert ("<tr><td>Critical warning</td><td>0x00</td>"
            "<td class='pass'>OK</td></tr>") in html


def test_ata_table_keeps_every_column():
    html = _render(_results("sata_ssd_bad_sectors"))
    assert ("<th>ID</th><th>ATTRIBUTE</th><th>VALUE</th><th>WORST</th>"
            "<th>THRESH</th><th>RAW</th><th>STATUS</th>") in html


def test_healthy_drive_headline_appears_once_per_section():
    html = _render(_results("nvme_healthy"))
    assert html.count("The SSD is in perfect condition.") == 2  # card + drive


def test_report_explains_temperature_and_unsafe_shutdowns():
    html = _render(_results("nvme_healthy"))
    assert "38 °C now · max not reported" in html
    assert "Unsafe shutdowns are times the drive lost power" in html


@pytest.mark.parametrize("admin, shown", [
    (False, True), (True, False), (None, False),
])
def test_report_banner_when_not_administrator(admin, shown):
    html = _render(_results("nvme_healthy"), admin=admin)
    banner = ('<section class="panel banner">Run as Administrator for '
              "temperatures and full drive details.</section>")
    assert (banner in html) is shown


def test_serial_numbers_can_be_hidden():
    drives = ("nvme_healthy", "sata_ssd_bad_sectors")
    html = _render(_results(*drives), hide_serials=True)
    for name in drives:
        assert _drive(name)["serial"] not in html
    assert "<td>Hidden</td>" in html
    assert "SERIAL NUMBERS <b>hidden</b>" in html


def test_report_is_self_contained_and_printable():
    html = _render(_results("nvme_healthy"))
    assert html.startswith("<!DOCTYPE html>")
    assert "<script" not in html
    assert not re.search(r"""(src|href)\s*=""", html)     # no external files
    assert "@media print" in html


def test_drive_text_is_escaped():
    results = _results("nvme_healthy")
    results["storage"]["drives"][0]["model"] = "<script>alert(1)</script>"
    html = _render(results, computer="<b>pc</b>")
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert "<b>pc</b>" not in html


def test_empty_results_still_render():
    html = _render({})
    assert "NO GRADE — NOTHING COULD BE MEASURED" in html
    assert "No drive data was collected." in html
    assert "RUN AS ADMINISTRATOR <b>Unknown</b>" in html
