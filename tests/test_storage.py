"""The layered storage collector, with every source faked.

``_inventory`` stands in for Win32_DiskDrive + MSFT_PhysicalDisk, and the
three S.M.A.R.T. layers are replaced per test.
"""

import json
from pathlib import Path

import pytest

from syscheck.scoring import score_storage
from syscheck.telemetry import storage
from syscheck.telemetry.smart_sources import reliability, smartctl

FIXTURES = Path(__file__).parent / "fixtures" / "smartctl"
NVME_PNP = r"SCSI\DISK&VEN_NVME&PROD_SAMSUNG_SSD_970\5&2B3F3A3&0&000000"
SATA_PNP = r"SCSI\DISK&VEN_SAMSUNG&PROD_SSD_860\4&1A2B3C&0&000100"


def _smartctl_drive(name, device="/dev/sda"):
    report = json.loads((FIXTURES / f"{name}.json").read_text("utf-8"))
    report["device"]["name"] = device
    return smartctl.parse_device(report)


def _disk(number=0, model="Samsung SSD 970 EVO Plus 500GB",
          serial="0025_3881_91B0_3D6E.", size=500.1, pnp=NVME_PNP,
          kind="NVMe SSD", health="OK", row=None):
    return {"disk_number": number, "model": model, "serial": serial,
            "size_gb": size, "pnp": pnp, "kind": kind,
            "windows_health": health, "row": row}


def _counter_row(**overrides):
    row = dict.fromkeys(reliability.DISK_FIELDS + reliability.COUNTER_FIELDS)
    row.update(DeviceId="0", MediaType=4, BusType=17, HealthStatus=0,
               PowerOnHours=5200, Temperature=41, Wear=3,
               ReadErrorsUncorrected=0, WriteErrorsUncorrected=0)
    row.update(overrides)
    return row


def _sata_entry():
    def rec(attr_id, raw):
        return {"flags": 0, "value": 100, "worst": 100, "raw": raw}
    return {"attrs": {5: rec(5, 8), 9: rec(9, 9100), 197: rec(197, 1)},
            "thresholds": {5: 10}, "predict_failure": False}


@pytest.fixture
def sources(monkeypatch):
    """Configurable fakes for the inventory and the three layers."""
    state = {"disks": [], "smartctl": [], "wmi": {}, "wmi_calls": 0}

    def fake_wmi(log):
        state["wmi_calls"] += 1
        return state["wmi"]

    monkeypatch.setattr(storage, "_inventory", lambda log: state["disks"])
    monkeypatch.setattr(storage.smartctl, "collect",
                        lambda log: list(state["smartctl"]))
    monkeypatch.setattr(storage.wmi_smart, "fetch", fake_wmi)
    return state


def test_smartctl_wins_and_matches_despite_windows_nvme_serial(sources):
    sources["disks"] = [_disk(row=_counter_row())]
    sources["smartctl"] = [_smartctl_drive("nvme_healthy")]

    result = storage.collect_storage_health()

    assert result["smart_readable"] is True
    [d] = result["drives"]
    assert d["source"] == "smartctl"
    assert d["serial"] == "S4EVNX0N123456A"            # the real serial
    assert d["raw"]["windows_serial"] == "0025_3881_91B0_3D6E."
    assert d["raw"]["disk_number"] == 0
    assert d["raw"]["windows_health"] == "OK"
    assert d["power_on_hours"] == 4321                  # not Layer B's 5200
    assert d["health"] == d["health_flag"] == "OK"
    assert sources["wmi_calls"] == 0                    # never needed


def test_identical_drives_pair_up_by_disk_number(sources):
    model = "Samsung SSD 860 EVO 500GB"
    sources["disks"] = [_disk(0, model, "W-A", pnp=SATA_PNP),
                        _disk(1, model, "W-B", pnp=SATA_PNP + "1")]
    first = _smartctl_drive("sata_ssd_bad_sectors", "/dev/sda")
    second = _smartctl_drive("sata_ssd_bad_sectors", "/dev/sdb")
    second["serial"] = "SECOND"
    sources["smartctl"] = [second, first]   # scan order doesn't matter

    drives = storage.collect_storage_health()["drives"]

    assert [(d["raw"]["disk_number"], d["serial"]) for d in drives] == [
        (0, "S3Z2NB0K123456X"), (1, "SECOND")]


def test_reliability_counter_used_when_smartctl_reads_nothing(sources):
    sources["disks"] = [_disk(row=_counter_row())]

    [d] = storage.collect_storage_health()["drives"]

    assert d["source"] == "reliability_counter"
    assert d["power_on_hours"] == 5200
    assert d["percentage_used"] == 3
    assert d["model"] == "Samsung SSD 970 EVO Plus 500GB"
    assert sources["wmi_calls"] == 0


def test_wmi_smart_used_when_nothing_else_reads_the_drive(sources):
    model = "Samsung SSD 860 EVO 500GB"
    sources["disks"] = [
        _disk(0, model, "SER-0", pnp=SATA_PNP, kind="SATA SSD",
              row=_counter_row(**dict.fromkeys(reliability.COUNTER_FIELDS))),
    ]
    sources["wmi"] = {"OTHER\\DISK_0": {}, SATA_PNP + "_0": _sata_entry()}

    [d] = storage.collect_storage_health()["drives"]

    assert d["source"] == "wmi_smart"
    assert d["kind"] == "SATA SSD"                      # from the inventory
    assert d["serial"] == "SER-0"
    assert d["reallocated_sectors"] == 8
    assert d["pending_sectors"] == 1
    assert d["power_on_hours"] == 9100
    assert d["host_writes_tb"] is None
    assert sources["wmi_calls"] == 1


def test_single_drive_takes_the_only_smart_entry(sources):
    # Instance names that don't embed the PNP ID still work on a laptop
    # with one drive and one SMART entry.
    sources["disks"] = [_disk(pnp=SATA_PNP, kind="SATA SSD")]
    sources["wmi"] = {"IDE\\SOMETHING_ELSE_0": _sata_entry()}
    [d] = storage.collect_storage_health()["drives"]
    assert d["source"] == "wmi_smart"


def test_unreadable_drive_is_listed_with_windows_health(sources):
    sources["disks"] = [_disk(health="Warning")]

    result = storage.collect_storage_health()

    assert result["smart_readable"] is False
    [d] = result["drives"]
    assert d["source"] == "none"
    assert d["health"] == d["health_flag"] == "Warning"
    assert d["model"] == "Samsung SSD 970 EVO Plus 500GB"
    assert d["power_on_hours"] is None
    assert d["host_writes_tb"] is None


def test_crashing_source_falls_through(sources, monkeypatch):
    def boom(log):
        raise RuntimeError("smartctl exploded")

    monkeypatch.setattr(storage.smartctl, "collect", boom)
    sources["disks"] = [_disk(row=_counter_row())]
    log = []

    [d] = storage.collect_storage_health(log.append)["drives"]

    assert d["source"] == "reliability_counter"
    assert any("smartctl exploded" in line for line in log)


def test_crashing_inventory_still_returns_smartctl_drives(sources,
                                                          monkeypatch):
    def boom(log):
        raise RuntimeError("WMI down")

    monkeypatch.setattr(storage, "_inventory", boom)
    sources["smartctl"] = [_smartctl_drive("nvme_healthy")]

    [d] = storage.collect_storage_health()["drives"]

    assert d["source"] == "smartctl"
    assert d["health"] == "OK"


def test_drive_only_smartctl_sees_is_still_listed(sources):
    sources["disks"] = [_disk(row=_counter_row())]
    sources["smartctl"] = [_smartctl_drive("nvme_healthy"),
                           _smartctl_drive("hdd_crc_only", "/dev/sdb")]

    drives = storage.collect_storage_health()["drives"]

    assert [d["kind"] for d in drives] == ["NVMe SSD", "HDD"]
    assert "windows_health" not in drives[1]["raw"]


def test_current_score_still_reads_the_output(sources):
    sources["disks"] = [_disk(row=_counter_row())]
    sources["smartctl"] = [_smartctl_drive("nvme_healthy")]

    comp = score_storage(storage.collect_storage_health())

    assert comp.status == "pass"
    assert any("4,321 power-on hours" in line for line in comp.details)


def test_unreadable_drive_keeps_the_administrator_cap(sources):
    sources["disks"] = [_disk(health="OK")]
    comp = score_storage(storage.collect_storage_health())
    assert comp.score == 80.0
    assert "Administrator" in comp.note


def test_real_collector_off_windows_finds_nothing(monkeypatch):
    # No WMI here; keep the bundled .exe from being launched through WSL
    # interop so the test never touches real hardware.
    monkeypatch.setattr(smartctl, "find_smartctl", lambda: None)
    assert storage.collect_storage_health() == {"drives": [],
                                                "smart_readable": False}
