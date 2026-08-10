"""
test_scoring.py — 점수 계산과 판정 로직 검증
===========================================

실행: python3 tests/test_scoring.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import config as cfg  # noqa: E402
import pipeline  # noqa: E402
import scoring  # noqa: E402
from fixtures import make_quarters  # noqa: E402

# 금액 단위를 짧게 쓰기 위한 도우미 ($1M = 100만 달러)
M = 1_000_000


# ---------------------------------------------------------------------------
# 델타 가속 (40점)
# ---------------------------------------------------------------------------
def test_delta_acceleration_up():
    """증가율이 점점 커지면 '가속' 판정에 만점권 점수를 줘야 함"""
    # 100 → 110(+10%) → 130(+18.2%) : 증가율이 커지는 중
    quarters = make_quarters([100 * M, 110 * M, 130 * M])
    result = scoring.score_delta_acceleration(quarters)

    assert result["direction"] == "가속", result
    assert result["score"] == cfg.W_DELTA_ACCEL, result   # 40점 만점
    assert result["capped"] is False


def test_delta_deceleration():
    """증가율이 작아지면 '감속' 판정으로 감점해야 함"""
    # 100 → 130(+30%) → 136(+4.6%) : 증가율이 크게 줄어듦
    quarters = make_quarters([100 * M, 130 * M, 136 * M])
    result = scoring.score_delta_acceleration(quarters)

    assert result["direction"] == "감속", result
    assert result["score"] < cfg.W_DELTA_ACCEL * 0.5, result


def test_delta_steady():
    """증가율이 비슷하면 '유지' 판정으로 중간 점수를 줘야 함"""
    # 100 → 110(+10%) → 121(+10%) : 증가율 동일
    quarters = make_quarters([100 * M, 110 * M, 121 * M])
    result = scoring.score_delta_acceleration(quarters)

    assert result["direction"] == "유지", result
    assert 0 < result["score"] < cfg.W_DELTA_ACCEL, result


def test_low_base_cap_applied():
    """최근 분기 이익이 $50M 미만이면 점수를 절반으로 제한해야 함 (저기저 함정 방지)"""
    # 이익 규모가 작지만(10M→11M→13M) 증가율은 가속하는 상황
    quarters = make_quarters([10 * M, 11 * M, 13 * M])
    result = scoring.score_delta_acceleration(quarters)

    assert result["direction"] == "가속", result
    assert result["capped"] is True, result
    assert result["score"] == cfg.W_DELTA_ACCEL * cfg.LOW_BASE_CAP_RATIO, result  # 20점


def test_low_base_cap_not_applied_above_threshold():
    """이익이 $50M 이상이면 상한을 걸지 않아야 함"""
    quarters = make_quarters([100 * M, 110 * M, 130 * M])
    result = scoring.score_delta_acceleration(quarters)
    assert result["capped"] is False, result


def test_delta_insufficient_data():
    """분기가 1개뿐이면 판단 불가로 처리해야 함"""
    result = scoring.score_delta_acceleration(make_quarters([100 * M]))
    assert result["direction"] == "판단불가", result


def test_delta_profit_shrinking_penalty():
    """최근 분기 이익이 줄었으면 추가 감점해야 함"""
    # 100 → 60(-40%) → 55(-8.3%) : 증가율은 올랐지만(가속) 이익 자체는 감소
    quarters = make_quarters([100 * M, 60 * M, 55 * M])
    result = scoring.score_delta_acceleration(quarters)
    assert "줄었습니다" in result["detail"], result["detail"]


# ---------------------------------------------------------------------------
# GM% 드라이버 (25점)
# ---------------------------------------------------------------------------
def test_gm_volume_type():
    """마진이 거의 그대로면 '물량형' 만점(25점)"""
    quarters = make_quarters([10 * M] * 5, margins=[50.0, 50.2, 49.8, 50.1, 50.0])
    result = scoring.score_gm_driver(quarters)

    assert result["type"] == "물량형", result
    assert result["score"] == cfg.GM_SCORE_VOLUME, result


def test_gm_mix_type():
    """마진이 +1~+3%p 오르면 '믹스형' 15점"""
    quarters = make_quarters([10 * M] * 5, margins=[50.0, 50.0, 50.0, 50.0, 52.0])
    result = scoring.score_gm_driver(quarters)

    assert result["type"] == "믹스형", result
    assert result["score"] == cfg.GM_SCORE_MIX, result


def test_gm_price_type():
    """마진이 +3%p 넘게 급등하면 '가격형' 5점 (지속성 낮음)"""
    quarters = make_quarters([10 * M] * 5, margins=[50.0, 50.0, 50.0, 50.0, 56.0])
    result = scoring.score_gm_driver(quarters)

    assert result["type"] == "가격형", result
    assert result["score"] == cfg.GM_SCORE_PRICE, result


def test_gm_decline():
    """마진이 −1%p 넘게 떨어지면 '마진 악화' 8점"""
    quarters = make_quarters([10 * M] * 5, margins=[50.0, 50.0, 50.0, 50.0, 46.0])
    result = scoring.score_gm_driver(quarters)

    assert result["type"] == "마진 악화", result
    assert result["score"] == cfg.GM_SCORE_DECLINE, result


def test_gm_uses_prior_four_quarters_as_baseline():
    """기준은 '직전 4개 분기 평균'이어야 함 (최근 분기는 평균에서 제외)"""
    # 직전 4개 평균 = (40+40+40+40)/4 = 40, 최근 = 42 → +2%p → 믹스형
    quarters = make_quarters([10 * M] * 5, margins=[40.0, 40.0, 40.0, 40.0, 42.0])
    result = scoring.score_gm_driver(quarters)
    assert abs(result["delta_pp"] - 2.0) < 0.01, result


# ---------------------------------------------------------------------------
# 매출 성장의 질 (20점)
# ---------------------------------------------------------------------------
def test_revenue_quality_high_growth():
    """매출이 크게 늘고 가속하면 높은 점수"""
    quarters = make_quarters(
        [10 * M] * 5, revenues=[100 * M, 110 * M, 125 * M, 145 * M, 175 * M]
    )
    result = scoring.score_revenue_quality(quarters)

    assert result["yoy"] is not None and result["yoy"] > 50, result
    assert result["score"] > cfg.W_REVENUE_QUALITY * 0.6, result


def test_revenue_quality_flat():
    """매출이 제자리면 낮은 점수"""
    quarters = make_quarters([10 * M] * 5, revenues=[100 * M] * 5)
    result = scoring.score_revenue_quality(quarters)

    assert result["yoy"] is not None and abs(result["yoy"]) < 0.01, result
    assert result["score"] < cfg.W_REVENUE_QUALITY * 0.3, result


# ---------------------------------------------------------------------------
# 포워드 신호 (15점)
# ---------------------------------------------------------------------------
def test_forward_growth_and_upward_revision():
    """전망이 좋고 애널리스트도 상향하면 만점에 가까워야 함"""
    quarters = make_quarters([100 * M])
    forward = {"forward_op_income": 130 * M, "revision": 1, "basis": cfg.SRC_GUIDANCE}
    result = scoring.score_forward(quarters, forward)

    assert result["growth_pct"] == 30.0, result
    assert result["score"] >= cfg.W_FORWARD * 0.9, result


def test_forward_decline_and_downward_revision():
    """전망이 나쁘고 하향 조정이면 0점에 가까워야 함"""
    quarters = make_quarters([100 * M])
    forward = {"forward_op_income": 80 * M, "revision": -1, "basis": cfg.SRC_ESTIMATE}
    result = scoring.score_forward(quarters, forward)

    assert result["score"] < cfg.W_FORWARD * 0.2, result


def test_forward_missing_data():
    """전망 데이터가 없으면 중간보다 낮은 기본 점수를 줘야 함"""
    result = scoring.score_forward(make_quarters([100 * M]), {})
    assert 0 < result["score"] < cfg.W_FORWARD, result


# ---------------------------------------------------------------------------
# 기술 점수 (100점)
# ---------------------------------------------------------------------------
def test_technical_perfect():
    """완전 정배열 + 상승 기울기 + 정상 이격 + 강한 RS = 100점"""
    price = {
        "state": cfg.S_FULL_UP,
        "slope": "상승",
        "disparity": 15.0,
        "rs": 25.0,
    }
    result = scoring.score_technical(price)
    assert result["total"] == 100.0, result


def test_technical_worst():
    """완전 역배열 + 하락 + 음수 이격 + 약한 RS = 0점"""
    price = {
        "state": cfg.S_FULL_DOWN,
        "slope": "하락",
        "disparity": -20.0,
        "rs": -30.0,
    }
    result = scoring.score_technical(price)
    assert result["total"] == 0.0, result


def test_technical_overheated_disparity():
    """이격도 +40% 초과는 과열로 감점해야 함"""
    price = {"state": cfg.S_FULL_UP, "slope": "상승", "disparity": 55.0, "rs": 25.0}
    result = scoring.score_technical(price)
    assert result["disparity_score"] == cfg.DISPARITY_SCORE_OVERHEAT, result
    assert "과열" in result["disparity_text"], result


def test_technical_rs_bands():
    """상대강도 구간별 점수가 순서대로 매겨져야 함"""
    base = {"state": cfg.S_NEUTRAL, "slope": "횡보", "disparity": 5.0}
    scores = [scoring.score_technical({**base, "rs": rs})["rs_score"] for rs in (30, 15, 5, -5, -30)]
    assert scores == sorted(scores, reverse=True), scores
    assert scores[0] == 25.0 and scores[-1] == 0.0, scores


def test_technical_missing_values_safe():
    """이격도·RS가 없어도 예외 없이 계산돼야 함"""
    result = scoring.score_technical({"state": cfg.S_NEUTRAL, "slope": "-"})
    assert result["total"] >= 0


# ---------------------------------------------------------------------------
# 매트릭스 판정 (5종)
# ---------------------------------------------------------------------------
def test_verdict_buy():
    """펀더 70+ & 추세 1~2단계 → 매수 후보"""
    assert scoring.decide_verdict(75.0, 1) == cfg.V_BUY
    assert scoring.decide_verdict(70.0, 2) == cfg.V_BUY


def test_verdict_warning():
    """펀더 70+ & 추세 4~5단계 → 경고 (실적 좋은데 추세 꺾임)"""
    assert scoring.decide_verdict(85.0, 4) == cfg.V_WARN
    assert scoring.decide_verdict(72.0, 5) == cfg.V_WARN


def test_verdict_watch():
    """펀더 40~70 & 추세 1~2단계 → 관찰"""
    assert scoring.decide_verdict(55.0, 1) == cfg.V_WATCH
    assert scoring.decide_verdict(40.0, 2) == cfg.V_WATCH


def test_verdict_momentum():
    """펀더 40 미만 & 추세 양호 → 펀더 없는 모멘텀"""
    assert scoring.decide_verdict(25.0, 1) == cfg.V_MOMENTUM


def test_verdict_exclude():
    """그 외는 모두 제외"""
    assert scoring.decide_verdict(75.0, 3) == cfg.V_EXCLUDE   # 실적 좋지만 중립 추세
    assert scoring.decide_verdict(30.0, 5) == cfg.V_EXCLUDE   # 둘 다 나쁨
    assert scoring.decide_verdict(50.0, 4) == cfg.V_EXCLUDE   # 애매 + 훼손


# ---------------------------------------------------------------------------
# 종합 (build_score) 및 순위 정렬
# ---------------------------------------------------------------------------
def test_build_score_weights():
    """최종점수 = 펀더 × 0.6 + 기술 × 0.4 이어야 함"""
    quarters = make_quarters([100 * M, 110 * M, 130 * M], margins=[50.0] * 3)
    forward = {"forward_op_income": 150 * M, "revision": 1, "basis": cfg.SRC_GUIDANCE}
    price = {
        "state": cfg.S_FULL_UP, "slope": "상승", "disparity": 15.0, "rs": 25.0,
        "stage": 1, "close": 100.0, "week_change": 2.0,
    }
    result = scoring.build_score("TEST", quarters, forward, price)

    expected = result["fund_score"] * 0.6 + result["tech_score"] * 0.4
    assert abs(result["final_score"] - round(expected, 1)) < 0.05, result
    assert result["tech_score"] == 100.0, result


def test_ranking_sorts_buy_candidates_by_rs():
    """매수 후보는 상대강도(RS)가 높은 순으로 위에 와야 함"""
    def build(ticker, rs, fund_ops):
        quarters = make_quarters(fund_ops, margins=[50.0] * len(fund_ops))
        forward = {"forward_op_income": fund_ops[-1] * 1.3, "revision": 1}
        price = {
            "state": cfg.S_FULL_UP, "slope": "상승", "disparity": 10.0, "rs": rs,
            "stage": 1, "close": 50.0, "week_change": 1.0,
        }
        return scoring.build_score(ticker, quarters, forward, price)

    scores = {
        "LOWRS": build("LOWRS", 5.0, [100 * M, 110 * M, 130 * M]),
        "HIGHRS": build("HIGHRS", 40.0, [100 * M, 110 * M, 130 * M]),
    }
    table = pipeline.build_ranking_table(scores)

    # 둘 다 매수 후보라면 RS가 높은 HIGHRS가 위에 있어야 함
    if (table["판정"] == cfg.V_BUY).all():
        assert table.iloc[0]["종목"] == "HIGHRS", table


def test_build_score_with_no_fundamentals():
    """실적이 하나도 없으면 예외 없이 점수를 내되, 판정은 '실적 없음'이어야 함.

    이때의 펀더멘털 점수는 '판단 불가' 항목들의 기본 배점이 더해진 값이라,
    실적이 있는 종목과 같은 기준으로 비교하면 안 됩니다.
    """
    price = {
        "state": cfg.S_NEUTRAL, "slope": "횡보", "disparity": 1.0, "rs": 0.0,
        "stage": 3, "close": 20.0, "week_change": 0.0,
    }
    result = scoring.build_score("EMPTY", [], {}, price)

    assert result["final_score"] >= 0
    assert result["data_source"] is None
    assert result["verdict"] == cfg.V_NO_DATA, result["verdict"]


def test_no_fundamentals_never_becomes_buy_candidate():
    """실적이 없는데 추세만 좋다고 '매수 후보'가 되면 안 됨"""
    strong_price = {
        "state": cfg.S_FULL_UP, "slope": "상승", "disparity": 10.0, "rs": 30.0,
        "stage": 1, "close": 50.0, "week_change": 1.0,
    }
    result = scoring.build_score("EMPTY", [], {}, strong_price)

    assert result["verdict"] == cfg.V_NO_DATA, result["verdict"]
    assert "비교할 수 없습니다" in result["verdict_info"]["meaning"]


def test_no_fundamentals_ranked_last():
    """실적 없는 종목은 최종점수가 높아도 순위표 맨 뒤로 가야 함"""
    strong_price = {
        "state": cfg.S_FULL_UP, "slope": "상승", "disparity": 10.0, "rs": 30.0,
        "stage": 1, "close": 50.0, "week_change": 1.0,
    }
    weak_price = {
        "state": cfg.S_NEUTRAL, "slope": "횡보", "disparity": 1.0, "rs": 0.0,
        "stage": 3, "close": 20.0, "week_change": 0.0,
    }
    quarters = make_quarters([10 * M, 12 * M, 15 * M], margins=[50.0, 50.0, 50.0])

    scores = {
        "NODATA": scoring.build_score("NODATA", [], {}, strong_price),
        "REAL": scoring.build_score("REAL", quarters, {}, weak_price),
    }
    table = pipeline.build_ranking_table(scores)

    assert list(table["종목"])[-1] == "NODATA", table[["종목", "최종점수", "판정"]]


def test_ranking_non_buy_sorted_by_final_score():
    """매수 후보가 아닌 종목은 RS가 아니라 최종점수 내림차순으로 정렬돼야 함"""
    def build(ticker, rs, state, stage, ops):
        quarters = make_quarters(ops, margins=[50.0] * len(ops))
        price = {
            "state": state, "slope": "하락", "disparity": -10.0, "rs": rs,
            "stage": stage, "close": 50.0, "week_change": -1.0,
        }
        return scoring.build_score(ticker, quarters, {}, price)

    # 둘 다 완전 역배열(제외권). LOW는 RS가 높지만 최종점수는 낮음
    scores = {
        "LOW": build("LOW", -5.0, cfg.S_FULL_DOWN, 5, [20 * M, 18 * M, 15 * M]),
        "HIGH": build("HIGH", -40.0, cfg.S_FULL_DOWN, 5, [100 * M, 120 * M, 150 * M]),
    }
    table = pipeline.build_ranking_table(scores)

    assert (table["판정"] != cfg.V_BUY).all(), table
    # RS는 LOW가 높지만, 매수 후보가 아니므로 최종점수가 높은 HIGH가 위에 와야 함
    assert table.iloc[0]["최종점수"] >= table.iloc[1]["최종점수"], table
    assert table.iloc[0]["종목"] == "HIGH", table


def test_ranking_buy_candidates_come_first():
    """매수 후보는 최종점수가 낮아도 맨 위에 모여야 함"""
    def build(ticker, state, stage, ops, rs):
        quarters = make_quarters(ops, margins=[50.0] * len(ops))
        forward = {"forward_op_income": ops[-1] * 1.3, "revision": 1}
        price = {
            "state": state, "slope": "상승" if stage <= 2 else "하락",
            "disparity": 10.0 if stage <= 2 else -10.0,
            "rs": rs, "stage": stage, "close": 50.0, "week_change": 1.0,
        }
        return scoring.build_score(ticker, quarters, forward, price)

    scores = {
        "BUY": build("BUY", cfg.S_FULL_UP, 1, [100 * M, 115 * M, 140 * M], 5.0),
        "WARN": build("WARN", cfg.S_FULL_DOWN, 5, [100 * M, 115 * M, 140 * M], 50.0),
    }
    table = pipeline.build_ranking_table(scores)

    if (table["판정"] == cfg.V_BUY).any():
        assert table.iloc[0]["판정"] == cfg.V_BUY, table


# ---------------------------------------------------------------------------
# 데이터 신뢰도 (점수에는 반영하지 않고 표시만)
# ---------------------------------------------------------------------------
def test_confidence_all_direct():
    """전부 직접공시 + 가이던스면 신뢰도가 가장 높아야 함"""
    quarters = make_quarters([100 * M] * 3, source=cfg.SRC_DIRECT)
    conf = scoring.compute_confidence(quarters, {"basis": cfg.SRC_GUIDANCE})

    assert conf["actual"] == 95.0, conf
    assert conf["forward"] == 85.0, conf
    # 95 × 0.7 + 85 × 0.3 = 92
    assert abs(conf["total"] - 92.0) < 0.05, conf


def test_confidence_all_approx():
    """전부 근사치 + 추정이면 신뢰도가 낮아야 함"""
    quarters = make_quarters([100 * M] * 3, source=cfg.SRC_APPROX)
    conf = scoring.compute_confidence(quarters, {"basis": cfg.SRC_ESTIMATE})

    # 55 × 0.7 + 40 × 0.3 = 50.5
    assert abs(conf["total"] - 50.5) < 0.05, conf


def test_confidence_weights_recent_quarters_more():
    """최근 분기가 직접공시면 과거가 근사치여도 신뢰도가 올라가야 함"""
    old_approx = make_quarters([100 * M] * 3, source=cfg.SRC_APPROX)
    recent_direct = [dict(q) for q in old_approx]
    recent_direct[-1]["source"] = cfg.SRC_DIRECT   # 최근 분기만 직접공시

    low = scoring.compute_confidence(old_approx, {"basis": cfg.SRC_ESTIMATE})
    high = scoring.compute_confidence(recent_direct, {"basis": cfg.SRC_ESTIMATE})

    assert high["actual"] > low["actual"], (low, high)


def test_confidence_empty_is_zero():
    """실적이 없으면 신뢰도 0"""
    conf = scoring.compute_confidence([], {})
    assert conf["total"] == 0.0, conf


def test_confidence_does_not_change_score():
    """신뢰도는 점수에 영향을 주면 안 됨 (표시 전용)"""
    price = {
        "state": cfg.S_FULL_UP, "slope": "상승", "disparity": 10.0, "rs": 20.0,
        "stage": 1, "close": 50.0,
    }
    ops, margins = [100 * M, 115 * M, 140 * M], [50.0] * 3
    forward = {"forward_op_income": 170 * M, "revision": 1, "basis": cfg.SRC_GUIDANCE}

    direct = scoring.build_score("A", make_quarters(ops, margins=margins, source=cfg.SRC_DIRECT),
                                 forward, price)
    approx = scoring.build_score("B", make_quarters(ops, margins=margins, source=cfg.SRC_APPROX),
                                 forward, price)

    assert direct["final_score"] == approx["final_score"], (direct, approx)
    assert direct["confidence"] > approx["confidence"], (direct, approx)


# ---------------------------------------------------------------------------
# 두 신호(단기 + 추세) 기반 델타 판정
# ---------------------------------------------------------------------------
def test_delta_mixed_when_signals_disagree():
    """단기와 추세가 반대 방향이면 '혼조'로 표시해야 함"""
    # 증가율: +60% → +25% → +10% → +13.6%  (추세는 급락, 마지막만 반등)
    quarters = make_quarters([100 * M, 160 * M, 200 * M, 220 * M, 250 * M])
    result = scoring.score_delta_acceleration(quarters)

    assert result["direction"] == cfg.D_MIXED, result
    trace = result["trace"]
    assert trace["short_signal"] * trace["trend_signal"] < 0, trace


def test_delta_uses_regression_slope():
    """회귀 기울기가 계산되어 추세 신호로 쓰여야 함"""
    quarters = make_quarters([100 * M, 110 * M, 130 * M, 160 * M, 200 * M])
    result = scoring.score_delta_acceleration(quarters)

    trace = result["trace"]
    assert trace["slope"] is not None, trace
    assert trace["slope"] > 0, trace          # 증가율이 커지는 중이므로 양수
    # 추세 신호만 방향을 보이고 단기 신호는 문턱 미만이므로 '약한 가속'이 정직합니다.
    # (예전에는 신호 하나만으로도 '가속'을 단정해, 실제로 방향이 있는 비율의
    #  두 배 넘게 단정하다가 무계산 기준선보다 성적이 나빴습니다)
    assert result["direction"] == cfg.D_WEAK_ACCEL, result
    assert trace["short_signal"] == 0 and trace["trend_signal"] == 1, trace


def test_linear_slope_math():
    """회귀 기울기 계산이 정확해야 함"""
    # 0, 2, 4, 6 → 기울기 2
    assert abs(scoring._linear_slope([0, 2, 4, 6]) - 2.0) < 1e-9
    # 내려가면 음수
    assert scoring._linear_slope([10, 7, 4, 1]) < 0
    # 값이 부족하면 None
    assert scoring._linear_slope([1, 2]) is None


def test_mixed_scores_between_accel_and_decel():
    """혼조 점수는 가속보다 낮고 감속보다 높아야 함"""
    assert cfg.DELTA_RATIO[cfg.D_DECEL] < cfg.DELTA_RATIO[cfg.D_MIXED]
    assert cfg.DELTA_RATIO[cfg.D_MIXED] < cfg.DELTA_RATIO[cfg.D_ACCEL]


# ---------------------------------------------------------------------------
# 델타 예측 (다음 분기 전망 기반)
# ---------------------------------------------------------------------------
def _forecast(ops, forward_op, basis=cfg.SRC_GUIDANCE):
    quarters = make_quarters(ops)
    delta = scoring.score_delta_acceleration(quarters)
    return scoring.predict_delta(
        quarters, {"forward_op_income": forward_op, "basis": basis}, delta
    )


def test_forecast_accel_keeps():
    """전망 증가율이 최근보다 크게 높으면 '가속 지속'"""
    # 최근 QoQ = 140/115−1 = +21.7%, 전망 = 200/140−1 = +42.9% (+21%p)
    result = _forecast([100 * M, 115 * M, 140 * M], 200 * M)
    assert result["label"] == cfg.F_ACCEL_KEEP, result


def test_forecast_accel_slows():
    """오르는 중인데 전망 증가율이 크게 낮으면 '가속 둔화'"""
    result = _forecast([100 * M, 115 * M, 140 * M], 142 * M)
    assert result["label"] == cfg.F_ACCEL_SLOW, result


def test_forecast_rebound():
    """줄어드는 중인데 전망이 크게 좋아지면 '반등'"""
    # 최근 QoQ = 70/100−1 = −30%, 전망 = 84/70−1 = +20% (+50%p)
    result = _forecast([120 * M, 100 * M, 70 * M], 84 * M)
    assert result["label"] == cfg.F_REBOUND, result


def test_forecast_decel_keeps():
    """줄어드는 중이고 전망도 더 나쁘면 '감속 지속'"""
    # 최근 QoQ = 90/100−1 = −10%, 전망 = 63/90−1 = −30% (−20%p)
    result = _forecast([120 * M, 100 * M, 90 * M], 63 * M)
    assert result["label"] == cfg.F_DECEL_KEEP, result


def test_forecast_none_without_data():
    """전망치가 없으면 '-' 로 표시해야 함"""
    result = _forecast([100 * M, 115 * M, 140 * M], None)
    assert result["label"] == cfg.F_NONE, result
    assert result["next_qoq"] is None


def test_forecast_carries_confidence():
    """예측에 전망 근거의 신뢰도가 함께 담겨야 함"""
    guidance = _forecast([100 * M, 115 * M, 140 * M], 200 * M, cfg.SRC_GUIDANCE)
    estimate = _forecast([100 * M, 115 * M, 140 * M], 200 * M, cfg.SRC_ESTIMATE)

    assert guidance["confidence"] == 85, guidance
    assert estimate["confidence"] == 40, estimate


def test_ranking_table_has_confidence_and_forecast():
    """순위 표에 신뢰도·델타예측 열이 있어야 함"""
    price = {
        "state": cfg.S_FULL_UP, "slope": "상승", "disparity": 10.0, "rs": 20.0,
        "stage": 1, "close": 50.0,
    }
    quarters = make_quarters([100 * M, 115 * M, 140 * M], source=cfg.SRC_DIRECT)
    forward = {"forward_op_income": 200 * M, "revision": 1, "basis": cfg.SRC_GUIDANCE}
    scores = {"A": scoring.build_score("A", quarters, forward, price)}

    table = pipeline.build_ranking_table(scores)
    assert "신뢰도" in table.columns, table.columns
    assert "델타예측(1분기후)" in table.columns, table.columns
    assert table.iloc[0]["신뢰도"] > 0


def test_ranking_table_empty_is_safe():
    """점수가 하나도 없으면 빈 표를 돌려줘야 함 (크래시 금지)"""
    table = pipeline.build_ranking_table({})
    assert table.empty


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
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
    print(f"\n스코어링 테스트: {passed}개 통과, {failed}개 실패")
    sys.exit(1 if failed else 0)
