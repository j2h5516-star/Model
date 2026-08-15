"""
dataset.py — 데이터 계층 (v3 4단계, 설계도.md ①)
==================================================

하는 일 (재료 손질):
  수집 로봇이 커밋한 data/measure/snapshot.json 을 읽어,
  ⑴ 검사를 통과한 값만 통행시키고
  ⑵ 시간순으로 정렬해
  ⑶ 측정 장치(5단계)가 쓰기 좋은 형태의 분기 표·가격 표로 돌려줍니다.

경계 (설계도.md 3장):
  · snapshot.json 은 **읽기만** 합니다. 파일을 쓰지 않습니다 —
    진실은 snapshot 하나여야 하고, 손질본 파일은 혼란의 씨앗입니다.
  · 값을 만들거나 고치지 않습니다 (창작 금지). 검사에서 탈락한 값은
    "없음"(None)으로 둡니다 — 없음이 틀림보다 안전합니다.
  · TTM·신고점 같은 파생 계산은 하지 않습니다 (측정 장치의 일).
  · 인터넷에 접속하지 않습니다 (수집은 로봇만).

무엇을 검사하나:
  · 숫자 칸: 숫자가 아니거나 무한대(NaN/inf)면 "없음"으로
  · 주당 금액(조정 EPS·GAAP EPS·가이던스): 절대값이 상한을 넘으면 "없음"으로
    — 파서의 소수점 규칙이 1차 방어이고, 이것은 2차 방어입니다
  · 날짜 칸: 형식이 YYYY-MM-DD 가 아니면 "없음"으로,
    분기의 시간축(filing_date)이 아예 없으면 그 행을 버립니다
  · 가격: 날짜·종가 개수가 어긋난 종목은 가격 전체를 버리고,
    0 이하·숫자 아님·중복 날짜는 점 단위로 버립니다
  · 기준지수(SPY)가 없으면 측정 자체가 불가능하므로 즉시 오류를 냅니다

무엇을 했는지는 전부 notes 에 남깁니다 — 조용히 버리지 않습니다.
"""

from __future__ import annotations

import json
import math
import os
from datetime import datetime

import config as cfg

# 주당 금액의 절대 상한 (2차 방어).
#
# 근거 (2026-08-12 snapshot 실측 — 실행 출력에서 복사):
#   · 검증된 정상 최대값: SNDK 26 Q4 조정 EPS 39.25 (실물 시험 있음)
#   · 실제 오류값: 정수를 잘못 문 202.0 이 30건 — 전부 100 위
#   상한 100은 정상 최대의 2.5배 여유를 두면서 실측 오류를 전부 막습니다.
# 한계 (감추지 않습니다): 100 아래의 오류(예: MCHP 의 31.0)는 이 상한으로
#   못 잡습니다. 그건 파서의 소수점 규칙(1차 방어)이 막습니다.
PER_SHARE_ABS_LIMIT = 100.0

# 분기 행에서 통행시키는 칸 (measure_store.EPS_FIELDS + 가이던스 3칸)
_NUMBER_FIELDS = ("revenue", "op_income", "adj_eps", "adjusted_ebitda",
                  "gaap_eps", "guid_eps_low", "guid_eps_high", "guid_eps_mid",
                  "guid_rev_low", "guid_rev_high", "guid_rev_mid",
                  "guid_ebitda_low", "guid_ebitda_high", "guid_ebitda_mid")
_PER_SHARE_FIELDS = ("adj_eps", "gaap_eps",
                     "guid_eps_low", "guid_eps_high", "guid_eps_mid")


def _finite_number(value) -> bool:
    """진짜 숫자인가 (NaN·무한대·불리언·문자 제외)."""
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _valid_date(value) -> bool:
    """YYYY-MM-DD 형식의 실제 날짜인가."""
    if not isinstance(value, str):
        return False
    try:
        datetime.strptime(value[:10], "%Y-%m-%d")
        return True
    except ValueError:
        return False


def load(path: str | None = None) -> dict:
    """snapshot.json 을 읽습니다 (읽기 전용)."""
    if path is None:
        path = os.path.join(cfg.MEASURE_DIR, "snapshot.json")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _clean_quarters(eps_map: dict, notes: list[str]) -> dict:
    """종목별 분기 행을 검사·정렬합니다. 원본은 바꾸지 않습니다."""
    cleaned: dict[str, list[dict]] = {}
    for ticker, rows in (eps_map or {}).items():
        kept: list[dict] = []
        for row in rows or []:
            # 시간축이 없는 행은 어디에도 놓을 수 없으므로 버립니다
            if not _valid_date(row.get("filing_date")):
                notes.append(
                    f"{ticker} {row.get('period_label', '?')}: "
                    "filing_date 가 없거나 형식이 이상해 행을 버렸습니다"
                )
                continue
            clean = dict(row)   # 사본 — 원본 불변

            # 발표일: 형식이 이상하면 "없음"으로 (사건 후보에서 빠질 뿐,
            # 분기 이력으로는 남습니다)
            if clean.get("announced_date") is not None and not _valid_date(
                clean["announced_date"]
            ):
                notes.append(
                    f"{ticker} {clean.get('period_label', '?')}: "
                    f"발표일 {clean['announced_date']!r} 형식이 이상해 없음 처리"
                )
                clean["announced_date"] = None

            # 숫자 칸: 숫자가 아니면 "없음"으로 (고치지 않고 버림)
            for field in _NUMBER_FIELDS:
                value = clean.get(field)
                if value is not None and not _finite_number(value):
                    notes.append(
                        f"{ticker} {clean.get('period_label', '?')}: "
                        f"{field}={value!r} 는 숫자가 아니어서 없음 처리"
                    )
                    clean[field] = None

            # 매출 최저 하한: 자릿수가 무너진 매출(실물: APP 21Q4 3.55달러 —
            # 주당·백만 단위 착오 3건 실측)은 없음으로. 이 유니버스에 분기
            # 매출 1만 달러 미만 회사는 없습니다.
            if clean.get("revenue") is not None and clean["revenue"] < 10_000:
                notes.append(
                    f"{ticker} {clean.get('period_label', '?')}: "
                    f"매출 {clean['revenue']} 은 자릿수가 무너진 값이라 없음 처리"
                )
                clean["revenue"] = None

            # 영업이익 단위 검사 (25차 감사): 보도자료 표는 '천/백만 달러'
            # 단위인데 병합 때 매출만 XBRL 달러값으로 바뀌면 단위가 어긋난다
            # (실물: CRDO 216,722 ↔ 매출 4.37억 = 마진 0.05%). 매출이 달러
            # 단위로 확실할 때(1천만 이상) 마진이 상식 밖이면 없음 처리.
            #   · |영업이익| < 매출의 0.1% → 단위 미환산·주당값 오인 의심
            #   · 영업이익 > 매출의 90% → 표의 다른 숫자를 집었을 가능성
            #   · 마진 < MARGIN_MIN_PCT(-500%) → 같은 이유
            # 진짜 0.1% 미만 마진 분기를 잃을 수 있으나, "없음"은 안전하고
            # "1000배 틀림"은 위험하다 (창작 금지 원칙의 버림 쪽).
            rev, op = clean.get("revenue"), clean.get("op_income")
            if rev is not None and op is not None and rev >= 10_000_000:
                margin = op / rev
                if (abs(op) < rev * 0.001 or op > rev * 0.9
                        or margin * 100.0 < cfg.MARGIN_MIN_PCT):
                    notes.append(
                        f"{ticker} {clean.get('period_label', '?')}: "
                        f"영업이익 {op} 은 매출 {rev} 대비 마진 "
                        f"{margin * 100.0:.3f}% — 단위 착오/오인 의심이라 없음 처리"
                    )
                    clean["op_income"] = None

            # 조정 EBITDA 단위 검사 (46차 감사) — 위 영업이익 가드와 같은 사고가
            # EBITDA 칸에서도 일어나고 있었습니다. 실물 85건 · 10종목
            # (BE CIEN CMCSA ENTG ETSY GNRC PINS PWR SNAP TTMI).
            # 가장 아픈 예: CIEN 은 25 Q3 157,962,000 → 25 Q4 **205,536** 으로
            # 저장돼 있어 **99.9% 급감**으로 읽혔습니다. 실제로는 205,536천 달러
            # (2.06억)로 **증가**입니다. 이 오염이 광통신 묶음의 "이익 델타"를
            # 통째로 뒤집어, 주도섹터 판정을 틀리게 만들고 있었습니다.
            # EBITDA 는 이익이므로 매출을 넘을 수 없고, 매출의 0.1% 미만이면
            # 천/백만 단위 미환산입니다. 고치지 않고 버립니다 (창작 금지).
            ebitda = clean.get("adjusted_ebitda")
            if rev is not None and ebitda is not None and rev >= 10_000_000:
                ratio = ebitda / rev
                if (abs(ebitda) < rev * 0.001 or ebitda > rev
                        or ratio * 100.0 < cfg.MARGIN_MIN_PCT):
                    notes.append(
                        f"{ticker} {clean.get('period_label', '?')}: "
                        f"조정 EBITDA {ebitda} 은 매출 {rev} 대비 "
                        f"{ratio * 100.0:.3f}% — 단위 착오 의심이라 없음 처리"
                    )
                    clean["adjusted_ebitda"] = None

            # 주당 금액 상한 (2차 방어 — 위 PER_SHARE_ABS_LIMIT 주석 참조)
            for field in _PER_SHARE_FIELDS:
                value = clean.get(field)
                if value is not None and abs(value) > PER_SHARE_ABS_LIMIT:
                    notes.append(
                        f"{ticker} {clean.get('period_label', '?')}: "
                        f"{field}={value} 는 주당 금액 상한({PER_SHARE_ABS_LIMIT})을 "
                        "넘어 없음 처리 (표의 다른 숫자를 잘못 집었을 가능성)"
                    )
                    clean[field] = None

            kept.append(clean)

        kept.sort(key=lambda r: r["filing_date"])
        cleaned[ticker] = kept
    return cleaned


def _clean_prices(price_map: dict, notes: list[str]) -> dict:
    """종목별 일봉을 검사·정렬합니다. 원본은 바꾸지 않습니다."""
    cleaned: dict[str, dict] = {}
    for ticker, series in (price_map or {}).items():
        dates = (series or {}).get("dates") or []
        closes = (series or {}).get("close") or []
        if len(dates) != len(closes):
            # 날짜와 종가의 짝이 어긋난 표는 어느 쪽도 믿을 수 없습니다
            notes.append(
                f"{ticker}: 가격 표의 날짜({len(dates)})와 종가({len(closes)}) "
                "개수가 달라 이 종목 가격을 통째로 버렸습니다"
            )
            continue

        by_date: dict[str, float] = {}
        dropped = 0
        duplicated = 0
        for date, close in zip(dates, closes):
            if not _valid_date(date) or not _finite_number(close) or close <= 0:
                dropped += 1
                continue
            if date in by_date:
                duplicated += 1   # 같은 날짜가 두 번이면 뒤의 값을 씁니다
            by_date[date] = float(close)
        if dropped:
            notes.append(f"{ticker}: 이상한 가격 점 {dropped}개를 버렸습니다")
        if duplicated:
            notes.append(f"{ticker}: 중복 날짜 {duplicated}개 — 뒤의 값만 남겼습니다")

        ordered = sorted(by_date)
        cleaned[ticker] = {
            "dates": ordered,
            "close": [by_date[d] for d in ordered],
        }
    return cleaned


def build(snapshot: dict) -> dict:
    """snapshot 을 검사·정렬해 측정용 밥상을 차립니다.

    반환:
      {
        "saved_at":  snapshot 이 만들어진 시각 (사실 그대로),
        "benchmark": 기준지수 티커,
        "tickers":   종목 목록,
        "quarters":  {종목: [검사·정렬된 분기 행]},
        "prices":    {종목: {"dates": [...], "close": [...]}},
        "notes":     [무엇을 버렸는지 전부],
      }

    기준지수 가격이 없으면 ValueError — SPY 대비 측정 자체가 불가능한
    재료이므로, 조용히 넘어가지 않고 즉시 실패를 드러냅니다.
    """
    notes: list[str] = []
    benchmark = snapshot.get("benchmark") or cfg.BENCHMARK

    quarters = _clean_quarters(snapshot.get("eps") or {}, notes)
    prices = _clean_prices(snapshot.get("prices") or {}, notes)

    if not prices.get(benchmark, {}).get("dates"):
        raise ValueError(
            f"기준지수({benchmark}) 가격이 없습니다 — SPY 대비 측정이 "
            "불가능한 재료입니다. 로봇 수집 기록을 확인하세요."
        )

    return {
        "saved_at": snapshot.get("saved_at"),
        "benchmark": benchmark,
        "tickers": list(snapshot.get("tickers") or []),
        "quarters": quarters,
        "prices": prices,
        "notes": notes,
    }
