"""Performance & hang judgement (pure): stutter counting, the score and
label, the use guide, every "why it might hang" rule, the event-log
parser and the recent-reinstall check."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from perf_samples import reference_perf
from syscheck import config
from syscheck.perf import events as ev
from syscheck.perf.describe import assess, measurement_rows, uses_line
from syscheck.perf.hangs import why_it_hangs
from syscheck.perf.score import (label_for, performance_score, ram_cap,
                                 responsiveness_score, scaled, subscores,
                                 use_guide)
from syscheck.perf.stutter import count_gaps
from syscheck.perf.system import (discrete_gpu, enabled_startup,
                                  memory_channels, parse_active_scheme,
                                  power_mode)
from syscheck.telemetry.smart_sources import smartctl

FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)


def _ticks(*gaps_ms, start=100.0):
    times = [start]
    for gap in gaps_ms:
        times.append(times[-1] + gap / 1000)
    return times


def _keys(perf, results=None):
    return [h.key for h in why_it_hangs(perf, results)]


def _hang(perf, key, results=None):
    return next(h for h in why_it_hangs(perf, results) if h.key == key)


# ── stutter gap counting ──────────────────────────────────────────────
def test_steady_ticks_have_no_stutters():
    gaps = count_gaps(_ticks(*[10.2] * 100))
    assert gaps == {"ticks": 100, "seconds": 1.0, "stutters": 0,
                    "freezes": 0, "worst_ms": 10.2}


def test_stutters_and_freezes_are_counted_once_each():
    gaps = count_gaps(_ticks(10, 60, 10, 250, 10, 251, 10, 900, 50))
    assert gaps["stutters"] == 2          # 60 and exactly 250
    assert gaps["freezes"] == 2           # 251 and 900
    assert gaps["worst_ms"] == 900.0


def test_gaps_at_the_stutter_limit_do_not_count():
    assert count_gaps(_ticks(config.PERF_STUTTER_MS))["stutters"] == 0


@pytest.mark.parametrize("times", [[], [5.0]])
def test_too_few_ticks_measure_nothing(times):
    gaps = count_gaps(times)
    assert gaps["ticks"] == 0 and gaps["worst_ms"] is None


# ── score and label ───────────────────────────────────────────────────
@pytest.mark.parametrize("ratio, points", [
    (1.0, 90), (0.5, 65), (0.25, 40), (2.0, 100), (8.0, 100), (1 / 64, 0),
])
def test_scaled_scores_on_a_log_scale(ratio, points):
    assert scaled(100 * ratio, 100) == pytest.approx(points)


@pytest.mark.parametrize("value", [None, 0, -3, "fast", True])
def test_scaled_ignores_missing_values(value):
    assert scaled(value, 100) is None


def test_reference_laptop_is_fast_at_about_90():
    a = assess(reference_perf())
    assert a.subscores == {"cpu": 90, "memory": 90, "drive": 90,
                           "responsiveness": 100}
    assert a.score == 92.0
    assert (a.label, a.status) == ("Fast", "pass")
    assert a.headline == ("Fast (92/100). Nothing found that should make it "
                          "hang.")


@pytest.mark.parametrize("score, label, status", [
    (100, "Fast", "pass"), (80, "Fast", "pass"), (79.9, "Good", "pass"),
    (60, "Good", "pass"), (59.9, "OK for basic use", "warn"),
    (40, "OK for basic use", "warn"), (39.9, "Slow", "fail"),
    (0, "Slow", "fail"), (None, "Not measured", "inconclusive"),
])
def test_label_bands(score, label, status):
    assert label_for(score) == (label, status)


@pytest.mark.parametrize("gb, cap", [(32, 100), (16, 100), (8, 75), (4, 45),
                                     (2, 25), (None, 100)])
def test_installed_memory_caps_the_memory_score(gb, cap):
    assert ram_cap(gb) == cap


def test_slow_old_laptop_scores_slow():
    perf = reference_perf(
        cpu={"single": 60.0, "multi": 120.0},
        memory={"bandwidth_gbps": 8.0, "installed_gb": 4.0},
        disk={"rand_read_iops": 150, "seq_read_mbps": 100.0,
              "seq_write_mbps": 90.0})
    a = assess(perf)
    assert a.subscores["drive"] == 0
    assert (a.label, a.status) == ("Slow", "fail")


def test_missing_parts_are_left_out_of_the_score():
    subs = subscores(reference_perf(disk={"ran": False}))
    assert subs["drive"] is None
    assert performance_score(subs) == pytest.approx(
        (0.30 * 90 + 0.15 * 90 + 0.20 * 100) / 0.65)


def test_buffered_drive_test_is_not_scored():
    assert subscores(reference_perf(disk={"unbuffered": False}))["drive"] \
        is None


def test_stutters_cost_points_and_load_counts_half():
    stutter = {"idle": {"ticks": 100, "stutters": 2, "freezes": 1},
               "load": {"ticks": 100, "stutters": 2, "freezes": 0}}
    # idle: 2 x 5 + 1 x 20 = 30; load: 2 x 5 x 0.5 = 5
    assert responsiveness_score(stutter) == 65
    assert responsiveness_score({}) is None


def test_freezes_alone_cannot_take_more_than_their_cap():
    stutter = {"idle": {"ticks": 100, "stutters": 0, "freezes": 50}}
    assert responsiveness_score(stutter) == 100 - config.PERF_FREEZE_MAX_LOSS


def test_a_serious_cause_turns_a_fast_pass_into_caution():
    a = assess(reference_perf(history={"counts": {"bsod": 2}}))
    assert a.label == "Fast" and a.status == "warn"


def test_nothing_measured_is_not_measured():
    a = assess({"completed": True})
    assert a.score is None and a.label == "Not measured"


# ── typical-use guide ─────────────────────────────────────────────────
def test_fast_laptop_without_graphics_card_games_lightly():
    uses = {u["use"]: u for u in assess(reference_perf()).uses}
    assert {k: u["verdict"] for k, u in uses.items()} == {
        "Browsing": "OK", "Office": "OK", "Video calls": "OK",
        "Light photo editing": "OK", "Gaming": "Slow"}
    assert uses["Gaming"]["note"].startswith("no separate graphics card")


def test_graphics_card_makes_gaming_ok():
    results = {"specs": {"gpus": [{"name": "Intel(R) Arc(TM) 140T GPU"},
                                  {"name": "NVIDIA GeForce RTX 4060 Laptop "
                                           "GPU"}]}}
    uses = assess(reference_perf(), results).uses
    assert uses[-1] == {"use": "Gaming", "verdict": "OK", "note": None}


def test_little_memory_limits_heavier_uses():
    subs = {"cpu": 90, "memory": 45, "drive": 90, "responsiveness": 100}
    verdicts = {u["use"]: u["verdict"] for u in use_guide(subs, 4, [])}
    assert verdicts == {"Browsing": "OK", "Office": "OK",
                        "Video calls": "Slow", "Light photo editing": "Slow",
                        "Gaming": "Not suitable"}


def test_any_working_laptop_can_browse_slowly():
    subs = {"cpu": 5, "memory": 25, "drive": 0, "responsiveness": 40}
    verdicts = {u["use"]: u["verdict"] for u in use_guide(subs, 4, [])}
    assert verdicts["Browsing"] == "Slow"
    assert verdicts["Light photo editing"] == "Not suitable"


def test_use_guide_line():
    assert uses_line([{"use": "Browsing", "verdict": "OK", "note": None},
                      {"use": "Gaming", "verdict": None, "note": None}]) == \
        "Browsing OK · Gaming n/a"


# ── why it might hang: one test per rule ──────────────────────────────
def test_reference_laptop_has_nothing_to_report():
    assert why_it_hangs(reference_perf()) == []


def test_hard_disk_is_the_main_cause():
    perf = reference_perf(disk={"rand_read_iops": 120},
                          background={"startup_count": 26})
    results = {"storage": {"drives": [{"kind": "HDD", "model": "WD Blue"}]}}
    hangs = why_it_hangs(perf, results)
    assert [h.key for h in hangs] == ["hdd", "startup"]   # most likely first
    assert hangs[0].cause == "Uses a slow hard disk — the main cause of " \
                             "freezing."
    assert not hangs[0].serious              # slow, not faulty
    assert hangs[0].fix == "An SSD upgrade (about £40) makes it much faster."
    assert hangs[1].cause == "26 programs start with Windows."


def test_hard_disk_from_storage_when_the_speed_test_did_not_run():
    perf = reference_perf(disk={"ran": False, "rand_read_iops": None})
    hdd = {"storage": {"drives": [{"kind": "HDD"}]}}
    mixed = {"storage": {"drives": [{"kind": "HDD"}, {"kind": "NVMe SSD"}]}}
    assert _keys(perf, hdd) == ["hdd"]
    assert _keys(perf, mixed) == []


def test_ssd_as_slow_as_a_hard_disk_says_so():
    perf = reference_perf(disk={"rand_read_iops": 250})
    hang = _hang(perf, "hdd", {"storage": {"drives": [{"kind": "SATA SSD"}]}})
    assert hang.cause.startswith("The drive is as slow as an old hard disk "
                                 "(250 reads a second)")
    assert hang.serious


def test_slow_ssd():
    hang = _hang(reference_perf(disk={"rand_read_iops": 1500}), "slow_drive")
    assert hang.cause == ("The drive is slow (1,500 reads a second), so "
                          "programs open slowly.")


def test_ram_nearly_full_at_idle():
    perf = reference_perf(memory={"installed_gb": 8.0, "modules": 1,
                                  "slots": 2},
                          idle={"ram_used_pct": 85.0})
    hang = _hang(perf, "ram")
    assert hang.cause == ("8 GB, 85% used at idle — will slow down with a "
                          "browser + Teams open.")
    assert hang.fix == "Add more memory — it has a free slot."


def test_too_little_memory():
    perf = reference_perf(memory={"installed_gb": 4.0},
                          idle={"ram_used_pct": 60.0})
    assert _hang(perf, "low_ram").cause.startswith("Only 4 GB of memory")


def test_memory_reserved_by_programs():
    perf = reference_perf(idle={"commit_used_gb": 35.0,
                                "commit_limit_gb": 36.0})
    assert _hang(perf, "commit").cause.startswith(
        "Programs have reserved almost all memory (35 of 36 GB)")


@pytest.mark.parametrize("perf_cpu, thermal", [
    ({"throttled": True}, {}),
    ({}, {"completed": True, "throttled": True}),
])
def test_thermal_throttling_from_either_test(perf_cpu, thermal):
    perf = reference_perf(cpu=perf_cpu)
    hang = _hang(perf, "thermal", {"thermal": thermal})
    assert hang.cause.startswith("Slows itself down when hot")
    assert hang.fix == "Needs cleaning and new thermal paste."


def test_stopped_thermal_test_is_not_used():
    thermal = {"completed": False, "throttled": True}
    assert _keys(reference_perf(), {"thermal": thermal}) == []


def test_freezes_during_the_test_are_serious():
    perf = reference_perf(stutter={"idle": {"ticks": 2990, "stutters": 1,
                                            "freezes": 2, "worst_ms": 640.0},
                                   "load": None})
    hang = _hang(perf, "freezes")
    assert hang.cause == "Froze twice during the test (for up to 0.6 s)."
    assert hang.serious


def test_many_stutters_at_idle():
    perf = reference_perf(stutter={"idle": {"ticks": 2990, "stutters": 7,
                                            "freezes": 0, "worst_ms": 120.0}})
    assert _hang(perf, "stutter").cause == ("Stuttered 7 times while doing "
                                            "nothing.")


def test_busy_at_idle():
    perf = reference_perf(idle={"cpu_pct": 22.0, "disk_busy_pct": 75.0,
                                "top_cpu": ["SearchIndexer.exe",
                                            "MsMpEng.exe"]})
    assert _hang(perf, "idle_cpu").cause == (
        "The processor is 22% busy at idle (busiest: SearchIndexer.exe, "
        "MsMpEng.exe).")
    assert _hang(perf, "idle_disk").cause == "The drive is 75% busy at idle."


def test_disk_nearly_full():
    perf = reference_perf(background={"free_pct": 6.0})
    assert _hang(perf, "disk_full").cause == "Only 6% free space on C:."


@pytest.mark.parametrize("power, start", [
    ({"plan": "Power saver", "saver": True}, "The Power saver power setting"),
    ({"plan": "Balanced", "mode": "Best power efficiency", "saver": True},
     "The Best power efficiency power setting"),
    ({"plan": "Balanced", "battery_saver": True, "saver": True},
     "Battery saver is on"),
])
def test_power_saving_settings(power, start):
    perf = reference_perf(background={"power": power})
    assert _hang(perf, "power_saver").cause.startswith(start)


def test_past_problems_from_the_event_log():
    history = {"counts": {"hang": 12, "crash": 4, "bsod": 1, "disk_error": 3,
                          "unexpected_shutdown": 2},
               "top_apps": {"hang": ["ms-teams.exe", "chrome.exe"],
                            "crash": ["explorer.exe"]}}
    hangs = why_it_hangs(reference_perf(history=history))
    assert [h.key for h in hangs] == ["disk_errors", "bsod", "app_hangs",
                                      "app_crashes", "shutdowns"]
    text = {h.key: h.cause for h in hangs}
    assert text["app_hangs"] == ("Programs stopped responding 12 times in "
                                 "the last 30 days (most often: ms-teams.exe,"
                                 " chrome.exe).")
    assert text["bsod"] == ("Windows crashed with a blue screen once in the "
                            "last 30 days.")
    assert text["disk_errors"].startswith("Windows logged 3 disk errors")


def test_a_few_hangs_are_not_worth_mentioning():
    history = {"counts": {"hang": 2, "crash": 2, "unexpected_shutdown": 1}}
    assert _keys(reference_perf(history=history)) == []


def test_no_history_means_no_history_causes():
    history = {"available": False, "counts": {"bsod": 5}}
    assert _keys(reference_perf(history=history)) == []


def test_drive_health_from_the_storage_module_comes_first():
    report = json.loads((FIXTURES / "smartctl" / "sata_ssd_bad_sectors.json")
                        .read_text("utf-8"))
    results = {"storage": {"drives": [smartctl.parse_device(report)]}}
    perf = reference_perf(background={"startup_count": 30})
    hangs = why_it_hangs(perf, results)
    assert [h.key for h in hangs] == ["drive_health", "startup"]
    assert hangs[0].cause.startswith("Drive health is only 78% (Samsung SSD "
                                     "860 EVO 500GB).")
    assert hangs[0].serious


# ── display rows ──────────────────────────────────────────────────────
def test_measurement_rows_in_plain_words():
    rows = dict(measurement_rows(reference_perf()))
    assert rows["Processor"] == ("90/100 — ran at about 4.8 GHz on one core, "
                                 "3.9 GHz on all cores (base 2.2 GHz)")
    assert rows["Memory"] == ("90/100 — 32 GB DDR5 5600 MHz, dual channel, "
                              "2 of 2 slots used, 23.5 GB/s")
    assert rows["Responsiveness"] == ("100/100 — idle: no stutters; under "
                                      "load: no stutters")
    assert rows["Windows installed"] == "12 Mar 2025"
    assert rows["Past problems (30 days)"].startswith(
        "0 programs stopped responding")


def test_history_row_says_when_history_is_missing():
    perf = reference_perf(history={"available": False, "reason":
                                   "history not available — Windows was "
                                   "reinstalled recently"})
    assert dict(measurement_rows(perf))["Past problems (30 days)"] == (
        "history not available — Windows was reinstalled recently")


# ── event-log parsing ─────────────────────────────────────────────────
def _sample_events():
    return ev.parse_events((FIXTURES / "events" / "history.xml")
                           .read_text("utf-8"))


def test_event_log_sample_is_parsed():
    events = _sample_events()
    assert len(events) == 9
    assert events[0] == {
        "provider": "Application Hang", "id": 1002,
        "time": datetime(2026, 10, 20, 8, 15, 30, tzinfo=timezone.utc),
        "app": "ms-teams.exe"}
    assert events[3]["app"] == "explorer.exe"         # <Data Name='AppName'>


def test_event_log_sample_is_summarised():
    summary = ev.summarise(_sample_events(), NOW)
    assert summary["counts"] == {"hang": 3, "crash": 1,
                                 "unexpected_shutdown": 1,  # one is 54 days old
                                 "bsod": 1, "disk_error": 1}
    assert summary["top_apps"] == {"hang": ["ms-teams.exe", "chrome.exe"],
                                   "crash": ["explorer.exe"]}


def test_other_providers_reusing_an_id_are_ignored():
    event = {"provider": "Microsoft-Windows-Kernel-General", "id": 7}
    assert ev.classify(event) is None
    assert ev.classify({"provider": "disk", "id": 7}) == "disk_error"


@pytest.mark.parametrize("text", ["", "not xml <", "<Event><broken>"])
def test_unreadable_event_output_is_empty(text):
    assert ev.parse_events(text) == []


def test_event_query():
    assert ev.xpath((41, 1001), 30) == (
        "*[System[(EventID=41 or EventID=1001) and "
        "TimeCreated[timediff(@SystemTime) <= 2592000000]]]")


# ── recent reinstall ──────────────────────────────────────────────────
def _days_ago(n):
    return NOW - timedelta(days=n)


def test_fresh_reinstall_has_no_history():
    status = ev.history_status(_days_ago(5), _days_ago(5), NOW)
    assert status == {"available": False, "days": 5,
                      "reason": "history not available — Windows was "
                                "reinstalled recently"}


def test_feature_update_keeps_the_history():
    # The install date moves with a feature update; the logs don't.
    status = ev.history_status(_days_ago(3), _days_ago(400), NOW)
    assert status == {"available": True, "days": 30, "reason": None}


def test_cleared_logs_have_no_history():
    status = ev.history_status(_days_ago(900), _days_ago(2), NOW)
    assert not status["available"]
    assert status["reason"].endswith("only go back 2 days (they may have been "
                                     "cleared)")


def test_short_history_counts_the_days_it_covers():
    assert ev.history_status(_days_ago(20), _days_ago(20), NOW)["days"] == 20


def test_unreadable_logs_have_no_history():
    status = ev.history_status(_days_ago(900), None, NOW, logs_read=False)
    assert not status["available"]
    assert "could not be read" in status["reason"]


# ── Windows settings ──────────────────────────────────────────────────
@pytest.mark.parametrize("text, expected", [
    ("Power Scheme GUID: 381b4222-f694-41f0-9685-ff5bb260df2e  (Balanced)",
     ("381b4222-f694-41f0-9685-ff5bb260df2e", "Balanced")),
    ("GUID des Energieschemas: A1841308-3541-4FAB-BC81-F71556F20B4A  "
     "(Energiesparmodus)\r\n",
     ("a1841308-3541-4fab-bc81-f71556f20b4a", "Energiesparmodus")),
    ("nothing here", (None, None)),
])
def test_active_power_scheme(text, expected):
    assert parse_active_scheme(text) == expected


def test_power_plans_are_named_by_guid_in_any_language():
    german = "GUID: a1841308-3541-4fab-bc81-f71556f20b4a  (Energiesparmodus)"
    assert power_mode(german) == {"plan": "Power saver", "mode": None,
                                  "battery_saver": False, "saver": True}
    custom = "GUID: 11111111-2222-3333-4444-555555555555  (Work)"
    assert power_mode(custom)["plan"] == "Work"


def test_power_mode_overlay_applies_to_balanced_only():
    balanced = "GUID: 381b4222-f694-41f0-9685-ff5bb260df2e  (Balanced)"
    saver = power_mode(balanced, "{961CC777-2547-4F9D-8174-7D86181B8A7A}")
    assert saver["mode"] == "Best power efficiency" and saver["saver"]
    best = power_mode(balanced, "ded574b5-45a0-4f42-8737-46345c09c238")
    assert best["mode"] == "Best performance" and not best["saver"]
    high = "GUID: 8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c  (High performance)"
    assert power_mode(high, "961cc777-2547-4f9d-8174-7d86181b8a7a")["mode"] \
        is None
    assert power_mode(balanced, battery_saver=True)["saver"]


def test_startup_items_turned_off_in_task_manager_are_left_out():
    entries = [("Run", "OneDrive"), ("Run", "Spotify"), ("Run32", "Steam"),
               ("StartupFolder", "Send to OneNote.lnk"), ("Run", "onedrive"),
               ("Run", "")]
    approved = {("Run", "spotify"): b"\x03\x00\x00\x00\x12",   # turned off
                ("Run32", "steam"): b"\x02" + b"\x00" * 11,     # on
                ("Run", "onedrive"): b"\x06" + b"\x00" * 11}    # on
    assert enabled_startup(entries, approved) == ["OneDrive", "Steam",
                                                  "Send to OneNote.lnk"]


@pytest.mark.parametrize("modules, channels", [
    ([], None),
    ([{"locator": "DIMM 0", "bank": "BANK 0"}], "single"),
    ([{"locator": "DIMM 0"}, {"locator": "DIMM 1"}], "dual"),
    ([{"bank": "ChannelA-DIMM0"}, {"bank": "ChannelB-DIMM0"}], "dual"),
    ([{"locator": "Controller0-ChannelA-DIMM0"},
      {"locator": "Controller0-ChannelA-DIMM1"}], "single"),
    ([{"bank": "CHANNEL A", "locator": "DIMM0"}], "single"),
])
def test_memory_channels(modules, channels):
    assert memory_channels(modules) == channels


@pytest.mark.parametrize("name, discrete", [
    ("NVIDIA GeForce RTX 3050 Laptop GPU", True),
    ("AMD Radeon RX 6600M", True),
    ("AMD Radeon(TM) RX 7600S", True),
    ("Intel(R) Arc(TM) A370M Graphics", True),
    ("Intel(R) Arc(TM) 140T GPU", False),
    ("Intel(R) UHD Graphics 620", False),
    ("AMD Radeon(TM) Graphics", False),
    ("AMD Radeon 780M", False),
])
def test_discrete_graphics_cards(name, discrete):
    assert discrete_gpu([{"name": name}]) is discrete
    assert discrete_gpu(None) is False
