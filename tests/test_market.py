"""
test_market.py — 주가 · 주봉 · 추세 · 상대강도 검증
==================================================

실행: python3 tests/test_market.py
"""

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import config as cfg  # noqa: E402
import market_data as md  # noqa: E402
from fixtures import flat_daily, make_daily, trending_daily  # noqa: E402


# ---------------------------------------------------------------------------
# 주봉 변환
# ---------------------------------------------------------------------------
def test_weekly_resample():
    """일봉 10일치(2주)가 주봉 2개로 정확히 합쳐져야 함"""
    daily = make_daily([10, 11, 12, 13, 14, 20, 21, 22, 23, 24], start="2023-01-02")
    weekly = md.to_weekly(daily)

    assert len(weekly) == 2, weekly

    first = weekly.iloc[0]
    assert abs(first["Open"] - 10 * 0.99) < 1e-9     # 월요일 시가
    assert abs(first["High"] - 14 * 1.01) < 1e-9     # 주중 최고가
    assert abs(first["Low"] - 10 * 0.98) < 1e-9      # 주중 최저가
    assert abs(first["Close"] - 14) < 1e-9           # 금요일 종가
    assert abs(first["Volume"] - 5000) < 1e-9        # 거래량 합계

    assert weekly.index[0].day_name() == "Friday"
    assert str(weekly.index[0].date()) == "2023-01-06"


def test_incomplete_week_by_market_close():
    """그 주 금요일 장 마감(미국 동부 16시) 전이면 '진행 중'으로 봐야 함"""
    daily = make_daily([10, 11, 12, 13, 14, 20, 21, 22], start="2023-01-02")
    weekly = md.to_weekly(daily)   # 마지막 주봉 라벨 = 2023-01-13(금)

    before = pd.Timestamp("2023-01-13 12:00", tz="America/New_York")
    after = pd.Timestamp("2023-01-13 16:30", tz="America/New_York")

    assert md.is_last_week_incomplete(weekly, now=before) is True
    assert md.is_last_week_incomplete(weekly, now=after) is False


def test_friday_holiday_week_completes():
    """금요일 휴장 주(굿프라이데이 등)도 주말부터는 완성된 주로 봐야 함"""
    # 2023-04-07(금)은 굿프라이데이 휴장 → 거래는 4/6(목)까지만
    daily = make_daily([10, 11, 12, 13, 14, 20, 21, 22, 23], start="2023-03-27")
    weekly = md.to_weekly(daily)
    assert str(weekly.index[-1].date()) == "2023-04-07"

    saturday = pd.Timestamp("2023-04-08 09:00", tz="America/New_York")
    assert md.is_last_week_incomplete(weekly, now=saturday) is False


# ---------------------------------------------------------------------------
# 추세 5단계
# ---------------------------------------------------------------------------
def _analyze(daily):
    weekly = md.to_weekly(daily)
    return md.classify_trend(md.add_moving_averages(weekly))


def test_trend_full_up():
    """꾸준히 오르는 종목 → 완전 정배열 + 상승 기울기 + 양수 이격도"""
    result = _analyze(trending_daily(900, 0.003))

    assert result["state"] == cfg.S_FULL_UP, result
    assert result["stage"] == 1
    assert result["slope"] == "상승", result
    assert result["disparity"] > cfg.NEUTRAL_BAND_PCT, result


def test_trend_full_down():
    """꾸준히 내리는 종목 → 완전 역배열 + 하락 기울기"""
    result = _analyze(trending_daily(900, -0.003, start_price=100.0))

    assert result["state"] == cfg.S_FULL_DOWN, result
    assert result["stage"] == 5
    assert result["slope"] == "하락", result


def test_trend_neutral_when_flat():
    """거의 안 움직이는 종목 → 중립 (26주선 ±3% 이내)"""
    result = _analyze(flat_daily(900))

    assert result["state"] == cfg.S_NEUTRAL, result
    assert abs(result["disparity"]) <= cfg.NEUTRAL_BAND_PCT, result


def test_trend_unknown_short_history():
    """52주치가 안 되면 판정 불가"""
    result = _analyze(trending_daily(150, 0.002))
    assert result["state"] == cfg.S_UNKNOWN, result
    assert result["stage"] == cfg.TREND_STAGE[cfg.S_UNKNOWN]


# ---------------------------------------------------------------------------
# 수익률 · 상대강도(RS)
# ---------------------------------------------------------------------------
def test_period_return():
    """최근 N주 수익률을 정확히 계산해야 함"""
    # 매주 정확히 1씩 오르는 20주치 주봉을 직접 만듭니다
    weekly = pd.DataFrame(
        {"Close": np.arange(100.0, 120.0)},
        index=pd.date_range("2025-01-03", periods=20, freq="W-FRI"),
    )
    # 13주 전 = 119 - 13 = 106 → (119/106 - 1) × 100 ≈ 12.26%
    result = md.period_return_pct(weekly, 13)
    assert abs(result - 12.264) < 0.01, result


def test_relative_strength():
    """RS = 내 종목 수익률 − 시장 수익률 (%p)"""
    mine = pd.DataFrame(
        {"Close": 100.0 * (1.02 ** np.arange(30))},       # 주당 +2%
        index=pd.date_range("2025-01-03", periods=30, freq="W-FRI"),
    )
    market = pd.DataFrame(
        {"Close": 100.0 * (1.005 ** np.arange(30))},      # 주당 +0.5%
        index=pd.date_range("2025-01-03", periods=30, freq="W-FRI"),
    )
    rs = md.relative_strength(mine, market, weeks=13)

    mine_return = (1.02 ** 13 - 1) * 100
    market_return = (1.005 ** 13 - 1) * 100
    assert abs(rs - (mine_return - market_return)) < 0.01, rs
    assert rs > 0   # 시장보다 잘 갔으므로 양수


def test_relative_strength_missing_benchmark():
    """벤치마크가 없으면 None을 돌려줘야 함 (크래시 금지)"""
    weekly = md.to_weekly(trending_daily(900, 0.003))
    assert md.relative_strength(weekly, None) is None


def test_period_return_insufficient_data():
    """데이터가 짧으면 None을 돌려줘야 함"""
    weekly = pd.DataFrame(
        {"Close": [100.0, 101.0]},
        index=pd.date_range("2025-01-03", periods=2, freq="W-FRI"),
    )
    assert md.period_return_pct(weekly, 13) is None


# ---------------------------------------------------------------------------
# 전체 종목 분석
# ---------------------------------------------------------------------------
def test_analyze_prices_end_to_end():
    """여러 종목 + 벤치마크를 넣으면 각 종목의 지표가 나와야 함"""
    daily_map = {
        "UP": trending_daily(900, 0.004),
        "DOWN": trending_daily(900, -0.003, start_price=100.0),
        cfg.BENCHMARK: trending_daily(900, 0.001),
    }
    result_map, weekly_map = md.analyze_prices(daily_map, tickers=["UP", "DOWN"])

    assert set(result_map) == {"UP", "DOWN"}
    assert result_map["UP"]["state"] == cfg.S_FULL_UP
    assert result_map["DOWN"]["state"] == cfg.S_FULL_DOWN

    # 빠르게 오른 종목의 RS는 양수, 내린 종목은 음수
    assert result_map["UP"]["rs"] > 0, result_map["UP"]["rs"]
    assert result_map["DOWN"]["rs"] < 0, result_map["DOWN"]["rs"]

    # 차트용 주봉에 이동평균선 4개가 모두 있어야 함
    assert all(f"MA{w}" in weekly_map["UP"].columns for w in cfg.MA_WINDOWS)


def test_analyze_prices_without_benchmark():
    """벤치마크 데이터가 없어도 RS만 None이고 나머지는 정상 계산돼야 함"""
    daily_map = {"UP": trending_daily(900, 0.004)}
    result_map, _ = md.analyze_prices(daily_map, tickers=["UP"])

    assert result_map["UP"]["state"] == cfg.S_FULL_UP
    assert result_map["UP"]["rs"] is None


def test_analyze_prices_skips_missing_ticker():
    """데이터가 없는 종목은 조용히 건너뛰어야 함"""
    daily_map = {"UP": trending_daily(900, 0.004)}
    result_map, _ = md.analyze_prices(daily_map, tickers=["UP", "MISSING"])
    assert "MISSING" not in result_map


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
    print(f"\n주가 테스트: {passed}개 통과, {failed}개 실패")
    sys.exit(1 if failed else 0)
