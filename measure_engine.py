"""
measure_engine.py — 측정 장치 (v3 5단계, 설계도.md ②)
======================================================

헌법 0장의 질문을 층별로 잽니다:
  · 아래층: 이 발표가 TTM 조정 EPS 의 신고점(H2)·첫 돌파(H2b)인가
  · 위층  : 발표 시점의 실적 폭 게이지(H5 고정 문턱 · H5b 자기 이력 중앙값)
  · 수익  : 발표 다음 거래일 진입 · 60거래일 · SPY 대비 초과수익

모든 문턱·창·정의는 **측정결과.md 11차 사전 등록**에서 옮겨 적은
것입니다. 여기서 값을 바꾸려면 새 사전 등록을 먼저 머지해야 합니다.

경계 (설계도.md 3장):
  · 입력은 데이터 계층(dataset.build)의 정돈 표만 — snapshot 직접 읽기 금지
  · 채택/미채택 판정은 하지 않습니다 (그건 judge.py 의 일)
  · 있는 숫자끼리의 산수만 합니다 — 값을 만들지 않습니다 (창작 금지)

사고 백서의 회귀 지점 (시험이 지킵니다):
  · 사고 6 신고점 망각 — 구간이 끊겨도 TTM 정점은 잊지 않는다
  · 사고 7 가짜 TTM   — 몇 년 떨어진 분기를 이웃으로 만들지 않는다
"""

from __future__ import annotations

import bisect
from datetime import date
from statistics import median

# --- 사전 등록 상수 (측정결과.md 11차 — 옮겨 적음, 여기서 고치지 않는다) ---
WINDOW_TRADING_DAYS = 60     # 보유 창
SURGE_PP = 20.0              # 폭등 = SPY 대비 +20%p
SURGE_AUX_PP = 30.0          # 보조 정의
SPACING_MIN_DAYS = 55        # 연속 분기로 인정할 최소 간격
SPACING_MAX_DAYS = 150       # 연속 분기로 인정할 최대 간격
FRESH_DAYS = 140             # 게이지: 발표가 이보다 오래되면 김빠진 것
GAUGE_FIXED_ON_PCT = 20.0    # H5 (기존 등록) — 고정 문턱
GAUGE_WARMUP_WEEKS = 52      # H5b — 이력이 이보다 짧으면 판단 불가
ENTRY_MAX_GAP_DAYS = 10      # 발표 후 이 일수 안에 거래일이 없으면 못 잰다
LADDER_MIN_QUARTERS = 8      # 12차 등록 — 잣대로 인정할 최소 보유 분기
LADDER = ("adj_eps", "adjusted_ebitda", "gaap_eps")   # 사다리 순서 (12차 등록)


def _to_date(text: str) -> date:
    return date.fromisoformat(str(text)[:10])


# ---------------------------------------------------------------------------
# TTM — 진짜 연속 4분기 합만 (사고 7)
# ---------------------------------------------------------------------------
def eps_runs(rows: list[dict], field: str = "adj_eps") -> list[list[dict]]:
    """잣대(field)가 있는 분기를 **연속 구간**으로 쪼갭니다.

    간격이 55~150일을 벗어나면 사이에 분기가 빠진 것 — 구간을 끊습니다.
    끊긴 분기를 이어붙여 TTM 을 만들면 한 푼도 안 늘었는데 "가속"이
    나옵니다 (사고 7). 데이터 계층이 이미 시간순 정렬을 보장합니다.
    """
    usable = [r for r in rows if r.get(field) is not None and r.get("filing_date")]
    runs: list[list[dict]] = []
    current: list[dict] = []
    for row in usable:
        if current:
            gap = (_to_date(row["filing_date"]) - _to_date(current[-1]["filing_date"])).days
            if not (SPACING_MIN_DAYS <= gap <= SPACING_MAX_DAYS):
                runs.append(current)
                current = []
        current.append(row)
    if current:
        runs.append(current)
    return runs


def ttm_series(values: list[float]) -> list[float]:
    """연속 분기 값의 굴러가는 4분기 합. 4개 미만이면 빈 목록."""
    return [sum(values[i - 3 : i + 1]) for i in range(3, len(values))]


# ---------------------------------------------------------------------------
# 발표 상태 걷기 — 각 발표 시점에서 그때까지의 분기만 본다 (미래 금지)
# ---------------------------------------------------------------------------
def earnings_states(rows: list[dict], field: str = "adj_eps") -> list[dict]:
    """한 종목의 발표 시점 상태 목록 (시간순).

    각 항목: {"announced", "new_high", "newhigh_streak", "ttm", "decidable"}
      · new_high        = 이번 발표로 TTM 이 **수집 이력 내 과거 정점**을
                          넘었는가. 구간이 끊겨도 정점은 기억한다 (사고 6)
      · newhigh_streak  = 몇 발표 연속 신고점인가 (첫 돌파 = 1)
      · ttm             = 이번 발표 시점의 TTM (4분기 못 채우면 None)
      · decidable       = 신고점 여부를 **판단할 수 있는** 발표였는가
                          (TTM 이 있고, 비교할 과거 정점도 있음). 판단
                          불가 발표는 게이지 분모에서 빠집니다 (11차 등록)
    발표일 없는 분기는 상태를 못 만들지만 정점 기억에는 참여합니다.
    같은 발표일의 중복(정정 공시)은 한 번만 셉니다.
    """
    states: list[dict] = []
    past_peak: float | None = None
    seen: set[str] = set()
    for run in eps_runs(rows, field):
        streak = 0    # 구간이 새로 시작하면 연속 셈도 새로
        values: list[float] = []
        for row in run:
            values.append(row[field])
            ttm = ttm_series(values)
            current = ttm[-1] if ttm else None
            had_prior = past_peak is not None
            new_high = current is not None and had_prior and current > past_peak
            if current is not None and (past_peak is None or current > past_peak):
                past_peak = current
            streak = streak + 1 if new_high else 0

            announce = row.get("announced_date")
            if not announce:
                continue
            announce = str(announce)[:10]
            if announce in seen:
                continue
            seen.add(announce)
            states.append(
                {
                    "announced": announce,
                    "new_high": new_high,
                    "newhigh_streak": streak,
                    "ttm": current,
                    "decidable": current is not None and had_prior,
                }
            )
    states.sort(key=lambda s: s["announced"])
    return states


# ---------------------------------------------------------------------------
# 잣대 사다리 (12차 등록) — 회사마다 측정 잣대를 하나로 고정
# ---------------------------------------------------------------------------
def yardstick_of(rows: list[dict]) -> str | None:
    """이 종목의 측정 잣대를 사다리 규칙으로 정합니다.

    조정 EPS ≥ 8분기 → 조정 EPS, 아니면 조정 EBITDA ≥ 8분기 → EBITDA,
    아니면 GAAP EPS ≥ 8분기 → GAAP EPS, 전부 미달이면 None (측정 제외).
    한 종목 안에서 잣대를 절대 섞지 않습니다.
    """
    for field in LADDER:
        have = sum(1 for r in rows or [] if r.get(field) is not None)
        if have >= LADDER_MIN_QUARTERS:
            return field
    return None


# ---------------------------------------------------------------------------
# 수익률 창 — 발표 다음 거래일 진입, 60거래일, SPY 대비
# ---------------------------------------------------------------------------
def window_return(dates: list[str], closes: list[float], announce: str):
    """(수익률%, 진입일, 청산일). 못 재면 (None, 이유, None)."""
    entry = bisect.bisect_right(dates, str(announce)[:10])   # 발표일 **다음** 거래일
    if entry >= len(dates):
        return None, "진입일없음", None
    # 주가 이력이 발표보다 늦게 시작하면 "다음 거래일"이 몇 달 뒤가 됩니다
    if (_to_date(dates[entry]) - _to_date(announce)).days > ENTRY_MAX_GAP_DAYS:
        return None, "주가시작전", None
    exit_ = entry + WINDOW_TRADING_DAYS
    if exit_ >= len(dates):
        return None, "우측검열", None    # 창이 아직 안 끝남 — 세지 않는다
    pct = (closes[exit_] / closes[entry] - 1.0) * 100.0
    return pct, dates[entry], dates[exit_]


def excess_return(prices: dict, spy: dict, announce: str):
    """종목 수익 − 같은 구간 SPY 수익 (%p). 못 재면 (None, 이유)."""
    stock_pct, entry_date, exit_date = window_return(
        prices["dates"], prices["close"], announce
    )
    if stock_pct is None:
        return None, entry_date              # entry_date 자리에 이유
    si = bisect.bisect_right(spy["dates"], entry_date) - 1
    sj = bisect.bisect_right(spy["dates"], exit_date) - 1
    if si < 0 or sj <= si:
        return None, "SPY정렬실패"
    spy_pct = (spy["close"][sj] / spy["close"][si] - 1.0) * 100.0
    return stock_pct - spy_pct, (entry_date, exit_date)


# ---------------------------------------------------------------------------
# 위층 — 실적 폭 게이지 (주간 격자, 그 시점까지의 데이터만)
# ---------------------------------------------------------------------------
def weekly_grid(daily_dates: list[str]) -> list[str]:
    """일봉 날짜에서 각 주의 마지막 거래일만 뽑습니다."""
    weeks: list[str] = []
    current = None
    for day in daily_dates:
        iso = date.fromisoformat(day).isocalendar()
        key = (iso[0], iso[1])
        if key != current:
            weeks.append(day)
            current = key
        else:
            weeks[-1] = day
    return weeks


def gauge_series(ds: dict, tickers: list[str] | None = None) -> dict:
    """실적 폭 게이지 시계열: {"weeks": [...], "values": [...]}.

    각 주 마지막 거래일에, 최근 발표가 140일 이내로 신선한 종목 중
    **최근 발표가 TTM 신고점 상태였던 종목의 비율**(%). 신선한 발표가
    하나도 없으면 그 주는 None (없음은 없음으로).

    tickers 로 부분집합(예: 한 섹터)을 주면 그 무리만의 폭을 잽니다.
    ⚠️ 섹터별 게이지는 **관찰용**입니다 — 사전 등록된 가설이 아니므로
    판정(judge)에는 쓰지 않고 계기판에 사실로만 표시합니다.
    """
    per_ticker: dict[str, list[dict]] = {
        t: earnings_states(ds["quarters"].get(t) or [])
        for t in (tickers if tickers is not None else ds["tickers"])
    }
    weeks = weekly_grid(ds["prices"][ds["benchmark"]]["dates"])
    values: list[float | None] = []
    for week_end in weeks:
        fresh_total = 0
        fresh_newhigh = 0
        for states in per_ticker.values():
            idx = bisect.bisect_right([s["announced"] for s in states], week_end) - 1
            if idx < 0:
                continue
            state = states[idx]
            age = (_to_date(week_end) - _to_date(state["announced"])).days
            if age > FRESH_DAYS or not state["decidable"]:
                continue          # 김빠졌거나 판단 불가면 분모에서 뺀다
            fresh_total += 1
            fresh_newhigh += bool(state["new_high"])
        values.append(
            round(fresh_newhigh / fresh_total * 100.0, 1) if fresh_total else None
        )
    return {"weeks": weeks, "values": values}


def gauge_at(series: dict, day: str) -> float | None:
    """그 날짜 기준 가장 최근 주의 게이지 값."""
    idx = bisect.bisect_right(series["weeks"], str(day)[:10]) - 1
    return series["values"][idx] if idx >= 0 else None


def gauge_h5_on(series: dict, day: str) -> bool | None:
    """H5 (기존 등록): 게이지 ≥ 20%. 값이 없으면 판단 불가(None)."""
    value = gauge_at(series, day)
    return None if value is None else value >= GAUGE_FIXED_ON_PCT


def gauge_h5b_on(series: dict, day: str) -> bool | None:
    """H5b (11차 등록): 게이지가 **그 시점 직전까지 이력의 중앙값 초과**.

    직전 이력이 52주 미만이면 판단 불가(None) — 억지로 판정하지 않습니다.
    """
    idx = bisect.bisect_right(series["weeks"], str(day)[:10]) - 1
    if idx < 0 or series["values"][idx] is None:
        return None
    history = [v for v in series["values"][:idx] if v is not None]
    if len(history) < GAUGE_WARMUP_WEEKS:
        return None
    return series["values"][idx] > median(history)


# ---------------------------------------------------------------------------
# 사건 수집 — 발표 사건마다 층별 상태 + 이후 초과수익
# ---------------------------------------------------------------------------
def collect_events(ds: dict) -> tuple[list[dict], dict]:
    """모든 발표 사건 목록과 건너뛴 사유 집계를 돌려줍니다.

    사건: {"ticker", "잣대", "announced", "new_high", "newhigh_streak",
           "h5", "h5b", "excess"}

    잣대는 12차 등록의 사다리로 종목마다 하나로 고정됩니다. 게이지(H5·H5b)는
    등록 정의 그대로 **조정 EPS 잣대**의 폭만 잽니다 (사다리와 무관).
    """
    spy = ds["prices"].get(ds["benchmark"])
    series = gauge_series(ds)
    events: list[dict] = []
    skipped = {"주가없음": 0, "우측검열": 0, "주가시작전": 0,
               "잣대없음": 0, "기타": 0}

    for ticker in ds["tickers"]:
        rows = ds["quarters"].get(ticker) or []
        yardstick = yardstick_of(rows)
        if yardstick is None:
            skipped["잣대없음"] += 1        # 종목 단위 — 측정 제외 (12차 ④)
            continue
        prices = ds["prices"].get(ticker)
        states = earnings_states(rows, field=yardstick)
        if not prices or not prices.get("dates"):
            skipped["주가없음"] += len(states)
            continue
        for state in states:
            excess, detail = excess_return(prices, spy, state["announced"])
            if excess is None:
                skipped[detail if detail in skipped else "기타"] += 1
                continue
            events.append(
                {
                    "ticker": ticker,
                    "잣대": yardstick,
                    "announced": state["announced"],
                    "new_high": state["new_high"],
                    "newhigh_streak": state["newhigh_streak"],
                    "h5": gauge_h5_on(series, state["announced"]),
                    "h5b": gauge_h5b_on(series, state["announced"]),
                    "excess": excess,
                }
            )
    events.sort(key=lambda e: e["announced"])
    return events, skipped
