"""
consensus_feed.py — 야후 컨센서스(애널리스트 EPS 추정) 원장 · 헌법 개정판
==========================================================================

2026-08-14 저장소 주인 결정(전략.md 2장 제1조 개정)으로 컨센서스가
전망 입력 ②로 허용되었습니다. 단 **관찰·기록 전용**이며, 판정 편입은
사전 등록 후 새 데이터로만 합니다.

컨센서스는 우리가 만들 수 없는 "받아 적는 값"이고 **사후에 소급
수정되는** 데이터입니다. 그래서 이 파일의 원칙은 하나입니다:

    **원장은 추가만 한다. 과거 항목은 절대 고치지 않는다.**

매일 로봇이 그날의 추정치를 수집 시점(as_of)과 함께 원장 끝에
붙입니다. 나중에 "발표일에 시장이 뭘 기대했나"를 물을 때는 발표일
**이전** 마지막 스냅샷만 씁니다 — 야후를 다시 조회하면 소급 수정된
값이 오므로, 과거 시점 재현은 이 원장만 믿습니다.

수집 실패는 수집 실패로 기록합니다. 값을 지어내지 않습니다.
"""

from __future__ import annotations

import json
import math

import config as cfg

# 주당 금액 상한 — dataset.PER_SHARE_ABS_LIMIT 과 같은 근거(실측 정상
# 최대 39.25달러, 오염 사례 202.0)를 컨센서스에도 적용합니다.
PER_SHARE_ABS_LIMIT = 100.0

# 원장에 담는 추정 구간: 이번 분기(0q) · 다음 분기(+1q)
PERIOD_LABELS = ("0q", "+1q")


def _num(value) -> float | None:
    """숫자면 float, 아니면(None·NaN·문자열) None — 값을 만들지 않습니다."""
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


REV_MIN_DOLLARS = 1e6      # 매출 추정이 이보다 작으면 단위 착오 의심 — 버림


def merge_revenue_frame(rows: dict, frame) -> list[str]:
    """야후 매출 추정 표(revenue_estimate)를 기존 행에 합칩니다 (30차).

    같은 원칙: 값은 그대로 받아 적고, 상식 밖(1백만 달러 미만·역전 범위)은
    버립니다. 매출 추정이 없어도 EPS 행은 그대로 살아 있습니다.
    """
    dropped: list[str] = []
    if frame is None or getattr(frame, "empty", True):
        return dropped
    for label in PERIOD_LABELS:
        if label not in frame.index or label not in rows:
            continue
        record = frame.loc[label]
        avg = _num(record.get("avg"))
        low = _num(record.get("low"))
        high = _num(record.get("high"))
        analysts = _num(record.get("numberOfAnalysts"))
        if avg is None:
            continue
        if avg < REV_MIN_DOLLARS:
            dropped.append(f"{label}: 매출 추정 {avg} 단위 의심")
            continue
        if (low is not None and low > avg) or (high is not None and avg > high):
            dropped.append(f"{label}: 매출 low≤avg≤high 위반")
            continue
        rows[label].update(rev_avg=avg, rev_low=low, rev_high=high,
                           rev_analysts=int(analysts) if analysts is not None else None)
    return dropped


def rows_from_frame(frame) -> tuple[dict, list[str]]:
    """yfinance earnings_estimate 표에서 원장 행을 만듭니다.

    반환: ({"0q": {...}, "+1q": {...}}, 탈락 사유 목록)
    탈락 규칙(오염 차단 — 값은 고치지 않고 버림):
      · avg 없음 → 행 제외
      · |avg| > 주당 상한(100) → 행 제외 (자릿수 붕괴 의심)
      · low ≤ avg ≤ high 위반 → 행 제외 (표를 잘못 읽었을 가능성)
    """
    rows: dict = {}
    dropped: list[str] = []
    if frame is None or getattr(frame, "empty", True):
        return rows, dropped
    for label in PERIOD_LABELS:
        if label not in frame.index:
            continue
        record = frame.loc[label]
        avg = _num(record.get("avg"))
        low = _num(record.get("low"))
        high = _num(record.get("high"))
        analysts = _num(record.get("numberOfAnalysts"))
        year_ago = _num(record.get("yearAgoEps"))
        if avg is None:
            dropped.append(f"{label}: avg 없음")
            continue
        if abs(avg) > PER_SHARE_ABS_LIMIT:
            dropped.append(f"{label}: avg {avg} 주당 상한 초과")
            continue
        if (low is not None and low > avg) or (high is not None and avg > high):
            dropped.append(f"{label}: low≤avg≤high 위반 ({low},{avg},{high})")
            continue
        rows[label] = {
            "avg": avg,
            "low": low,
            "high": high,
            "analysts": int(analysts) if analysts is not None else None,
            "year_ago": year_ago,
        }
    return rows, dropped


def fetch_one(ticker: str) -> tuple[dict, list[str]]:
    """야후에서 한 종목의 EPS·매출 추정 표를 받아 원장 행으로 바꿉니다."""
    import yfinance as yf

    tk = yf.Ticker(ticker)
    rows, dropped = rows_from_frame(tk.earnings_estimate)
    if rows:
        try:
            dropped += merge_revenue_frame(rows, tk.revenue_estimate)
        except Exception:
            pass    # 매출 추정 실패는 EPS 행을 막지 않습니다
    return rows, dropped


def empty_ledger() -> dict:
    return {
        "설명": (
            "야후 컨센서스 EPS 추정 원장 — 추가 전용. 과거 항목은 절대 "
            "고치지 않는다 (컨센서스는 소급 수정되므로 과거 시점 재현은 "
            "이 원장만 믿는다). 사건에는 발표일 이전 마지막 스냅샷만 쓴다. "
            "관찰 전용 — 판정 편입은 사전 등록 후 새 데이터로만 "
            "(전략.md 2장 제1조, 2026-08-14 개정)."
        ),
        "tickers": {},
    }


def load(path: str) -> dict:
    """원장을 읽습니다. 없으면 빈 원장 — 깨져 있으면 그대로 예외(덮어쓰기 방지)."""
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return empty_ledger()


def append_snapshot(ledger: dict, ticker: str, as_of: str, rows: dict) -> bool:
    """원장 끝에 오늘 스냅샷을 붙입니다. 과거 항목은 건드리지 않습니다.

    붙이지 않는 경우(False):
      · rows 가 비어 있음 (수집 실패는 원장에 기록하지 않음 — 로봇 로그가 담당)
      · 같은 날짜(as_of) 항목이 이미 있음 (하루 한 번)
      · 마지막 항목과 값이 완전히 같음 (변화 없는 날은 원장을 불리지 않음 —
        '발표일 이전 마지막 스냅샷' 조회 결과는 달라지지 않습니다)
    """
    if not rows:
        return False
    entries = ledger.setdefault("tickers", {}).setdefault(ticker, [])
    if entries:
        last = entries[-1]
        if last.get("as_of") == as_of:
            return False
        if last.get("rows") == rows:
            return False
        if str(last.get("as_of", "")) > as_of:
            return False        # 시간 역행 금지 — 원장은 항상 앞으로만
    entries.append({"as_of": as_of, "rows": rows})
    return True


def collect(tickers: list[str], ledger: dict, as_of: str, progress=print) -> str:
    """전 종목을 수집해 원장에 붙입니다. 반환: 한 줄 요약 (로봇 로그용)."""
    appended = fetched = failed = 0
    for ticker in tickers:
        try:
            rows, _dropped = fetch_one(ticker)
        except Exception:
            failed += 1
            continue
        if rows:
            fetched += 1
            if append_snapshot(ledger, ticker, as_of, rows):
                appended += 1
    summary = f"컨센서스: 확보 {fetched}종목 · 원장 추가 {appended}건 · 실패 {failed}종목"
    progress(summary)
    return summary


def to_json(ledger: dict) -> str:
    return json.dumps(ledger, ensure_ascii=False, indent=1)
