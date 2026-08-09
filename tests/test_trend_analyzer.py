"""
test_trend_analyzer.py — 추세 판정 로직 자동 검증 테스트
======================================================

인터넷 연결 없이, 미리 만들어 둔 가상의 주가 데이터로
trend_analyzer.py 의 계산이 정확한지 확인합니다.

실행 방법 (프로젝트 폴더에서):
    python tests/test_trend_analyzer.py
또는 pytest가 설치되어 있다면:
    pytest tests/
"""

import os
import sys

import numpy as np
import pandas as pd

# 프로젝트 최상위 폴더를 import 경로에 추가 (어디서 실행해도 동작하도록)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import trend_analyzer as ta  # noqa: E402  (경로 설정 뒤에 import)


# ---------------------------------------------------------------------------
# 테스트용 데이터 만들기 도우미
# ---------------------------------------------------------------------------
def make_daily(closes, start="2023-01-02"):
    """종가 리스트를 받아 가상의 일봉 DataFrame을 만듭니다.

    시가/고가/저가는 종가를 기준으로 적당히 만들어 붙입니다.
    날짜는 평일(월~금)만 사용합니다. 2023-01-02는 월요일입니다.
    """
    closes = np.asarray(closes, dtype=float)
    dates = pd.bdate_range(start=start, periods=len(closes))
    return pd.DataFrame(
        {
            "Open": closes * 0.99,
            "High": closes * 1.01,
            "Low": closes * 0.98,
            "Close": closes,
            "Volume": np.full(len(closes), 1000.0),
        },
        index=dates,
    )


def make_weekly_ma(close, ma4, ma13, ma26, ma52, ma26_history=None):
    """classify_trend()에 바로 넣을 수 있는 '이평선 포함 주봉' 한 조각을 만듭니다.

    ma26_history: 기울기 계산 검증용 과거 26주선 값 리스트 (마지막 값이 최신)
    """
    if ma26_history is None:
        # 기울기 계산에 필요한 최소 길이(5개)를 채우되 값은 평평하게
        ma26_history = [ma26 if ma26 is not None else np.nan] * 6

    n = len(ma26_history)
    dates = pd.date_range(end="2026-08-07", periods=n, freq="W-FRI")
    df = pd.DataFrame(
        {
            "Close": [np.nan] * (n - 1) + [close],
            "MA4": [np.nan] * (n - 1) + [ma4 if ma4 is not None else np.nan],
            "MA13": [np.nan] * (n - 1) + [ma13 if ma13 is not None else np.nan],
            "MA26": ma26_history,
            "MA52": [np.nan] * (n - 1) + [ma52 if ma52 is not None else np.nan],
        },
        index=dates,
    )
    return df


# ---------------------------------------------------------------------------
# 1) 추세 판정 5단계: 각 상태가 정확히 나오는지 검사
# ---------------------------------------------------------------------------
def test_full_up():
    """주가 > 4주 > 13주 > 26주 > 52주 → 완전 정배열"""
    df = make_weekly_ma(close=110, ma4=105, ma13=100, ma26=95, ma52=90)
    result = ta.classify_trend(df)
    assert result["state"] == ta.S_FULL_UP, result
    # 이격도 = (110/95 - 1) * 100 ≈ +15.8%
    assert abs(result["disparity"] - 15.789) < 0.01, result["disparity"]


def test_full_down():
    """4주 < 13주 < 26주 < 52주 → 완전 역배열"""
    df = make_weekly_ma(close=70, ma4=80, ma13=85, ma26=90, ma52=95)
    result = ta.classify_trend(df)
    assert result["state"] == ta.S_FULL_DOWN, result


def test_neutral_band():
    """주가가 26주선 ±3% 이내 → 중립 (완전 정배열이 아닌 상태에서)"""
    # ma4(101) > close(100) 이므로 완전 정배열은 아님. 이격도 +1.0% → 중립
    df = make_weekly_ma(close=100, ma4=101, ma13=100.5, ma26=99, ma52=90)
    result = ta.classify_trend(df)
    assert result["state"] == ta.S_NEUTRAL, result


def test_damaged_price_below_ma26():
    """주가가 26주선보다 3% 넘게 아래 → 추세 훼손"""
    # 이격도 = (90/95 - 1) * 100 ≈ -5.3% → 중립 밴드 밖 → 훼손
    df = make_weekly_ma(close=90, ma4=92, ma13=96, ma26=95, ma52=91)
    result = ta.classify_trend(df)
    assert result["state"] == ta.S_DAMAGED, result
    assert "26주선 아래" in result["reason"], result["reason"]


def test_damaged_dead_cross():
    """주가는 26주선 위지만 13주선이 26주선 아래로 꺾임(데드크로스) → 추세 훼손"""
    # 이격도 = (100/96 - 1) * 100 ≈ +4.2% (밴드 밖), ma13(95) < ma26(96)
    df = make_weekly_ma(close=100, ma4=97, ma13=95, ma26=96, ma52=94)
    result = ta.classify_trend(df)
    assert result["state"] == ta.S_DAMAGED, result
    assert "하향 돌파" in result["reason"], result["reason"]


def test_semi_up():
    """4주선이 13주선 아래로 내려왔지만 주가 > 26주 > 52주 유지 → 준정배열"""
    # ma4(100) < ma13(101), close(105) > ma26(98) > ma52(93), 이격도 +7.1%
    df = make_weekly_ma(close=105, ma4=100, ma13=101, ma26=98, ma52=93)
    result = ta.classify_trend(df)
    assert result["state"] == ta.S_SEMI_UP, result


def test_unknown_when_short_history():
    """52주선이 계산되지 않으면(신규 상장) → 판정 불가"""
    df = make_weekly_ma(close=100, ma4=98, ma13=95, ma26=90, ma52=None)
    result = ta.classify_trend(df)
    assert result["state"] == ta.S_UNKNOWN, result


def test_fallback_neutral():
    """어느 조건에도 안 걸리는 애매한 회복 초기 구간 → 중립"""
    # close(105) > ma26(100) 이지만 ma26(100) < ma52(101) → 준정배열 조건 미달
    df = make_weekly_ma(close=105, ma4=104, ma13=102, ma26=100, ma52=101)
    result = ta.classify_trend(df)
    assert result["state"] == ta.S_NEUTRAL, result


# ---------------------------------------------------------------------------
# 2) 26주선 기울기 판정 검사
# ---------------------------------------------------------------------------
def test_slope_up_down_flat():
    """26주선이 오르면 '상승', 내리면 '하락', 거의 그대로면 '횡보'"""
    up = make_weekly_ma(110, 105, 100, 95, 90, ma26_history=[90, 91, 92, 93, 94, 95])
    assert ta.classify_trend(up)["slope"] == "상승"

    down = make_weekly_ma(70, 80, 85, 90, 95, ma26_history=[95, 94, 93, 92, 91, 90])
    assert ta.classify_trend(down)["slope"] == "하락"

    flat = make_weekly_ma(100, 101, 100.5, 99, 90, ma26_history=[99.0, 99.0, 99.0, 99.0, 99.0, 99.0])
    assert ta.classify_trend(flat)["slope"] == "횡보"


# ---------------------------------------------------------------------------
# 3) 주봉 변환이 정확한지 검사
# ---------------------------------------------------------------------------
def test_weekly_resample():
    """일봉 10일치(2주) → 주봉 2개로 정확히 합쳐지는지 확인"""
    closes = [10, 11, 12, 13, 14, 20, 21, 22, 23, 24]  # 1주차, 2주차
    daily = make_daily(closes)  # 2023-01-02(월) 시작
    weekly = ta.to_weekly(daily)

    assert len(weekly) == 2, weekly

    # 1주차 검사: 시가=월요일 시가, 고가=주중 최고, 저가=주중 최저, 종가=금요일 종가
    w1 = weekly.iloc[0]
    assert abs(w1["Open"] - 10 * 0.99) < 1e-9   # 월요일 시가
    assert abs(w1["High"] - 14 * 1.01) < 1e-9   # 금요일 고가가 주중 최고
    assert abs(w1["Low"] - 10 * 0.98) < 1e-9    # 월요일 저가가 주중 최저
    assert abs(w1["Close"] - 14) < 1e-9         # 금요일 종가
    assert abs(w1["Volume"] - 5000) < 1e-9      # 거래량 합계 (1000 × 5일)

    # 주봉 날짜 라벨이 금요일인지 확인
    assert weekly.index[0].day_name() == "Friday"
    assert str(weekly.index[0].date()) == "2023-01-06"

    # 2주차 종가 = 둘째 주 금요일 종가
    assert abs(weekly.iloc[1]["Close"] - 24) < 1e-9


def test_incomplete_week_detection():
    """그 주 금요일 장 마감(미국 동부 16시) 전이면 '진행 중인 주'로 인식해야 함"""
    # 8일치 = 1주(월~금) + 둘째 주 월~수 → 마지막 주봉 라벨은 2023-01-13(금)
    daily = make_daily([10, 11, 12, 13, 14, 20, 21, 22])
    weekly = ta.to_weekly(daily)

    # 수요일 저녁 기준 → 아직 진행 중
    wed_night = pd.Timestamp("2023-01-11 20:00", tz="America/New_York")
    assert ta.is_last_week_incomplete(weekly, now=wed_night) is True

    # 금요일 장중(정오) 기준 → 아직 진행 중
    fri_noon = pd.Timestamp("2023-01-13 12:00", tz="America/New_York")
    assert ta.is_last_week_incomplete(weekly, now=fri_noon) is True

    # 금요일 장 마감 후 → 완성된 주
    fri_after_close = pd.Timestamp("2023-01-13 16:30", tz="America/New_York")
    assert ta.is_last_week_incomplete(weekly, now=fri_after_close) is False


def test_friday_holiday_week_completes():
    """금요일 휴장 주(굿프라이데이 등)도 주말부터는 '완성된 주'로 인식해야 함

    (예전 방식은 마지막 거래일과 비교했기 때문에 목요일에 이미 끝난 주를
     다음 주 월요일까지 계속 '진행 중'으로 잘못 판단하는 버그가 있었음)
    """
    # 2023-04-07(금)은 굿프라이데이 휴장.
    # 3/27(월)~3/31(금) 5일 + 4/3(월)~4/6(목) 4일 = 9일치
    daily = make_daily([10, 11, 12, 13, 14, 20, 21, 22, 23], start="2023-03-27")
    weekly = ta.to_weekly(daily)
    # 마지막 주봉 라벨은 2023-04-07(금)이지만 거래는 4/6(목)까지만 있음
    assert str(weekly.index[-1].date()) == "2023-04-07"

    # 토요일 아침 기준 → 이미 완성된 주로 인식해야 함 (버그 수정 검증)
    sat = pd.Timestamp("2023-04-08 09:00", tz="America/New_York")
    assert ta.is_last_week_incomplete(weekly, now=sat) is False


# ---------------------------------------------------------------------------
# 4) 실제 사용 흐름 그대로 (일봉 → 주봉 → 이평선 → 판정) 통합 검사
# ---------------------------------------------------------------------------
def test_end_to_end_uptrend():
    """3년 내내 꾸준히 오른 가상 종목 → 완전 정배열 + 26주선 상승"""
    n_days = 780  # 약 3년치 평일
    closes = 10.0 * (1.003 ** np.arange(n_days))  # 하루 +0.3%씩 상승
    daily_map = {"FAKEUP": make_daily(closes)}

    summary, chart_map = ta.analyze_all(daily_map, include_current_week=True)

    assert len(summary) == 1
    row = summary.iloc[0]
    assert row["종목"] == "FAKEUP"
    assert row["추세 판정"] == ta.S_FULL_UP, row
    assert row["26주선 기울기"] == "상승", row
    assert row["이격도(%)"] > 3.0, row  # 꾸준한 상승이면 이격도는 플러스

    # 차트용 데이터에 이평선 4개가 모두 있는지 확인
    assert all(col in chart_map["FAKEUP"].columns for col in ["MA4", "MA13", "MA26", "MA52"])


def test_end_to_end_downtrend():
    """3년 내내 꾸준히 내린 가상 종목 → 완전 역배열 + 26주선 하락"""
    n_days = 780
    closes = 100.0 * (0.997 ** np.arange(n_days))  # 하루 -0.3%씩 하락
    daily_map = {"FAKEDN": make_daily(closes)}

    summary, _ = ta.analyze_all(daily_map, include_current_week=True)
    row = summary.iloc[0]
    assert row["추세 판정"] == ta.S_FULL_DOWN, row
    assert row["26주선 기울기"] == "하락", row
    assert row["이격도(%)"] < -3.0, row


def test_end_to_end_short_history():
    """상장 30주밖에 안 된 가상 종목 → 판정 불가"""
    n_days = 150  # 약 30주
    closes = 20.0 * (1.002 ** np.arange(n_days))
    daily_map = {"FAKENEW": make_daily(closes)}

    summary, _ = ta.analyze_all(daily_map, include_current_week=True)
    assert summary.iloc[0]["추세 판정"] == ta.S_UNKNOWN


def test_summary_sort_order():
    """요약 테이블은 좋은 추세(정배열)가 위로 오도록 정렬되어야 함"""
    up = 10.0 * (1.003 ** np.arange(780))
    down = 100.0 * (0.997 ** np.arange(780))
    daily_map = {
        "FAKEDN": make_daily(down),  # 일부러 역배열을 먼저 넣어도
        "FAKEUP": make_daily(up),
    }
    summary, _ = ta.analyze_all(daily_map, include_current_week=True)
    # 정배열(FAKEUP)이 첫 줄에 와야 함
    assert summary.iloc[0]["종목"] == "FAKEUP"
    assert summary.iloc[-1]["종목"] == "FAKEDN"


# ---------------------------------------------------------------------------
# 직접 실행했을 때: 모든 테스트를 순서대로 돌리고 결과 출력
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    test_functions = [
        (name, fn)
        for name, fn in sorted(globals().items())
        if name.startswith("test_") and callable(fn)
    ]
    passed = 0
    failed = 0
    for name, fn in test_functions:
        try:
            fn()
            print(f"  ✅ {name}")
            passed += 1
        except AssertionError as e:
            print(f"  ❌ {name} — 실패: {e}")
            failed += 1
    print(f"\n결과: {passed}개 통과, {failed}개 실패")
    sys.exit(1 if failed else 0)
