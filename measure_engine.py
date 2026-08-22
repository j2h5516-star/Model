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

# H26 (143차 등록) — 125거래일(약 6개월) 창.
# 141차 탐색에서 창 길이를 훑어 보니 조정 EPS 신고점 **첫 돌파**가
# 125일에서만 채택 기준을 넘었습니다(25.8% 하한 21.1 vs 기준 17.4%
# 상한 18.4). 60일·250일에서는 안 넘습니다 — 창이 길어지면 기준선도
# 같이 올라가기 때문입니다(60일 10.0% → 250일 25.8%).
# ⚠️ 탐색은 채택 근거가 아니므로(헌법 5조), 이 창으로는 **탐색에 쓰지
#    않은 새 종목**만 판정합니다. 자세한 규칙은 judge.H26_* 에 있습니다.
H26_WINDOW_DAYS = 125
SURGE_PP = 20.0              # 폭등 = SPY 대비 +20%p
SURGE_AUX_PP = 30.0          # 보조 정의
SPACING_MIN_DAYS = 55        # 연속 분기로 인정할 최소 간격
SPACING_MAX_DAYS = 150       # 연속 분기로 인정할 최대 간격
FRESH_DAYS = 140             # 게이지: 발표가 이보다 오래되면 김빠진 것
GAUGE_FIXED_ON_PCT = 20.0    # H5 (기존 등록) — 고정 문턱
GAUGE_WARMUP_WEEKS = 52      # H5b — 이력이 이보다 짧으면 판단 불가

# 게이지를 계산할 **최소 종목 수** (100차 — 10년 확장이 드러낸 장치 결함).
#
# 무엇이 잘못됐었나: 게이지는 "실적 신기록을 낸 종목의 **비율**"인데,
# 분모가 몇 개든 상관없이 값을 냈습니다. 10년으로 늘리자 표본 앞머리가
# 드러났고, 실측이 이랬습니다.
#
#   연도   분모(종목 수) 평균   게이지 중앙값
#   2017        2.4              85.7%
#   2018       21.0              85.7%
#   2026       88.1              40.0%
#
# **2017년의 "시장 전체 실적 신기록 폭 85.7%"는 평균 2.4개 종목으로 잰
# 값입니다.** 시장의 폭이 아니라 두세 종목의 형편입니다.
#
# 이 허상이 한 번 들어가면 **영원히 남습니다.** H5b 의 문턱은 "그 시점
# 직전까지 이력의 중앙값"(확장 중앙값)이라, 앞머리의 85.7% 가 문턱을
# 끌어올려 2023~2026 내내 48~58% 에 머물게 합니다. 실제 게이지는 35~40%
# 이므로 **한 번도 못 넘습니다** — 96차에서 본 "2022-10 이후 신호 0건"의
# 정체가 이것입니다.
#
# 왜 하필 10인가: 새 숫자를 지어내지 않고 **이 저장소가 이미 사전 등록한
# 최소 표본(n≥10)** 을 그대로 씁니다. 결과를 보고 고른 문턱이 아닙니다
# (결과를 보고 고르면 그것이 바로 헌법 2조가 막는 사후 맞추기입니다).
#
# 분모가 이보다 작으면 값을 내지 않고 **없음**으로 둡니다 —
# "신선한 발표가 하나도 없으면 그 주는 None" 과 같은 규칙의 연장입니다.
GAUGE_MIN_TICKERS = 10
ENTRY_MAX_GAP_DAYS = 10      # 발표 후 이 일수 안에 거래일이 없으면 못 잰다
LADDER_MIN_QUARTERS = 8      # 12차 등록 — 잣대로 인정할 최소 보유 분기
LADDER = ("adj_eps", "adjusted_ebitda", "gaap_eps")   # 사다리 순서 (12차 등록)

# H23 (116차 등록) — "깊은 게이지"의 이력 문턱.
#
# 100차 ③에서 실측한 편향: 이력이 짧으면 신고점이 수학적으로 쉽습니다
# (1~5번째 판단 가능한 발표는 56.8% 가 신고점, 12번째 이후는 40.4% —
# 단조 감소). 그래서 게이지 앞머리가 시장 사실이 아니라 허상으로 붑니다.
# 깊은 게이지는 그 종목의 GAUGE_MIN_HISTORY 번째 이상 판단 가능한
# 발표만 분자·분모에 넣습니다. 8 은 결과를 보고 고른 수가 아니라
# 이미 등록돼 있던 LADDER_MIN_QUARTERS 를 그대로 쓴 것입니다 (100차 ⑥).
GAUGE_MIN_HISTORY = LADDER_MIN_QUARTERS


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
            # 신고점의 **폭** — 직전 정점을 몇 % 넘었나 (H22·H22b, 109차 등록).
            #
            # 왜 필요한가: `new_high` 는 참/거짓이라 직전 정점을 1% 넘은
            # 것과 50% 넘은 것이 **똑같이 "신고점"** 이었습니다. 109차
            # 탐색(3,100건)에서 폭이 단조로 듣는 것이 나왔습니다 —
            #   폭 1~3% 7.5% · 3~5% 7.8% · 10~20% 16.1% · 20%↑ 18.5%
            #   (기준선 13.0% · 신고점 이진 전체 12.6%)
            # 간신히 넘긴 것은 기준선보다 **나쁘고**, 크게 넘은 것만
            # 뚜렷이 웃돕니다. 둘이 한 바구니에 섞여 상쇄되고 있었습니다.
            #
            # 정점이 음수면 비율의 뜻이 뒤집히므로 **없음**으로 둡니다
            # (적자에서 적자로 옮겨간 것을 "몇 % 성장"이라 부를 수 없습니다).
            폭 = None
            if new_high and past_peak and past_peak > 0:
                폭 = (current - past_peak) / past_peak * 100.0
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
                    "신고점폭": 폭,
                    "decidable": current is not None and had_prior,
                }
            )
    states.sort(key=lambda s: s["announced"])
    # 몇 번째 판단 가능한 발표인가 (누적) — H23(116차 등록)이 씁니다.
    # 판단 불가 발표는 세지 않고 값도 없음으로 둡니다.
    깊이 = 0
    for state in states:
        if state["decidable"]:
            깊이 += 1
            state["판단횟수"] = 깊이
        else:
            state["판단횟수"] = None
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
def window_return(dates: list[str], closes: list[float], announce: str,
                  days: int | None = None):
    """(수익률%, 진입일, 청산일). 못 재면 (None, 이유, None).

    days 를 주면 그 길이의 창으로 잽니다. 기본값(None)은 등록된
    기본형 60거래일 — **기존 가설의 표본을 한 칸도 바꾸지 않기 위해**
    기본 동작을 그대로 둡니다 (143차).
    """
    entry = bisect.bisect_right(dates, str(announce)[:10])   # 발표일 **다음** 거래일
    if entry >= len(dates):
        return None, "진입일없음", None
    # 주가 이력이 발표보다 늦게 시작하면 "다음 거래일"이 몇 달 뒤가 됩니다
    if (_to_date(dates[entry]) - _to_date(announce)).days > ENTRY_MAX_GAP_DAYS:
        return None, "주가시작전", None
    exit_ = entry + (WINDOW_TRADING_DAYS if days is None else int(days))
    if exit_ >= len(dates):
        return None, "우측검열", None    # 창이 아직 안 끝남 — 세지 않는다
    pct = (closes[exit_] / closes[entry] - 1.0) * 100.0
    return pct, dates[entry], dates[exit_]


def excess_return(prices: dict, spy: dict, announce: str,
                  days: int | None = None):
    """종목 수익 − 같은 구간 SPY 수익 (%p). 못 재면 (None, 이유).

    days 를 주면 그 길이의 창으로 잽니다 (기본은 등록된 60거래일).
    """
    stock_pct, entry_date, exit_date = window_return(
        prices["dates"], prices["close"], announce, days=days
    )
    if stock_pct is None:
        return None, entry_date              # entry_date 자리에 이유
    si = bisect.bisect_right(spy["dates"], entry_date) - 1
    sj = bisect.bisect_right(spy["dates"], exit_date) - 1
    if si < 0 or sj <= si:
        return None, "SPY정렬실패"
    spy_pct = (spy["close"][sj] / spy["close"][si] - 1.0) * 100.0
    return stock_pct - spy_pct, (entry_date, exit_date)


def backward_excess(prices: dict, spy: dict, day: str,
                    days: int = 60) -> float | None:
    """발표일 **이전** days 거래일 동안의 SPY 대비 초과수익 (%p).

    (124차 — H25 의 "미리 달려온 폭" 측정.) 측정 창이 발표일에 끝나고
    결과 창(excess_return)은 발표일 다음 거래일에 시작하므로 둘은 겹치지
    않습니다. 이력이 days 거래일보다 짧으면 None — 없는 값은 만들지
    않습니다.
    """
    j = bisect.bisect_right(prices["dates"], str(day)[:10]) - 1
    i = j - days
    if j < 0 or i < 0:
        return None
    start, end = prices["dates"][i], prices["dates"][j]
    stock_pct = (prices["close"][j] / prices["close"][i] - 1.0) * 100.0
    si = bisect.bisect_right(spy["dates"], start) - 1
    sj = bisect.bisect_right(spy["dates"], end) - 1
    if si < 0 or sj <= si:
        return None
    spy_pct = (spy["close"][sj] / spy["close"][si] - 1.0) * 100.0
    return stock_pct - spy_pct


def attach_runup(ds: dict, events: list[dict], days: int = 60) -> None:
    """발표 사건마다 발표 전 days 거래일의 초과수익을 "런업" 키로 붙입니다."""
    spy = ds["prices"][ds["benchmark"]]
    for event in events:
        prices = ds["prices"].get(event["ticker"])
        event["런업"] = (
            backward_excess(prices, spy, event["announced"], days)
            if prices else None
        )


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


def gauge_series(
    ds: dict,
    tickers: list[str] | None = None,
    min_tickers: int = GAUGE_MIN_TICKERS,
    min_history: int = 0,
) -> dict:
    """실적 폭 게이지 시계열: {"weeks": [...], "values": [...]}.

    각 주 마지막 거래일에, 최근 발표가 140일 이내로 신선한 종목 중
    **최근 발표가 TTM 신고점 상태였던 종목의 비율**(%). 신선한 발표가
    하나도 없으면 그 주는 None (없음은 없음으로).

    tickers 로 부분집합(예: 한 섹터)을 주면 그 무리만의 폭을 잽니다.
    ⚠️ 섹터별 게이지는 **관찰용**입니다 — 사전 등록된 가설이 아니므로
    판정(judge)에는 쓰지 않고 계기판에 사실로만 표시합니다.

    min_tickers: 이 수보다 분모가 작은 주는 **없음**으로 둡니다 (100차).
      기본값은 시장 전체 게이지용(GAUGE_MIN_TICKERS=10)입니다.
      섹터 게이지는 원래 종목이 적고(1~3개도 있음) 화면에 **종목수를 함께
      보여 주므로** 부르는 쪽에서 1 로 낮춥니다 — 판정에 안 쓰이는 값을
      최소치로 죽이면 관찰 자체가 사라지기 때문입니다.

    min_history: 그 종목의 **몇 번째 이상** 판단 가능한 발표만 넣을 것인가
      (H23, 116차 등록). 0 이면 기존 게이지 그대로입니다. 이력이 얕은
      발표는 분자·분모 **양쪽에서** 뺍니다 — 분자만 빼면 비율이 거짓말을
      합니다.
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
            if min_history and (state.get("판단횟수") or 0) < min_history:
                continue          # 이력이 얕은 발표는 안 센다 (H23)
            fresh_total += 1
            fresh_newhigh += bool(state["new_high"])
        # 분모가 최소치에 못 미치면 **없음** (100차). 비율은 분모가 작을수록
        # 시장이 아니라 몇 종목의 형편을 말합니다 — 자세한 실측은
        # GAUGE_MIN_TICKERS 주석에 적어 두었습니다.
        values.append(
            round(fresh_newhigh / fresh_total * 100.0, 1)
            if fresh_total >= min_tickers
            else None
        )
    return {"weeks": weeks, "values": values}


def below_52wk_ma(prices: dict, announce: str) -> bool | None:
    """발표 시점 주봉 종가가 52주 이동평균 아래인가 (H9 — 21차 등록).

    주봉 = 각 주 마지막 거래일 종가. 발표일까지의 주봉만 씁니다 (미래 금지).
    이력이 52주 미만이면 판단 불가(None) — H9 표본에서 제외.
    """
    upto = str(announce)[:10]
    weeks: list[float] = []
    current = None
    for day, close in zip(prices["dates"], prices["close"]):
        if day > upto:
            break
        iso = date.fromisoformat(day).isocalendar()
        key = (iso[0], iso[1])
        if key != current:
            weeks.append(close)
            current = key
        else:
            weeks[-1] = close
    if len(weeks) < 52:
        return None
    return weeks[-1] < sum(weeks[-52:]) / 52.0


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
           "h5", "h5b", "below52", "신고점폭", "excess"}

    잣대는 12차 등록의 사다리로 종목마다 하나로 고정됩니다. 게이지(H5·H5b)는
    등록 정의 그대로 **조정 EPS 잣대**의 폭만 잽니다 (사다리와 무관).
    """
    spy = ds["prices"].get(ds["benchmark"])
    series = gauge_series(ds)
    # H23 (116차 등록) — 이력 8번째 이상 발표만으로 계산한 "깊은 게이지".
    # 기존 게이지와 나란히 계산해 사건마다 h5b_깊은 으로 붙입니다.
    # 기존 h5·h5b 는 하나도 안 바뀝니다 (등록된 가설의 표본 보존).
    deep_series = gauge_series(ds, min_history=GAUGE_MIN_HISTORY)
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
                    # H23 (116차 등록) — 깊은 게이지의 H5b 규칙
                    "h5b_깊은": gauge_h5b_on(deep_series, state["announced"]),
                    # H9 (21차 등록): 발표 시점 주봉 종가 < 52주 이동평균
                    "below52": below_52wk_ma(prices, state["announced"]),
                    # H22·H22b (109차 등록) — 신고점을 직전 정점 대비 몇 % 넘었나
                    "신고점폭": state.get("신고점폭"),
                    "excess": excess,
                    # H26 (143차 등록) — 125거래일 창. **기존 excess(60일)는
                    # 한 칸도 안 바뀝니다.** 창이 더 길어 최근 사건은 아직
                    # 안 끝나 None 이 됩니다(우측검열) — 지어내지 않습니다.
                    "초과125": excess_return(
                        prices, spy, state["announced"],
                        days=H26_WINDOW_DAYS)[0],
                }
            )
    events.sort(key=lambda e: e["announced"])
    return events, skipped


def collect_metric_events(ds: dict, field: str,
                          min_quarters: int = LADDER_MIN_QUARTERS) -> list[dict]:
    """한 지표(field)만으로 발표 사건을 수집합니다 — H10(23차 등록)용.

    잣대 사다리와 별도의 사건 목록입니다. collect_events 가 만드는 목록에
    섞이지 않으므로 H2~H9 의 표본을 오염시키지 않습니다. 사건 모양은
    collect_events 와 같되 잣대 칸에 field 이름이 들어갑니다.
    """
    spy = ds["prices"].get(ds["benchmark"])
    events: list[dict] = []
    for ticker in ds["tickers"]:
        rows = ds["quarters"].get(ticker) or []
        if sum(1 for r in rows if r.get(field) is not None) < min_quarters:
            continue                     # 등록 조건: 값이 8분기 이상인 종목만
        prices = ds["prices"].get(ticker)
        if not prices or not prices.get("dates"):
            continue
        for state in earnings_states(rows, field=field):
            excess, _detail = excess_return(prices, spy, state["announced"])
            if excess is None:
                continue
            events.append(
                {
                    "ticker": ticker,
                    "잣대": field,
                    "announced": state["announced"],
                    "new_high": state["new_high"],
                    "newhigh_streak": state["newhigh_streak"],
                    "below52": below_52wk_ma(prices, state["announced"]),
                    "excess": excess,
                }
            )
    events.sort(key=lambda e: e["announced"])
    return events
