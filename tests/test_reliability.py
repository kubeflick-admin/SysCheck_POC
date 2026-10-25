"""Layer B: MSFT_PhysicalDisk + MSFT_StorageReliabilityCounter rows, as
the WMI fetch hands them over (uint64 values arrive as strings)."""

from syscheck.telemetry.smart_sources import reliability


def _row(**overrides):
    row = {
        "DeviceId": "0", "FriendlyName": "Samsung SSD 970 EVO Plus 500GB",
        "Model": "Samsung SSD 970 EVO Plus 500GB",
        "SerialNumber": "0025_3881_91B0_3D6E.", "Size": "500107862016",
        "MediaType": 4, "BusType": 17, "HealthStatus": 0,
        "FirmwareVersion": "2B2QEXM7 ", "SpindleSpeed": 0,
        "Wear": 3, "Temperature": 41, "TemperatureMax": 70,
        "PowerOnHours": 5200, "ReadErrorsUncorrected": 0,
        "WriteErrorsUncorrected": 0, "ReadErrorsTotal": 7,
        "StartStopCycleCount": 900,
    }
    row.update(overrides)
    return row


def test_nvme_row():
    d = reliability.to_drive(_row())
    assert d["source"] == "reliability_counter"
    assert d["kind"] == "NVMe SSD"
    assert d["model"] == "Samsung SSD 970 EVO Plus 500GB"
    assert d["serial"] == "0025_3881_91B0_3D6E."
    assert d["size_gb"] == 500.1
    assert d["health_flag"] == "OK"
    assert d["power_on_hours"] == 5200
    assert d["temperature_c"] == 41
    assert d["temperature_max_c"] == 70
    assert d["percentage_used"] == 3
    assert d["uncorrectable_errors"] == 0
    assert d["raw"]["interface"] == "NVMe"
    assert d["raw"]["firmware"] == "2B2QEXM7"
    assert d["raw"]["disk_number"] == 0
    assert d["raw"]["read_errors_total"] == 7
    assert d["raw"]["start_stop_cycles"] == 900
    assert "rotation_rpm" not in d["raw"]
    # Fields this source never provides stay unknown.
    for field in ("reallocated_sectors", "pending_sectors", "crc_errors",
                  "host_writes_tb", "available_spare_pct", "self_test"):
        assert d[field] is None


def test_no_counter_data_means_this_layer_did_not_work():
    row = _row(**dict.fromkeys(reliability.COUNTER_FIELDS))
    assert reliability.to_drive(row) is None


def test_zero_temperature_is_unsupported_not_freezing():
    d = reliability.to_drive(_row(Temperature=0, TemperatureMax=0))
    assert d["temperature_c"] is None
    assert d["temperature_max_c"] is None


def test_nvme_zero_wear_is_a_new_drive():
    assert reliability.to_drive(_row(Wear=0))["percentage_used"] == 0


def test_sata_zero_wear_is_unknown():
    d = reliability.to_drive(_row(BusType=11, Wear=0))
    assert d["kind"] == "SATA SSD"
    assert d["percentage_used"] is None


def test_hdd_wear_is_ignored_and_spindle_speed_kept():
    d = reliability.to_drive(_row(MediaType=3, BusType=11, Wear=5,
                                  SpindleSpeed=5400))
    assert d["kind"] == "HDD"
    assert d["percentage_used"] is None
    assert d["raw"]["rotation_rpm"] == 5400


def test_unknown_spindle_speed_is_dropped():
    d = reliability.to_drive(_row(MediaType=3, SpindleSpeed=0xFFFFFFFF))
    assert "rotation_rpm" not in d["raw"]


def test_uncorrectable_read_and_write_errors_add_up():
    d = reliability.to_drive(_row(ReadErrorsUncorrected=3,
                                  WriteErrorsUncorrected=None))
    assert d["uncorrectable_errors"] == 3
    d = reliability.to_drive(_row(ReadErrorsUncorrected=3,
                                  WriteErrorsUncorrected=2))
    assert d["uncorrectable_errors"] == 5


def test_health_status_mapping():
    assert reliability.health_flag(0) == "OK"
    assert reliability.health_flag(1) == "Warning"
    assert reliability.health_flag(2) == "FAILING"
    assert reliability.health_flag(5) == "Unknown"
    assert reliability.health_flag(None) == "Unknown"


def test_kind_mapping():
    assert reliability.kind_of(4, 17) == "NVMe SSD"
    assert reliability.kind_of(0, 17) == "NVMe SSD"     # media unspecified
    assert reliability.kind_of(3, 11) == "HDD"
    assert reliability.kind_of(4, 11) == "SATA SSD"
    assert reliability.kind_of(4, 8) == "Unknown"       # behind RAID / RST
    assert reliability.kind_of(None, None) == "Unknown"


def test_identity_falls_back_to_friendly_name():
    ident = reliability.identity(_row(Model=None, SerialNumber="  ",
                                      Size=None))
    assert ident == {"model": "Samsung SSD 970 EVO Plus 500GB",
                     "serial": None, "size_gb": None}


def test_fetch_off_windows_returns_nothing():
    assert reliability.fetch(lambda _: None) == []
