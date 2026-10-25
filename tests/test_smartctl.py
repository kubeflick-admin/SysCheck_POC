"""Layer A: smartctl JSON parsing against hand-built smartctl 7.5 reports,
and the runner driven by a fake ``subprocess.run``."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from syscheck import config
from syscheck.telemetry.smart_sources import smartctl
from syscheck.telemetry.smart_sources.schema import FIELDS, new_drive

FIXTURES = Path(__file__).parent / "fixtures" / "smartctl"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


class FakeSmartctl:
    """Stands in for subprocess.run: maps smartctl arguments to output."""

    def __init__(self, outputs):
        self.outputs = outputs
        self.calls = []

    def __call__(self, cmd, **kwargs):
        self.calls.append((cmd[1:], kwargs))
        out = self.outputs[" ".join(cmd[1:])]
        if isinstance(out, Exception):
            raise out
        stdout = out if isinstance(out, bytes) else json.dumps(out).encode()
        return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr=b"")


@pytest.fixture
def elevated(monkeypatch):
    monkeypatch.setattr(smartctl, "is_admin", lambda: True)
    monkeypatch.setattr(smartctl, "find_smartctl",
                        lambda: Path("smartctl.exe"))


# ── locating and running smartctl ─────────────────────────────────────
def test_finds_the_bundled_exe_in_the_source_tree(monkeypatch):
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    exe = smartctl.find_smartctl()
    assert exe is not None
    assert exe.parts[-3:] == ("vendor", "smartmontools", "smartctl.exe")


def test_finds_the_exe_inside_a_pyinstaller_bundle(monkeypatch, tmp_path):
    bundled = tmp_path / "vendor" / "smartmontools" / "smartctl.exe"
    bundled.parent.mkdir(parents=True)
    bundled.write_bytes(b"MZ")
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert smartctl.find_smartctl() == bundled


def test_missing_exe_in_bundle_gives_none(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert smartctl.find_smartctl() is None


def test_not_elevated_still_tries_and_hints_on_failure(monkeypatch):
    # NVMe drives are readable without elevation; SATA ones usually aren't.
    monkeypatch.setattr(smartctl, "is_admin", lambda: False)
    monkeypatch.setattr(smartctl, "find_smartctl",
                        lambda: Path("smartctl.exe"))
    log = []
    fake = FakeSmartctl({
        "--scan-open -j": _load("scan_open"),
        "-a -j -d sat /dev/sda": _load("open_failed"),
        "-a -j -d nvme /dev/nvme0": _load("nvme_healthy"),
    })
    monkeypatch.setattr(smartctl.subprocess, "run", fake)

    drives = smartctl.collect(log.append)

    assert [d["kind"] for d in drives] == ["NVMe SSD"]
    assert any("run as Administrator" in line for line in log)


def test_missing_exe_skips_smartctl(monkeypatch):
    monkeypatch.setattr(smartctl, "find_smartctl", lambda: None)
    fake = FakeSmartctl({})
    monkeypatch.setattr(smartctl.subprocess, "run", fake)
    assert smartctl.collect(lambda _: None) == []
    assert fake.calls == []


def test_is_admin_is_false_off_windows():
    if sys.platform != "win32":
        assert smartctl.is_admin() is False


def test_collect_scans_then_reads_each_device_with_its_type(
        monkeypatch, elevated):
    fake = FakeSmartctl({
        "--scan-open -j": _load("scan_open"),
        "-a -j -d sat /dev/sda": _load("sata_ssd_bad_sectors"),
        "-a -j -d nvme /dev/nvme0": _load("nvme_healthy"),
    })
    monkeypatch.setattr(smartctl.subprocess, "run", fake)

    drives = smartctl.collect(lambda _: None)

    assert [d["model"] for d in drives] == ["Samsung SSD 860 EVO 500GB",
                                            "Samsung SSD 970 EVO Plus 500GB"]
    assert [args for args, _ in fake.calls] == [
        ["--scan-open", "-j"],
        ["-a", "-j", "-d", "sat", "/dev/sda"],
        ["-a", "-j", "-d", "nvme", "/dev/nvme0"],
    ]
    for _, kwargs in fake.calls:
        assert kwargs["timeout"] == config.SMARTCTL_TIMEOUT_S
        assert kwargs["capture_output"] is True
        assert "creationflags" in kwargs


def test_one_bad_device_does_not_stop_the_others(monkeypatch, elevated):
    log = []
    fake = FakeSmartctl({
        "--scan-open -j": _load("scan_open"),
        "-a -j -d sat /dev/sda": subprocess.TimeoutExpired("smartctl", 10),
        "-a -j -d nvme /dev/nvme0": _load("nvme_healthy"),
    })
    monkeypatch.setattr(smartctl.subprocess, "run", fake)

    drives = smartctl.collect(log.append)

    assert [d["serial"] for d in drives] == ["S4EVNX0N123456A"]
    assert any("timed out" in line for line in log)


def test_unreadable_output_is_skipped(monkeypatch, elevated):
    log = []
    fake = FakeSmartctl({
        "--scan-open -j": _load("scan_open"),
        "-a -j -d sat /dev/sda": b"not json",
        "-a -j -d nvme /dev/nvme0": _load("open_failed"),
    })
    monkeypatch.setattr(smartctl.subprocess, "run", fake)

    assert smartctl.collect(log.append) == []
    assert any("Access is denied" in line for line in log)


def test_failed_scan_gives_no_drives(monkeypatch, elevated):
    fake = FakeSmartctl({"--scan-open -j": OSError("cannot execute")})
    monkeypatch.setattr(smartctl.subprocess, "run", fake)
    assert smartctl.collect(lambda _: None) == []


# ── schema ─────────────────────────────────────────────────────────────
def test_new_drive_starts_with_every_reading_unknown():
    drive = new_drive()
    assert set(drive) == set(FIELDS)
    assert drive["kind"] == "Unknown"
    assert drive["source"] == "none"
    assert drive["health_flag"] == "Unknown"
    assert drive["raw"] == {}
    readings = set(FIELDS) - {"kind", "source", "health_flag", "raw"}
    assert all(drive[f] is None for f in readings)


def test_new_drive_rejects_unknown_fields():
    with pytest.raises(KeyError):
        new_drive(power_on_hour=5)


# ── scan ───────────────────────────────────────────────────────────────
def test_scan_keeps_devices_that_opened_with_their_type():
    assert smartctl.parse_scan(_load("scan_open")) == [
        {"name": "/dev/sda", "type": "sat"},
        {"name": "/dev/nvme0", "type": "nvme"},
    ]


def test_scan_tolerates_garbage():
    assert smartctl.parse_scan(None) == []
    assert smartctl.parse_scan({"devices": [{"type": "sat"}, "x"]}) == []


# ── exit status ────────────────────────────────────────────────────────
@pytest.mark.parametrize("status, usable", [
    (0, True),
    (4, True),     # some command failed: partial data
    (8, True),     # "disk failing": still a full report
    (72, True),    # failing + error log entries
    (1, False),    # command line did not parse
    (2, False),    # device open failed
    (3, False),
])
def test_only_exit_bits_0_and_1_mean_no_data(status, usable):
    assert smartctl.report_usable({"smartctl": {"exit_status": status}}) is usable


def test_report_without_exit_status_is_not_usable():
    assert smartctl.report_usable({}) is False
    assert smartctl.parse_device(_load("open_failed")) is None


# ── NVMe ───────────────────────────────────────────────────────────────
def test_healthy_nvme():
    d = smartctl.parse_device(_load("nvme_healthy"))
    assert d["source"] == "smartctl"
    assert d["kind"] == "NVMe SSD"
    assert d["model"] == "Samsung SSD 970 EVO Plus 500GB"
    assert d["serial"] == "S4EVNX0N123456A"
    assert d["size_gb"] == 500.1
    assert d["health_flag"] == "OK"
    assert d["power_on_hours"] == 4321
    assert d["power_cycles"] == 1234
    assert d["unsafe_shutdowns"] == 56
    assert d["temperature_c"] == 38
    assert d["temperature_max_c"] is None
    assert d["percentage_used"] == 2
    assert d["available_spare_pct"] == 100
    assert d["available_spare_threshold_pct"] == 10
    assert d["media_errors"] == 0
    assert d["error_log_entries"] == 12
    # 23,456,789 data units x 512,000 bytes
    assert d["host_writes_tb"] == pytest.approx(12.01)
    assert d["raw"]["host_reads_tb"] == pytest.approx(7.8)
    assert d["raw"]["interface"] == "NVMe"
    assert d["raw"]["firmware"] == "2B2QEXM7"
    assert d["self_test"] == {"type": "Short",
                              "status": "Completed without error",
                              "passed": True, "lifetime_hours": 4300,
                              "count": 1}
    # ATA-only fields stay unknown rather than zero.
    for field in ("reallocated_sectors", "pending_sectors",
                  "uncorrectable_errors", "crc_errors"):
        assert d[field] is None


def test_worn_nvme_with_critical_warning_is_failing():
    d = smartctl.parse_device(_load("nvme_worn"))
    assert d["health_flag"] == "FAILING"
    assert d["raw"]["nvme_critical_warning"] == 1
    assert d["percentage_used"] == 97
    assert d["available_spare_pct"] == 4
    assert d["available_spare_threshold_pct"] == 10
    assert d["media_errors"] == 3
    assert d["host_writes_tb"] == pytest.approx(148.48)
    assert d["self_test"] is None
    assert d["raw"]["messages"] == [
        "SMART overall-health self-assessment test result: FAILED!"]


def test_nvme_critical_warning_alone_marks_failing():
    report = _load("nvme_healthy")
    del report["smart_status"]
    report["nvme_smart_health_information_log"]["critical_warning"] = 4
    assert smartctl.parse_device(report)["health_flag"] == "FAILING"


# ── SATA SSD ───────────────────────────────────────────────────────────
def test_sata_ssd_with_reallocated_and_pending_sectors():
    d = smartctl.parse_device(_load("sata_ssd_bad_sectors"))
    assert d["kind"] == "SATA SSD"
    assert d["health_flag"] == "OK"          # the drive's own verdict
    assert d["reallocated_sectors"] == 2
    assert d["pending_sectors"] == 5
    assert d["uncorrectable_errors"] == 0     # 198 wins over 187
    assert d["crc_errors"] == 0
    assert d["percentage_used"] == 12         # Wear_Leveling_Count 88
    assert d["power_on_hours"] == 12000
    assert d["power_cycles"] == 2100
    assert d["temperature_c"] == 36
    assert d["temperature_max_c"] == 61       # "36 (Min/Max 18/61)"
    assert d["error_log_entries"] == 3
    assert d["unsafe_shutdowns"] is None      # no attribute smartctl names so
    # 30,000,000,000 LBAs x 512 bytes
    assert d["host_writes_tb"] == pytest.approx(15.36)
    assert d["raw"]["interface"] == "SATA"
    assert d["raw"]["rotation_rpm"] == 0
    assert d["self_test"]["passed"] is True
    assert d["self_test"]["type"] == "Short offline"

    attrs = {a["id"]: a for a in d["raw"]["attributes"]}
    assert attrs[187]["raw"] == 4
    assert attrs[5] == {"id": 5, "name": "Reallocated_Sector_Ct",
                        "value": 99, "worst": 99, "thresh": 10, "raw": 2,
                        "raw_string": "2", "when_failed": None,
                        "prefailure": True}


def test_uncorrectable_falls_back_to_attribute_187():
    report = _load("sata_ssd_bad_sectors")
    table = report["ata_smart_attributes"]["table"]
    report["ata_smart_attributes"]["table"] = [a for a in table
                                               if a["id"] != 198]
    assert smartctl.parse_device(report)["uncorrectable_errors"] == 4


def test_standard_endurance_figure_beats_vendor_wear_attribute():
    report = _load("sata_ssd_bad_sectors")
    report["endurance_used"] = {"current_percent": 20}
    assert smartctl.parse_device(report)["percentage_used"] == 20


def test_temperature_falls_back_to_attribute_low_byte():
    report = _load("sata_ssd_bad_sectors")
    del report["temperature"]
    assert smartctl.parse_device(report)["temperature_c"] == 36


# ── HDD ────────────────────────────────────────────────────────────────
def test_hdd_with_crc_errors_only():
    d = smartctl.parse_device(_load("hdd_crc_only"))
    assert d["kind"] == "HDD"
    assert d["health_flag"] == "OK"
    assert d["crc_errors"] == 37
    assert d["reallocated_sectors"] == 0
    assert d["pending_sectors"] == 0
    assert d["uncorrectable_errors"] == 0
    assert d["unsafe_shutdowns"] == 85        # Power-Off_Retract_Count
    assert d["power_on_hours"] == 20000
    # 194's raw string has no Min/Max here, so 190's is used.
    assert d["temperature_max_c"] == 50
    assert d["percentage_used"] is None       # wear doesn't apply to HDDs
    assert d["host_writes_tb"] == pytest.approx(6.32)
    assert d["raw"]["rotation_rpm"] == 5400
    assert d["self_test"] is None             # empty self-test log


# ── missing data ───────────────────────────────────────────────────────
def test_missing_fields_are_none_not_zero():
    d = smartctl.parse_device(_load("missing_fields"))
    assert d["source"] == "smartctl"
    assert d["kind"] == "Unknown"
    assert d["health_flag"] == "Unknown"
    assert d["model"] == "Generic SSD"
    readings = set(FIELDS) - {"model", "kind", "source", "health_flag", "raw"}
    assert {f: d[f] for f in readings} == dict.fromkeys(readings)
    assert d["raw"]["attributes"] == []
    assert d["raw"]["messages"] == [
        "Read SMART Data failed: scsi error badly formed scsi parameters"]


# ── host-write units ───────────────────────────────────────────────────
@pytest.mark.parametrize("name, raw, block, expected", [
    ("Total_LBAs_Written", 2 * 10 ** 9, 512, 1.02),
    ("Total_LBAs_Written", 2 * 10 ** 9, 4096, 8.19),
    ("Total_LBAs_Written", 2 * 10 ** 9, None, 1.02),   # default 512 B
    ("Host_Writes_32MiB", 100_000, 512, 3.36),
    ("Host_Writes_GiB", 10_000, 512, 10.74),
    ("Lifetime_Writes_GiB", 10_000, 512, 10.74),
    ("Host_Writes_MiB", 1_000_000, 512, 1.05),
    ("Unknown_Attribute", 2 * 10 ** 9, 512, None),     # don't guess
    ("Total_LBAs_Written", None, 512, None),
])
def test_ata_host_writes_units(name, raw, block, expected):
    result = smartctl.ata_host_tb(name, raw, block)
    if expected is None:
        assert result is None
    else:
        assert result == pytest.approx(expected, abs=0.01)


def test_ata_host_reads_use_their_own_names():
    tb = smartctl.ata_host_tb("Total_LBAs_Read", 2 * 10 ** 9, 512,
                              smartctl.HOST_READ_UNITS)
    assert tb == pytest.approx(1.02, abs=0.01)
    assert smartctl.ata_host_tb("Total_LBAs_Written", 2 * 10 ** 9, 512,
                                smartctl.HOST_READ_UNITS) is None
    d = smartctl.parse_device(_load("hdd_crc_only"))
    assert d["raw"]["host_reads_tb"] == pytest.approx(12.01, abs=0.01)


def test_nvme_health_log_is_kept_for_the_report():
    log = smartctl.parse_device(_load("nvme_healthy"))["raw"]["nvme_log"]
    assert log["data_units_written"] == 23456789
    assert log["num_err_log_entries"] == 12
    assert "temperature_sensors" not in log           # lists are dropped


def test_unknown_241_unit_leaves_host_writes_unknown():
    report = _load("hdd_crc_only")
    for a in report["ata_smart_attributes"]["table"]:
        if a["id"] == 241:
            a["name"] = "Unknown_Attribute"
    assert smartctl.parse_device(report)["host_writes_tb"] is None
