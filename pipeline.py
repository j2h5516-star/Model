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
import data_quality as dq
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

    # ★ 계산에 넣기 전에 먼저 숫자를 검사합니다 (모델링의 1단계).
    #   단위가 어긋난 값을 고치고, 고칠 수 없는 값은 그 항목만 빼며,
    #   무엇을 했는지 진단에 남깁니다. 이 단계가 없어서 매출이 100만 배 작게
    #   들어온 분기 하나가 평균 마진 → 전망 → 증가율 → 차트까지 오염시켰습니다.
    try:
        quarters = dq.validate_quarters(quarters, report)
    except Exception as exc:
        if not report.get("first_error"):
            report["first_error"] = f"[검사] {type(exc).__name__}: {str(exc)[:180]}"

    try:
        forward = fe.estimate_forward(ticker, quarters)
        # 컨센서스 수집 실패 사유를 진단에 남깁니다 (전망이 비는 원인 추적용)
        errors = (forward.get("consensus") or {}).get("errors") or []
        if errors:
            report["forward_note"] = " · ".join(errors[:3])
    except Exception as exc:
        forward = {
            "forward_op_income": None,
            "basis": None,
            "forward_op_income_2": None,
            "basis_2": None,
            "revision": 0,
            "revision_velocity_pct": None,
            "consensus": {},
            "detail": "전망 계산 중 문제가 발생했습니다",
            "detail_2": "",
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
# 2) 주가 — 종목 단위 증분 저장소
# ---------------------------------------------------------------------------
# 예전에는 종목 목록 전체를 통째로 캐시해서, 티커를 하나만 추가해도
# 전 종목을 다시 내려받았습니다. 그때 야후가 요청 폭주로 일부를 거부하면
# 새 종목이 데이터 없이 등록되는 문제가 있었습니다.
# 이제는 저장소(store)에 종목별로 담아 두고 "없는 종목만" 받아옵니다.

PRICE_TTL_SEC = 3600.0        # 종목별 주가를 1시간 동안 재사용
PRICE_RETRY_BACKOFF_SEC = 300.0   # 갱신 실패 시 5분 뒤에만 재시도 (야후 폭주 방지)
PRICE_STORE_CAP = 80          # 저장소에 담아 둘 최대 종목 수 (삭제한 종목 정리)


def refresh_price_store(
    store: dict,
    tickers: list[str],
    fetch=None,
    now: float | None = None,
    sleep=None,
) -> tuple[list[str], list[str]]:
    """저장소에 없는(또는 오래된) 종목을 받아 채웁니다.

    반환: (failed, stale)
      failed = 데이터가 아예 없는 종목 (화면에서 제외됨)
      stale  = 갱신에는 실패했지만 이전 데이터로 계속 표시 중인 종목 (안내 필요)

    설계 메모:
      · 만료된 종목이 하나라도 있으면 목록 전체를 함께 다시 받습니다.
        종목마다 시점이 다른 주가가 한 순위표에 섞이면 상대강도(RS)가 왜곡되어
        매수 후보 정렬이 뒤집힐 수 있기 때문입니다. (한 번의 일괄 요청이라 저렴)
      · 새 종목만 추가된 경우에는 그 종목만 받습니다 (티커 추가가 빨라야 하므로)
      · 여러 사용자가 동시에 접속해도 중복 다운로드가 나지 않게 락으로 감쌉니다.
    """
    import contextlib
    import time as _time

    if fetch is None:
        fetch = md.fetch_daily_data
    if now is None:
        now = _time.time()
    if sleep is None:
        sleep = _time.sleep

    store.setdefault("data", {})
    store.setdefault("ts", {})
    lock = store.get("lock") or contextlib.nullcontext()

    with lock:
        wanted = list(dict.fromkeys(list(tickers) + [cfg.BENCHMARK]))
        missing = [t for t in wanted if t not in store["data"]]
        expired = [
            t for t in wanted
            if t in store["data"] and now - store["ts"].get(t, 0) > PRICE_TTL_SEC
        ]

        # 삭제·이탈한 종목이 저장소에 무한히 쌓이지 않게 오래된 것부터 정리
        # (요청이 없는 조용한 실행에서도 정리되도록 fetch 전에 수행합니다)
        if len(store["data"]) > PRICE_STORE_CAP:
            keep = set(wanted)
            removable = sorted(
                (t for t in store["data"] if t not in keep),
                key=lambda t: store["ts"].get(t, 0),
            )
            for ticker in removable[: len(store["data"]) - PRICE_STORE_CAP]:
                store["data"].pop(ticker, None)
                store["ts"].pop(ticker, None)

        # 만료가 하나라도 있으면 전체를 함께 갱신 (시점 혼재 방지),
        # 만료가 없으면 새로 추가된 종목만 (티커 추가 고속 경로)
        need = wanted if expired else missing
        if not need:
            return [], []

        daily_map, _ = fetch(need)

        # 야후는 요청이 몰리면 일부 종목을 조용히 거부합니다 → 한 번 더 시도
        not_received = [t for t in need if t not in daily_map]
        if not_received:
            sleep(2.0)
            retry_map, _ = fetch(not_received)
            daily_map.update(retry_map)

        failed: list[str] = []
        stale: list[str] = []
        for ticker in need:
            fresh = daily_map.get(ticker)
            if fresh is not None and not fresh.empty:
                store["data"][ticker] = fresh
                store["ts"][ticker] = now
            elif ticker in store["data"]:
                # 갱신 실패 — 이전 데이터로 계속 표시하되 사용자에게 알리고,
                # 매 화면 새로고침마다 재시도하지 않도록 5분 뒤로 미룹니다
                stale.append(ticker)
                store["ts"][ticker] = now - PRICE_TTL_SEC + PRICE_RETRY_BACKOFF_SEC
            else:
                failed.append(ticker)

        return failed, stale


def prices_from_store(
    store: dict,
    tickers: list[str],
    include_current_week: bool = True,
):
    """저장소의 일봉으로 추세·상대강도를 계산합니다.

    반환: (price_map, weekly_map)
    """
    daily_map = {
        t: store.get("data", {})[t]
        for t in list(tickers) + [cfg.BENCHMARK]
        if t in store.get("data", {})
    }
    return md.analyze_prices(
        daily_map, tickers=list(tickers), include_current_week=include_current_week
    )


def fetch_prices(tickers: list[str], include_current_week: bool = True):
    """주가를 받아 추세·상대강도까지 계산합니다 (단독 실행·테스트용 일괄 경로).

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
    max_workers: int = 1,
    fundamentals_timeout_sec: float = 300.0,
) -> dict:
    """전체 파이프라인을 한 번에 실행합니다 (테스트·단독 실행용).

    대시보드는 종목 단위 캐시를 쓰기 위해 위 함수들을 따로 호출합니다.

    max_workers 기본값이 1인 이유: 여러 종목을 동시에 받으면 SEC 신원 설정이
    공용 접속 창구를 닫아 수집이 통째로 실패한 적이 있습니다
    (sec_fundamentals._ensure_identity 주석 참고). 지금은 자물쇠로 막았지만,
    앱 화면과 같은 순차 방식을 기본으로 두어 동작을 일치시킵니다.
    """
    if tickers is None:
        tickers = cfg.TICKERS

    price_map, weekly_map, failed = fetch_prices(tickers, include_current_week)

    # 동시에 받기 전에 SEC 신원을 미리 한 번 설정해 둡니다
    # (수집 도중에 설정이 일어나면 다른 종목의 접속이 끊깁니다)
    try:
        sf._ensure_identity()
    except Exception:
        pass  # 신원 설정 실패는 각 종목 수집에서 진단으로 기록됩니다

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
    phase_mark = {
        cfg.PH_TURNAROUND: "턴어라운드 ⤴",
        cfg.PH_NEW_HIGH: "3년내 최고 ★",
        cfg.PH_ROLLOVER: "고점 이탈 ⤵",
        cfg.PH_NONE: "중간 자리 -",
        cfg.PH_LOSS: "적자 지속 ✕",
        cfg.PH_UNKNOWN: "판단불가",
    }

    rows = []
    for score in scores.values():
        rows.append(
            {
                "종목": score["ticker"],
                "주가($)": score.get("close"),
                "최종점수": score["final_score"],
                # 국면을 앞쪽에 둡니다. 검증에서 가장 크게 갈린 항목인데
                # (신호 있음 95.5% vs 중간 자리 46.9%), 뒤쪽에 두면 폰 화면에서는
                # 옆으로 밀어야만 보여서 사실상 없는 것과 같습니다.
                "국면": phase_mark.get(score.get("phase"), score.get("phase", "-")),
                "델타방향": arrow.get(score["delta_direction"], score["delta_direction"]),
                "펀더": score["fund_score"],
                "기술": score["tech_score"],
                "신뢰도": score["confidence"],
                "추세상태": score["trend_state"],
                "GM%드라이버": score["gm_type"],
                "델타예측(1분기후)": score["delta_forecast"],
                "델타예측(2분기후)": score.get("accel_forecast", "-"),
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
    # 실적을 못 구한 종목은 점수를 같은 기준으로 비교할 수 없어 맨 뒤로 보냅니다
    no_data = df[df["판정"] == cfg.V_NO_DATA].sort_values(by=["최종점수"], ascending=False)
    others = df[~df["판정"].isin([cfg.V_BUY, cfg.V_NO_DATA])].sort_values(
        by=["최종점수", "_rs"], ascending=[False, False]
    )

    ordered = pd.concat([buy, others, no_data]).drop(columns=["_rs"])
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

    # QoQ 증가율(%) — 이익이 얼마나 빠르게 늘고 있는지.
    # 점수 계산과 **같은 안전 계산**을 씁니다. 예전에는 여기서만 그냥 나눠서,
    # 점수 쪽에서 걸러낸 +461,711,116% 가 차트 오른쪽 축에는 그대로 그려졌습니다.
    if "op_income" in df.columns:
        values = df["op_income"].tolist()
        growths = [None]
        for prev, curr in zip(values, values[1:]):
            growths.append(dq.safe_growth_pct(prev, curr))
        df["qoq_pct"] = growths

    # 표시 기간으로 자르기 (증가율 계산 이후에 잘라야 첫 줄도 값이 남습니다)
    if start_date and "filing_date" in df.columns:
        trimmed = df[df["filing_date"] >= start_date]
        if not trimmed.empty:
            df = trimmed

    return df.reset_index(drop=True)
