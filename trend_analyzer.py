"""
trend_analyzer.py — 주가 데이터 수집 및 추세 판정 모듈
=====================================================

이 파일은 대시보드의 "두뇌" 역할을 합니다.
  1) yfinance로 일봉(하루 단위) 주가를 내려받고
  2) 주봉(일주일 단위)으로 변환한 뒤
  3) 4주/13주/26주/52주 이동평균선을 계산하고
  4) 종목별 추세 상태를 5단계로 판정합니다.

화면 표시는 app.py 가 담당하고, 계산은 모두 이 파일에서 합니다.
"""

from __future__ import annotations

import pandas as pd
import yfinance as yf

# ---------------------------------------------------------------------------
# 기본 설정값
# ---------------------------------------------------------------------------

# 분석 대상 종목 리스트 (티커 = 미국 주식 시장에서 쓰는 종목 코드)
TICKERS = [
    "CRDO",  # Credo Technology
    "CLS",   # Celestica
    "MU",    # Micron Technology
    "SNDK",  # SanDisk
    "LITE",  # Lumentum
    "STRL",  # Sterling Infrastructure
    "MXL",   # MaxLinear
    "COHR",  # Coherent
    "TTMI",  # TTM Technologies
    "ICHR",  # Ichor Holdings
    "AMD",   # Advanced Micro Devices
    "PLTR",  # Palantir
    "ZETA",  # Zeta Global
]

# 이동평균선 기간 (주 단위)
MA_WINDOWS = [4, 13, 26, 52]

# "중립" 판정 기준: 주가가 26주선에서 위아래 ±3% 이내면 "26주선 부근 공방"으로 봅니다.
NEUTRAL_BAND_PCT = 3.0

# 26주선 기울기를 잴 때 몇 주 전과 비교할지 (4주 전과 비교)
SLOPE_LOOKBACK_WEEKS = 4

# 기울기 변화율이 이 값(%) 이내면 "횡보"로 표시합니다.
SLOPE_FLAT_BAND_PCT = 0.3

# ---------------------------------------------------------------------------
# 추세 상태 이름 (5단계 + 데이터 부족 시 예외 1개)
# ---------------------------------------------------------------------------
S_FULL_UP = "완전 정배열"    # 주가 > 4주 > 13주 > 26주 > 52주 : 가장 강한 상승 추세
S_SEMI_UP = "준정배열"      # 단기 조정은 있으나 중기 상승 구조(주가 > 26주 > 52주)는 유지
S_NEUTRAL = "중립"          # 주가가 26주선 부근에서 공방 중
S_DAMAGED = "추세 훼손"     # 주가가 26주선 아래로 이탈했거나 13주선이 26주선 아래로 꺾임
S_FULL_DOWN = "완전 역배열"  # 4주 < 13주 < 26주 < 52주 : 가장 약한 하락 추세
S_UNKNOWN = "판정 불가"     # 상장한 지 얼마 안 돼 52주치 데이터가 부족한 경우

# 요약 테이블 정렬 순서 (좋은 상태가 위로 오도록)
STATE_SORT_ORDER = {
    S_FULL_UP: 0,
    S_SEMI_UP: 1,
    S_NEUTRAL: 2,
    S_DAMAGED: 3,
    S_FULL_DOWN: 4,
    S_UNKNOWN: 5,
}


# ---------------------------------------------------------------------------
# 1단계: 데이터 내려받기
# ---------------------------------------------------------------------------
def fetch_daily_data(
    tickers: list[str] | None = None,
    period: str = "5y",
) -> tuple[dict[str, pd.DataFrame], list[str]]:
    """야후 파이낸스에서 여러 종목의 일봉 데이터를 한 번에 내려받습니다.

    period="5y" : 최근 5년치를 가져옵니다.
                  (52주 이동평균선을 계산하려면 최소 1년 이상 필요하고,
                   차트로 과거 흐름도 보려면 넉넉히 받아두는 것이 좋습니다)

    반환값:
      - data_map : {티커: 일봉 DataFrame} 딕셔너리 (성공한 종목만)
      - failed   : 데이터를 받지 못한 티커 리스트
    """
    if tickers is None:
        tickers = TICKERS

    data_map: dict[str, pd.DataFrame] = {}
    failed: list[str] = []

    try:
        # 여러 종목을 한 번의 요청으로 내려받아 속도를 높입니다.
        # auto_adjust=True : 액면분할/배당을 반영한 수정주가를 사용합니다.
        #                    (이동평균선 계산에는 수정주가가 정확합니다)
        raw = yf.download(
            tickers,
            period=period,
            interval="1d",          # 일봉
            auto_adjust=True,
            group_by="ticker",      # 종목별로 컬럼을 묶어서 받기
            progress=False,
            threads=True,
        )
    except Exception:
        # 인터넷이 끊겼거나 야후 서버 문제 등으로 전체 요청이 실패한 경우
        return {}, list(tickers)

    if raw is None or raw.empty:
        return {}, list(tickers)

    for ticker in tickers:
        try:
            # 종목별 데이터 꺼내기 (없는 종목이면 KeyError 발생)
            df = raw[ticker].copy()
        except KeyError:
            failed.append(ticker)
            continue

        df = _normalize_daily(df)
        if df.empty:
            failed.append(ticker)
        else:
            data_map[ticker] = df

    return data_map, failed


def _normalize_daily(df: pd.DataFrame) -> pd.DataFrame:
    """내려받은 일봉 데이터를 정리합니다.

    - 필요한 컬럼(Open/High/Low/Close/Volume)만 남기고
    - 값이 전부 비어 있는 날(휴장일 등)을 제거하고
    - 시간대(timezone) 정보를 제거해 날짜만 남깁니다.
    """
    # 'Close'가 없고 'Adj Close'만 있는 경우를 대비한 안전장치
    if "Close" not in df.columns and "Adj Close" in df.columns:
        df = df.rename(columns={"Adj Close": "Close"})

    needed = ["Open", "High", "Low", "Close", "Volume"]
    if not all(col in df.columns for col in needed):
        return pd.DataFrame()  # 필요한 컬럼이 없으면 빈 데이터 반환

    df = df[needed]

    # 시간대 정보가 붙어 있으면 제거 (주봉 변환 시 날짜 계산을 단순하게)
    if isinstance(df.index, pd.DatetimeIndex) and df.index.tz is not None:
        df.index = df.index.tz_localize(None)

    # 종가가 비어 있는 행(데이터 없는 날)은 제거
    df = df.dropna(subset=["Close"])
    return df


# ---------------------------------------------------------------------------
# 2단계: 주봉 변환
# ---------------------------------------------------------------------------
def to_weekly(daily: pd.DataFrame) -> pd.DataFrame:
    """일봉 데이터를 주봉으로 변환합니다.

    "W-FRI" = 금요일을 한 주의 마지막 날로 삼는다는 뜻입니다.
    (미국 주식 시장은 월~금 열리므로 금요일 종가가 그 주의 주봉 종가가 됩니다)

    한 주의 주봉은 이렇게 만들어집니다:
      시가(Open)  = 그 주 첫 거래일의 시가
      고가(High)  = 그 주 중 가장 높았던 가격
      저가(Low)   = 그 주 중 가장 낮았던 가격
      종가(Close) = 그 주 마지막 거래일의 종가
      거래량(Volume) = 그 주 거래량의 합
    """
    weekly = daily.resample("W-FRI").agg(
        {
            "Open": "first",
            "High": "max",
            "Low": "min",
            "Close": "last",
            "Volume": "sum",
        }
    )
    # 거래가 아예 없었던 주(연휴 등)는 제거
    weekly = weekly.dropna(subset=["Close"])
    return weekly


def is_last_week_incomplete(daily: pd.DataFrame, weekly: pd.DataFrame) -> bool:
    """마지막 주봉이 아직 '진행 중'(금요일 장 마감 전)인지 확인합니다.

    주봉의 날짜 라벨은 그 주 금요일 날짜입니다.
    실제 마지막 거래일이 그 금요일보다 앞서 있으면 아직 주가 끝나지 않은 것입니다.
    """
    if weekly.empty or daily.empty:
        return False
    last_trading_day = daily.index[-1]
    last_week_label = weekly.index[-1]
    return last_trading_day < last_week_label


# ---------------------------------------------------------------------------
# 3단계: 이동평균선 계산
# ---------------------------------------------------------------------------
def add_moving_averages(weekly: pd.DataFrame) -> pd.DataFrame:
    """주봉 종가 기준으로 4/13/26/52주 이동평균선을 계산해 컬럼으로 추가합니다.

    예) MA4 = 최근 4주 종가의 평균. 데이터가 4주보다 적으면 값이 비어 있게 됩니다.
    """
    out = weekly.copy()
    for window in MA_WINDOWS:
        out[f"MA{window}"] = out["Close"].rolling(window=window).mean()
    return out


# ---------------------------------------------------------------------------
# 4단계: 추세 판정
# ---------------------------------------------------------------------------
def classify_trend(weekly_ma: pd.DataFrame) -> dict:
    """이동평균선이 계산된 주봉 데이터의 '가장 최근 주'를 보고 추세를 판정합니다.

    판정은 아래 순서(우선순위)대로 검사합니다. 먼저 걸리는 조건이 최종 판정입니다.
      1. 데이터 부족           → 판정 불가
      2. 주가 > 4주 > 13주 > 26주 > 52주 → 완전 정배열
      3. 4주 < 13주 < 26주 < 52주        → 완전 역배열
      4. 주가가 26주선 ±3% 이내           → 중립 (26주선 부근 공방)
      5. 주가 < 26주선 또는 13주선 < 26주선 → 추세 훼손
      6. 주가 > 26주 > 52주 (단기 조정 중) → 준정배열
      7. 그 외 애매한 경우                → 중립

    반환값(딕셔너리):
      state      : 추세 상태 (5단계 중 하나)
      reason     : 판정 근거를 사람이 읽을 수 있는 문장으로
      slope      : 26주선 기울기 ("상승" / "하락" / "횡보")
      slope_pct  : 26주선이 4주 전 대비 몇 % 움직였는지
      disparity  : 이격도 = 주가가 26주선보다 몇 % 위/아래에 있는지
      close      : 최근 주봉 종가
      ma4~ma52   : 각 이동평균선의 최근 값
    """
    row = weekly_ma.iloc[-1]  # 가장 최근 주봉
    close = float(row["Close"])

    # 이동평균선 값 꺼내기 (데이터가 부족하면 NaN = 비어 있음)
    ma4 = float(row["MA4"]) if pd.notna(row["MA4"]) else None
    ma13 = float(row["MA13"]) if pd.notna(row["MA13"]) else None
    ma26 = float(row["MA26"]) if pd.notna(row["MA26"]) else None
    ma52 = float(row["MA52"]) if pd.notna(row["MA52"]) else None

    # --- 보조 신호 1: 26주선 대비 이격도(%) ---
    # 이격도 = (주가 / 26주선 - 1) × 100
    # 예) +10% 면 주가가 26주선보다 10% 위에 있다는 뜻
    disparity = None
    if ma26 is not None and ma26 > 0:
        disparity = (close / ma26 - 1.0) * 100.0

    # --- 보조 신호 2: 26주선 기울기 ---
    # 4주 전의 26주선 값과 비교해서 올라가는 중인지 내려가는 중인지 확인
    slope, slope_pct = _ma26_slope(weekly_ma)

    # --- 5단계 판정 시작 ---
    if any(v is None for v in (ma4, ma13, ma26, ma52)):
        # 1) 52주 이동평균선을 계산할 만큼 데이터가 쌓이지 않은 종목 (신규 상장 등)
        state = S_UNKNOWN
        reason = "상장 기간이 짧아 52주 이동평균선을 계산할 수 없습니다"
    elif close > ma4 > ma13 > ma26 > ma52:
        # 2) 모든 선이 완벽한 오름차순 = 가장 이상적인 상승 추세
        state = S_FULL_UP
        reason = "주가 > 4주 > 13주 > 26주 > 52주 순서가 완벽하게 유지되고 있습니다"
    elif ma4 < ma13 < ma26 < ma52:
        # 3) 모든 선이 완벽한 내림차순 = 가장 약한 하락 추세
        state = S_FULL_DOWN
        reason = "4주 < 13주 < 26주 < 52주 순서로 완전히 뒤집혀 있습니다"
    elif disparity is not None and abs(disparity) <= NEUTRAL_BAND_PCT:
        # 4) 주가가 26주선 바로 근처(±3%)에서 오르내리는 중 = 방향 탐색 구간
        state = S_NEUTRAL
        reason = f"주가가 26주선 ±{NEUTRAL_BAND_PCT:.0f}% 이내({disparity:+.1f}%)에서 공방 중입니다"
    elif close < ma26 or ma13 < ma26:
        # 5) 주가가 26주선 아래로 떨어졌거나, 13주선이 26주선 아래로 꺾임(데드크로스)
        if close < ma26 and ma13 < ma26:
            reason = "주가가 26주선 아래에 있고 13주선도 26주선을 하향 돌파했습니다"
        elif close < ma26:
            reason = "주가가 26주선 아래로 내려왔습니다"
        else:
            reason = "13주선이 26주선을 하향 돌파했습니다(데드크로스)"
        state = S_DAMAGED
    elif close > ma26 > ma52:
        # 6) 단기(4주선)는 흔들리지만 중기 상승 구조는 살아 있음
        state = S_SEMI_UP
        reason = "단기 조정 중이지만 주가 > 26주 > 52주의 상승 구조는 유지되고 있습니다"
    else:
        # 7) 위 어디에도 딱 맞지 않는 애매한 구간 (예: 26주선이 52주선 아래인 회복 초기)
        state = S_NEUTRAL
        reason = "뚜렷한 방향 없이 추세 전환을 탐색하는 구간입니다"

    return {
        "state": state,
        "reason": reason,
        "slope": slope,
        "slope_pct": slope_pct,
        "disparity": disparity,
        "close": close,
        "ma4": ma4,
        "ma13": ma13,
        "ma26": ma26,
        "ma52": ma52,
    }


def _ma26_slope(weekly_ma: pd.DataFrame) -> tuple[str, float | None]:
    """26주선의 기울기를 계산합니다.

    현재 26주선 값을 4주 전 값과 비교해서:
      +0.3% 넘게 올랐으면 "상승", -0.3% 넘게 내렸으면 "하락", 그 사이면 "횡보"
    """
    ma26_series = weekly_ma["MA26"].dropna()
    if len(ma26_series) < SLOPE_LOOKBACK_WEEKS + 1:
        return "-", None  # 비교할 과거 값이 없으면 표시 생략

    now = float(ma26_series.iloc[-1])
    past = float(ma26_series.iloc[-1 - SLOPE_LOOKBACK_WEEKS])
    if past <= 0:
        return "-", None

    change_pct = (now / past - 1.0) * 100.0
    if change_pct > SLOPE_FLAT_BAND_PCT:
        return "상승", change_pct
    elif change_pct < -SLOPE_FLAT_BAND_PCT:
        return "하락", change_pct
    else:
        return "횡보", change_pct


# ---------------------------------------------------------------------------
# 종합: 전체 종목 분석
# ---------------------------------------------------------------------------
def analyze_all(
    daily_map: dict[str, pd.DataFrame],
    include_current_week: bool = True,
) -> tuple[pd.DataFrame, dict[str, pd.DataFrame]]:
    """모든 종목을 분석해 요약 테이블과 차트용 데이터를 만듭니다.

    include_current_week=False 로 하면 아직 끝나지 않은 이번 주 주봉을
    빼고 계산합니다. (지난 금요일 종가 기준으로 보고 싶을 때 사용)

    반환값:
      - summary  : 요약 테이블 (종목별 한 줄씩)
      - chart_map: {티커: 이동평균선 포함 주봉 DataFrame} — 차트 그릴 때 사용
    """
    rows = []
    chart_map: dict[str, pd.DataFrame] = {}

    for ticker, daily in daily_map.items():
        weekly = to_weekly(daily)

        # 진행 중인 주 제외 옵션 처리
        if not include_current_week and is_last_week_incomplete(daily, weekly):
            weekly = weekly.iloc[:-1]

        if weekly.empty:
            continue

        weekly_ma = add_moving_averages(weekly)
        chart_map[ticker] = weekly_ma

        result = classify_trend(weekly_ma)

        # 주간 등락률: 이번 주 종가가 지난주 종가 대비 몇 % 움직였는지
        week_change = None
        if len(weekly_ma) >= 2:
            prev_close = float(weekly_ma["Close"].iloc[-2])
            if prev_close > 0:
                week_change = (result["close"] / prev_close - 1.0) * 100.0

        rows.append(
            {
                "종목": ticker,
                "현재가($)": result["close"],
                "주간 등락(%)": week_change,
                "추세 판정": result["state"],
                "26주선 기울기": result["slope"],
                "이격도(%)": result["disparity"],
                "판정 근거": result["reason"],
            }
        )

    summary = pd.DataFrame(rows)
    if not summary.empty:
        # 좋은 추세(정배열)가 위로 오도록 정렬하고, 같은 상태끼리는 이격도 큰 순
        summary["_순서"] = summary["추세 판정"].map(STATE_SORT_ORDER)
        summary = summary.sort_values(
            by=["_순서", "이격도(%)"], ascending=[True, False]
        ).drop(columns=["_순서"])
        summary = summary.reset_index(drop=True)

    return summary, chart_map
