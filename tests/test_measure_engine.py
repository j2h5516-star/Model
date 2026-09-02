"""
test_measure_engine.py — 측정 장치 검증 · v3 5단계
===================================================

11차 사전 등록의 정의가 코드에 그대로 구현됐는지,
그리고 사고 백서의 회귀 지점(사고 6·7)이 지켜지는지 확인합니다.

실행: python3 tests/test_measure_engine.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import measure_engine as me  # noqa: E402


def q(filing, announced, eps):
    return {"filing_date": filing, "announced_date": announced,
            "period_label": filing[:7], "adj_eps": eps}


# ---------------------------------------------------------------------------
# TTM — 진짜 연속 4분기 합만 (사고 7 회귀)
# ---------------------------------------------------------------------------
def test_ttm_series_needs_four():
    assert me.ttm_series([1.0, 2.0, 3.0]) == []
    assert me.ttm_series([1.0, 2.0, 3.0, 4.0]) == [10.0]
    assert me.ttm_series([1.0, 2.0, 3.0, 4.0, 5.0]) == [10.0, 14.0]


def test_runs_split_on_gap():
    """몇 년 떨어진 분기를 이웃으로 만들면 안 됩니다 (사고 7)."""
    rows = [
        q("2021-03-31", None, 1.0),
        q("2021-06-30", None, 1.0),
        # 2년 공백
        q("2023-06-30", None, 1.0),
        q("2023-09-30", None, 1.0),
    ]
    runs = me.eps_runs(rows)
    assert len(runs) == 2, [len(r) for r in runs]
    # 어떤 구간도 4분기를 못 채우므로 TTM 은 없어야 합니다
    for run in runs:
        assert me.ttm_series([r["adj_eps"] for r in run]) == []


def test_peak_survives_run_break():
    """신고점 망각 금지 (사고 6): 구간이 끊겨도 옛 정점을 기억한다.

    앞 구간 TTM 정점 40 → 공백 → 뒤 구간 TTM 8 → 9.
    9 는 뒤 구간 안에서는 최고지만 옛 정점 40 아래이므로
    '신고점 아님'이어야 합니다. (정점을 잊으면 9 > 8 이라 가짜
    신고점이 됩니다 — 8차 감사에서 실데이터 27건이 이 사고였습니다.)
    """
    rows = (
        [q(f"2021-{m:02d}-28", f"2021-{m:02d}-28", 10.0) for m in (1, 4, 7, 10)]
        + [q(f"2024-{m:02d}-28", f"2024-{m:02d}-28", 2.0) for m in (1, 4, 7, 10)]
        + [q("2025-01-28", "2025-01-28", 3.0)]     # 뒤 구간 TTM 8 → 9
    )
    states = me.earnings_states(rows)
    later = [s for s in states if not s["announced"].startswith("2021")]
    assert later, "뒤 구간 상태가 없습니다"
    assert all(not s["new_high"] for s in later), later


def test_first_breakout_streak():
    """첫 돌파(streak==1)와 연속 돌파(streak>1)를 구분해야 합니다."""
    # 1.0 네 분기(TTM 4.0 형성) → 2.0 (TTM 5.0 신고점, 첫) → 3.0 (TTM 7.0, 연속)
    rows = [
        q("2024-01-31", "2024-01-31", 1.0),
        q("2024-04-30", "2024-04-30", 1.0),
        q("2024-07-31", "2024-07-31", 1.0),
        q("2024-10-31", "2024-10-31", 1.0),
        q("2025-01-31", "2025-01-31", 2.0),
        q("2025-04-30", "2025-04-30", 3.0),
    ]
    states = {s["announced"]: s for s in me.earnings_states(rows)}
    assert states["2025-01-31"]["new_high"] and states["2025-01-31"]["newhigh_streak"] == 1
    assert states["2025-04-30"]["new_high"] and states["2025-04-30"]["newhigh_streak"] == 2


def test_duplicate_announcement_counted_once():
    """같은 발표일의 정정 공시는 한 번만 셉니다."""
    rows = [
        q("2024-01-31", "2024-02-15", 1.0),
        q("2024-01-31", "2024-02-15", 1.0),
    ]
    assert len(me.earnings_states(rows)) == 1


# ---------------------------------------------------------------------------
# 수익률 창 — 발표 다음 거래일 진입 · 60거래일 · 우측검열
# ---------------------------------------------------------------------------
def _daily(n, start_close=100.0, step=1.0):
    from datetime import date, timedelta
    day = date(2024, 1, 2)
    dates, closes = [], []
    close = start_close
    while len(dates) < n:
        if day.weekday() < 5:
            dates.append(day.isoformat())
            closes.append(close)
            close += step
        day += timedelta(days=1)
    return dates, closes


def test_window_enters_next_trading_day():
    dates, closes = _daily(70)
    pct, entry, exit_ = me.window_return(dates, closes, dates[0])
    assert entry == dates[1]                       # 발표 **다음** 거래일
    assert exit_ == dates[1 + me.WINDOW_TRADING_DAYS]
    expected = (closes[61] / closes[1] - 1.0) * 100.0
    assert abs(pct - expected) < 1e-9


def test_window_right_censored():
    """창이 아직 안 끝난 사건은 재지 않습니다."""
    dates, closes = _daily(30)
    pct, reason, _ = me.window_return(dates, closes, dates[0])
    assert pct is None and reason == "우측검열"


def test_window_before_price_history():
    """주가 이력 시작 전의 발표는 재지 않습니다 (몇 달 뒤 진입 방지)."""
    dates, closes = _daily(70)
    pct, reason, _ = me.window_return(dates, closes, "2023-01-01")
    assert pct is None and reason == "주가시작전"


def test_excess_return_subtracts_spy():
    dates, closes = _daily(70, 100.0, 1.0)
    spy_dates, spy_closes = _daily(70, 500.0, 0.0)   # SPY 는 제자리
    excess, _detail = me.excess_return(
        {"dates": dates, "close": closes},
        {"dates": spy_dates, "close": spy_closes},
        dates[0],
    )
    stock_only = (closes[61] / closes[1] - 1.0) * 100.0
    assert abs(excess - stock_only) < 1e-9          # SPY 0% 이므로 그대로


# ---------------------------------------------------------------------------
# 게이지 — 신선도 140일 · H5 고정 20% · H5b 자기 이력 중앙값 + 워밍업
# ---------------------------------------------------------------------------
def _mini_ds(quarters, price_days=800):
    dates, closes = _daily(price_days)
    prices = {"SPY": {"dates": dates, "close": closes}}
    for t in quarters:
        prices[t] = {"dates": dates, "close": closes}
    return {
        "benchmark": "SPY",
        "tickers": list(quarters),
        "quarters": quarters,
        "prices": prices,
    }


def _history_rows(eps_by_quarter):
    """분기마다 (발표일, adj_eps) 를 만들어 줍니다 — 91일 간격."""
    from datetime import date, timedelta
    rows = []
    day = date(2024, 1, 31)
    for eps in eps_by_quarter:
        rows.append(q(day.isoformat(), day.isoformat(), eps))
        day += timedelta(days=91)
    return rows


def test_gauge_counts_fresh_newhigh_ratio():
    """게이지 = 신선한(140일) 발표 중 신고점 상태 비율."""
    ds = _mini_ds({
        "A": _history_rows([1, 1, 1, 1, 2]),   # 마지막 발표 = 신고점
        "B": _history_rows([1, 1, 1, 1, 1]),   # 마지막 발표 = 아님
    })
    series = me.gauge_series(ds, min_tickers=1)   # 규칙만 보는 시험 — 분모 최소치는 따로 시험합니다
    # 두 종목의 5번째 발표(2025-01-30) 직후 시점: 신고점 1/2 = 50%
    value = me.gauge_at(series, "2025-02-10")
    assert value == 50.0, value
    # 발표 140일 뒤에는 김이 빠져 분모에서 빠집니다
    stale = me.gauge_at(series, "2025-08-01")
    assert stale is None, stale


def test_gauge_h5_fixed_threshold():
    ds = _mini_ds({"A": _history_rows([1, 1, 1, 1, 2])})
    series = me.gauge_series(ds, min_tickers=1)   # 규칙만 보는 시험 — 분모 최소치는 따로 시험합니다
    assert me.gauge_h5_on(series, "2025-02-10") is True     # 100% ≥ 20%


def test_gauge_h5b_needs_warmup():
    """이력 52주 미만이면 H5b 는 판단 불가(None)여야 합니다."""
    ds = _mini_ds({"A": _history_rows([1, 1, 1, 1, 2])})
    series = me.gauge_series(ds, min_tickers=1)   # 규칙만 보는 시험 — 분모 최소치는 따로 시험합니다
    # 5번째 발표(2025-01-29) 직후 — 첫 판단 가능 게이지 값은 있으나
    # 그 이전 이력이 없다시피 하므로 H5b 는 판단 불가여야 합니다
    assert me.gauge_at(series, "2025-02-10") is not None
    assert me.gauge_h5b_on(series, "2025-02-10") is None


def test_게이지는_분모가_작으면_값을_내지_않는다():
    """비율은 분모가 작으면 시장이 아니라 몇 종목의 형편입니다 (100차).

    ⚠️ 왜 이 시험이 필요한가 — 10년으로 늘리자 표본 앞머리가 드러났고,
    **2017년의 "시장 전체 실적 신기록 폭 85.7%" 가 평균 2.4개 종목으로
    잰 값**이었습니다. 그 허상이 H5b 의 확장 중앙값 문턱을 영구히
    끌어올려 2023~2026 내내 신호가 한 번도 못 뜨게 만들었습니다.

    분모가 최소치에 못 미치면 **없음**이어야 합니다 — 억지로 비율을
    내지 않는 것이 "없음은 없음으로" 원칙입니다.
    """
    ds = _mini_ds({
        "A": _history_rows([1, 1, 1, 1, 2]),
        "B": _history_rows([1, 1, 1, 1, 1]),
    })
    # 종목이 2개뿐 — 최소치 10 에 한참 못 미친다
    막힘 = me.gauge_series(ds)
    assert me.gauge_at(막힘, "2025-02-10") is None, (
        "종목 2개로 시장의 폭을 말하면 안 된다"
    )
    # 최소치를 낮추면 같은 자료로 값이 나온다 (규칙 자체는 그대로)
    열림 = me.gauge_series(ds, min_tickers=1)
    assert me.gauge_at(열림, "2025-02-10") == 50.0


def test_섹터_게이지는_최소치에_안_걸린다():
    """섹터는 원래 종목이 적고(1~3개) 화면에 종목수를 함께 보여 주므로,
    부르는 쪽이 최소치를 1 로 낮춥니다 (app.py). 판정에 안 쓰이는 값을
    최소치로 죽이면 관찰 자체가 사라집니다."""
    ds = _mini_ds({"A": _history_rows([1, 1, 1, 1, 2])})
    섹터 = me.gauge_series(ds, tickers=["A"], min_tickers=1)
    assert me.gauge_at(섹터, "2025-02-10") == 100.0


def test_gauge_h5b_median_rule():
    """워밍업을 채우면: 이력 중앙값 초과 = ON."""
    # 12분기 (약 33개월): 앞 2년여는 전부 신고점 아님(게이지 0%),
    # 마지막 발표만 신고점 → 게이지 100% > 이력 중앙값 0% → ON
    ds = _mini_ds({
        "A": _history_rows([1] * 11 + [30]),
    }, price_days=800)
    series = me.gauge_series(ds, min_tickers=1)   # 규칙만 보는 시험 — 분모 최소치는 따로 시험합니다
    # 게이지는 그 날짜 "직전 주까지"의 상태입니다 — 발표(2026-10-28) 당일에
    # 조회하면 아직 0%(자기 발표를 자기 조건에 넣지 않음), 다음 주에는 100%.
    assert me.gauge_at(series, "2026-10-28") == 0.0
    assert me.gauge_at(series, "2026-11-06") == 100.0
    assert me.gauge_h5b_on(series, "2026-11-06") is True
    assert me.gauge_h5b_on(series, "2026-10-28") is False   # 중앙값 0 초과 아님


# ---------------------------------------------------------------------------
# 잣대 사다리 (12차 등록)
# ---------------------------------------------------------------------------
def _rows_with(field, n, start="2023-01-31"):
    from datetime import date, timedelta
    day = date.fromisoformat(start)
    rows = []
    for i in range(n):
        rows.append({"filing_date": day.isoformat(),
                     "announced_date": day.isoformat(),
                     "period_label": str(day)[:7], field: 1.0 + i * 0.1})
        day += timedelta(days=91)
    return rows


def test_yardstick_ladder_order_and_threshold():
    assert me.yardstick_of(_rows_with("adj_eps", 8)) == "adj_eps"
    assert me.yardstick_of(_rows_with("adjusted_ebitda", 8)) == "adjusted_ebitda"
    assert me.yardstick_of(_rows_with("gaap_eps", 8)) == "gaap_eps"
    assert me.yardstick_of(_rows_with("adj_eps", 7)) is None          # 8분기 미달
    # 조정 EPS 가 8분기 이상이면 EBITDA 가 더 많아도 상위 잣대가 이깁니다
    both = _rows_with("adj_eps", 8)
    for r in _rows_with("adjusted_ebitda", 12, start="2020-01-31"):
        both.append(r)
    assert me.yardstick_of(both) == "adj_eps"


def test_collect_events_tags_yardstick_and_excludes_short_history():
    """사건에 잣대가 붙고, 8분기 미달 종목은 측정에서 빠져야 합니다."""
    ds = _mini_ds({
        "EB": _rows_with("adjusted_ebitda", 9, start="2024-01-31"),
        "SHORT": _rows_with("adj_eps", 5, start="2024-01-31"),
    }, price_days=800)
    events, skipped = me.collect_events(ds)
    assert events and all(e["잣대"] == "adjusted_ebitda" for e in events), events[:2]
    assert all(e["ticker"] == "EB" for e in events)
    assert skipped["잣대없음"] == 1, skipped


# ---------------------------------------------------------------------------
# 사건 수집 종합
# ---------------------------------------------------------------------------
def test_collect_events_fields_and_censoring():
    ds = _mini_ds({"A": _history_rows([1, 1, 1, 1, 2, 2, 2, 2])}, price_days=800)
    events, skipped = me.collect_events(ds)
    assert events, "사건이 없습니다"
    e = events[-1]
    for key in ("ticker", "announced", "new_high", "newhigh_streak",
                "h5", "h5b", "excess"):
        assert key in e, key
    assert isinstance(e["excess"], float)


def test_below_52wk_ma():
    """H9 (21차 등록): 주봉 종가 vs 52주 이동평균 — 이력 부족은 판단 불가."""
    dates, closes = _daily(300)                      # 약 60주
    prices = {"dates": dates, "close": closes}
    # 꾸준히 오르는 가격 → 항상 52주선 위
    assert me.below_52wk_ma(prices, dates[-1]) is False
    # 이력 52주 미만 시점 → 판단 불가
    assert me.below_52wk_ma(prices, dates[100]) is None
    # 마지막에 급락한 가격 → 52주선 아래
    dropped = {"dates": dates, "close": closes[:-1] + [closes[0] * 0.3]}
    assert me.below_52wk_ma(dropped, dates[-1]) is True


def test_collect_events_carry_below52():
    ds = _mini_ds({"A": _history_rows([1, 1, 1, 1, 2, 2, 2, 2])}, price_days=800)
    events, _ = me.collect_events(ds)
    assert all("below52" in e for e in events)


def test_collect_metric_events_op_income_only():
    """H10 (23차 등록): 논갭 영업이익 단독 사건 — 8분기 미달 종목 제외."""
    ds = _mini_ds({
        "OP": _rows_with("op_income", 9, start="2024-01-31"),
        "SHORT": _rows_with("op_income", 5, start="2024-01-31"),
        "EPSONLY": _rows_with("adj_eps", 9, start="2024-01-31"),
    }, price_days=800)
    events = me.collect_metric_events(ds, "op_income")
    assert events, "사건이 없습니다"
    assert all(e["ticker"] == "OP" for e in events), events[:2]
    assert all(e["잣대"] == "op_income" for e in events)
    for key in ("announced", "newhigh_streak", "below52", "excess"):
        assert key in events[0], key
    # 사다리 사건 목록과 섞이지 않는다 — collect_events 는 op_income 을 모른다
    ladder_events, _ = me.collect_events(ds)
    assert all(e["잣대"] != "op_income" for e in ladder_events)


def test_신고점의_폭을_함께_담는다():
    """H22·H22b (109차 등록) — 신고점을 직전 정점 대비 몇 % 넘었나.

    ⚠️ 왜 필요한가: `new_high` 는 참/거짓이라 1% 넘은 것과 50% 넘은 것이
    똑같이 "신고점"이었습니다. 109차 탐색에서 폭이 단조로 듣는 것이
    나왔습니다 — 간신히 넘긴 것은 기준선보다 **나쁘고**(1~3% 7.5%),
    크게 넘은 것만 뚜렷이 웃돕니다(20%↑ 18.5%, 기준선 13.0%).
    """
    # TTM 이 4분기 합이므로 값을 조절해 정점을 만든다
    rows = _history_rows([1, 1, 1, 1, 1, 1, 2])
    states = me.earnings_states(rows)
    폭들 = [s["신고점폭"] for s in states if s["신고점폭"] is not None]
    assert 폭들, "신고점인데 폭이 하나도 안 담겼습니다"
    # 4+1 = 5 → 4 에서 25% 오름
    assert abs(폭들[-1] - 25.0) < 0.01, 폭들


def test_정점이_음수면_폭을_안_잰다():
    """적자에서 적자로 옮겨간 것을 "몇 % 성장"이라 부를 수 없습니다.
    억지로 숫자를 만들지 않고 **없음**으로 둡니다 (창작 금지)."""
    rows = _history_rows([-1, -1, -1, -1, -1, -1, 3])
    states = me.earnings_states(rows)
    돌파 = [s for s in states if s["new_high"]]
    # ⚠️ 처음에는 `ttm > 0` 인 돌파만 보게 썼는데, 이 자료의 돌파는 TTM 이
    #    정확히 0 이라 **조건절이 한 번도 참이 안 되어** 시험이 아무것도
    #    안 지켰습니다 (오늘 다섯 번째 가짜 초록불). 돌파 자체를 봅니다.
    assert 돌파, "이 자료에는 돌파가 있어야 시험이 뜻을 가집니다"
    for s in 돌파:
        assert s["신고점폭"] is None, (
            f"직전 정점이 음수(-4)인데 폭을 쟀습니다: {s}"
        )


def test_판단횟수는_판단_가능한_발표만_센다():
    """H23 (116차 등록)의 재료 — 몇 번째 판단 가능한 발표인가.

    100차 ③에서 실측한 편향(이력이 짧으면 신고점이 수학적으로 쉬움)을
    다루려면 각 발표의 깊이를 알아야 합니다. TTM 이 없거나 비교할 정점이
    없는 발표는 세지 않고 값도 없음입니다 — 억지로 0을 만들지 않습니다."""
    states = me.earnings_states(_history_rows([1, 1, 1, 1, 2, 2, 2]))
    # 1~4번째: TTM 미완성 또는 비교할 정점 없음 → 판단 불가, 깊이 없음
    for s in states[:4]:
        assert not s["decidable"] and s["판단횟수"] is None, s
    # 5번째부터 판단 가능 → 깊이 1, 2, 3
    assert [s["판단횟수"] for s in states[4:]] == [1, 2, 3], states[4:]


def _late_rows(eps_by_quarter, skip_quarters):
    """_history_rows 와 같되 91일 간격 skip_quarters 개만큼 늦게 시작합니다."""
    from datetime import date, timedelta
    rows = []
    day = date(2024, 1, 31) + timedelta(days=91 * skip_quarters)
    for eps in eps_by_quarter:
        rows.append(q(day.isoformat(), day.isoformat(), eps))
        day += timedelta(days=91)
    return rows


def test_깊은_게이지는_얕은_이력_발표를_안_센다():
    """H23 (116차 등록) — 깊은 게이지는 그 종목의 8번째 이상 판단 가능한
    발표만 분자·분모에 넣습니다.

    깊은 종목(12분기, 마지막 발표 깊이 8, 신고점 아님)과 얕은 종목
    (5분기, 마지막 발표 깊이 1, 신고점)이 같은 날 신선합니다.
    기존 게이지는 50%(얕은 신고점이 분자를 채움), 깊은 게이지는 0%
    (얕은 발표가 분자·분모 모두에서 빠짐)여야 합니다."""
    ds = _mini_ds({
        "깊은": _history_rows([1] * 12),               # 마지막 발표 = 12번째 분기, 깊이 8
        "얕은": _late_rows([1, 1, 1, 1, 2], 7),        # 같은 날 5번째 분기, 깊이 1
    }, price_days=800)
    from datetime import date, timedelta
    마지막발표 = me.earnings_states(ds["quarters"]["깊은"])[-1]["announced"]
    # 주간 격자는 각 주 마지막 거래일이므로, 발표 주가 끝난 뒤로 잡습니다
    기준일 = (date.fromisoformat(마지막발표) + timedelta(days=10)).isoformat()
    기존 = me.gauge_series(ds, min_tickers=1)
    깊은 = me.gauge_series(ds, min_tickers=1, min_history=me.GAUGE_MIN_HISTORY)
    assert me.gauge_at(기존, 기준일) == 50.0, me.gauge_at(기존, 기준일)
    assert me.gauge_at(깊은, 기준일) == 0.0, me.gauge_at(깊은, 기준일)


def test_collect_events_carry_h5b_깊은():
    """사건마다 깊은 게이지의 H5b 판정이 붙어야 판정기가 H23 을 잴 수
    있습니다. 판단 불가면 없음(None)으로 붙습니다 — 값을 만들지 않습니다."""
    ds = _mini_ds({"A": _history_rows([1, 1, 1, 1, 2, 2, 2, 2])}, price_days=800)
    events, _ = me.collect_events(ds)
    assert events and all("h5b_깊은" in e for e in events)




def test_런업은_발표_전_구간이고_이력이_짧으면_None():
    """(124차) 런업 = 발표 전 60거래일 SPY 대비 초과수익. 결과 창과
    겹치지 않아야 하고(발표일에 끝남), 이력이 짧으면 None 입니다."""
    from datetime import date, timedelta
    day0 = date(2024, 1, 1)
    dates = []
    d = day0
    while len(dates) < 130:
        if d.weekday() < 5:
            dates.append(d.isoformat())
        d += timedelta(days=1)
    # 종목: 앞 70일 100 고정, 그 뒤 매일 +1 — SPY: 내내 100 고정
    closes = [100.0]*70 + [100.0 + i for i in range(1, 61)]
    prices = {"dates": dates, "close": closes}
    spy = {"dates": dates, "close": [100.0]*130}
    last = dates[-1]
    got = me.backward_excess(prices, spy, last, days=60)
    want = (closes[-1]/closes[-61] - 1.0) * 100.0
    assert got is not None and abs(got - want) < 1e-9, (got, want)
    assert me.backward_excess(prices, spy, dates[30], days=60) is None, \
        "이력 부족인데 값을 만들었습니다"


def test_사건에_125일_창이_붙고_60일과_다르다():
    """(143차 H26) 창 길이를 바꿔도 **기존 60일 값은 한 칸도 안 바뀌어야**
    하고, 125일 값은 실제로 125거래일을 재야 합니다.

    141차 탐색에서 125일에서만 채택 기준을 넘었으므로, 이 창 길이가
    조용히 다른 값으로 바뀌면 H26 은 **다른 가설이 되어 버립니다.**
    """
    import measure_engine as me

    # 200거래일: 60일까지는 종목과 SPY가 똑같이 오르고(초과 0),
    # 그 뒤부터 종목만 더 오릅니다 → 60일 초과 ≈ 0, 125일 초과 > 0
    날 = [f"2020-{(i // 21) + 1:02d}-{(i % 21) + 1:02d}" for i in range(200)]
    스파이 = [100.0 * (1.0 + 0.001 * i) for i in range(200)]
    종목 = list(스파이[:70])                      # 70일까지는 SPY 와 똑같이
    종목 += [스파이[69] * (1.0 + 0.01 * (i - 69)) for i in range(70, 200)]
    prices = {"dates": 날, "close": 종목}
    spy = {"dates": 날, "close": 스파이}
    발표 = 날[0]
    초과60, _ = me.excess_return(prices, spy, 발표)
    초과125, _ = me.excess_return(prices, spy, 발표, days=me.H26_WINDOW_DAYS)
    assert abs(초과60) < 0.001, f"60일 초과가 0이 아닙니다: {초과60}"
    assert 초과125 > 10.0, f"125일 초과가 안 잡힙니다: {초과125}"
    # 창 길이 자체를 못박습니다 — 141차 탐색이 이 길이에서만 분리됐습니다
    assert me.H26_WINDOW_DAYS == 125, me.H26_WINDOW_DAYS
    # 기본값(days 없음)은 등록된 60거래일 그대로여야 합니다
    assert me.WINDOW_TRADING_DAYS == 60



# ---------------------------------------------------------------------------
# 가뭄 셈 · 고가대비 (151차 — H27·H28 의 재료)
# ---------------------------------------------------------------------------
def test_가뭄은_직전_신고점_이후_발표_수다():
    """신고점 발표에는 **방금 끝난 가뭄의 길이**가 적히고, 이력에 신고점이
    아직 없으면 None(판단 불가를 0 으로 지어내지 않음)입니다."""
    def 행(끝, 발표, eps):
        return {"filing_date": 끝, "announced_date": 발표, "adj_eps": eps}

    # 4분기를 채우고(TTM), 정점 1.2 → 하락 → 다시 돌파하는 이력
    rows = [
        행("2020-03-31", "2020-05-01", 0.3), 행("2020-06-30", "2020-08-01", 0.3),
        행("2020-09-30", "2020-11-01", 0.3), 행("2020-12-31", "2021-02-01", 0.3),
        행("2021-03-31", "2021-05-01", 0.4),   # TTM 1.3 > 1.2 → 신고점
        행("2021-06-30", "2021-08-01", 0.1),   # TTM 1.1 — 가뭄 시작
        행("2021-09-30", "2021-11-01", 0.1),
        행("2021-12-31", "2022-02-01", 0.1),
        행("2022-03-31", "2022-05-01", 1.2),   # TTM 1.5 → 신고점 (가뭄 3 끝)
    ]
    states = me.earnings_states(rows)
    가뭄들 = {s["announced"]: s["drought"] for s in states}
    assert 가뭄들["2021-05-01"] is None, "이력상 최초 신고점의 가뭄은 None 이어야 합니다"
    assert 가뭄들["2021-08-01"] == 0
    assert 가뭄들["2022-02-01"] == 2
    assert 가뭄들["2022-05-01"] == 3, "신고점 발표에는 방금 끝난 가뭄 길이가 적혀야 합니다"


def test_사건에_가뭄이_실려_온다():
    """상태에만 있고 사건에 안 실리면 판정기가 볼 수 없습니다."""
    ds = _mini_ds({"A": _history_rows([1, 1, 1, 1, 2, 2, 2, 2])}, price_days=800)
    events, _ = me.collect_events(ds)
    assert events, "사건이 하나도 없습니다 — 시험 재료가 잘못됨"
    assert all("가뭄" in e for e in events)


def test_고가대비는_발표_전일까지의_종가만_본다():
    """발표 다음 날 주가가 두 배로 뛰어도 고가대비는 변하면 안 됩니다 —
    발표 후 움직임이 새어 들면 신호가 미래를 봅니다."""
    from datetime import date, timedelta
    시작 = date(2024, 1, 1)
    dates, closes = [], []
    d = 시작
    while len(dates) < 100:
        if d.weekday() < 5:
            dates.append(d.isoformat())
            closes.append(100.0)
        d += timedelta(days=1)
    고점자리 = 70
    closes[고점자리] = 200.0                       # 52주 고가
    발표일 = dates[90]
    ds = {"prices": {"AA": {"dates": dates, "close": closes}},
          "benchmark": "SPY"}
    사건 = [{"ticker": "AA", "announced": 발표일}]
    me.attach_high52(ds, 사건)
    assert 사건[0]["고가대비"] is not None
    assert abs(사건[0]["고가대비"] - (100.0 / 200.0 - 1.0) * 100.0) < 1e-6

    # 발표 **다음** 거래일 종가를 조작해도 값이 그대로인가 (미래 금지)
    조작 = list(closes)
    조작[91] = 1000.0
    사건2 = [{"ticker": "AA", "announced": 발표일}]
    me.attach_high52({"prices": {"AA": {"dates": dates, "close": 조작}},
                      "benchmark": "SPY"}, 사건2)
    assert 사건2[0]["고가대비"] == 사건[0]["고가대비"], "발표 후 종가가 새어 들었습니다"


def test_고가대비는_이력이_짧으면_None():
    ds = {"prices": {"AA": {"dates": ["2024-01-02", "2024-01-03"],
                            "close": [1.0, 2.0]}}, "benchmark": "SPY"}
    사건 = [{"ticker": "AA", "announced": "2024-01-03"}]
    me.attach_high52(ds, 사건)
    assert 사건[0]["고가대비"] is None


# ---------------------------------------------------------------------------
# 가속 (164차 — H31 의 재료)
# ---------------------------------------------------------------------------
def test_가속은_같은_구간의_TTM_세개로만_잰다():
    """이번 TTM 증가율 > 직전 증가율이면 가속(True), 작으면 감속(False),
    TTM 이 셋 안 되면 None. 직전 TTM 이 0 이하면 비율의 뜻이 없어 None."""
    def 행(끝, 발표, eps):
        return {"filing_date": 끝, "announced_date": 발표, "adj_eps": eps}

    rows = [
        행("2020-03-31", "2020-05-01", 1.0), 행("2020-06-30", "2020-08-01", 1.0),
        행("2020-09-30", "2020-11-01", 1.0), 행("2020-12-31", "2021-02-01", 1.0),  # TTM 4.0
        행("2021-03-31", "2021-05-01", 1.4),   # TTM 4.4 (+10%) — 직전 증가율 없음 → None
        행("2021-06-30", "2021-08-01", 2.2),   # TTM 5.6 (+27.3%) > 10% → 가속
        행("2021-09-30", "2021-11-01", 1.6),   # TTM 6.2 (+10.7%) < 27.3% → 감속
    ]
    가속 = {s["announced"]: s["accel"] for s in me.earnings_states(rows)}
    assert 가속["2021-02-01"] is None, "TTM 하나로는 증가율도 없어야 합니다"
    assert 가속["2021-05-01"] is None, "TTM 둘로는 직전 증가율이 없어 판단 불가"
    assert 가속["2021-08-01"] is True
    assert 가속["2021-11-01"] is False
    증가 = {s["announced"]: s["ttm_growth"] for s in me.earnings_states(rows)}
    assert abs(증가["2021-05-01"] - 10.0) < 1e-9, 증가


def test_가속은_구간이_끊기면_새로_센다():
    """빠진 분기 앞뒤를 이어붙여 TTM 을 만들면 한 푼도 안 늘었는데 가속이
    나옵니다(사고 7). 새 구간의 첫 TTM 셋이 채워지기 전에는 None."""
    def 행(끝, 발표, eps):
        return {"filing_date": 끝, "announced_date": 발표, "adj_eps": eps}

    rows = [
        행("2020-03-31", "2020-05-01", 1.0), 행("2020-06-30", "2020-08-01", 1.0),
        행("2020-09-30", "2020-11-01", 1.0), 행("2020-12-31", "2021-02-01", 1.0),
        행("2021-03-31", "2021-05-01", 1.4), 행("2021-06-30", "2021-08-01", 2.2),
        # 2021-09-30 분기가 빠짐 → 구간 끊김
        행("2021-12-31", "2022-02-01", 3.0), 행("2022-03-31", "2022-05-01", 3.0),
        행("2022-06-30", "2022-08-01", 3.0), 행("2022-09-30", "2022-11-01", 3.0),
        행("2022-12-31", "2023-02-01", 3.0), 행("2023-03-31", "2023-05-01", 3.0),
    ]
    가속 = {s["announced"]: s["accel"] for s in me.earnings_states(rows)}
    assert 가속["2021-08-01"] is True                 # 옛 구간의 마지막은 그대로
    assert 가속["2022-11-01"] is None, "새 구간의 첫 TTM — 직전이 없어야 합니다"
    assert 가속["2023-02-01"] is None, "새 구간의 두 번째 TTM — 아직 판단 불가"
    assert 가속["2023-05-01"] is False, "12.0→12.0→12.0: 0% > 0% 은 거짓(가속 아님)"


def test_직전_TTM_이_0_이하면_가속은_없음():
    """적자에서 적자로 옮긴 것을 '몇 % 성장'이라 부르지 않습니다."""
    assert me.ttm_growth_pct(1.0, 0.0) is None
    assert me.ttm_growth_pct(1.0, -2.0) is None
    assert me.ttm_growth_pct(None, 2.0) is None
    assert abs(me.ttm_growth_pct(3.0, 2.0) - 50.0) < 1e-9


def test_사건에_가속이_실려_온다():
    """상태에만 있고 사건에 안 실리면 H31 판정기가 볼 수 없습니다."""
    ds = _mini_ds({"A": _history_rows([1, 1, 1, 1, 2, 3, 5, 8])}, price_days=800)
    events, _ = me.collect_events(ds)
    assert events, "사건이 하나도 없습니다 — 시험 재료가 잘못됨"
    assert all("가속" in e for e in events)
    assert any(e["가속"] is not None for e in events), "가속이 전부 None — 재료 부족"


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    passed = failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  ✅ {name}")
            passed += 1
        except AssertionError as e:
            print(f"  ❌ {name} — {e}")
            failed += 1
        except Exception as e:
            print(f"  💥 {name} — {type(e).__name__}: {e}")
            failed += 1
    print(f"\n측정 장치 검증: {passed}개 통과, {failed}개 실패")
    sys.exit(1 if failed else 0)
