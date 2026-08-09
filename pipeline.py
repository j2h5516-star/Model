"""
pipeline.py — 전체 자동 파이프라인
==================================

버튼 한 번(또는 앱 실행)으로 아래 과정을 전부 자동으로 수행합니다:

  1) yfinance에서 주가 내려받기 → 주봉·이동평균선·추세·상대강도 계산
  2) SEC에서 각 종목의 8-K 실적발표 수집 → 논갭 영업이익·GM% 추출
  3) 다음 분기 전망(가이던스 또는 추정) 계산
  4) 펀더멘털·기술 점수 계산 → 최종 판정

수동으로 입력할 값은 하나도 없습니다.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import pandas as pd

import config as cfg
import forward_estimates as fe
import market_data as md
import scoring
import sec_fundamentals as sf


def collect_one_ticker(ticker: str, use_cache: bool = True) -> dict:
    """한 종목의 실적과 전망을 모읍니다 (여러 종목을 동시에 처리하기 위해 분리)."""
    try:
        quarters = sf.get_fundamentals(ticker, use_cache=use_cache)
    except Exception:
        quarters = []

    try:
        forward = fe.estimate_forward(ticker, quarters)
    except Exception:
        forward = {
            "forward_op_income": None,
            "basis": None,
            "revision": 0,
            "detail": "전망 계산 중 문제가 발생했습니다",
        }

    return {"ticker": ticker, "quarters": quarters, "forward": forward}


def run_pipeline(
    tickers: list[str] | None = None,
    include_current_week: bool = True,
    use_cache: bool = True,
    max_workers: int = 4,
    fundamentals_timeout_sec: float = 300.0,
) -> dict:
    """전체 파이프라인을 실행하고 결과를 한 덩어리로 돌려줍니다.

    반환:
      ranking     순위 표 (최종점수 내림차순 DataFrame)
      scores      {티커: 점수 상세}
      weekly_map  {티커: 주봉 표} — 차트용
      failed      데이터를 받지 못한 종목
      updated_at  실행 시각
    """
    if tickers is None:
        tickers = cfg.TICKERS

    # --- 1) 주가 ---
    daily_map, failed_prices = md.fetch_daily_data(tickers)
    price_map, weekly_map = md.analyze_prices(
        daily_map, tickers=tickers, include_current_week=include_current_week
    )

    # --- 2~3) 실적 + 전망 (종목별로 동시에 처리해 시간을 줄입니다) ---
    # SEC 서버가 느리거나 막혀 있어도 앱이 무한정 멈추지 않도록 전체 시간 제한을 둡니다.
    # 제한 시간을 넘긴 종목은 "실적 없음"으로 처리되고 주가 지표만 반영됩니다.
    def _empty(ticker: str) -> dict:
        return {
            "ticker": ticker,
            "quarters": [],
            "forward": {
                "forward_op_income": None,
                "basis": None,
                "revision": 0,
                "detail": "실적 데이터를 가져오지 못했습니다",
            },
        }

    fundamentals: dict[str, dict] = {}
    pool = ThreadPoolExecutor(max_workers=max_workers)
    try:
        futures = {
            pool.submit(collect_one_ticker, ticker, use_cache): ticker for ticker in tickers
        }
        try:
            for future in as_completed(futures, timeout=fundamentals_timeout_sec):
                ticker = futures[future]
                try:
                    fundamentals[ticker] = future.result()
                except Exception:
                    fundamentals[ticker] = _empty(ticker)
        except TimeoutError:
            pass  # 시간이 다 됐으면 여기까지 받은 것만 사용합니다

        # 아직 끝나지 않은 종목은 빈 값으로 채웁니다
        for ticker in tickers:
            fundamentals.setdefault(ticker, _empty(ticker))
    finally:
        # 남은 작업이 끝나기를 기다리지 않고 즉시 진행합니다
        pool.shutdown(wait=False, cancel_futures=True)

    # --- 4) 점수 계산 ---
    scores: dict[str, dict] = {}
    for ticker in tickers:
        price_info = price_map.get(ticker)
        if price_info is None:
            continue  # 주가를 못 받은 종목은 제외
        bundle = fundamentals.get(ticker, {"quarters": [], "forward": {}})
        scores[ticker] = scoring.build_score(
            ticker, bundle["quarters"], bundle["forward"], price_info
        )

    ranking = build_ranking_table(scores)

    return {
        "ranking": ranking,
        "scores": scores,
        "weekly_map": weekly_map,
        "failed": [t for t in failed_prices if t != cfg.BENCHMARK],
        "no_fundamentals": [t for t in scores if not scores[t]["quarters"]],
        "updated_at": datetime.now(),
    }


def build_ranking_table(scores: dict[str, dict]) -> pd.DataFrame:
    """점수 딕셔너리를 화면에 보여줄 순위 표로 바꿉니다."""
    rows = []
    for score in scores.values():
        rows.append(
            {
                "종목": score["ticker"],
                "최종점수": score["final_score"],
                "펀더": score["fund_score"],
                "기술": score["tech_score"],
                "추세상태": score["trend_state"],
                "GM%드라이버": score["gm_type"],
                "델타방향": {"가속": "가속 ↗", "감속": "감속 ↘", "유지": "유지 →"}.get(
                    score["delta_direction"], "판단불가"
                ),
                "RS": score["rs"],
                "판정": score["verdict"],
            }
        )

    df = pd.DataFrame(rows)
    if df.empty:
        return df

    # 매수 후보는 상대강도(RS)가 높은 순으로 위에 오도록,
    # 그 외에는 최종점수 순으로 정렬합니다.
    df["_buy"] = (df["판정"] == cfg.V_BUY).astype(int)
    df["_rs"] = df["RS"].fillna(-999)
    df = df.sort_values(
        by=["_buy", "_rs", "최종점수"], ascending=[False, False, False]
    ).drop(columns=["_buy", "_rs"])
    return df.reset_index(drop=True)


def quarters_to_frame(quarters: list[dict]) -> pd.DataFrame:
    """분기 실적 목록을 차트용 표로 바꿉니다 (QoQ 증가율 포함)."""
    if not quarters:
        return pd.DataFrame()

    df = pd.DataFrame(quarters)
    keep = [c for c in ("period_label", "filing_date", "revenue", "op_income",
                        "gross_margin_pct", "source") if c in df.columns]
    df = df[keep].copy()

    # QoQ 증가율(%) — 이익이 얼마나 빠르게 늘고 있는지
    if "op_income" in df.columns:
        df["qoq_pct"] = df["op_income"].pct_change() * 100.0
        # 직전 분기가 적자면 증가율이 의미 없으므로 비웁니다
        prev = df["op_income"].shift(1)
        df.loc[prev <= 0, "qoq_pct"] = None

    return df
