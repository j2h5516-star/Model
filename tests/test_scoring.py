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
    """실적 데이터가 하나도 없어도 예외 없이 점수를 내야 함"""
    price = {
        "state": cfg.S_NEUTRAL, "slope": "횡보", "disparity": 1.0, "rs": 0.0,
        "stage": 3, "close": 20.0, "week_change": 0.0,
    }
    result = scoring.build_score("EMPTY", [], {}, price)

    assert result["final_score"] >= 0
    assert result["data_source"] is None
    assert result["verdict"] in (
        cfg.V_BUY, cfg.V_WARN, cfg.V_WATCH, cfg.V_MOMENTUM, cfg.V_EXCLUDE
    )


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
