"""
test_parsing.py — 보도자료 파싱 로직 검증
========================================

인터넷 없이, 실제 실적 보도자료와 같은 형식의 가상 텍스트로
숫자를 제대로 뽑아내는지 확인합니다.

실행: python3 tests/test_parsing.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import forward_estimates as fe  # noqa: E402
import sec_fundamentals as sf  # noqa: E402
from fixtures import (  # noqa: E402
    PR_LOSS_PARENTHESES,
    PR_NARRATIVE_MILLIONS,
    PR_NEEDS_DERIVATION,
    PR_NO_GUIDANCE,
    PR_PERCENT_TRAP,
    PR_TABLE_THOUSANDS,
)


# ---------------------------------------------------------------------------
# 숫자 뽑기 기본 동작
# ---------------------------------------------------------------------------
def test_table_thousands_unit():
    """표 제목의 "(In thousands)"를 읽어 천 단위를 곱해야 함"""
    result = sf.parse_press_release(PR_TABLE_THOUSANDS)

    # 매출 135,000천 달러 = $135M
    assert abs(result["revenue"] - 135_000_000) < 1, result["revenue"]
    # 논갭 영업이익 45,450천 달러 = $45.45M
    assert abs(result["op_income"] - 45_450_000) < 1, result["op_income"]
    # 논갭 GM% 66.0
    assert abs(result["gross_margin_pct"] - 66.0) < 0.01, result["gross_margin_pct"]
    assert result["source"] == "직접공시", result["source"]
    assert result["gm_is_gaap"] is False


def test_narrative_millions():
    """문장 속 "$185.5 million" 표기를 정확히 읽어야 함"""
    result = sf.parse_press_release(PR_NARRATIVE_MILLIONS)

    assert abs(result["revenue"] - 2_650_000_000) < 1, result["revenue"]
    assert abs(result["op_income"] - 185_500_000) < 1, result["op_income"]
    assert abs(result["gross_margin_pct"] - 10.8) < 0.01, result["gross_margin_pct"]
    assert result["source"] == "직접공시"


def test_derivation_fallback():
    """논갭 영업이익이 없으면 매출 × GM% − opex 로 역산해야 함"""
    result = sf.parse_press_release(PR_NEEDS_DERIVATION)

    # 120,000천 × 58% − 60,000천 = 69,600천 − 60,000천 = 9,600천 = $9.6M
    assert result["source"] == "역산", result["source"]
    assert abs(result["op_income"] - 9_600_000) < 1, result["op_income"]


def test_negative_in_parentheses():
    """회계 표기의 괄호 (4,500) 는 음수 −4,500 으로 읽어야 함"""
    result = sf.parse_press_release(PR_LOSS_PARENTHESES)

    assert result["op_income"] is not None
    assert result["op_income"] < 0, result["op_income"]
    assert abs(result["op_income"] - (-4_500_000)) < 1, result["op_income"]


def test_percent_not_scaled():
    """퍼센트 값에는 천 단위 배수를 곱하면 안 됨"""
    result = sf.parse_press_release(PR_TABLE_THOUSANDS)
    assert 0 < result["gross_margin_pct"] <= 100, result["gross_margin_pct"]


def test_percent_trap_does_not_fool_amounts():
    """"Revenue grew 20% ... to $135.0 million" 에서 20을 매출로 오인하면 안 됨"""
    result = sf.parse_press_release(PR_PERCENT_TRAP)

    # 매출은 20(%)이 아니라 $135.0M 이어야 함
    assert abs(result["revenue"] - 135_000_000) < 1, result["revenue"]
    # 영업이익은 35(%)가 아니라 $28.5M 이어야 함
    assert abs(result["op_income"] - 28_500_000) < 1, result["op_income"]
    # GM%는 24.5% 를 잡아야 함
    assert abs(result["gross_margin_pct"] - 24.5) < 0.01, result["gross_margin_pct"]


def test_empty_text_is_safe():
    """빈 텍스트를 넣어도 예외 없이 빈 결과를 돌려줘야 함"""
    result = sf.parse_press_release("")
    assert result["revenue"] is None
    assert result["op_income"] is None
    assert result["source"] is None


def test_garbage_text_is_safe():
    """실적과 무관한 텍스트에서도 터지지 않아야 함"""
    result = sf.parse_press_release("The company will host a webcast next Tuesday.")
    assert result["op_income"] is None


# ---------------------------------------------------------------------------
# 분기 이름 뽑기
# ---------------------------------------------------------------------------
def test_period_label_from_text():
    """"third quarter of fiscal 2025" → "25 Q3" (차트 축에 들어가도록 짧게)"""
    label = sf.extract_period_label(PR_TABLE_THOUSANDS, "2025-03-04")
    assert label == "25 Q3", label


def test_period_label_fallback():
    """분기 표현이 없으면 제출 연월을 짧게 표시해야 함"""
    label = sf.extract_period_label("no quarter mentioned here", "2025-05-20")
    assert label == "25/05", label


def test_period_end_label():
    """XBRL 기간종료일도 짧은 형식으로 바뀌어야 함"""
    assert sf.period_end_label("2026-01-31") == "26/01"


def test_earnings_detection():
    """실적발표 공시인지 알아보는 판별이 동작해야 함"""
    assert sf._looks_like_earnings(PR_TABLE_THOUSANDS) is True
    assert sf._looks_like_earnings("Notice of annual meeting of shareholders") is False


# ---------------------------------------------------------------------------
# 가이던스 파싱
# ---------------------------------------------------------------------------
def test_guidance_range_with_gm_and_opex():
    """매출 범위 + GM% 범위 + opex 범위로 포워드 영업이익을 계산해야 함"""
    guidance = fe.parse_guidance(PR_TABLE_THOUSANDS)

    # 매출 $155M~$165M 중간값 = $160M
    assert abs(guidance["revenue"] - 160_000_000) < 1, guidance["revenue"]
    # GM 64~66% 중간값 = 65%
    assert abs(guidance["gross_margin_pct"] - 65.0) < 0.01, guidance["gross_margin_pct"]
    # opex $46M~$48M 중간값 = $47M
    assert abs(guidance["opex"] - 47_000_000) < 1, guidance["opex"]

    # 포워드 = 160M × 65% − 47M = 104M − 47M = 57M
    forward = fe.forward_from_guidance(guidance)
    assert abs(forward - 57_000_000) < 1, forward


def test_guidance_with_operating_margin():
    """영업마진(%)만 제시한 경우에도 포워드를 계산해야 함"""
    guidance = fe.parse_guidance(PR_NARRATIVE_MILLIONS)

    # 매출 $2,600M~$2,750M 중간값 = $2,675M
    assert abs(guidance["revenue"] - 2_675_000_000) < 1_000, guidance["revenue"]
    assert guidance["operating_margin_pct"] is not None

    # 포워드 = 2,675M × 7.2% ≈ 192.6M
    forward = fe.forward_from_guidance(guidance)
    assert 180_000_000 < forward < 205_000_000, forward


def test_no_guidance_returns_none():
    """가이던스 문단이 없으면 None을 돌려줘야 함"""
    guidance = fe.parse_guidance(PR_NO_GUIDANCE)
    assert fe.forward_from_guidance(guidance) is None


def test_guidance_section_not_found():
    """전망 관련 표현이 전혀 없으면 빈 문자열을 돌려줘야 함"""
    assert fe.find_guidance_section("Revenue was $100 million.") == ""


# ---------------------------------------------------------------------------
# 평균 영업마진 계산
# ---------------------------------------------------------------------------
def test_average_operating_margin():
    """최근 4개 분기 평균 논갭 영업마진(%)을 정확히 계산해야 함"""
    from fixtures import make_quarters

    # 영업이익 10, 20, 30, 40 / 매출 100, 100, 100, 100 → 마진 10,20,30,40% → 평균 25%
    quarters = make_quarters([10, 20, 30, 40], revenues=[100, 100, 100, 100])
    avg = fe.average_operating_margin(quarters, n=4)
    assert abs(avg - 25.0) < 0.01, avg


def test_average_margin_empty():
    """데이터가 없으면 None을 돌려줘야 함"""
    assert fe.average_operating_margin([]) is None


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
    print(f"\n파싱 테스트: {passed}개 통과, {failed}개 실패")
    sys.exit(1 if failed else 0)
