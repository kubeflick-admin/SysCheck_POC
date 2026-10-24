"""Layer C: hand-built ATA S.M.A.R.T. attribute and threshold blobs."""

from syscheck.telemetry.smart_sources import wmi_smart


def _record(attr_id: int, raw: int, value: int = 100, worst: int = 100,
            flags: int = 0) -> bytes:
    # id, flags(2 LE), current, worst, raw(6 LE), reserved
    return (bytes([attr_id]) + flags.to_bytes(2, "little")
            + bytes([value, worst]) + raw.to_bytes(6, "little") + b"\0")


def _thresh(attr_id: int, threshold: int) -> bytes:
    return bytes([attr_id, threshold]) + b"\0" * 10


def _blob(*records: bytes) -> bytes:
    blob = b"\x10\x00" + b"".join(records)
    return blob + b"\0" * (512 - len(blob))


def _temp_raw(current: int, low: int, high: int) -> int:
    return current | (low << 16) | (high << 32)


ATTRS = wmi_smart.parse_smart_blob(_blob(
    _record(5, 3, value=98, worst=98, flags=0x33),
    _record(9, (17 << 32) | 15_000),            # minutes packed above hours
    _record(12, 1_800),
    _record(187, 2),
    _record(188, (5 << 32) | (5 << 16) | 9),    # Seagate-style packed words
    _record(192, 44),
    _record(194, _temp_raw(37, 15, 58)),
    _record(196, 1),
    _record(197, 4, value=100, worst=100),
    _record(198, 1),
    _record(199, 0),
    _record(231, 0, value=91),
    _record(233, 0, value=12, worst=12, flags=0x32),
    _record(241, 2 ** 34),
    _record(242, 2 ** 35),
))
THRESHOLDS = wmi_smart.parse_threshold_blob(_blob(
    _thresh(5, 10), _thresh(233, 15), _thresh(9, 0)))


# ── blob parsing ───────────────────────────────────────────────────────
def test_parse_smart_blob_reads_value_worst_flags_and_raw():
    blob = _blob(_record(9, 12_345, value=97, worst=95, flags=0x32),
                 _record(0, 999),                   # empty slot: ignored
                 _record(241, 2 ** 40))
    assert wmi_smart.parse_smart_blob(blob) == {
        9: {"flags": 0x32, "value": 97, "worst": 95, "raw": 12_345},
        241: {"flags": 0, "value": 100, "worst": 100, "raw": 2 ** 40},
    }


def test_parse_smart_blob_tolerates_truncated_record():
    # Only the first raw byte survives the cut; parsing must not raise.
    assert wmi_smart.parse_smart_blob(b"\x10\x00" + _record(9, 7)[:6]) == {
        9: {"flags": 0, "value": 100, "worst": 100, "raw": 7}}


def test_parse_smart_blob_ignores_bytes_past_30_records():
    records = [_record(i, i) for i in range(1, 32)]
    assert len(wmi_smart.parse_smart_blob(b"\x10\x00" + b"".join(records))) \
        == 30


def test_parse_threshold_blob():
    assert THRESHOLDS == {5: 10, 233: 15, 9: 0}
    assert wmi_smart.parse_threshold_blob(b"") == {}


# ── normalised drive ───────────────────────────────────────────────────
def test_decodes_the_standard_attributes():
    d = wmi_smart.to_drive(ATTRS, THRESHOLDS, predict_failure=False)
    assert d["source"] == "wmi_smart"
    assert d["health_flag"] == "OK"
    assert d["reallocated_sectors"] == 3
    assert d["pending_sectors"] == 4
    assert d["uncorrectable_errors"] == 1        # 198 wins over 187
    assert d["crc_errors"] == 0
    assert d["power_cycles"] == 1_800
    assert d["unsafe_shutdowns"] == 44
    assert d["power_on_hours"] == 15_000         # low 32 bits only
    assert d["raw"]["power_on_hours_raw"] == (17 << 32) | 15_000
    assert d["raw"]["command_timeouts"] == 9
    assert d["temperature_c"] == 37
    assert d["temperature_max_c"] == 58


def test_vendor_specific_units_are_not_guessed():
    d = wmi_smart.to_drive(ATTRS, THRESHOLDS, predict_failure=False)
    assert d["host_writes_tb"] is None
    assert d["percentage_used"] is None
    assert d["kind"] == "Unknown"                # the inventory fills this


def test_attribute_table_joins_thresholds_and_flags_failures():
    d = wmi_smart.to_drive(ATTRS, THRESHOLDS, predict_failure=False)
    table = {a["id"]: a for a in d["raw"]["attributes"]}
    assert [a["id"] for a in d["raw"]["attributes"]] == sorted(ATTRS)
    assert table[5] == {"id": 5, "name": "Reallocated_Sector_Ct",
                        "value": 98, "worst": 98, "thresh": 10, "raw": 3,
                        "raw_string": None, "when_failed": None,
                        "prefailure": True}
    assert table[233]["when_failed"] == "now"    # 12 <= threshold 15
    assert table[233]["name"] is None            # vendor-specific id
    assert table[9]["when_failed"] is None       # threshold 0 never fails
    assert table[197]["thresh"] is None          # no threshold record
    assert table[241]["raw"] == 2 ** 34


def test_predict_failure_sets_the_flag():
    assert wmi_smart.to_drive(ATTRS, {}, True)["health_flag"] == "FAILING"
    assert wmi_smart.to_drive(ATTRS, {}, None)["health_flag"] == "Unknown"


def test_uncorrectable_falls_back_to_187():
    attrs = {k: v for k, v in ATTRS.items() if k != 198}
    assert wmi_smart.to_drive(attrs, {}, None)["uncorrectable_errors"] == 2


def test_missing_attributes_stay_none():
    d = wmi_smart.to_drive({1: {"flags": 0, "value": 100, "worst": 100,
                                "raw": 0}}, {}, None)
    for field in ("power_on_hours", "reallocated_sectors", "pending_sectors",
                  "temperature_c", "temperature_max_c", "crc_errors"):
        assert d[field] is None


def test_no_attributes_means_this_layer_did_not_work():
    assert wmi_smart.to_drive({}, {}, False) is None


# ── temperature byte layouts ───────────────────────────────────────────
def test_temperature_min_max_in_either_byte_order():
    assert wmi_smart._temperatures(_temp_raw(40, 20, 55)) == (40, 55)
    assert wmi_smart._temperatures(_temp_raw(40, 55, 20)) == (40, 55)


def test_temperature_without_plausible_max():
    assert wmi_smart._temperatures(40) == (40, None)
    assert wmi_smart._temperatures(_temp_raw(40, 0, 200)) == (40, None)
    assert wmi_smart._temperatures(0) == (None, None)
    assert wmi_smart._temperatures(None) == (None, None)


def test_temperature_falls_back_to_airflow_attribute():
    attrs = {190: {"flags": 0, "value": 60, "worst": 50,
                   "raw": _temp_raw(33, 18, 49)}}
    d = wmi_smart.to_drive(attrs, {}, None)
    assert (d["temperature_c"], d["temperature_max_c"]) == (33, 49)


def test_fetch_off_windows_returns_nothing():
    assert wmi_smart.fetch(lambda _: None) == {}
