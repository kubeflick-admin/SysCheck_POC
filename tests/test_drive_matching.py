"""Recognising the same drive across smartctl, MSFT_PhysicalDisk,
Win32_DiskDrive and the WMI SMART instance names."""

import pytest

from syscheck.telemetry.smart_sources import matching, smartctl
from syscheck.telemetry.smart_sources.schema import (normalise_model,
                                                     normalise_serial)


def _ident(serial=None, model=None, size_gb=None, disk_number=None):
    return {"serial": serial, "model": model, "size_gb": size_gb,
            "disk_number": disk_number}


# ── normalisation ──────────────────────────────────────────────────────
def test_serial_normalisation_ignores_padding_case_and_separators():
    assert normalise_serial("  s4evnx0n123456a ") == "S4EVNX0N123456A"
    assert normalise_serial("WD-WX1234_5678.") == "WDWX12345678"
    assert normalise_serial(None) == ""


def test_model_normalisation_strips_windows_bus_suffix():
    assert normalise_model("ST1000LM035-1RK172 ATA Device") == \
        "ST1000LM035-1RK172"
    assert normalise_model("  Samsung  SSD 860 ") == "SAMSUNG SSD 860"


# ── find_match ─────────────────────────────────────────────────────────
def test_serial_match_wins():
    target = _ident(serial="s3z2nb0k123456x ", model="Other name")
    candidates = [_ident(serial="AAA"), _ident(serial="S3Z2NB0K123456X")]
    assert matching.find_match(target, candidates) == 1


def test_nvme_namespace_id_from_windows_falls_back_to_model_and_size():
    # Seen on a real laptop: Windows reports the NVMe namespace ID as the
    # serial, and Win32_DiskDrive rounds the size down a little.
    windows = _ident(serial="ACE4_2E00_1234_5678_2EE4_AC00_0000_0001.",
                     model="SK hynix PC801 HFS001TEJ9X101N", size_gb=1024.2)
    smart = _ident(serial="SSB2N12345678ABCD",
                   model="SK hynix PC801 HFS001TEJ9X101N", size_gb=1024.2)
    assert matching.find_match(windows, [smart]) == 0


def test_disk_number_tells_identical_drives_apart():
    target = _ident(serial="NGUID-B", model="Same SSD", size_gb=512.1,
                    disk_number=1)
    candidates = [_ident(serial="REAL-A", model="Same SSD", size_gb=512.1,
                         disk_number=0),
                  _ident(serial="REAL-B", model="Same SSD", size_gb=512.1,
                         disk_number=1)]
    assert matching.find_match(target, candidates) == 1


def test_disk_number_with_conflicting_model_is_not_trusted():
    target = _ident(model="Drive A", size_gb=500.1, disk_number=0)
    assert matching.find_match(
        target, [_ident(model="Drive B", size_gb=500.1, disk_number=0)]) \
        is None


def test_identical_drives_without_other_keys_are_not_guessed():
    target = _ident(model="Same SSD", size_gb=512.1)
    candidates = [_ident(model="Same SSD", size_gb=512.1),
                  _ident(model="Same SSD", size_gb=512.1)]
    assert matching.find_match(target, candidates) is None


@pytest.mark.parametrize("other_size, expected", [
    (500.1, 0),
    (499.0, 0),      # 0.2% off: same drive
    (480.0, None),   # 4% off: different drive
    (None, None),    # unknown size: model alone isn't enough
])
def test_model_and_size_tolerance(other_size, expected):
    target = _ident(model="Samsung SSD 860 EVO 500GB", size_gb=500.1)
    candidates = [_ident(model="SAMSUNG SSD 860 EVO 500GB ATA Device",
                         size_gb=other_size)]
    assert matching.find_match(target, candidates) == expected


def test_no_candidates():
    assert matching.find_match(_ident(serial="X"), []) is None


# ── smartctl device names -> Windows disk number ──────────────────────
@pytest.mark.parametrize("name, number", [
    ("/dev/sda", 0), ("/dev/sdb", 1), ("/dev/sdz", 25), ("/dev/sdaa", 26),
    ("/dev/pd3", 3), ("/dev/nvme0", None), ("/dev/csmi0,1", None),
    (None, None),
])
def test_smartctl_disk_number(name, number):
    assert smartctl.disk_number(name) == number


# ── WMI SMART instance names ───────────────────────────────────────────
PNP = r"SCSI\DISK&VEN_NVME&PROD_SAMSUNG_SSD_970\5&2B3F3A3&0&000000"


def test_instance_name_is_the_pnp_id_plus_suffix():
    inst = r"SCSI\Disk&Ven_NVMe&Prod_Samsung_SSD_970\5&2b3f3a3&0&000000_0"
    assert matching.pnp_matches(PNP, inst)


def test_shared_prefix_does_not_cross_match():
    # The old pnp[:20] match paired these two disks with the same entry.
    other = r"SCSI\DISK&VEN_NVME&PROD_SAMSUNG_SSD_970\5&2B3F3A3&0&000001_0"
    assert not matching.pnp_matches(PNP, other)
    assert not matching.pnp_matches(PNP[:-1], PNP + "_0")


def test_missing_pnp_never_matches():
    assert not matching.pnp_matches(None, PNP + "_0")
    assert not matching.pnp_matches("", PNP + "_0")
