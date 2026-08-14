"""
collect_job.py — 수집 로봇의 몸통 (깃허브 액션에서 매일 실행)
=============================================================

v2 구조(전략.md 4장)의 첫 몸입니다. 사람 조작 없이:

  ① SEC 8-K에서 조정 EPS 원문을 수집하고 (sec_fundamentals — v1 이식)
  ② 야후에서 일봉 주가를 받고 (market_data — v1 이식)
  ③ 검사·꾸리기를 거쳐 (measure_store — v1 이식)
  ④ data/measure/ 아래에 **파일로 쓴다** (커밋은 워크플로가 한다)

로봇의 실행 기록은 깃허브 액션 로그와 data/measure/robot_log.json 에
남습니다 — "새 코드 경로는 실행 흔적으로 증명한다" 규칙의 이행입니다.

절반 이상의 종목에서 실적 수집이 실패하면 종료코드 1 로 끝나
그날 커밋을 막고 실패를 드러냅니다 (반쯤 깨진 데이터로 덮어쓰지 않기).
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import config as cfg
import dataset
import judge
import market_data as md
import measure_engine
import measure_store
import sec_fundamentals as sf


def collect_fundamentals(tickers: list[str], progress=print) -> list[dict]:
    """종목별 실적을 **절제된 병렬**로 수집합니다 (26차 개선).

    v1 사고(무절제 병렬 → SEC 403·"client has been closed")의 원인은 병렬
    자체가 아니라 ① set_identity 경쟁 ② 속도 무제한이었습니다. ①은
    sec_fundamentals 의 _identity_lock 이 막고(신원을 병렬 시작 **전에**
    한 번 설정해 두 번째 방어), ②는 일꾼 수를 config.COLLECT_WORKERS(=3)
    로 묶어 SEC 허용치(초당 10요청)의 절반 아래로 유지합니다.
    문서 캐시(증분 수집) 덕에 평상시 요청 수 자체도 적습니다.
    결과 목록은 입력 종목 순서 그대로 돌려줍니다 (재현성).
    """
    sf._ensure_identity()          # 병렬 시작 전에 신원 설정 — 경쟁 원천 차단
    reports: list[dict | None] = [None] * len(tickers)
    done_lock = threading.Lock()
    done = 0

    def _one(index: int, ticker: str) -> None:
        nonlocal done
        started = time.monotonic()
        try:
            _quarters, report = sf.get_fundamentals(ticker, use_cache=False)
        except Exception as exc:
            report = sf.new_report(ticker)
            report["first_error"] = f"{type(exc).__name__}: {str(exc)[:160]}"
        report["seconds"] = round(time.monotonic() - started, 1)
        reports[index] = report
        with done_lock:
            done += 1
            progress(
                f"[{done}/{len(tickers)}] {ticker}: "
                f"직접공시 {report.get('merged_direct', 0)}건 · "
                f"조정EPS {report.get('adj_eps_ok', 0)}건 · "
                f"캐시 {report.get('cache_hits', 0)}/신규 "
                f"{report.get('cache_downloads', 0)} · "
                f"{report['seconds']}초"
                + (f" · ⚠️ {report['first_error']}" if report.get("first_error") else "")
            )

    with ThreadPoolExecutor(max_workers=cfg.COLLECT_WORKERS) as pool:
        for index, ticker in enumerate(tickers):
            pool.submit(_one, index, ticker)
    return [r for r in reports if r is not None]


def collect_prices(tickers: list[str], progress=print) -> dict:
    """일봉을 일괄 수집합니다. 야후가 일부를 거부하면 한 번 더 시도합니다."""
    wanted = list(dict.fromkeys(list(tickers) + [cfg.BENCHMARK]))
    daily_map, _failed = md.fetch_daily_data(wanted)
    missing = [t for t in wanted if t not in daily_map]
    if missing:
        progress(f"주가 재시도: {missing}")
        time.sleep(3.0)
        retry_map, _ = md.fetch_daily_data(missing)
        daily_map.update(retry_map)
    progress(f"주가 확보: {len(daily_map)}/{len(wanted)}종목")
    return daily_map


def success_enough(reports: list[dict], daily_map: dict, tickers: list[str]) -> bool:
    """커밋해도 될 만큼 수집됐는가 — 반쯤 깨진 날은 덮어쓰지 않습니다."""
    fund_ok = sum(1 for r in reports if r.get("adj_eps_ok", 0) > 0 or r.get("xbrl_quarters", 0) > 0)
    price_ok = sum(1 for t in tickers if t in daily_map)
    half = len(tickers) / 2.0
    return fund_ok >= half and price_ok >= half and cfg.BENCHMARK in daily_map


def write_files(files: dict[str, str], progress=print) -> None:
    for path, content in files.items():
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        progress(f"기록: {path} ({len(content):,}자)")


def run(tickers: list[str] | None = None, progress=print) -> int:
    if tickers is None:
        tickers = list(cfg.TICKERS)
    progress(f"수집 로봇 시작 — {len(tickers)}종목 · {datetime.now(timezone.utc).isoformat()}")

    reports = collect_fundamentals(tickers, progress)
    daily_map = collect_prices(tickers, progress)

    if not success_enough(reports, daily_map, tickers):
        progress("⛔ 절반 이상 실패 — 오늘 데이터로 덮어쓰지 않습니다 (종료코드 1)")
        return 1

    files, summary = measure_store.build_files(tickers, daily_map, reports)

    # v3 5단계 — 수집 성공 시 등록된 판정(11차)을 자동 계산합니다 (사람 개입 없음).
    # 방금 만든 snapshot 내용으로 데이터 계층 → 측정 장치 → 자동 판정 순서.
    # ⚠️ 판정이 실패해도 그날의 **데이터 커밋은 막지 않습니다** — 데이터가 더
    #    귀합니다. 대신 실패를 로봇 기록에 남겨 조용히 넘어가지 않게 합니다.
    try:
        snap = json.loads(files[f"{cfg.MEASURE_DIR}/snapshot.json"])
        ds = dataset.build(snap)
        events, _skipped = measure_engine.collect_events(ds)
        # H10 (23차 등록) — 논갭 영업이익 단독 사건은 별도 목록으로
        op_events = measure_engine.collect_metric_events(ds, "op_income")
        verdict = judge.run(events, op_events=op_events)
        files[f"{cfg.MEASURE_DIR}/verdict.json"] = judge.to_json(verdict)
        verdict_note = " · ".join(
            f"{name}: {entry['판정']}" for name, entry in verdict["가설"].items()
        )
        progress(f"자동 판정 — {verdict_note}")
    except Exception as exc:
        verdict_note = f"판정 실패: {type(exc).__name__}: {str(exc)[:160]}"
        progress(f"⚠️ {verdict_note}")

    # 로봇 실행 기록 — 다음 세션이 읽습니다
    log = {
        "ran_at": datetime.now(timezone.utc).isoformat(),
        "tickers": len(tickers),
        "summary": summary,
        "verdict": verdict_note,
        "per_ticker": [
            {
                "ticker": r.get("ticker"),
                "adj_eps_ok": r.get("adj_eps_ok", 0),
                "merged_direct": r.get("merged_direct", 0),
                "seconds": r.get("seconds"),
                "cache_hits": r.get("cache_hits", 0),
                "cache_downloads": r.get("cache_downloads", 0),
                "first_error": r.get("first_error", ""),
            }
            for r in reports
        ],
    }
    files[f"{cfg.MEASURE_DIR}/robot_log.json"] = json.dumps(log, ensure_ascii=False, indent=1)

    write_files(files, progress)
    progress(f"✅ 완료 — {summary}")
    return 0


if __name__ == "__main__":
    sys.exit(run())
