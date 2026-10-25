"""Performance collectors on this machine: the drive test always deletes
its file, the CPU units run and stop, and the whole check stops early
or runs only some stages."""

from collections import namedtuple

import pytest

from perf_samples import reference_perf
from syscheck import config
from syscheck.perf.stutter import count_gaps
from syscheck.scoring import build_report, score_performance
from syscheck.telemetry import disk_speed
from syscheck.telemetry import performance as perf

Usage = namedtuple("Usage", "total used free")


@pytest.fixture
def quick_drive(monkeypatch):
    monkeypatch.setattr(config, "PERF_DISK_RANDOM_S", 0.2)


def _leftovers(folder):
    return list(folder.glob("syscheck-speed-*"))


# ── drive test ────────────────────────────────────────────────────────
def test_drive_test_measures_and_deletes_its_file(tmp_path, quick_drive):
    samples = []
    result = disk_speed.measure_drive(tmp_path, size_mb=8,
                                      on_sample=samples.append)
    assert result["ran"] and result["reason"] is None
    assert not result["unbuffered"]          # not Windows: the cache is used
    assert result["seq_write_mbps"] > 0 and result["seq_read_mbps"] > 0
    assert result["rand_read_iops"] > 0 and result["rand_read_ms"] > 0
    assert {s["phase"] for s in samples} >= {"write", "read"}
    assert _leftovers(tmp_path) == []


def test_stopping_the_drive_test_deletes_its_file(tmp_path, quick_drive):
    calls = []

    def stop_after_a_few():
        calls.append(1)
        return len(calls) > 2

    result = disk_speed.measure_drive(tmp_path, size_mb=16,
                                      should_stop=stop_after_a_few)
    assert not result["ran"] and result["reason"] == "stopped"
    assert _leftovers(tmp_path) == []


def test_write_error_deletes_the_file(tmp_path, quick_drive, monkeypatch):
    def disk_full(self, size):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(disk_speed._PyFile, "write", disk_full)
    result = disk_speed.measure_drive(tmp_path, size_mb=8)
    assert not result["ran"]
    assert result["reason"].startswith("the test file could not be written")
    assert _leftovers(tmp_path) == []


def test_drive_test_needs_room_for_the_file(tmp_path, monkeypatch):
    monkeypatch.setattr(disk_speed.shutil, "disk_usage",
                        lambda _: Usage(10 * 2 ** 30, 0, 300 * 2 ** 20))
    result = disk_speed.measure_drive(tmp_path)
    assert result == {"ran": False, "folder": str(tmp_path), "size_mb": 256,
                      "unbuffered": False,
                      "reason": "not enough free space for the test file"}
    assert _leftovers(tmp_path) == []


def test_missing_temp_folder_is_reported(tmp_path):
    result = disk_speed.measure_drive(tmp_path / "gone", size_mb=8)
    assert not result["ran"]
    assert result["reason"].startswith("temporary folder not usable")


# ── processor units and stutter ticks ─────────────────────────────────
def test_cpu_units_run_on_several_threads():
    assert perf.run_units(2, 0.3, lambda: False) > 0


def test_stopped_cpu_units_give_nothing():
    assert perf.run_units(1, 30, lambda: True) is None


def test_tick_loop_wakes_every_10_ms():
    times = perf.tick_loop(0.3, lambda: False)
    gaps = count_gaps(times)
    assert gaps["seconds"] >= 0.3
    assert gaps["ticks"] >= 10
    assert perf.tick_loop(5, lambda: True) is None


# ── the whole check ───────────────────────────────────────────────────
def test_stopped_check_is_not_completed():
    result = perf.run_performance_check(should_stop=lambda: True)
    assert result["completed"] is False
    assert score_performance(result).status == "skip"


def test_check_can_run_some_stages_and_degrades_off_windows():
    stages, progress = [], []
    result = perf.run_performance_check(
        stages=("background", "history"),
        on_stage=lambda key, _title: stages.append(key),
        on_progress=progress.append)
    assert result["completed"] is True
    assert stages == ["background", "history"]
    assert progress[-1] == 100
    assert result["background"]["free_pct"] is not None
    assert result["history"]["available"] is False   # no event logs here
    assert "could not be read" in result["history"]["reason"]


def test_a_failing_stage_does_not_stop_the_others(monkeypatch):
    def broken():
        raise RuntimeError("registry exploded")

    logs = []
    monkeypatch.setattr(perf, "collect_background", broken)
    result = perf.run_performance_check(stages=("background", "history"),
                                        log=logs.append)
    assert result["completed"] is True
    assert "background" not in result and "history" in result
    assert any("registry exploded" in line for line in logs)


def test_unreadable_event_logs_mark_the_history_unavailable(monkeypatch):
    monkeypatch.setattr(perf, "_run", lambda *a, **k: None)
    history = perf.collect_history()
    assert history["available"] is False
    assert history["counts"]["hang"] == 0


# ── performance in the grade ──────────────────────────────────────────
def test_skipped_check_is_left_out_of_the_grade():
    comp = score_performance({"skipped": True})
    assert (comp.status, comp.note) == ("skip", "Performance check skipped.")
    assert score_performance({}).status == "skip"


def test_stopped_check_says_so():
    comp = score_performance({"completed": False, "cpu": {"single": 200}})
    assert comp.status == "skip"
    assert comp.note == "Performance check stopped early (excluded from grade)."


def test_completed_check_scores_with_causes_and_use_guide():
    comp = score_performance(reference_perf(background={"startup_count": 22}))
    assert (comp.key, comp.label) == ("performance", "Performance")
    assert comp.score == 92.0 and comp.status == "pass"
    assert comp.note == "Fast (92/100). 1 thing can make it hang or feel slow."
    assert comp.details[0] == ("22 programs start with Windows. Fix: Turn off "
                               "the ones you don't need in Task Manager > "
                               "Startup apps.")
    assert comp.details[-1].startswith("Typical use: Browsing OK")
    assert any(line.startswith("Processor") for line in comp.technical)


def test_nothing_measured_is_inconclusive():
    comp = score_performance({"completed": True})
    assert comp.status == "inconclusive"


def test_performance_counts_in_the_overall_grade():
    assert config.SCORE_WEIGHTS["performance"] == 0.15
    report = build_report({"performance": reference_perf(
        cpu={"single": 30.0, "multi": 90.0},
        memory={"bandwidth_gbps": 5.0, "installed_gb": 4.0},
        disk={"rand_read_iops": 150, "seq_read_mbps": 90.0,
              "seq_write_mbps": 80.0})})
    comp = next(c for c in report["components"] if c.key == "performance")
    assert comp.status == "fail"
    assert report["overall"]["score"] == comp.score    # the only one measured
