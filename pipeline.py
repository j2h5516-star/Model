"""
pipeline.py — 전체 자동 파이프라인
==================================

버튼 한 번(또는 앱 실행)으로 아래 과정을 전부 자동으로 수행합니다:

  1) yfinance에서 주가 내려받기 → 주봉·이동평균선·추세·상대강도 계산
  2) SEC에서 각 종목의 분기 실적 수집
       · XBRL로 전체 분기 뼈대를 만들고 (빠진 4분기도 계산해 채움)
       · 8-K 보도자료에서 공식 논갭 수치를 뽑아 성공한 분기만 덮어씀
  3) 다음 분기 전망(가이던스 또는 추정) 계산
  4) 펀더멘털·기술 점수 계산 → 최종 판정

수동으로 입력할 값은 하나도 없습니다.

**종목 단위로 나뉘어 있는 이유**
  대시보드에서 종목을 하나 추가했을 때 나머지 종목까지 전부 다시 받으면
  1~3분이 걸립니다. 그래서 종목별로 따로 받을 수 있게 함수를 분리해 두고,
  app.py에서 종목마다 따로 캐시합니다. 추가한 종목만 몇 초면 끝납니다.
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


# ---------------------------------------------------------------------------
# 1) 종목 하나의 실적 + 전망 (종목 단위 캐시의 기본 단위)
# ---------------------------------------------------------------------------
def collect_one_ticker(ticker: str, use_cache: bool = True) -> dict:
    """한 종목의 실적과 전망을 모읍니다.

    반환: {"ticker", "quarters", "forward", "report"}
      report = 수집 과정 진단 기록 (어느 단계에서 끊겼는지 확인용)
    """
    try:
        quarters, report = sf.get_fundamentals(ticker, use_cache=use_cache)
    except Exception as exc:
        quarters = []
        report = sf.new_report(ticker)
        report["first_error"] = f"[수집] {type(exc).__name__}: {str(exc)[:180]}"

    try:
        forward = fe.estimate_forward(ticker, quarters)
    except Exception as exc:
        forward = {
            "forward_op_income": None,
            "basis": None,
            "revision": 0,
            "detail": "전망 계산 중 문제가 발생했습니다",
        }
        if not report.get("first_error"):
            report["first_error"] = f"[전망] {type(exc).__name__}: {str(exc)[:180]}"

    return {"ticker": ticker, "quarters": quarters, "forward": forward, "report": report}


def empty_bundle(ticker: str, reason: str = "") -> dict:
    """실적을 못 받은 종목의 빈 자료를 만듭니다 (앱이 멈추지 않도록)."""
    report = sf.new_report(ticker)
    report["note"] = reason or "실적 데이터를 가져오지 못했습니다"
    return {
        "ticker": ticker,
        "quarters": [],
        "forward": {
            "forward_op_income": None,
            "basis": None,
            "revision": 0,
            "detail": "실적 데이터를 가져오지 못했습니다",
        },
        "report": report,
    }


# ---------------------------------------------------------------------------
# 2) 주가 (여러 종목을 한 번에 받는 편이 빨라 묶어서 처리)
# ---------------------------------------------------------------------------
def fetch_prices(tickers: list[str], include_current_week: bool = True):
    """주가를 받아 추세·상대강도까지 계산합니다.

    반환: (price_map, weekly_map, failed)
    """
    daily_map, failed = md.fetch_daily_data(tickers)
    price_map, weekly_map = md.analyze_prices(
        daily_map, tickers=tickers, include_current_week=include_current_week
    )
    return price_map, weekly_map, [t for t in failed if t != cfg.BENCHMARK]


# ---------------------------------------------------------------------------
# 3) 점수 계산 및 결과 조립
# ---------------------------------------------------------------------------
def assemble(
    tickers: list[str],
    price_map: dict,
    weekly_map: dict,
    bundles: dict[str, dict],
    failed: list[str],
) -> dict:
    """모아 온 자료로 점수를 내고 화면에서 쓸 형태로 정리합니다."""
    scores: dict[str, dict] = {}
    reports: list[dict] = []

    for ticker in tickers:
        bundle = bundles.get(ticker) or empty_bundle(ticker)
        reports.append(bundle["report"])

        price_info = price_map.get(ticker)
        if price_info is None:
            continue  # 주가를 못 받은 종목은 순위에서 제외

        scores[ticker] = scoring.build_score(
            ticker, bundle["quarters"], bundle["forward"], price_info
        )

    return {
        "ranking": build_ranking_table(scores),
        "scores": scores,
        "weekly_map": weekly_map,
        "failed": failed,
        "no_fundamentals": [t for t in scores if not scores[t]["quarters"]],
        "reports": reports,
        "updated_at": datetime.now(),
    }


def run_pipeline(
    tickers: list[str] | None = None,
    include_current_week: bool = True,
    use_cache: bool = True,
    max_workers: int = 4,
    fundamentals_timeout_sec: float = 300.0,
) -> dict:
    """전체 파이프라인을 한 번에 실행합니다 (테스트·단독 실행용).

    대시보드는 종목 단위 캐시를 쓰기 위해 위 함수들을 따로 호출합니다.
    """
    if tickers is None:
        tickers = cfg.TICKERS

    price_map, weekly_map, failed = fetch_prices(tickers, include_current_week)

    # SEC 서버가 느리거나 막혀 있어도 앱이 무한정 멈추지 않도록 전체 시간 제한을 둡니다.
    bundles: dict[str, dict] = {}
    pool = ThreadPoolExecutor(max_workers=max_workers)
    try:
        futures = {
            pool.submit(collect_one_ticker, ticker, use_cache): ticker for ticker in tickers
        }
        try:
            for future in as_completed(futures, timeout=fundamentals_timeout_sec):
                ticker = futures[future]
                try:
                    bundles[ticker] = future.result()
                except Exception as exc:
                    bundles[ticker] = empty_bundle(ticker, str(exc)[:120])
        except TimeoutError:
            pass  # 시간이 다 됐으면 여기까지 받은 것만 사용합니다

        for ticker in tickers:
            bundles.setdefault(ticker, empty_bundle(ticker, "시간 제한을 초과했습니다"))
    finally:
        pool.shutdown(wait=False, cancel_futures=True)

    return assemble(tickers, price_map, weekly_map, bundles, failed)


# ---------------------------------------------------------------------------
# 순위 표
# ---------------------------------------------------------------------------
def build_ranking_table(scores: dict[str, dict]) -> pd.DataFrame:
    """점수 딕셔너리를 화면에 보여줄 순위 표로 바꿉니다."""
    arrow = {
        cfg.D_ACCEL: "가속 ↗",
        cfg.D_DECEL: "감속 ↘",
        cfg.D_STEADY: "유지 →",
        cfg.D_MIXED: "혼조 ↕",
        cfg.D_UNKNOWN: "판단불가",
    }

    rows = []
    for score in scores.values():
        rows.append(
            {
                "종목": score["ticker"],
                "최종점수": score["final_score"],
                "펀더": score["fund_score"],
                "기술": score["tech_score"],
                "신뢰도": score["confidence"],
                "추세상태": score["trend_state"],
                "GM%드라이버": score["gm_type"],
                "델타방향": arrow.get(score["delta_direction"], score["delta_direction"]),
                "델타예측": score["delta_forecast"],
                "RS": score["rs"],
                "판정": score["verdict"],
            }
        )

    df = pd.DataFrame(rows)
    if df.empty:
        return df

    # 정렬 규칙:
    #   · 매수 후보를 맨 위에 모으고, 그 안에서는 상대강도(RS)가 높은 순
    #   · 나머지 종목은 최종점수가 높은 순
    df["_rs"] = df["RS"].fillna(-999)

    buy = df[df["판정"] == cfg.V_BUY].sort_values(
        by=["_rs", "최종점수"], ascending=[False, False]
    )
    others = df[df["판정"] != cfg.V_BUY].sort_values(
        by=["최종점수", "_rs"], ascending=[False, False]
    )

    ordered = pd.concat([buy, others]).drop(columns=["_rs"])
    return ordered.reset_index(drop=True)


def quarters_to_frame(quarters: list[dict], start_date: str | None = None) -> pd.DataFrame:
    """분기 실적 목록을 차트용 표로 바꿉니다 (QoQ 증가율 포함).

    start_date를 주면 그 이후 분기만 남깁니다 (기본 화면은 2025년부터 표시).
    QoQ 증가율은 잘라내기 **전** 자료로 계산하므로, 첫 분기의 증가율도 정확합니다.
    """
    if not quarters:
        return pd.DataFrame()

    df = pd.DataFrame(quarters)
    keep = [c for c in ("period_label", "filing_date", "announced_date", "revenue",
                        "op_income", "gross_margin_pct", "source") if c in df.columns]
    df = df[keep].copy()

    # QoQ 증가율(%) — 이익이 얼마나 빠르게 늘고 있는지
    if "op_income" in df.columns:
        df["qoq_pct"] = df["op_income"].pct_change() * 100.0
        # 직전 분기가 적자면 증가율이 의미 없으므로 비웁니다
        prev = df["op_income"].shift(1)
        df.loc[prev <= 0, "qoq_pct"] = None

    # 표시 기간으로 자르기 (증가율 계산 이후에 잘라야 첫 줄도 값이 남습니다)
    if start_date and "filing_date" in df.columns:
        trimmed = df[df["filing_date"] >= start_date]
        if not trimmed.empty:
            df = trimmed

    return df.reset_index(drop=True)
