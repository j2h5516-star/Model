"""
test_measure_engine.py — 측정 장치 검증 · v3 5단계
===================================================

11차 사전 등록의 정의가 코드에 그대로 구현됐는지,
그리고 사고 백서의 회귀 지점(사고 6·7)이 지켜지는지 확인합니다.

실행: python3 tests/test_measure_engine.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import measure_engine as me  # noqa: E402


def q(filing, announced, eps):
    return {"filing_date": filing, "announced_date": announced,
            "period_label": filing[:7], "adj_eps": eps}


# ---------------------------------------------------------------------------
# TTM — 진짜 연속 4분기 합만 (사고 7 회귀)
# ---------------------------------------------------------------------------
def test_ttm_series_needs_four():
    assert me.ttm_series([1.0, 2.0, 3.0]) == []
    assert me.ttm_series([1.0, 2.0, 3.0, 4.0]) == [10.0]
    assert me.ttm_series([1.0, 2.0, 3.0, 4.0, 5.0]) == [10.0, 14.0]


def test_runs_split_on_gap():
    """몇 년 떨어진 분기를 이웃으로 만들면 안 됩니다 (사고 7)."""
    rows = [
        q("2021-03-31", None, 1.0),
        q("2021-06-30", None, 1.0),
        # 2년 공백
        q("2023-06-30", None, 1.0),
        q("2023-09-30", None, 1.0),
    ]
    runs = me.eps_runs(rows)
    assert len(runs) == 2, [len(r) for r in runs]
    # 어떤 구간도 4분기를 못 채우므로 TTM 은 없어야 합니다
    for run in runs:
        assert me.ttm_series([r["adj_eps"] for r in run]) == []


def test_peak_survives_run_break():
    """신고점 망각 금지 (사고 6): 구간이 끊겨도 옛 정점을 기억한다.

    앞 구간 TTM 정점 40 → 공백 → 뒤 구간 TTM 8 → 9.
    9 는 뒤 구간 안에서는 최고지만 옛 정점 40 아래이므로
    '신고점 아님'이어야 합니다. (정점을 잊으면 9 > 8 이라 가짜
    신고점이 됩니다 — 8차 감사에서 실데이터 27건이 이 사고였습니다.)
    """
    rows = (
        [q(f"2021-{m:02d}-28", f"2021-{m:02d}-28", 10.0) for m in (1, 4, 7, 10)]
        + [q(f"2024-{m:02d}-28", f"2024-{m:02d}-28", 2.0) for m in (1, 4, 7, 10)]
        + [q("2025-01-28", "2025-01-28", 3.0)]     # 뒤 구간 TTM 8 → 9
    )
    states = me.earnings_states(rows)
    later = [s for s in states if not s["announced"].startswith("2021")]
    assert later, "뒤 구간 상태가 없습니다"
    assert all(not s["new_high"] for s in later), later


def test_first_breakout_streak():
    """첫 돌파(streak==1)와 연속 돌파(streak>1)를 구분해야 합니다."""
    # 1.0 네 분기(TTM 4.0 형성) → 2.0 (TTM 5.0 신고점, 첫) → 3.0 (TTM 7.0, 연속)
    rows = [
        q("2024-01-31", "2024-01-31", 1.0),
        q("2024-04-30", "2024-04-30", 1.0),
        q("2024-07-31", "2024-07-31", 1.0),
        q("2024-10-31", "2024-10-31", 1.0),
        q("2025-01-31", "2025-01-31", 2.0),
        q("2025-04-30", "2025-04-30", 3.0),
    ]
    states = {s["announced"]: s for s in me.earnings_states(rows)}
    assert states["2025-01-31"]["new_high"] and states["2025-01-31"]["newhigh_streak"] == 1
    assert states["2025-04-30"]["new_high"] and states["2025-04-30"]["newhigh_streak"] == 2


def test_duplicate_announcement_counted_once():
    """같은 발표일의 정정 공시는 한 번만 셉니다."""
    rows = [
        q("2024-01-31", "2024-02-15", 1.0),
        q("2024-01-31", "2024-02-15", 1.0),
    ]
    assert len(me.earnings_states(rows)) == 1


# ---------------------------------------------------------------------------
# 수익률 창 — 발표 다음 거래일 진입 · 60거래일 · 우측검열
# ---------------------------------------------------------------------------
def _daily(n, start_close=100.0, step=1.0):
    from datetime import date, timedelta
    day = date(2024, 1, 2)
    dates, closes = [], []
    close = start_close
    while len(dates) < n:
        if day.weekday() < 5:
            dates.append(day.isoformat())
            closes.append(close)
            close += step
        day += timedelta(days=1)
    return dates, closes


def test_window_enters_next_trading_day():
    dates, closes = _daily(70)
    pct, entry, exit_ = me.window_return(dates, closes, dates[0])
    assert entry == dates[1]                       # 발표 **다음** 거래일
    assert exit_ == dates[1 + me.WINDOW_TRADING_DAYS]
    expected = (closes[61] / closes[1] - 1.0) * 100.0
    assert abs(pct - expected) < 1e-9


def test_window_right_censored():
    """창이 아직 안 끝난 사건은 재지 않습니다."""
    dates, closes = _daily(30)
    pct, reason, _ = me.window_return(dates, closes, dates[0])
    assert pct is None and reason == "우측검열"


def test_window_before_price_history():
    """주가 이력 시작 전의 발표는 재지 않습니다 (몇 달 뒤 진입 방지)."""
    dates, closes = _daily(70)
    pct, reason, _ = me.window_return(dates, closes, "2023-01-01")
    assert pct is None and reason == "주가시작전"


def test_excess_return_subtracts_spy():
    dates, closes = _daily(70, 100.0, 1.0)
    spy_dates, spy_closes = _daily(70, 500.0, 0.0)   # SPY 는 제자리
    excess, _detail = me.excess_return(
        {"dates": dates, "close": closes},
        {"dates": spy_dates, "close": spy_closes},
        dates[0],
    )
    stock_only = (closes[61] / closes[1] - 1.0) * 100.0
    assert abs(excess - stock_only) < 1e-9          # SPY 0% 이므로 그대로


# ---------------------------------------------------------------------------
# 게이지 — 신선도 140일 · H5 고정 20% · H5b 자기 이력 중앙값 + 워밍업
# ---------------------------------------------------------------------------
def _mini_ds(quarters, price_days=800):
    dates, closes = _daily(price_days)
    prices = {"SPY": {"dates": dates, "close": closes}}
    for t in quarters:
        prices[t] = {"dates": dates, "close": closes}
    return {
        "benchmark": "SPY",
        "tickers": list(quarters),
        "quarters": quarters,
        "prices": prices,
    }


def _history_rows(eps_by_quarter):
    """분기마다 (발표일, adj_eps) 를 만들어 줍니다 — 91일 간격."""
    from datetime import date, timedelta
    rows = []
    day = date(2024, 1, 31)
    for eps in eps_by_quarter:
        rows.append(q(day.isoformat(), day.isoformat(), eps))
        day += timedelta(days=91)
    return rows


def test_gauge_counts_fresh_newhigh_ratio():
    """게이지 = 신선한(140일) 발표 중 신고점 상태 비율."""
    ds = _mini_ds({
        "A": _history_rows([1, 1, 1, 1, 2]),   # 마지막 발표 = 신고점
        "B": _history_rows([1, 1, 1, 1, 1]),   # 마지막 발표 = 아님
    })
    series = me.gauge_series(ds)
    # 두 종목의 5번째 발표(2025-01-30) 직후 시점: 신고점 1/2 = 50%
    value = me.gauge_at(series, "2025-02-10")
    assert value == 50.0, value
    # 발표 140일 뒤에는 김이 빠져 분모에서 빠집니다
    stale = me.gauge_at(series, "2025-08-01")
    assert stale is None, stale


def test_gauge_h5_fixed_threshold():
    ds = _mini_ds({"A": _history_rows([1, 1, 1, 1, 2])})
    series = me.gauge_series(ds)
    assert me.gauge_h5_on(series, "2025-02-10") is True     # 100% ≥ 20%


def test_gauge_h5b_needs_warmup():
    """이력 52주 미만이면 H5b 는 판단 불가(None)여야 합니다."""
    ds = _mini_ds({"A": _history_rows([1, 1, 1, 1, 2])})
    series = me.gauge_series(ds)
    # 5번째 발표(2025-01-29) 직후 — 첫 판단 가능 게이지 값은 있으나
    # 그 이전 이력이 없다시피 하므로 H5b 는 판단 불가여야 합니다
    assert me.gauge_at(series, "2025-02-10") is not None
    assert me.gauge_h5b_on(series, "2025-02-10") is None


def test_gauge_h5b_median_rule():
    """워밍업을 채우면: 이력 중앙값 초과 = ON."""
    # 12분기 (약 33개월): 앞 2년여는 전부 신고점 아님(게이지 0%),
    # 마지막 발표만 신고점 → 게이지 100% > 이력 중앙값 0% → ON
    ds = _mini_ds({
        "A": _history_rows([1] * 11 + [30]),
    }, price_days=800)
    series = me.gauge_series(ds)
    # 게이지는 그 날짜 "직전 주까지"의 상태입니다 — 발표(2026-10-28) 당일에
    # 조회하면 아직 0%(자기 발표를 자기 조건에 넣지 않음), 다음 주에는 100%.
    assert me.gauge_at(series, "2026-10-28") == 0.0
    assert me.gauge_at(series, "2026-11-06") == 100.0
    assert me.gauge_h5b_on(series, "2026-11-06") is True
    assert me.gauge_h5b_on(series, "2026-10-28") is False   # 중앙값 0 초과 아님


# ---------------------------------------------------------------------------
# 사건 수집 종합
# ---------------------------------------------------------------------------
def test_collect_events_fields_and_censoring():
    ds = _mini_ds({"A": _history_rows([1, 1, 1, 1, 2])}, price_days=800)
    events, skipped = me.collect_events(ds)
    assert events, "사건이 없습니다"
    e = events[-1]
    for key in ("ticker", "announced", "new_high", "newhigh_streak",
                "h5", "h5b", "excess"):
        assert key in e, key
    assert isinstance(e["excess"], float)


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
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
    print(f"\n측정 장치 검증: {passed}개 통과, {failed}개 실패")
    sys.exit(1 if failed else 0)
