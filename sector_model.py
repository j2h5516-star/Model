"""
sector_model.py — 섹터 정배열 폭 모델 (H11 · 33차 사전 등록의 구현)
====================================================================

저장소 주인 지시(2026-08-14): "정배열 이후 최소 1년을 분석해 앞으로
상승할 섹터를 포착한다."

이 파일이 재는 것 (33차 등록에서 옮겨 적음 — 여기서 고치지 않는다):
  · 섹터 정배열 폭 = 그 주, 섹터 종목 중 완전 정배열인 비율(%)
    완전 정배열 = 주봉 종가 > 4주선 > 13주선 > 26주선 > 52주선
    52주 이력 미만 종목은 분모에서 제외. 유효 3종목 미만 = 판단 불가
  · 신호 = 폭이 처음 문턱(H11 60% · H11b 80%)을 넘긴 주
  · 표적 = 다음 거래일부터 250거래일, 섹터 동일가중 평균 − SPY (%p)
  · 폭등 = +20%p

경계 (설계도.md 3장): 판정(채택/미채택)은 judge.py 가 합니다.
이 파일은 사건과 상태를 만들 뿐, 스스로 결론 내지 않습니다.
"""

from __future__ import annotations

import bisect
from datetime import date
from statistics import mean

import config as cfg

# --- 33차 사전 등록 상수 (옮겨 적음 — 여기서 고치지 않는다) ---
MA_WEEKS = (4, 13, 26, 52)      # 주봉 이동평균
WARMUP_WEEKS = 52               # 이 미만 이력의 종목은 분모에서 제외
MIN_MEMBERS = 3                 # 유효 종목이 이보다 적은 주는 판단 불가
H11_THRESHOLD = 60.0            # 신호 문턱 (%)
H11B_THRESHOLD = 80.0           # 보조 등록 문턱 (%)
FORWARD_DAYS = 250              # 표적 창 (거래일 ≈ 1년)
SURGE_PP = 20.0                 # 폭등 = SPY 대비 +20%p


def _iso_week(day: str) -> tuple[int, int]:
    y, m, d = map(int, day.split("-"))
    return date(y, m, d).isocalendar()[:2]


def weekly_indices(dates: list[str]) -> list[int]:
    """각 주의 마지막 거래일 인덱스 목록."""
    out: list[int] = []
    current_key = None
    current_index = None
    for index, day in enumerate(dates):
        key = _iso_week(day)
        if key != current_key:
            if current_index is not None:
                out.append(current_index)
            current_key = key
        current_index = index
    if current_index is not None:
        out.append(current_index)
    return out


def aligned_flags(prices: dict) -> dict[str, bool]:
    """종목의 주별 정배열 여부 {주 마지막 거래일: True/False}.

    이력이 52주 미만인 주는 아예 넣지 않습니다 (판단 불가 = 분모 제외).
    """
    dates, closes = prices.get("dates") or [], prices.get("close") or []
    if not dates:
        return {}
    weeks = weekly_indices(dates)
    week_closes = [closes[i] for i in weeks]
    flags: dict[str, bool] = {}
    for k in range(WARMUP_WEEKS - 1, len(weeks)):
        price = week_closes[k]
        averages = [mean(week_closes[k - n + 1: k + 1]) for n in MA_WEEKS]
        ordered = all(
            averages[i] > averages[i + 1] for i in range(len(averages) - 1)
        )
        flags[dates[weeks[k]]] = price > averages[0] and ordered
    return flags


def breadth_series(ds: dict, members: list[str]) -> list[tuple[str, float]]:
    """섹터 정배열 폭 시계열 [(주 마지막 거래일, 폭%)] — 판단 가능 주만."""
    per_ticker = {}
    for ticker in members:
        prices = ds["prices"].get(ticker)
        if prices:
            flags = aligned_flags(prices)
            if flags:
                per_ticker[ticker] = flags
    if not per_ticker:
        return []
    # 기준지수의 주 격자를 공통 축으로 씁니다 (섹터마다 같은 날짜로 비교)
    grid = [ds["prices"][ds["benchmark"]]["dates"][i]
            for i in weekly_indices(ds["prices"][ds["benchmark"]]["dates"])]
    out: list[tuple[str, float]] = []
    for day in grid:
        values = [flags[day] for flags in per_ticker.values() if day in flags]
        if len(values) < MIN_MEMBERS:
            continue                       # 판단 불가 — 값을 만들지 않는다
        out.append((day, sum(values) / len(values) * 100.0))
    return out


def _forward_excess(prices: dict, spy: dict, day: str) -> float | None:
    """day 다음 거래일 진입 → FORWARD_DAYS 뒤. SPY 대비 초과 %p."""
    dates, closes = prices["dates"], prices["close"]
    entry = bisect.bisect_right(dates, day)
    if entry >= len(dates) or entry + FORWARD_DAYS >= len(dates):
        return None                        # 진입 불가 또는 우측 검열
    exit_index = entry + FORWARD_DAYS
    stock = (closes[exit_index] / closes[entry] - 1.0) * 100.0
    si = bisect.bisect_right(spy["dates"], dates[entry]) - 1
    sj = bisect.bisect_right(spy["dates"], dates[exit_index]) - 1
    if si < 0 or sj <= si:
        return None
    market = (spy["close"][sj] / spy["close"][si] - 1.0) * 100.0
    return stock - market


def sector_forward_excess(ds: dict, members: list[str], day: str) -> float | None:
    """섹터 동일가중 평균 초과수익 (%p). 유효 종목 3개 미만이면 없음."""
    spy = ds["prices"][ds["benchmark"]]
    values = []
    for ticker in members:
        prices = ds["prices"].get(ticker)
        if not prices or not prices.get("dates"):
            continue
        excess = _forward_excess(prices, spy, day)
        if excess is not None:
            values.append(excess)
    if len(values) < MIN_MEMBERS:
        return None
    return mean(values)


def sector_members(ds: dict, group: str = "섹터") -> dict[str, list[str]]:
    """섹터(또는 테마)별 종목 묶음."""
    out: dict[str, list[str]] = {}
    for ticker in ds["tickers"]:
        if group == "테마":
            name = cfg.theme_of(ticker)
        else:
            name = cfg.SECTORS.get(ticker, "미분류")
        out.setdefault(name, []).append(ticker)
    return out


def collect_breadth_events(ds: dict, group: str = "섹터") -> list[dict]:
    """H11 사건 목록: 판단 가능한 모든 (섹터, 주)와 그 주의 신호 여부.

    사건: {"섹터", "announced"(주 마지막 거래일), "breadth",
           "cross60", "cross80", "excess"}
    · cross60/80 = 이번 주 폭 ≥ 문턱 이면서 직전 주는 문턱 미만
    · excess = 250거래일 뒤 섹터 동일가중 초과수익 (우측 검열은 제외)
    """
    events: list[dict] = []
    for name, members in sector_members(ds, group).items():
        series = breadth_series(ds, members)
        for index in range(1, len(series)):
            day, breadth = series[index]
            previous = series[index - 1][1]
            excess = sector_forward_excess(ds, members, day)
            if excess is None:
                continue                   # 우측 검열 — 세지 않는다
            events.append({
                "섹터": name,
                "announced": day,
                "breadth": round(breadth, 1),
                "cross60": breadth >= H11_THRESHOLD and previous < H11_THRESHOLD,
                "cross80": breadth >= H11B_THRESHOLD and previous < H11B_THRESHOLD,
                "excess": excess,
            })
    events.sort(key=lambda e: e["announced"])
    return events


def current_breadth(ds: dict, group: str = "섹터") -> list[dict]:
    """지금 각 섹터의 정배열 폭과 상태 (화면용 — 판단 아님).

    상태: "신호 발생"(이번 주 처음 60% 돌파) · "정배열 다수(60%+)" ·
          "쌓이는 중" · "약함"
    """
    rows = []
    for name, members in sector_members(ds, group).items():
        series = breadth_series(ds, members)
        if not series:
            rows.append({"섹터": name, "종목수": len(members), "폭": None,
                         "직전폭": None, "상태": "판단 불가 (이력 부족)"})
            continue
        day, breadth = series[-1]
        previous = series[-2][1] if len(series) > 1 else None
        if previous is not None and breadth >= H11_THRESHOLD > previous:
            status = "🔥 신호 발생 (이번 주 60% 첫 돌파)"
        elif breadth >= H11B_THRESHOLD:
            status = "정배열 압도 (80%+)"
        elif breadth >= H11_THRESHOLD:
            status = "정배열 다수 (60%+)"
        elif previous is not None and breadth > previous:
            status = "쌓이는 중"
        else:
            status = "약함"
        rows.append({
            "섹터": name,
            "종목수": len(members),
            "폭": round(breadth, 1),
            "직전폭": round(previous, 1) if previous is not None else None,
            "상태": status,
            "기준일": day,
        })
    rows.sort(key=lambda r: (r["폭"] is not None, r["폭"] or 0), reverse=True)
    return rows


# ---------------------------------------------------------------------------
# 사이클 추적 시계열 (37차) — 정배열 폭 · 이익 델타 폭 · 상대수익
# ---------------------------------------------------------------------------
DELTA_FRESH_DAYS = 140      # 발표 신선도 (게이지와 같은 기준)


def _delta_series(ds: dict, ticker: str) -> list[tuple[str, bool]]:
    """종목의 [(발표일, 델타 상승 여부)] — 연속 분기끼리만 비교."""
    rows = ds["quarters"].get(ticker) or []
    yardstick = yardstick_of_safe(ds, ticker)
    if not yardstick:
        return []
    values = sorted(
        (r["announced_date"], r[yardstick]) for r in rows
        if r.get(yardstick) is not None and r.get("announced_date")
    )
    out = []
    for i in range(1, len(values)):
        gap = (date.fromisoformat(values[i][0])
               - date.fromisoformat(values[i - 1][0])).days
        if 55 <= gap <= 200:            # 연속 분기만 (사고 7 규칙)
            out.append((values[i][0], values[i][1] > values[i - 1][1]))
    return out


def yardstick_of_safe(ds: dict, ticker: str) -> str | None:
    import measure_engine as me
    return me.yardstick_of(ds["quarters"].get(ticker) or [])


def month_end_days(dates: list[str]) -> list[str]:
    """각 달의 마지막 거래일."""
    out, previous = [], None
    for index, day in enumerate(dates):
        if previous is not None and day[:7] != previous[0]:
            out.append(dates[previous[1]])
        previous = (day[:7], index)
    if previous:
        out.append(dates[previous[1]])
    return out


def cycle_series(ds: dict, members: list[str], base_day: str,
                 since: str = "2024-01-01") -> list[dict]:
    """월별 [{"월", "정배열폭", "델타폭", "상대수익"}] — 그 시점까지 알려진 값만.

    상대수익 = base_day 이후 동일가중 누적 수익 − SPY 누적 수익 (%p).
    미래 엿보기 금지: 델타는 발표일이 그 시점 이전인 것만, 140일 넘으면 제외.
    """
    spy = ds["prices"][ds["benchmark"]]
    deltas = {t: _delta_series(ds, t) for t in members}
    aligns = {}
    for ticker in members:
        prices = ds["prices"].get(ticker)
        if prices:
            flags = aligned_flags(prices)
            if flags:
                aligns[ticker] = (sorted(flags), flags)

    def delta_at(ticker, day):
        series = deltas.get(ticker) or []
        days = [s[0] for s in series]
        index = bisect.bisect_right(days, day) - 1
        if index < 0:
            return None
        if (date.fromisoformat(day)
                - date.fromisoformat(days[index])).days > DELTA_FRESH_DAYS:
            return None
        return series[index][1]

    def aligned_at(ticker, day):
        entry = aligns.get(ticker)
        if not entry:
            return None
        keys, flags = entry
        index = bisect.bisect_right(keys, day) - 1
        return flags[keys[index]] if index >= 0 else None

    def relative(day):
        gains = []
        for ticker in members:
            prices = ds["prices"].get(ticker)
            if not prices:
                continue
            dates, closes = prices["dates"], prices["close"]
            i0 = bisect.bisect_right(dates, base_day) - 1
            i1 = bisect.bisect_right(dates, day) - 1
            if i0 < 0 or i1 <= i0:
                continue
            gains.append((closes[i1] / closes[i0] - 1) * 100.0)
        if not gains:
            return None
        s0 = bisect.bisect_right(spy["dates"], base_day) - 1
        s1 = bisect.bisect_right(spy["dates"], day) - 1
        if s0 < 0 or s1 <= s0:
            return None
        market = (spy["close"][s1] / spy["close"][s0] - 1) * 100.0
        return mean(gains) - market

    out = []
    for day in month_end_days(spy["dates"]):
        if day < since:
            continue
        decidable = [t for t in members
                     if delta_at(t, day) is not None and aligned_at(t, day) is not None]
        if len(decidable) < MIN_MEMBERS:
            continue
        out.append({
            "월": day,
            "정배열폭": round(sum(1 for t in decidable if aligned_at(t, day))
                              / len(decidable) * 100.0, 1),
            "델타폭": round(sum(1 for t in decidable if delta_at(t, day))
                            / len(decidable) * 100.0, 1),
            "상대수익": (round(relative(day), 1)
                         if relative(day) is not None else None),
        })
    return out


def ai_members(ds: dict) -> tuple[list[str], list[str]]:
    """(AI 사이클 종목, 비AI 종목) — config.theme_of 기준."""
    ai = [t for t in ds["tickers"] if cfg.theme_of(t).startswith("AI-")]
    other = [t for t in ds["tickers"] if not cfg.theme_of(t).startswith("AI-")]
    return ai, other
