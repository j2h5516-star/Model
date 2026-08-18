"""
market_data.py — 주가 수집 · 주봉 변환 · 추세 판정 · 상대강도(RS)
================================================================

이 파일이 하는 일:
  1) yfinance로 일봉(하루 단위) 주가를 내려받고
  2) 주봉(일주일 단위)으로 바꾼 뒤
  3) 4/13/26/52주 이동평균선을 계산하고
  4) 추세를 5단계로 판정하고
  5) SPY(S&P500 ETF) 대비 얼마나 잘 갔는지(상대강도 RS)를 계산합니다.
"""

from __future__ import annotations

import pandas as pd
import yfinance as yf

import config as cfg

# config에서 자주 쓰는 값들을 그대로 가져옵니다 (다른 파일에서 편하게 쓰기 위해)
S_FULL_UP = cfg.S_FULL_UP
S_SEMI_UP = cfg.S_SEMI_UP
S_NEUTRAL = cfg.S_NEUTRAL
S_DAMAGED = cfg.S_DAMAGED
S_FULL_DOWN = cfg.S_FULL_DOWN
S_UNKNOWN = cfg.S_UNKNOWN


# ---------------------------------------------------------------------------
# 1단계: 데이터 내려받기
# ---------------------------------------------------------------------------
def fetch_daily_data(
    tickers: list[str] | None = None,
    # 10년 (2026-08-18, 5y → 10y). 실적 수집을 10년으로 늘렸으므로
    # (config.HISTORY_START_DATE) 그 시절 발표에도 진입가가 있어야 합니다.
    # 야후는 **일봉**을 10년 넘게 줍니다 — 5년 제한은 분·시간봉 이야기지
    # 일봉에는 걸리지 않습니다. 5y 였던 것은 야후의 한계가 아니라 그냥
    # 우리가 그렇게 적어 둔 기본값이었습니다.
    # 상장한 지 10년이 안 된 종목은 있는 만큼만 옵니다 (없는 값은 없는 채로).
    period: str = "10y",
) -> tuple[dict[str, pd.DataFrame], list[str]]:
    """야후 파이낸스에서 여러 종목의 일봉 데이터를 한 번에 내려받습니다.

    벤치마크(SPY)도 함께 받아옵니다. RS 계산에 필요하기 때문입니다.

    반환값:
      - data_map : {티커: 일봉 표} — 성공한 종목만
      - failed   : 데이터를 받지 못한 티커 목록
    """
    if tickers is None:
        tickers = cfg.TICKERS

    # 벤치마크가 목록에 없으면 추가 (RS 계산용)
    request_list = list(tickers)
    if cfg.BENCHMARK not in request_list:
        request_list.append(cfg.BENCHMARK)

    data_map: dict[str, pd.DataFrame] = {}
    failed: list[str] = []

    try:
        raw = yf.download(
            request_list,
            period=period,
            interval="1d",
            auto_adjust=True,      # 액면분할·배당 반영된 수정주가 사용
            group_by="ticker",
            progress=False,
            threads=True,
        )
    except Exception:
        # 인터넷 문제 등으로 전체 요청이 실패한 경우
        return {}, list(request_list)

    if raw is None or raw.empty:
        return {}, list(request_list)

    for ticker in request_list:
        try:
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
    """내려받은 일봉 표를 정리합니다 (필요한 칸만 남기고, 빈 날 제거)."""
    if "Close" not in df.columns and "Adj Close" in df.columns:
        df = df.rename(columns={"Adj Close": "Close"})

    needed = ["Open", "High", "Low", "Close", "Volume"]
    if not all(col in df.columns for col in needed):
        return pd.DataFrame()

    df = df[needed]

    # 시간대 정보 제거 (날짜 계산을 단순하게)
    if isinstance(df.index, pd.DatetimeIndex) and df.index.tz is not None:
        df.index = df.index.tz_localize(None)

    return df.dropna(subset=["Close"])


# ---------------------------------------------------------------------------
# 2단계: 주봉 변환
# ---------------------------------------------------------------------------
def to_weekly(daily: pd.DataFrame) -> pd.DataFrame:
    """일봉을 주봉으로 바꿉니다 (금요일이 한 주의 마지막 날).

    한 주의 주봉:
      시가 = 그 주 첫 거래일 시가 / 고가 = 주중 최고 / 저가 = 주중 최저
      종가 = 그 주 마지막 거래일 종가 / 거래량 = 그 주 합계
    """
    weekly = daily.resample("W-FRI").agg(
        {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}
    )
    return weekly.dropna(subset=["Close"])


def is_last_week_incomplete(
    weekly: pd.DataFrame,
    now: pd.Timestamp | None = None,
) -> bool:
    """마지막 주봉이 아직 '진행 중'인지 (그 주 금요일 장 마감 전인지) 확인합니다.

    미국 동부시간 금요일 오후 4시(정규장 마감)를 기준으로 삼습니다.
    마지막 거래일과 비교하지 않는 이유: 금요일이 휴장인 주(굿프라이데이 등)를
    계속 '진행 중'으로 잘못 판단하기 때문입니다.
    """
    if weekly.empty:
        return False
    if now is None:
        now = pd.Timestamp.now(tz="America/New_York")
    friday_close = weekly.index[-1].tz_localize("America/New_York") + pd.Timedelta(hours=16)
    return now < friday_close


# ---------------------------------------------------------------------------
# 3단계: 이동평균선
# ---------------------------------------------------------------------------
def add_moving_averages(weekly: pd.DataFrame) -> pd.DataFrame:
    """주봉 종가로 4/13/26/52주 이동평균선을 계산해 칸을 추가합니다."""
    out = weekly.copy()
    for window in cfg.MA_WINDOWS:
        out[f"MA{window}"] = out["Close"].rolling(window=window).mean()
    return out


# ---------------------------------------------------------------------------
# 4단계: 추세 판정 (5단계)
# ---------------------------------------------------------------------------
def classify_trend(weekly_ma: pd.DataFrame) -> dict:
    """가장 최근 주를 보고 추세를 5단계로 판정합니다.

    검사 순서 (먼저 걸리는 조건이 최종 판정):
      1. 데이터 부족                        → 판정 불가
      2. 주가 > 4주 > 13주 > 26주 > 52주    → 완전 정배열
      3. 4주 < 13주 < 26주 < 52주           → 완전 역배열
      4. 주가가 26주선 ±3% 이내             → 중립
      5. 주가 < 26주선 또는 13주선 < 26주선 → 추세 훼손
      6. 주가 > 26주 > 52주                 → 준정배열
      7. 그 외                               → 중립
    """
    row = weekly_ma.iloc[-1]
    close = float(row["Close"])

    ma4 = float(row["MA4"]) if pd.notna(row["MA4"]) else None
    ma13 = float(row["MA13"]) if pd.notna(row["MA13"]) else None
    ma26 = float(row["MA26"]) if pd.notna(row["MA26"]) else None
    ma52 = float(row["MA52"]) if pd.notna(row["MA52"]) else None

    # 이격도(%) = 주가가 26주선보다 몇 % 위/아래인지
    disparity = None
    if ma26 is not None and ma26 > 0:
        disparity = (close / ma26 - 1.0) * 100.0

    slope, slope_pct = _ma26_slope(weekly_ma)

    if any(v is None for v in (ma4, ma13, ma26, ma52)):
        state = S_UNKNOWN
        reason = "상장 기간이 짧아 52주 이동평균선을 계산할 수 없습니다"
    elif close > ma4 > ma13 > ma26 > ma52:
        state = S_FULL_UP
        reason = "주가 > 4주 > 13주 > 26주 > 52주 순서가 완벽하게 유지되고 있습니다"
    elif ma4 < ma13 < ma26 < ma52:
        state = S_FULL_DOWN
        reason = "4주 < 13주 < 26주 < 52주 순서로 완전히 뒤집혀 있습니다"
    elif disparity is not None and abs(disparity) <= cfg.NEUTRAL_BAND_PCT:
        state = S_NEUTRAL
        reason = (
            f"주가가 26주선 ±{cfg.NEUTRAL_BAND_PCT:.0f}% 이내"
            f"({disparity:+.1f}%)에서 공방 중입니다"
        )
    elif close < ma26 or ma13 < ma26:
        if close < ma26 and ma13 < ma26:
            reason = "주가가 26주선 아래에 있고 13주선도 26주선을 하향 돌파했습니다"
        elif close < ma26:
            reason = "주가가 26주선 아래로 내려왔습니다"
        else:
            reason = "13주선이 26주선을 하향 돌파했습니다(데드크로스)"
        state = S_DAMAGED
    elif close > ma26 > ma52:
        state = S_SEMI_UP
        reason = "단기 조정 중이지만 주가 > 26주 > 52주의 상승 구조는 유지되고 있습니다"
    else:
        state = S_NEUTRAL
        reason = "뚜렷한 방향 없이 추세 전환을 탐색하는 구간입니다"

    return {
        "state": state,
        "stage": cfg.TREND_STAGE[state],
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
    """26주선이 4주 전보다 올랐는지 내렸는지 판단합니다."""
    ma26_series = weekly_ma["MA26"].dropna()
    if len(ma26_series) < cfg.SLOPE_LOOKBACK_WEEKS + 1:
        return "-", None

    now = float(ma26_series.iloc[-1])
    past = float(ma26_series.iloc[-1 - cfg.SLOPE_LOOKBACK_WEEKS])
    if past <= 0:
        return "-", None

    change_pct = (now / past - 1.0) * 100.0
    if change_pct > cfg.SLOPE_FLAT_BAND_PCT:
        return "상승", change_pct
    if change_pct < -cfg.SLOPE_FLAT_BAND_PCT:
        return "하락", change_pct
    return "횡보", change_pct


# ---------------------------------------------------------------------------
# 5단계: 상대강도(RS) — 시장(SPY)보다 얼마나 잘 갔는가
# ---------------------------------------------------------------------------
def period_return_pct(weekly: pd.DataFrame, weeks: int) -> float | None:
    """최근 N주 동안의 수익률(%)을 계산합니다."""
    closes = weekly["Close"].dropna()
    if len(closes) < weeks + 1:
        return None
    past = float(closes.iloc[-1 - weeks])
    if past <= 0:
        return None
    return (float(closes.iloc[-1]) / past - 1.0) * 100.0


def relative_strength(
    weekly: pd.DataFrame,
    benchmark_weekly: pd.DataFrame | None,
    weeks: int | None = None,
) -> float | None:
    """상대강도(RS) = 내 종목 13주 수익률 − SPY 13주 수익률 (단위: %p)

    양수면 시장보다 잘 간 것, 음수면 시장보다 못 간 것입니다.
    """
    if weeks is None:
        weeks = cfg.RS_LOOKBACK_WEEKS
    if benchmark_weekly is None or benchmark_weekly.empty:
        return None

    mine = period_return_pct(weekly, weeks)
    market = period_return_pct(benchmark_weekly, weeks)
    if mine is None or market is None:
        return None
    return mine - market


# ---------------------------------------------------------------------------
# 종합: 전체 종목의 주가 지표 계산
# ---------------------------------------------------------------------------
def analyze_prices(
    daily_map: dict[str, pd.DataFrame],
    tickers: list[str] | None = None,
    include_current_week: bool = True,
) -> tuple[dict[str, dict], dict[str, pd.DataFrame]]:
    """모든 종목의 추세·기울기·이격도·RS를 계산합니다.

    반환값:
      - result_map : {티커: 지표 딕셔너리}
      - weekly_map : {티커: 이동평균선이 붙은 주봉 표} — 차트용
    """
    if tickers is None:
        tickers = [t for t in daily_map if t != cfg.BENCHMARK]

    # 먼저 벤치마크(SPY) 주봉을 만들어 둡니다
    benchmark_weekly = None
    if cfg.BENCHMARK in daily_map:
        bw = to_weekly(daily_map[cfg.BENCHMARK])
        if not include_current_week and is_last_week_incomplete(bw):
            bw = bw.iloc[:-1]
        benchmark_weekly = bw if not bw.empty else None

    result_map: dict[str, dict] = {}
    weekly_map: dict[str, pd.DataFrame] = {}

    for ticker in tickers:
        daily = daily_map.get(ticker)
        if daily is None or daily.empty:
            continue

        weekly = to_weekly(daily)
        if not include_current_week and is_last_week_incomplete(weekly):
            weekly = weekly.iloc[:-1]
        if weekly.empty:
            continue

        weekly_ma = add_moving_averages(weekly)
        weekly_map[ticker] = weekly_ma

        info = classify_trend(weekly_ma)
        info["rs"] = relative_strength(weekly_ma, benchmark_weekly)
        info["return_13w"] = period_return_pct(weekly_ma, cfg.RS_LOOKBACK_WEEKS)

        # 주간 등락률 (지난주 종가 대비)
        info["week_change"] = None
        if len(weekly_ma) >= 2:
            prev = float(weekly_ma["Close"].iloc[-2])
            if prev > 0:
                info["week_change"] = (info["close"] / prev - 1.0) * 100.0

        info["as_of"] = weekly_ma.index[-1]
        result_map[ticker] = info

    return result_map, weekly_map
