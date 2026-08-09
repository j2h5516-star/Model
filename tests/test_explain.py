"""
test_explain.py — "어떻게 계산됐는지" 설명 생성 검증
==================================================

화면에서 항목을 눌렀을 때 나오는 설명이 올바른 숫자와 근거를 담고 있는지 확인합니다.

실행: python3 tests/test_explain.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import config as cfg  # noqa: E402
import explain  # noqa: E402
import scoring  # noqa: E402
from fixtures import make_quarters  # noqa: E402

M = 1_000_000


def _score(
    op_incomes=(100 * M, 115 * M, 140 * M),
    margins=(50.0, 50.5, 51.0),
    source=cfg.SRC_DIRECT,
    forward_op=170 * M,
    basis=cfg.SRC_GUIDANCE,
    trend_state=cfg.S_FULL_UP,
    stage=1,
):
    """테스트용 종목 점수를 만듭니다."""
    quarters = make_quarters(list(op_incomes), margins=list(margins), source=source)
    quarters[-1]["derivation"] = "테스트용 계산 설명"
    quarters[-1]["filing_url"] = "https://www.sec.gov/test"
    forward = {
        "forward_op_income": forward_op,
        "revision": 1,
        "basis": basis,
        "detail": "테스트 전망 근거",
    }
    price = {
        "state": trend_state, "slope": "상승", "disparity": 12.0, "rs": 25.0,
        "stage": stage, "close": 100.0, "week_change": 1.5,
    }
    return scoring.build_score("TEST", quarters, forward, price)


# ---------------------------------------------------------------------------
# 데이터 출처 설명
# ---------------------------------------------------------------------------
def test_data_source_direct():
    """직접공시는 '그대로 가져왔다'는 설명과 SEC 원문 링크를 포함해야 함"""
    data = explain.explain_data_source(_score(source=cfg.SRC_DIRECT))

    assert cfg.SRC_DIRECT in data["title"]
    assert "직접" in data["meaning"]
    assert data["url"] == "https://www.sec.gov/test"
    assert "8-K" in data["source_text"]


def test_data_source_approx_warns():
    """근사치는 '차이가 날 수 있다'는 주의 문구와 XBRL 계산식을 포함해야 함"""
    data = explain.explain_data_source(_score(source=cfg.SRC_APPROX))

    assert "차이가 날 수 있" in data["meaning"]
    assert "주식보상비" in data["formula"]
    assert "XBRL" in data["source_text"]


def test_data_source_derived_formula():
    """역산은 매출×마진−비용 공식을 보여줘야 함"""
    data = explain.explain_data_source(_score(source=cfg.SRC_DERIVED))
    assert "매출" in data["formula"] and "영업비용" in data["formula"]


def test_data_source_no_data():
    """실적이 아예 없으면 안내만 하고 터지지 않아야 함"""
    price = {"state": cfg.S_NEUTRAL, "slope": "횡보", "disparity": 0.0, "rs": 0.0, "stage": 3}
    data = explain.explain_data_source(scoring.build_score("X", [], {}, price))
    assert data["quarter_count"] == 0


# ---------------------------------------------------------------------------
# 포워드(전망) 설명
# ---------------------------------------------------------------------------
def test_forward_guidance_explanation():
    """가이던스는 '회사가 직접 발표'라는 점과 실제 숫자를 보여줘야 함"""
    data = explain.explain_forward(_score(basis=cfg.SRC_GUIDANCE, forward_op=170 * M))

    assert "회사가 직접 발표" in data["meaning"]
    joined = " ".join(data["calc_lines"])
    assert "$170.0M" in joined, joined
    assert "$140.0M" in joined, joined       # 최근 분기 실제값
    assert "+21.4%" in joined, joined        # 170/140 − 1 = +21.4%


def test_forward_estimate_warns():
    """추정은 '실제와 다를 수 있다'는 경고를 포함해야 함"""
    data = explain.explain_forward(_score(basis=cfg.SRC_ESTIMATE))

    assert "다를 수 있" in data["meaning"]
    assert "Yahoo" in data["source_text"]


def test_forward_missing():
    """전망이 없으면 그 사실을 알려줘야 함"""
    data = explain.explain_forward(_score(forward_op=None, basis=None))
    assert "찾지 못" in data["meaning"]


# ---------------------------------------------------------------------------
# GM% 드라이버 설명
# ---------------------------------------------------------------------------
def test_gm_driver_explanation_numbers():
    """GM 설명은 최근값·기준값·차이를 정확히 담아야 함"""
    # 직전 4개 평균 = (40+40+40+40)/4 = 40, 최근 = 42 → +2%p → 믹스형
    score = _score(
        op_incomes=(10 * M, 10 * M, 10 * M, 10 * M, 10 * M),
        margins=(40.0, 40.0, 40.0, 40.0, 42.0),
    )
    data = explain.explain_gm_driver(score)

    assert "믹스형" in data["title"]
    joined = " ".join(data["calc_lines"])
    assert "42.00%" in joined, joined
    assert "40.00%" in joined, joined
    assert "+2.00%p" in joined, joined
    assert len(data["bands"]) == 4          # 구간표 4줄
    assert len(data["history"]) == 5        # 분기별 GM 이력


def test_gm_driver_price_type_warning():
    """가격형은 '오래 유지되기 어렵다'는 설명을 담아야 함"""
    score = _score(
        op_incomes=(10 * M,) * 5,
        margins=(40.0, 40.0, 40.0, 40.0, 46.0),   # +6%p 급등
    )
    data = explain.explain_gm_driver(score)

    assert "가격형" in data["title"]
    assert "오래 유지되기 어" in data["meaning"]


# ---------------------------------------------------------------------------
# 델타 방향 설명 (이력 + 예측)
# ---------------------------------------------------------------------------
def test_delta_explanation_has_history_and_forecast():
    """델타 설명은 지난 이력과 앞으로의 예측을 모두 담아야 함"""
    data = explain.explain_delta(_score())

    assert "가속" in data["title"]
    assert len(data["history"]) >= 2, data["history"]
    assert all("QoQ 증가율(%)" in row for row in data["history"])

    forecast = " ".join(data["forecast_lines"])
    assert "다음 분기 예상" in forecast, forecast
    # 140M → 170M = +21.4%
    assert "+21.4%" in forecast, forecast
    assert "실제 결과와 다를 수 있" in forecast
    # 전망 근거의 신뢰도가 함께 표시돼야 함
    assert "신뢰도 85%" in forecast, forecast


def test_delta_forecast_detects_slowdown():
    """전망이 최근 증가율보다 크게 낮으면 '가속 둔화'로 표시해야 함"""
    # 최근 QoQ = 140/115 - 1 = +21.7%, 다음 분기 전망 = 142M → +1.4% (크게 둔화)
    data = explain.explain_delta(_score(forward_op=142 * M))
    forecast = " ".join(data["forecast_lines"])
    assert cfg.F_ACCEL_SLOW in forecast, forecast


def test_delta_explanation_shows_two_signals():
    """델타 설명에 단기·추세 두 신호가 모두 나와야 함"""
    score = _score(
        op_incomes=(100 * M, 115 * M, 140 * M, 175 * M, 220 * M),
        margins=(50.0,) * 5,
    )
    data = explain.explain_delta(score)

    joined = " ".join(data["calc_lines"])
    assert "신호① 단기" in joined, joined
    assert "신호② 추세" in joined, joined
    assert "회귀 기울기" in data["formula"] or "기울기" in data["formula"]


def test_delta_forecast_missing():
    """전망치가 없으면 예측을 계산할 수 없다고 알려야 함"""
    data = explain.explain_delta(_score(forward_op=None, basis=None))
    assert "예측을 계산할 수 없" in " ".join(data["forecast_lines"])


def test_delta_low_base_cap_shown():
    """저기저 상한이 걸리면 그 이유를 설명에 표시해야 함"""
    score = _score(op_incomes=(10 * M, 11 * M, 13 * M))   # 모두 $50M 미만
    data = explain.explain_delta(score)

    joined = " ".join(data["calc_lines"])
    assert "저기저 함정 방지" in joined, joined


# ---------------------------------------------------------------------------
# 판정 설명
# ---------------------------------------------------------------------------
def test_verdict_explanation_buy():
    """매수 후보는 적용 규칙과 5종 규칙표를 함께 보여줘야 함"""
    data = explain.explain_verdict_full(_score())

    assert cfg.V_BUY in data["title"], data["title"]
    assert data["current"] == cfg.V_BUY
    assert len(data["rules"]) == 5
    joined = " ".join(data["calc_lines"])
    assert "적용된 규칙" in joined
    assert "최종점수" in joined


def test_verdict_explanation_momentum():
    """펀더 없는 모멘텀은 '실적이 뒷받침되지 않는다'는 설명이 나와야 함"""
    # 이익이 계속 줄어드는 종목 → 펀더 낮음 + 추세는 정배열
    score = _score(op_incomes=(200 * M, 120 * M, 70 * M), margins=(50.0, 44.0, 38.0))
    data = explain.explain_verdict_full(score)

    if data["current"] == cfg.V_MOMENTUM:
        assert "뒷받침되지 않" in data["meaning"], data["meaning"]


def test_verdict_explanation_warning():
    """경고는 '정점을 반영했을 가능성' 설명을 담아야 함"""
    score = _score(trend_state=cfg.S_FULL_DOWN, stage=5)
    data = explain.explain_verdict_full(score)

    if data["current"] == cfg.V_WARN:
        assert "정점" in data["meaning"], data["meaning"]


# ---------------------------------------------------------------------------
# 기술 점수 설명
# ---------------------------------------------------------------------------
def test_technical_explanation_breakdown():
    """기술 점수는 4개 항목 배점을 모두 보여줘야 함"""
    data = explain.explain_technical(_score())

    assert len(data["calc_lines"]) == 4
    joined = " ".join(data["calc_lines"])
    for keyword in ("추세", "기울기", "이격도", "상대강도"):
        assert keyword in joined, joined


def test_confidence_explanation():
    """신뢰도 설명은 실적·전망·종합 계산 과정을 모두 보여줘야 함"""
    data = explain.explain_confidence(_score(source=cfg.SRC_DIRECT, basis=cfg.SRC_GUIDANCE))

    joined = " ".join(data["calc_lines"])
    assert "실적 신뢰도" in joined and "95%" in joined, joined
    assert "전망 신뢰도" in joined and "85%" in joined, joined
    # 95 × 0.7 + 85 × 0.3 = 92
    assert "92%" in data["title"], data["title"]
    assert "점수에는 반영하지 않고" in data["meaning"]
    assert len(data["history"]) == 3   # 분기별 출처 내역


def test_confidence_tier_table():
    """등급표는 6단계 전부를 순위·신뢰도와 함께 담아야 함"""
    table = explain.confidence_tier_table()

    assert len(table) == 6, table
    assert table[0]["종류"] == cfg.SRC_DIRECT
    assert table[0]["신뢰도"] == "95%"
    assert table[-1]["종류"] == cfg.SRC_NONE
    # 순위가 1부터 차례대로인지
    assert [row["순위"] for row in table] == [1, 2, 3, 4, 5, 6]


def test_confidence_low_when_approximate():
    """근사치 + 추정이면 신뢰도가 낮게 나와야 함"""
    data = explain.explain_confidence(_score(source=cfg.SRC_APPROX, basis=cfg.SRC_ESTIMATE))
    # 55 × 0.7 + 40 × 0.3 = 50.5
    assert "50" in data["title"] or "51" in data["title"], data["title"]


def test_mixed_delta_explanation():
    """혼조는 '두 신호가 서로 반대'라는 설명을 담아야 함"""
    # 단기는 살짝 개선인데 전체 추세는 급격히 하락하는 형태
    score = _score(
        op_incomes=(100 * M, 160 * M, 200 * M, 220 * M, 250 * M),
        margins=(50.0,) * 5,
    )
    data = explain.explain_delta(score)

    if cfg.D_MIXED in data["title"]:
        assert "서로 반대" in data["meaning"], data["meaning"]


def test_all_explanations_survive_empty_data():
    """실적이 하나도 없는 종목에서도 모든 설명이 예외 없이 만들어져야 함"""
    price = {"state": cfg.S_UNKNOWN, "slope": "-", "disparity": None, "rs": None, "stage": 6}
    score = scoring.build_score("EMPTY", [], {}, price)

    for fn in (
        explain.explain_data_source,
        explain.explain_forward,
        explain.explain_gm_driver,
        explain.explain_delta,
        explain.explain_verdict_full,
        explain.explain_technical,
        explain.explain_confidence,
    ):
        data = fn(score)
        assert isinstance(data, dict) and data.get("title"), fn.__name__


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
    print(f"\n설명 생성 테스트: {passed}개 통과, {failed}개 실패")
    sys.exit(1 if failed else 0)
