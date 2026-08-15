"""
test_sector_model.py — 정배열 장치 검증 (39·43차 등록의 구현)
================================================================

여기서 지키는 약속:
  · 33차 장치(`aligned_flags`, 종가 조건 있음)와 39차 장치
    (`aligned_flags_chart`, 이평선 배열만)는 **서로 다른 등록**이므로
    따로 움직여야 한다 — 한쪽을 고쳐 다른 쪽이 바뀌면 사전 등록이 무너진다
  · 이격도는 완성 시점에 **미리 알 수 있는** 값이어야 한다
  · 완성 사건은 완성된 그 주에 **한 번만** 생긴다 (깜빡임 금지)
  · H18 판정 표본은 **등록일 뒤**의 완성만 쓴다 (원칙 5 — 탐색 표본 재사용 금지)

실행: python3 tests/test_sector_model.py
"""

import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import judge  # noqa: E402
import sector_model as sm  # noqa: E402


def weekly_prices(closes: list[float], start: str = "2020-01-03") -> dict:
    """주당 거래일 하나짜리 가짜 주가 (한 주에 하루면 주봉 계산에 충분)."""
    day = date.fromisoformat(start)
    dates = []
    for _ in closes:
        dates.append(day.isoformat())
        day += timedelta(days=7)
    return {"dates": dates, "close": list(closes)}


# ---------------------------------------------------------------------------
# 두 장치는 따로 움직인다
# ---------------------------------------------------------------------------
def test_chart_device_ignores_close_condition():
    """이평선은 정배열인데 그 주 종가만 4주선 아래로 눌린 경우.

    39차 장치는 True(사람이 차트에서 보는 정배열은 안 깨짐),
    33차 장치는 False — 이 차이가 실제로 나야 합니다.
    """
    closes = [100.0 + i for i in range(80)]     # 꾸준한 우상향 → 정배열
    closes[-1] = closes[-2] - 12.0              # 마지막 주만 급락 (4주선 아래)
    prices = weekly_prices(closes)
    last = prices["dates"][-1]
    assert sm.aligned_flags_chart(prices)[last] is True
    assert sm.aligned_flags(prices)[last] is False


def test_warmup_weeks_are_not_judged():
    """52주 이력이 없는 주는 값을 만들지 않습니다 (판단 불가)."""
    prices = weekly_prices([100.0 + i for i in range(60)])
    flags = sm.aligned_flags_chart(prices)
    assert len(flags) == 60 - sm.WARMUP_WEEKS + 1, len(flags)
    assert prices["dates"][sm.WARMUP_WEEKS - 2] not in flags


# ---------------------------------------------------------------------------
# 이격도 — 산술 검산
# ---------------------------------------------------------------------------
def test_gap_over_52w_arithmetic():
    """종가 100 · 52주선 80 이면 이격도는 정확히 +25.0% 여야 합니다."""
    closes = [80.0] * 51 + [100.0]      # 52주 평균 = (80*51 + 100)/52
    prices = weekly_prices(closes)
    expected = (100.0 / (sum(closes) / 52) - 1.0) * 100.0
    got = sm.gap_over_52w(prices, prices["dates"][-1])
    assert abs(got - expected) < 1e-9, (got, expected)
    assert 0 < got < 25.0


def test_gap_is_none_without_history():
    prices = weekly_prices([100.0] * 10)
    assert sm.gap_over_52w(prices, prices["dates"][-1]) is None


# ---------------------------------------------------------------------------
# 완성 사건
# ---------------------------------------------------------------------------
def fake_ds(prices_by_ticker: dict) -> dict:
    bench = "SPY"
    return {
        "tickers": [t for t in prices_by_ticker if t != bench],
        "benchmark": bench,
        "prices": prices_by_ticker,
        "quarters": {},
    }


def test_completion_fires_once_per_completion():
    """정배열이 성립한 **첫 주**에만 사건이 생겨야 합니다."""
    # 60주 하락(바닥) → 60주 상승 → 정배열 완성 1회
    closes = [200.0 - i for i in range(60)] + [140.0 + i * 3 for i in range(60)]
    ds = fake_ds({"AAA": weekly_prices(closes),
                  "SPY": weekly_prices([100.0] * 120)})
    events = sm.completion_events(ds)
    assert len(events) == 1, [e["day"] for e in events]
    event = events[0]
    assert event["ticker"] == "AAA"
    assert event["바닥주"] > 0                 # 완성 직전 미성립 주수
    assert event["유지주"] > 0
    assert event["델타"] is None               # 분기 자료가 없으면 만들지 않는다
    assert event["이격도"] is not None


def test_completion_target_is_none_when_window_unfinished():
    """표적 창이 아직 안 끝난 사건은 값을 만들지 않습니다 (우측 검열)."""
    closes = [200.0 - i for i in range(60)] + [140.0 + i * 3 for i in range(60)]
    ds = fake_ds({"AAA": weekly_prices(closes),
                  "SPY": weekly_prices([100.0] * 120)})
    event = sm.completion_events(ds)[0]
    # 주당 거래일이 1개뿐이라 60거래일 = 60주 뒤 — 표본 길이를 넘습니다
    assert event["초과60"] is None
    assert event["초과250"] is None


# ---------------------------------------------------------------------------
# H18 판정 — 탐색 표본 재사용 금지
# ---------------------------------------------------------------------------
def completion(day, gap, excess):
    return {"ticker": "AAA", "day": day, "이격도": gap, "초과60": excess}


def test_h18_uses_only_events_after_registration():
    """등록일 이전 사건은 '탐색표본(참고)'으로만 가고 판정에 못 들어갑니다."""
    old = ([completion("2026-01-01", 40.0, 50.0) for _ in range(30)]
           + [completion("2026-01-01", 5.0, 0.0) for _ in range(30)])
    new = ([completion("2026-09-01", 40.0, 50.0) for _ in range(12)]
           + [completion("2026-09-01", 5.0, 0.0) for _ in range(40)])
    result = judge.judge_completion_gap(old + new, "2026-08-15", 30.0)
    entry = result[judge.H18_NAME]
    assert entry["신규(판정)"]["신호"]["n"] == 12, entry
    assert entry["탐색표본(참고)"]["신호"]["n"] == 30, entry
    assert entry["판정"] == "채택", entry
    assert entry["등록일"] == "2026-08-15"


def test_h18_small_new_sample_is_undecidable():
    """등록 직후에는 새 표본이 없으므로 '판정 불가' 여야 합니다 — 억지 결론 금지."""
    old = [completion("2026-01-01", 40.0, 50.0) for _ in range(50)]
    result = judge.judge_completion_gap(old, "2026-08-15", 30.0)
    assert result[judge.H18_NAME]["판정"] == "판정 불가"


def test_h18_drops_unmeasurable_events():
    """표적이 없거나 이격도를 못 잰 사건은 표본에서 빠집니다."""
    events = [completion("2026-09-01", 40.0, None),
              completion("2026-09-01", None, 50.0),
              completion("2026-09-01", 40.0, 50.0)]
    entry = judge.judge_completion_gap(events, "2026-08-15", 30.0)[judge.H18_NAME]
    assert entry["신규(판정)"]["기준선"]["n"] == 1, entry


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
    print(f"\n정배열 장치 검증: {passed}개 통과, {failed}개 실패")
    sys.exit(1 if failed else 0)
