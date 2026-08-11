"""
test_measurement.py — 2단계 측정 장치 검증
==========================================

측정 코드가 사전 등록 규칙(전략.md 7장)을 그대로 지키는지,
가짜 데이터로 인터넷 없이 확인합니다.

실행: python3 tests/test_measurement.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import config as cfg  # noqa: E402
import measurement as mm  # noqa: E402


# ---------------------------------------------------------------------------
# 가짜 데이터
# ---------------------------------------------------------------------------
def _trading_days(n: int, start_year: int = 2024) -> list[str]:
    """주말 없는 가짜 거래일 목록 (평일만, 충분히 단순하게)."""
    from datetime import date, timedelta

    days = []
    day = date(start_year, 1, 2)
    while len(days) < n:
        if day.weekday() < 5:
            days.append(day.isoformat())
        day += timedelta(days=1)
    return days


def _flat_prices(n: int, price: float = 100.0) -> dict:
    return {"dates": _trading_days(n), "close": [price] * n}


def _rising_prices(n: int, start: float = 100.0, step: float = 1.0) -> dict:
    return {"dates": _trading_days(n), "close": [start + i * step for i in range(n)]}


# ---------------------------------------------------------------------------
# 주가 창 규칙
# ---------------------------------------------------------------------------
def test_entry_is_strictly_after_announce():
    """발표일이 거래일이어도 진입은 그 **다음** 거래일이어야 함 (미래 보기 차단)."""
    prices = _rising_prices(80)
    announce = prices["dates"][5]
    pct, entry_date, _exit = mm.window_return(prices["dates"], prices["close"], announce)
    assert entry_date == prices["dates"][6], (announce, entry_date)
    assert pct is not None


def test_window_is_exactly_60_trading_days():
    prices = _rising_prices(80)
    announce = prices["dates"][5]
    _pct, entry_date, exit_date = mm.window_return(prices["dates"], prices["close"], announce)
    entry_index = prices["dates"].index(entry_date)
    exit_index = prices["dates"].index(exit_date)
    assert exit_index - entry_index == mm.WINDOW_TRADING_DAYS


def test_right_censored_window_is_skipped():
    """창이 아직 안 끝난 최근 발표는 '우측검열'로 세지 않고 건너뜀."""
    prices = _rising_prices(30)                  # 60거래일 창을 채울 수 없음
    announce = prices["dates"][5]
    pct, reason, _ = mm.window_return(prices["dates"], prices["close"], announce)
    assert pct is None
    assert reason == "우측검열"


def test_announce_before_price_history_is_skipped():
    """주가 이력이 시작되기 전의 발표는 재지 않아야 함.

    5년 rolling 주가 창 밖의 옛 발표에 '다음 거래일'을 적용하면 몇 달
    뒤 가격으로 진입하는 셈이 됩니다 — 그런 사건은 '주가시작전'으로 제외.
    """
    prices = {"dates": _trading_days(80, start_year=2024),
              "close": [100.0 + i for i in range(80)]}
    pct, reason, _ = mm.window_return(prices["dates"], prices["close"], "2023-01-15")
    assert pct is None
    assert reason == "주가시작전", reason


def test_excess_subtracts_spy():
    """종목 +측정치 − SPY 측정치 = 초과수익 (%p)."""
    stock = {"dates": _trading_days(80), "close": [100 + i for i in range(80)]}
    spy = {"dates": _trading_days(80), "close": [100.0] * 80}   # SPY 는 제자리
    announce = stock["dates"][0]
    excess, _detail = mm.excess_return(stock, spy, announce)
    # 진입 1번째 종가 101, 청산 61번째 종가 161 → +59.4% − 0% ≈ +59.4%p
    assert excess is not None and abs(excess - (161 / 101 - 1) * 100) < 0.01


# ---------------------------------------------------------------------------
# 분기 연속 구간
# ---------------------------------------------------------------------------
def test_gap_splits_runs():
    """분기 사이가 크게 비면 TTM 오염을 막기 위해 구간을 끊어야 함."""
    rows = [
        {"adj_eps": 1.0, "filing_date": "2024-01-31"},
        {"adj_eps": 1.1, "filing_date": "2024-04-30"},
        # ← 2024-07 분기가 빠짐 (간격 184일)
        {"adj_eps": 1.3, "filing_date": "2024-10-31"},
        {"adj_eps": 1.4, "filing_date": "2025-01-31"},
    ]
    runs, breaks = mm.eps_runs(rows)
    assert len(runs) == 2 and breaks == 1
    assert [len(r) for r in runs] == [2, 2]


def test_missing_eps_rows_are_not_fabricated():
    """조정 EPS 없는 분기는 구간에 들어가지 않아야 함 (창작 금지)."""
    rows = [
        {"adj_eps": 1.0, "filing_date": "2024-01-31"},
        {"adj_eps": None, "filing_date": "2024-04-30"},
        {"adj_eps": 1.2, "filing_date": "2024-07-31"},
    ]
    runs, _ = mm.eps_runs(rows)
    total_rows = sum(len(r) for r in runs)
    assert total_rows == 2


# ---------------------------------------------------------------------------
# 사건 수집 — 미래를 보지 않는지
# ---------------------------------------------------------------------------
def _fake_snapshot(eps_values, price_days=400):
    """8분기 성장 시계열 + 상승 주가로 만든 최소 스냅샷."""
    rows = []
    quarter_dates = ["2024-01-31", "2024-04-30", "2024-07-31", "2024-10-31",
                     "2025-01-31", "2025-04-30", "2025-07-31", "2025-10-31"]
    days = _trading_days(price_days)
    for i, (value, qd) in enumerate(zip(eps_values, quarter_dates)):
        announce = qd[:8] + "28" if qd[5:7] != "01" else qd[:5] + "03-05"
        rows.append({"adj_eps": value, "filing_date": qd,
                     "announced_date": announce, "period_label": f"Q{i+1}"})
    return {
        "tickers": ["TT"],
        "benchmark": "SPY",
        "eps": {"TT": rows},
        "prices": {
            "TT": {"dates": days, "close": [100 + 0.5 * i for i in range(price_days)]},
            "SPY": {"dates": days, "close": [100.0] * price_days},
        },
    }


def test_no_look_ahead_in_judgment():
    """뒤 분기를 아무리 바꿔도 앞 사건의 판정은 그대로여야 함."""
    values = [1.0, 1.1, 1.25, 1.45, 1.7, 2.0, 2.4, 2.9]
    base = _fake_snapshot(values)
    tampered = _fake_snapshot(values[:-1] + [0.1])     # 마지막 분기를 폭락으로

    events_a, _ = mm.collect_events(base)
    events_b, _ = mm.collect_events(tampered)
    # 마지막 사건을 뺀 나머지 판정은 완전히 같아야 함
    for a, b in zip(events_a[:-1], events_b[:-1]):
        assert a["direction"] == b["direction"], (a, b)
        assert a["announced"] == b["announced"]


def test_duplicate_announcements_counted_once():
    """원본 + 정정 발표(같은 발표일)는 한 번만 사건이 되어야 함."""
    snap = _fake_snapshot([1.0, 1.1, 1.25, 1.45, 1.7, 2.0, 2.4, 2.9])
    snap["eps"]["TT"].append(dict(snap["eps"]["TT"][-1]))   # 같은 발표일 중복 행
    events, skipped = mm.collect_events(snap)
    dates = [e["announced"] for e in events]
    assert len(dates) == len(set(dates))


def test_trend_state_rising_series_is_uptrend():
    """꾸준히 오르는 주가는 '지속상승'으로 판정되어야 함."""
    prices = _rising_prices(400, start=100.0, step=0.5)
    state = mm.trend_state(prices["dates"], prices["close"], prices["dates"][-1])
    assert state == "지속상승", state


def test_trend_state_falling_series_is_not_uptrend():
    """꾸준히 내리는 주가는 '아님'이어야 함."""
    n = 400
    prices = {"dates": _trading_days(n), "close": [400.0 - 0.5 * i for i in range(n)]}
    state = mm.trend_state(prices["dates"], prices["close"], prices["dates"][-1])
    assert state == "아님", state


def test_trend_state_short_history_is_none():
    """역사가 모자라면 판정하지 않아야 함 (없으면 없음 — 창작 금지)."""
    prices = _rising_prices(60)      # 39주(26+13)를 채울 수 없는 길이
    state = mm.trend_state(prices["dates"], prices["close"], prices["dates"][-1])
    assert state is None, state


def test_trend_state_needs_close_above_long_ma():
    """오래 오르다 최근 26주선 아래로 무너지면 '지속상승'이 아니어야 함.

    일부러 깨뜨리기에서 발견한 빈틈: 13주선>26주선 조건만으로는 이
    경우를 '지속상승'으로 잘못 판정합니다. 종가>26주선 조건을 뭅니다.
    """
    n = 360
    closes = [100.0 + 0.5 * i for i in range(n - 10)] + [230.0] * 10
    days = _trading_days(n)
    state = mm.trend_state(days, closes, days[-1])
    assert state == "아님", state


def test_trend_state_ignores_future_prices():
    """발표일 이후의 주가는 추세 판정에 들어가면 안 됨 (미래 보기 차단)."""
    n = 400
    up = [100.0 + 0.5 * i for i in range(n)]
    crashed = up[: n - 50] + [1.0] * 50          # 발표 '이후' 폭락
    days = _trading_days(n)
    announce = days[n - 51]
    a = mm.trend_state(days, up, announce)
    b = mm.trend_state(days, crashed, announce)
    assert a == b == "지속상승", (a, b)


def test_battery_fields_streaks_and_volatility():
    """배터리 요인이 걸어가며 옳게 계산되는지 — 가속 연속·신고점 연속·변동성."""
    values = [1.0, 1.05, 1.12, 1.22, 1.37, 1.60, 1.95, 2.50]   # 가속이 이어지는 성장
    snap = _fake_snapshot(values, price_days=560)              # 뒤 사건이 검열로 안 잘리게
    events, _ = mm.collect_events(snap)
    assert events, "사건이 없음"
    last = events[-1]
    assert last["accel_streak"] >= 2, last                    # 증가율이 계속 커짐
    assert last["newhigh_streak"] >= 2, last                  # 신고점이 연속으로 갱신
    assert last["growth_vol"] is not None and last["growth_vol"] >= 0.0

    flat = _fake_snapshot([1.0] * 8, price_days=560)          # 완전 제자리 장사
    flat_events, _ = mm.collect_events(flat)
    assert flat_events[-1]["accel_streak"] == 0
    assert flat_events[-1]["newhigh_streak"] == 0
    assert flat_events[-1]["growth_vol"] == 0.0               # 흔들림 없음


def test_surge_threshold_is_20pp_excess():
    """폭등 판정은 시장 대비 +20%p 경계에서 갈려야 함."""
    stats = mm._group_stats([
        {"excess": 19.9}, {"excess": 20.0}, {"excess": 35.0},
    ])
    assert stats["n"] == 3
    assert stats["rate20"] == round(2 / 3 * 100, 1)
    assert stats["rate30"] == round(1 / 3 * 100, 1)


def test_breadth_crossings_boundary():
    """게이지 '켜짐'은 아래→위 통과 순간만 잡아야 함 (경계 포함)."""
    import breadth
    weeks = ["w1", "w2", "w3", "w4", "w5"]
    values = [10.0, 25.0, 15.0, 20.0, 30.0]
    assert breadth.crossings(weeks, values, 20.0) == ["w2", "w4"]


def test_breadth_latest_state_goes_stale():
    """발표가 140일 넘게 오래되면 그 종목 상태는 세지 않아야 함."""
    import breadth
    states = [("2025-01-02", True, True)]
    assert breadth.latest_state(states, "2025-03-01") is not None
    assert breadth.latest_state(states, "2025-06-30") is None    # 179일 경과
    assert breadth.latest_state(states, "2024-12-30") is None    # 발표 전


def test_breadth_earnings_state_is_walk_forward():
    """뒤 분기를 바꿔도 앞 발표 시점의 신고점 판정은 그대로여야 함."""
    import breadth
    rows = [
        {"adj_eps": v, "filing_date": d, "announced_date": d}
        for v, d in zip(
            [1.0, 1.1, 1.2, 1.3, 1.4, 1.5],
            ["2024-01-31", "2024-04-30", "2024-07-31",
             "2024-10-31", "2025-01-31", "2025-04-30"],
        )
    ]
    tampered = [dict(r) for r in rows]
    tampered[-1]["adj_eps"] = 0.01
    a = breadth.earnings_states(rows)
    b = breadth.earnings_states(tampered)
    assert a[:-1] == b[:-1], (a, b)


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
    print(f"\n측정 장치 검증: {passed}개 통과, {failed}개 실패")
    sys.exit(1 if failed else 0)
