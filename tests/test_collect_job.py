"""
test_collect_job.py — 수집 로봇 검증 (인터넷 없이 가짜로)
========================================================

실행: python3 tests/test_collect_job.py
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pandas as pd  # noqa: E402

import collect_job as cj  # noqa: E402
import config as cfg  # noqa: E402


def _quiet(*_args, **_kwargs):
    pass


def _fake_daily():
    dates = pd.to_datetime(["2026-08-05", "2026-08-06", "2026-08-07"])
    return pd.DataFrame({"Close": [10.0, 10.5, 11.0]}, index=dates)


# ---------------------------------------------------------------------------
# 반쯤 깨진 날 방어 — 로봇의 핵심 안전장치
# ---------------------------------------------------------------------------
def test_success_enough_requires_half_fundamentals():
    """실적이 절반 넘게 실패하면 그날 데이터로 덮어쓰면 안 됨."""
    tickers = ["A", "B", "C", "D"]
    daily = {t: _fake_daily() for t in tickers} | {cfg.BENCHMARK: _fake_daily()}
    good = {"adj_eps_ok": 3, "xbrl_quarters": 0}
    bad = {"adj_eps_ok": 0, "xbrl_quarters": 0}
    assert cj.success_enough([good, good, bad, bad], daily, tickers)
    assert not cj.success_enough([good, bad, bad, bad], daily, tickers)


def test_success_enough_requires_benchmark():
    """기준지수(SPY)가 없으면 초과수익을 못 재므로 실패로 판정."""
    tickers = ["A", "B"]
    good = {"adj_eps_ok": 3, "xbrl_quarters": 0}
    daily_no_spy = {t: _fake_daily() for t in tickers}
    assert not cj.success_enough([good, good], daily_no_spy, tickers)


def test_success_enough_requires_half_prices():
    tickers = ["A", "B", "C", "D"]
    good = {"adj_eps_ok": 3, "xbrl_quarters": 0}
    daily = {"A": _fake_daily(), cfg.BENCHMARK: _fake_daily()}   # 주가 1/4 뿐
    assert not cj.success_enough([good] * 4, daily, tickers)


# ---------------------------------------------------------------------------
# 전체 실행 (가짜 수집기로)
# ---------------------------------------------------------------------------
def test_run_writes_snapshot_and_log(tmp_dir="/tmp/claude-0/robot_test"):
    """로봇이 스냅샷과 실행 기록을 파일로 남기고 0으로 끝나야 함."""
    import shutil
    shutil.rmtree(tmp_dir, ignore_errors=True)
    os.makedirs(tmp_dir, exist_ok=True)
    cwd = os.getcwd()
    os.chdir(tmp_dir)
    try:
        tickers = ["TT", "UU"]
        fake_rows = [
            {"filing_date": "2026-04-30", "announced_date": "2026-05-28",
             "adj_eps": 1.0, "source": cfg.SRC_DIRECT},
        ]
        original_get = cj.sf.get_fundamentals
        original_load = cj.measure_store.sf.load_cache
        original_fetch = cj.md.fetch_daily_data
        cj.sf.get_fundamentals = lambda t, use_cache=False: (
            fake_rows, {**cj.sf.new_report(t), "adj_eps_ok": 1, "merged_direct": 1}
        )
        cj.measure_store.sf.load_cache = lambda t: fake_rows
        cj.md.fetch_daily_data = lambda wanted: (
            {t: _fake_daily() for t in wanted}, []
        )
        try:
            code = cj.run(tickers, progress=_quiet)
        finally:
            cj.sf.get_fundamentals = original_get
            cj.measure_store.sf.load_cache = original_load
            cj.md.fetch_daily_data = original_fetch

        assert code == 0
        with open(f"{cfg.MEASURE_DIR}/snapshot.json", encoding="utf-8") as f:
            snap = json.load(f)
        assert snap["tickers"] == tickers
        assert snap["eps"]["TT"][0]["adj_eps"] == 1.0
        with open(f"{cfg.MEASURE_DIR}/robot_log.json", encoding="utf-8") as f:
            log = json.load(f)
        assert log["tickers"] == 2
        assert log["per_ticker"][0]["adj_eps_ok"] == 1
        # v3 5단계 — 수집 성공 시 자동 판정도 기록되어야 합니다.
        # (가짜 데이터는 분기 1개뿐이라 사건 0건 → 전 가설 '판정 불가'가 정답)
        assert "verdict" in log, log.keys()
        with open(f"{cfg.MEASURE_DIR}/verdict.json", encoding="utf-8") as f:
            verdict = json.load(f)
        assert "H2b_신고점_첫돌파" in verdict["가설"]
        assert verdict["가설"]["H2b_신고점_첫돌파"]["판정"] == "판정 불가"
    finally:
        os.chdir(cwd)
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_run_aborts_when_mostly_failed():
    """수집이 대부분 실패한 날은 종료코드 1 — 파일을 쓰지 않아야 함."""
    tickers = ["TT", "UU"]
    original_get = cj.sf.get_fundamentals
    original_fetch = cj.md.fetch_daily_data
    cj.sf.get_fundamentals = lambda t, use_cache=False: ([], cj.sf.new_report(t))
    cj.md.fetch_daily_data = lambda wanted: ({}, list(wanted))
    try:
        code = cj.run(tickers, progress=_quiet)
    finally:
        cj.sf.get_fundamentals = original_get
        cj.md.fetch_daily_data = original_fetch
    assert code == 1


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    passed = failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  ✅ {name}")
            passed += 1
        except AssertionError as e:
            print(f"  ❌ {name} — {e}")
            failed += 1
        except Exception as e:
            print(f"  💥 {name} — {type(e).__name__}: {e}")
            failed += 1
    print(f"\n수집 로봇 검증: {passed}개 통과, {failed}개 실패")
    sys.exit(1 if failed else 0)
