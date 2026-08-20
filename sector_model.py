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
import measure_engine as me

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


def aligned_flags_chart(prices: dict) -> dict[str, bool]:
    """**39차 등록 정의**의 정배열 {주 마지막 거래일: True/False}.

    위의 `aligned_flags` 와 일부러 **따로 둡니다.** 둘은 서로 다른 등록의
    장치입니다 — 한쪽을 고쳐 다른 쪽까지 바꾸면 사전 등록이 무너집니다.

      · `aligned_flags`       : 33차 등록(H11·H12·H14 정배열 **폭**)
                                조건에 `주봉 종가 > 4주선` 이 들어 있습니다.
      · `aligned_flags_chart` : 39차 등록(H15·H16·H18 정배열 **완성**)
                                **이평선 배열만** 봅니다. 종가 조건 없음.

    39차에서 확인한 대로, 종가 조건이 있으면 주가가 잠깐 눌릴 때마다
    상태가 깨져 "완성"이 수십 번 반복됩니다(실측: COHR 8번 vs 1번).
    사람이 차트에서 보는 정배열은 이동평균선의 배열이므로, 완성 시점을
    묻는 등록에서는 이 함수를 씁니다.
    """
    dates, closes = prices.get("dates") or [], prices.get("close") or []
    if not dates:
        return {}
    weeks = weekly_indices(dates)
    week_closes = [closes[i] for i in weeks]
    flags: dict[str, bool] = {}
    for k in range(WARMUP_WEEKS - 1, len(weeks)):
        averages = [mean(week_closes[k - n + 1: k + 1]) for n in MA_WEEKS]
        flags[dates[weeks[k]]] = all(
            averages[i] > averages[i + 1] for i in range(len(averages) - 1)
        )
    return flags


def gap_over_52w(prices: dict, day: str) -> float | None:
    """그 주 종가가 **52주선 위로 몇 % 떨어져 있나** (H18 의 신호 변수).

    완성 시점에 **미리 알 수 있는** 값입니다 (사후 정보 아님).
    52주 이력이 없으면 없음(None) — 만들지 않습니다.
    """
    dates, closes = prices.get("dates") or [], prices.get("close") or []
    if not dates:
        return None
    weeks = weekly_indices(dates)
    week_closes = [closes[i] for i in weeks]
    for k in range(WARMUP_WEEKS - 1, len(weeks)):
        if dates[weeks[k]] != day:
            continue
        ma52 = mean(week_closes[k - MA_WEEKS[-1] + 1: k + 1])
        if ma52 <= 0:
            return None
        return (week_closes[k] / ma52 - 1.0) * 100.0
    return None


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


def market_breadth_series(ds: dict) -> list[tuple[str, float]]:
    """**시장 전체** 정배열 폭 시계열 (121차 — H24 의 장세 게이지).

    섹터가 아니라 기준지수를 뺀 전 종목으로 폭을 잽니다. 정배열 정의는
    33차 등록(aligned_flags)을 그대로 씁니다 — 새 정의를 만들지 않습니다.
    """
    members = [t for t in ds["prices"] if t != ds["benchmark"]]
    return breadth_series(ds, members)


def attach_market_breadth(ds: dict, events: list[dict]) -> None:
    """발표 사건마다 그 발표일 이전 가장 가까운 주의 시장 폭을 붙입니다.

    (121차 등록) 키 이름은 "장세폭"(%). 발표일보다 앞선 주가 없으면
    None — 없는 값은 만들지 않습니다 (헌법 1조).
    """
    series = market_breadth_series(ds)
    days = [d for d, _ in series]
    values = [v for _, v in series]
    for event in events:
        i = bisect.bisect_right(days, event["announced"]) - 1
        event["장세폭"] = values[i] if i >= 0 else None


def _forward_excess(prices: dict, spy: dict, day: str,
                    days: int = FORWARD_DAYS) -> float | None:
    """day 다음 거래일 진입 → days 거래일 뒤. SPY 대비 초과 %p.

    기본값은 33차 등록의 250거래일이고, 완성 사건(39차 이후)은 측정
    기본형인 60거래일도 함께 재려고 days 를 받습니다.
    """
    dates, closes = prices["dates"], prices["close"]
    entry = bisect.bisect_right(dates, day)
    if entry >= len(dates) or entry + days >= len(dates):
        return None                        # 진입 불가 또는 우측 검열
    exit_index = entry + days
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
        # 사고 7 규칙: 측정 장치와 **같은** 간격을 써야 한다.
        # 200일 창을 쓰면 반년(182일) 점프가 연속 분기로 통과해 사이 분기를
        # 건너뛴 가짜 델타가 생긴다 (41차 검증단 실측: 72쌍, 그중 37쌍은
        # 사이 분기가 존재하는데 잣대값이 비어서 생긴 것).
        if me.SPACING_MIN_DAYS <= gap <= me.SPACING_MAX_DAYS:
            out.append((values[i][0], values[i][1] > values[i - 1][1]))
    return out


def yardstick_of_safe(ds: dict, ticker: str) -> str | None:
    import measure_engine as me
    return me.yardstick_of(ds["quarters"].get(ticker) or [])


# ---------------------------------------------------------------------------
# 정배열 **완성** 사건 전수 (39·40·43차 등록의 공통 장치)
# ---------------------------------------------------------------------------
# 이 목록 하나로 H15(델타 동반) · H16(바닥 30주) · H18(52주선 이격도)를
# 모두 잽니다. 장치가 하나여야 셋을 같은 잣대로 비교할 수 있습니다.
H18_GAP_MIN = 30.0          # H18 신호 문턱 — 완성 시점 52주선 이격도 30% 이상
H18_START_DAY = "2026-08-15"   # 이 날 **뒤**의 완성만 H18 판정 표본 (원칙 5)
SHORT_FORWARD_DAYS = 60     # 측정 기본형 창 (전략.md 고정값)


def completion_events(ds: dict) -> list[dict]:
    """정배열 **완성**(39차 정의) 사건 전수 목록.

    각 사건에 담기는 것 — 전부 **완성 시점에 알 수 있는 값**입니다:
      · 이격도   완성 주 종가가 52주선 위로 몇 %  (H18 신호 변수)
      · 델타     그때까지 발표된 최신 분기 이익이 직전 연속 분기보다 늘었나
      · 바닥주   완성 직전에 정배열이 아니었던 연속 주수 (H16 관찰용)
    사후에만 알 수 있는 값은 이름에 그렇게 적습니다:
      · 유지주   완성 후 정배열이 깨질 때까지의 주수 — **매매 규칙으로 쓸 수
                 없습니다**(41차 검증: 결과를 완전히 가르지만 사후 정보).
    표적은 두 가지를 함께 담습니다:
      · 초과60   측정 기본형(60거래일) — 판정은 이쪽으로 합니다
      · 초과250  1년 창 — 참고용(최근 국면은 아직 창이 안 끝나 비어 있음)
    """
    spy = ds["prices"][ds["benchmark"]]
    events: list[dict] = []
    for ticker in ds["tickers"]:
        prices = ds["prices"].get(ticker)
        if not prices or not prices.get("dates"):
            continue
        flags = aligned_flags_chart(prices)
        if not flags:
            continue
        weeks = sorted(flags)
        series = _delta_series(ds, ticker)
        delta_days = [s[0] for s in series]
        base = 0
        for index, day in enumerate(weeks):
            if not flags[day]:
                base += 1
                continue
            if index > 0 and not flags[weeks[index - 1]]:
                hold = 0
                for j in range(index, len(weeks)):
                    if not flags[weeks[j]]:
                        break
                    hold += 1
                position = bisect.bisect_right(delta_days, day) - 1
                delta = None
                if position >= 0 and (
                    date.fromisoformat(day)
                    - date.fromisoformat(delta_days[position])
                ).days <= DELTA_FRESH_DAYS:
                    delta = series[position][1]
                events.append({
                    "ticker": ticker,
                    "섹터": cfg.SECTORS.get(ticker, "미분류"),
                    "테마": cfg.theme_of(ticker),
                    "day": day,
                    "이격도": gap_over_52w(prices, day),
                    "델타": delta,
                    "바닥주": base,
                    "유지주": hold,
                    "초과60": _forward_excess(prices, spy, day,
                                              SHORT_FORWARD_DAYS),
                    "초과250": _forward_excess(prices, spy, day, FORWARD_DAYS),
                })
            base = 0
    events.sort(key=lambda e: (e["day"], e["ticker"]))
    return events


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


# ---------------------------------------------------------------------------
# H14 (38차 등록) — 주도 교체 "확인" 신호
# ---------------------------------------------------------------------------
CONFIRM_PAST_DAYS = 63      # 전제 (a): 직전 3개월
CONFIRM_ALIGN_MIN = 40.0    # (b) 정배열 폭 문턱
CONFIRM_DELTA_MIN = 50.0    # (c) 이익 델타 폭 문턱


def _group_excess(ds: dict, members: list[str], day: str,
                  window: int, forward: bool) -> float | None:
    """섹터 동일가중 초과수익 — forward=False 면 직전 window 거래일."""
    spy = ds["prices"][ds["benchmark"]]
    values = []
    for ticker in members:
        prices = ds["prices"].get(ticker)
        if not prices:
            continue
        dates, closes = prices["dates"], prices["close"]
        if forward:
            i0 = bisect.bisect_right(dates, day)
            if i0 >= len(dates) or i0 + window >= len(dates):
                continue
            i1 = i0 + window
        else:
            i1 = bisect.bisect_right(dates, day) - 1
            i0 = i1 - window
            if i0 < 0:
                continue
        s0 = bisect.bisect_right(spy["dates"], dates[i0]) - 1
        s1 = bisect.bisect_right(spy["dates"], dates[i1]) - 1
        if s0 < 0 or s1 <= s0:
            continue
        values.append((closes[i1] / closes[i0] - 1) * 100.0
                      - (spy["close"][s1] / spy["close"][s0] - 1) * 100.0)
    return mean(values) if len(values) >= MIN_MEMBERS else None


def confirmation_rows(ds: dict) -> list[dict]:
    """지금 각 묶음(섹터·테마)의 H14 확인 상태 (화면용 — 판단 아님).

    반환 행: {묶음, 종류, 3개월상대, 정배열폭, 직전정배열폭, 델타폭,
              직전델타폭, 전제, 정배열확인, 델타확인, 확인}
    """
    spy_dates = ds["prices"][ds["benchmark"]]["dates"]
    months = month_end_days(spy_dates)
    if len(months) < 2:
        return []
    today, previous_month = spy_dates[-1], months[-2]
    groups = [(name, members, "섹터")
              for name, members in sector_members(ds, "섹터").items()]
    groups += [(name, members, "테마")
               for name, members in sector_members(ds, "테마").items()]

    rows = []
    # 종목별 중간 계산을 한 번만 하고 나눠 씁니다 (104차 — 값은 그대로).
    # 섹터·테마 × 지금·직전 으로 같은 종목을 네 번 계산하고 있었습니다.
    memo: dict = {}
    for name, members, kind in groups:
        now = _breadths_at(ds, members, today, memo)
        before = _breadths_at(ds, members, previous_month, memo)
        if now is None or before is None:
            continue
        past = _group_excess(ds, members, today, CONFIRM_PAST_DAYS, forward=False)
        if past is None:
            continue
        align_ok = now[0] >= CONFIRM_ALIGN_MIN and now[0] > before[0]
        delta_ok = now[1] >= CONFIRM_DELTA_MIN and now[1] > before[1]
        rows.append({
            "묶음": name, "종류": kind,
            "3개월상대": round(past, 1),
            "정배열폭": now[0], "직전정배열폭": before[0],
            "델타폭": now[1], "직전델타폭": before[1],
            "전제": past > 0,
            "정배열확인": align_ok, "델타확인": delta_ok,
            "확인": past > 0 and align_ok and delta_ok,
        })
    rows.sort(key=lambda r: (r["확인"], r["델타확인"], r["정배열확인"],
                             r["3개월상대"]), reverse=True)
    return rows


def _breadths_at(ds: dict, members: list[str], day: str, memo: dict | None = None):
    """(정배열 폭, 델타 폭) — 판단 가능 종목 3개 미만이면 None.

    memo: 종목별 중간 계산을 담아 두는 그릇 (104차 — 속도만 바꿉니다).

    ⚠️ 왜 필요한가: 이 함수는 종목마다 `aligned_flags`(이동평균)와
    `_delta_series`(분기 델타)를 다시 계산하는데, `confirmation_rows` 가
    **섹터·테마 × 지금·직전** 으로 네 번 부릅니다. 실측: 종목 160개인데
    호출이 **640회** (정확히 4배), 전체 27.9초.

    같은 종목의 같은 계산은 결과가 같으므로 한 번만 하고 담아 둡니다.
    **값은 하나도 안 바뀝니다** — 시험으로 못박습니다.
    """
    import measure_engine as me

    if memo is None:
        memo = {}

    decidable = aligned = delta_up = 0
    for ticker in members:
        prices = ds["prices"].get(ticker)
        if not prices:
            continue
        담김 = memo.get(ticker)
        if 담김 is None:
            _flags = aligned_flags(prices)
            담김 = (_flags, sorted(_flags) if _flags else [],
                   _delta_series(ds, ticker))
            memo[ticker] = 담김
        flags, keys, series = 담김
        if not flags:
            continue
        index = bisect.bisect_right(keys, day) - 1
        if index < 0:
            continue
        if not series:
            continue
        days = [s[0] for s in series]
        di = bisect.bisect_right(days, day) - 1
        if di < 0:
            continue
        if (date.fromisoformat(day)
                - date.fromisoformat(days[di])).days > DELTA_FRESH_DAYS:
            continue
        decidable += 1
        aligned += bool(flags[keys[index]])
        delta_up += bool(series[di][1])
    if decidable < MIN_MEMBERS:
        return None
    return (round(aligned / decidable * 100.0, 1),
            round(delta_up / decidable * 100.0, 1))
