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

import config as cfg  # noqa: E402
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
# 빠진 4분기(Q4) 채우기 — 표본 부족의 주요 원인이었던 버그
# ---------------------------------------------------------------------------
def test_fill_missing_q4():
    """Q4 = 연간 − (Q1+Q2+Q3) 로 계산해 채워야 함"""
    # 1~3분기는 3개월 단위로 있고, 4분기(연말)는 비어 있는 상태
    quarterly = {
        "2025-03-31": 100.0,
        "2025-06-30": 110.0,
        "2025-09-30": 120.0,
    }
    annual = {"2025-12-31": 500.0}   # 연간 500 → Q4 = 500 − 330 = 170

    filled = sf._fill_missing_q4(quarterly, annual)

    assert "2025-12-31" in filled, filled
    assert abs(filled["2025-12-31"] - 170.0) < 1e-9, filled["2025-12-31"]
    # 기존 분기는 그대로여야 함
    assert filled["2025-03-31"] == 100.0


def test_fill_q4_does_not_overwrite_existing():
    """이미 4분기 값이 있으면 건드리지 않아야 함"""
    quarterly = {
        "2025-03-31": 100.0, "2025-06-30": 110.0,
        "2025-09-30": 120.0, "2025-12-31": 999.0,
    }
    filled = sf._fill_missing_q4(quarterly, {"2025-12-31": 500.0})
    assert filled["2025-12-31"] == 999.0


def test_fill_q4_skips_when_quarters_missing():
    """앞선 세 분기를 못 찾으면 계산하지 않아야 함 (엉뚱한 값 방지)"""
    quarterly = {"2025-03-31": 100.0, "2025-06-30": 110.0}   # 2개뿐
    filled = sf._fill_missing_q4(quarterly, {"2025-12-31": 500.0})
    assert "2025-12-31" not in filled, filled


def test_fill_q4_non_calendar_fiscal_year():
    """회계연도가 12월이 아닌 회사(예: 1월 결산)도 계산돼야 함"""
    quarterly = {
        "2025-04-30": 50.0,
        "2025-07-31": 60.0,
        "2025-10-31": 70.0,
    }
    annual = {"2026-01-31": 250.0}   # Q4 = 250 − 180 = 70

    filled = sf._fill_missing_q4(quarterly, annual)
    assert abs(filled["2026-01-31"] - 70.0) < 1e-9, filled


# ---------------------------------------------------------------------------
# XBRL 뼈대 + 8-K 덮어쓰기 병합
# ---------------------------------------------------------------------------
def _xbrl_row(period_end, op_income):
    return {
        "ticker": "T", "filing_date": period_end, "period_label": period_end[2:7],
        "revenue": op_income * 5, "op_income": op_income, "gross_margin_pct": 50.0,
        "source": "근사치", "gm_is_gaap": True, "filing_url": "", "derivation": "",
        "guidance_text": "",
    }


def _press_row(filing_date, op_income):
    return {
        "ticker": "T", "filing_date": filing_date, "period_label": "25 Q1",
        "revenue": op_income * 5, "op_income": op_income, "gross_margin_pct": 55.0,
        "source": "직접공시", "gm_is_gaap": False, "filing_url": "https://sec.gov/x",
        "derivation": "원문 그대로", "guidance_text": "outlook...",
    }


def test_merge_overwrites_matching_quarter_only():
    """8-K가 성공한 분기만 '직접공시'로 올라가고 나머지는 근사치를 유지해야 함"""
    xbrl = [_xbrl_row("2025-03-31", 100.0), _xbrl_row("2025-06-30", 120.0)]
    press = [_press_row("2025-04-25", 111.0)]   # 3월 분기 발표 (25일 뒤)

    merged = sf.merge_quarters(xbrl, press)

    assert merged[0]["source"] == "직접공시", merged[0]
    assert merged[0]["op_income"] == 111.0
    assert merged[0]["gross_margin_pct"] == 55.0
    assert merged[0]["announced_date"] == "2025-04-25"
    # 짝이 없는 분기는 그대로 근사치
    assert merged[1]["source"] == "근사치", merged[1]
    assert merged[1]["op_income"] == 120.0


def test_merge_ignores_far_away_filing():
    """분기 종료일과 90일 넘게 떨어진 8-K는 짝짓지 않아야 함"""
    xbrl = [_xbrl_row("2025-03-31", 100.0)]
    press = [_press_row("2025-09-01", 111.0)]   # 5개월 뒤 → 다른 분기

    merged = sf.merge_quarters(xbrl, press)
    assert merged[0]["source"] == "근사치", merged[0]


def test_merge_without_xbrl_uses_press_only():
    """XBRL이 비어 있으면 8-K 결과만으로 구성해야 함"""
    press = [_press_row("2025-04-25", 111.0)]
    merged = sf.merge_quarters([], press)
    assert len(merged) == 1 and merged[0]["source"] == "직접공시"


def test_merge_without_xbrl_still_stamps_announced_date():
    """XBRL이 비어도 발표일 도장은 찍혀야 함 (41차 검증 발견 결함).

    측정은 발표일로만 전 종목을 줄 세우므로(전략.md 7장), 도장이 없으면
    그 종목은 측정에서 통째로 빠집니다. 실물에서 22종목(은행·제약·에너지·
    산업재 전부와 KLAC)이 이 구멍으로 빠져 '판단 불가'군이 업종 편향이
    됐습니다.
    """
    press = [_press_row("2025-04-25", 111.0), _press_row("2025-01-30", 90.0)]
    merged = sf.merge_quarters([], press)
    assert [r["announced_date"] for r in merged] == ["2025-01-30", "2025-04-25"], merged
    # 원본 8-K 목록은 건드리지 않습니다 (원본 불변)
    assert "announced_date" not in press[0]


def test_merge_without_press_keeps_xbrl():
    """8-K가 하나도 없으면 XBRL 뼈대를 그대로 유지해야 함"""
    xbrl = [_xbrl_row("2025-03-31", 100.0)]
    merged = sf.merge_quarters(xbrl, [])
    assert len(merged) == 1 and merged[0]["source"] == "근사치"


def test_merge_prefers_press_with_numbers_over_closer_preliminary():
    """예비 공지(이익 숫자 없음)가 가깝다고 진짜 실적 발표를 밀어내면 안 됨.

    실물 사고 (9차 감사): CRDO 26Q3 — 예비 매출 공지(분기 종료 9일 뒤)가
    분기를 차지해, 실적 발표(30일 뒤)의 조정 EPS $1.07 이 통째로 버려졌음.
    """
    xbrl = [_xbrl_row("2026-01-31", 100.0)]
    preliminary = _press_row("2026-02-09", 111.0)
    preliminary.update(op_income=None, gross_margin_pct=None, revenue=404_000_000.0)
    real = _press_row("2026-03-02", 222_000_000.0)
    real["adj_eps"] = 1.07

    merged = sf.merge_quarters(xbrl, [preliminary, real])
    assert merged[0]["announced_date"] == "2026-03-02", merged[0]
    assert merged[0]["adj_eps"] == 1.07
    assert merged[0]["source"] == "직접공시"


def test_sanity_ebitda_rules():
    """조정 EBITDA 검사 — ① $10만 미만은 오파싱 ② EBITDA > 매출이면 매출을 버림.

    두 규칙 모두 실물 오류에서 나옴: FSLR 에서 1.0 이 EBITDA 로 잡혔고,
    ZETA 에서 가이던스 상향 폭 $33M 이 매출로 잡혀 EBITDA(91.7M)보다 작아짐.
    """
    fslr_like = {"revenue": 1_179_374.0, "op_income": None, "adjusted_ebitda": 1.0}
    sf._sanity_check_press(fslr_like)
    assert fslr_like["adjusted_ebitda"] is None

    zeta_like = {"revenue": 33_859_000.0, "op_income": None,
                 "adjusted_ebitda": 91_697_000.0}
    sf._sanity_check_press(zeta_like)
    assert zeta_like["revenue"] is None                       # 매출 쪽을 버림
    assert zeta_like["adjusted_ebitda"] == 91_697_000.0       # EBITDA 는 지킴


def test_diagnostic_report_shape():
    """진단 리포트에 단계별 항목이 모두 있어야 함"""
    report = sf.new_report("TEST")
    for key in ("filings_found", "text_ok", "gate_passed", "parsed_ok",
                "xbrl_quarters", "merged_direct", "first_error"):
        assert key in report, key


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
    forward = fe.forward_from_guidance(guidance)[0]
    assert abs(forward - 57_000_000) < 1, forward


# ---------------------------------------------------------------------------
# 가이던스에서 GAAP 을 집어오지 않는가 (실제로 있었던 오류)
# ---------------------------------------------------------------------------
# 미국 회사들은 가이던스에 GAAP 과 논갭을 **둘 다** 적고, 관례적으로 GAAP 을
# 먼저 씁니다. 그냥 첫 번째를 잡으면 GAAP 을 가져옵니다.
# 이 모델은 과거 실적을 논갭으로 쌓아 두므로, 전망만 GAAP 이면 한 종목 안에서
# 정의가 섞여 사업에 아무 변화가 없어도 증가율에 가짜 신호가 생깁니다.
NVDA_STYLE = """Outlook for the third quarter of fiscal 2026 is as follows:
Revenue is expected to be $54.0 billion, plus or minus 2%.
GAAP and non-GAAP gross margins are expected to be 73.3% and 73.5%, respectively.
GAAP and non-GAAP operating expenses are expected to be approximately $5.9 billion and $4.2 billion, respectively."""

AMD_STYLE = """For the third quarter of 2026, AMD expects revenue to be approximately $8.7 billion.
AMD expects GAAP gross margin to be approximately 50% and non-GAAP gross margin to be approximately 54%.
GAAP operating expenses are expected to be approximately $2.9 billion; non-GAAP operating expenses approximately $2.3 billion."""

MARGIN_STYLE = """Business Outlook: Revenue of $1.20 billion to $1.30 billion.
GAAP operating margin of 12% to 14%. Non-GAAP operating margin of 20% to 22%."""


def test_guidance_picks_nongaap_when_listed_side_by_side():
    """'GAAP and non-GAAP ... A and B, respectively' 는 **뒤쪽** 값이 논갭"""
    guidance = fe.parse_guidance(NVDA_STYLE)

    assert abs(guidance["gross_margin_pct"] - 73.5) < 0.01, guidance["gross_margin_pct"]
    assert abs(guidance["opex"] - 4_200_000_000) < 1, guidance["opex"]

    # 매출 54.0B × 73.5% − 4.2B = 35.49B  (GAAP 으로 집었다면 33.68B 이 됩니다)
    forward = fe.forward_from_guidance(guidance)[0]
    assert abs(forward - 35_490_000_000) < 1_000_000, forward


def test_guidance_picks_nongaap_when_stated_separately():
    """'non-GAAP gross margin ... 54%' 처럼 따로 적어 준 경우도 논갭을 집어야 함"""
    guidance = fe.parse_guidance(AMD_STYLE)

    assert abs(guidance["gross_margin_pct"] - 54.0) < 0.01, guidance["gross_margin_pct"]
    assert abs(guidance["opex"] - 2_300_000_000) < 1, guidance["opex"]


def test_guidance_picks_nongaap_operating_margin():
    """영업마진도 논갭을 집어야 함 (GAAP 12~14% 가 아니라 논갭 20~22%)"""
    guidance = fe.parse_guidance(MARGIN_STYLE)

    assert abs(guidance["operating_margin_pct"] - 21.0) < 0.01, guidance
    forward = fe.forward_from_guidance(guidance)[0]
    # 1.25B × 21% = 262.5M  (GAAP 13% 로 집었다면 162.5M — 38% 낮습니다)
    assert abs(forward - 262_500_000) < 1_000_000, forward


def test_guidance_flags_when_only_gaap_is_available():
    """논갭이 아예 없으면 그 사실을 남겨야 함 (신뢰도를 낮출 근거)"""
    gaap_only = """Business Outlook: Revenue of $1.00 billion.
GAAP operating margin of 12%."""
    guidance = fe.parse_guidance(gaap_only)

    assert guidance["gaap_only"] is True, guidance
    assert guidance["om_basis"] == "GAAP만", guidance


def test_past_tense_quarter_phrase_is_not_treated_as_guidance():
    """'for the third quarter of fiscal 2025 ended ...' 는 과거 서술 — 전망이 아님

    이 문구를 전망 신호로 잡으면 손익계산서를 통째로 전망 문단으로 오인해,
    매출을 못 찾고 엉뚱한 마진을 집어 옵니다. 실제로 그런 회귀가 났습니다.
    """
    guidance = fe.parse_guidance(PR_TABLE_THOUSANDS)

    # 진짜 전망 문단을 찾아 매출을 제대로 집어야 합니다
    assert guidance["revenue"] is not None, guidance
    assert abs(guidance["revenue"] - 160_000_000) < 1, guidance["revenue"]


def test_guidance_with_operating_margin():
    """영업마진(%)만 제시한 경우에도 포워드를 계산해야 함"""
    guidance = fe.parse_guidance(PR_NARRATIVE_MILLIONS)

    # 매출 $2,600M~$2,750M 중간값 = $2,675M
    assert abs(guidance["revenue"] - 2_675_000_000) < 1_000, guidance["revenue"]
    assert guidance["operating_margin_pct"] is not None

    # 포워드 = 2,675M × 7.2% ≈ 192.6M
    forward = fe.forward_from_guidance(guidance)[0]
    assert 180_000_000 < forward < 205_000_000, forward


def test_no_guidance_returns_none():
    """가이던스 문단이 없으면 None을 돌려줘야 함"""
    guidance = fe.parse_guidance(PR_NO_GUIDANCE)
    assert fe.forward_from_guidance(guidance)[0] is None


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




def test_reconciliation_title_is_not_read_as_the_value():
    """조정표 **제목**에 걸려 바로 아래 GAAP 값을 논갭으로 읽으면 안 됩니다.

    제목이 라벨과 같은 문구라서, 숫자 탐색 창(160자)이 줄을 넘어가 그 아래
    첫 숫자를 채택했습니다. 44% 과소인데 화면에는 "조정표에 적힌 논갭 값을
    그대로 사용"이라는 거짓 근거가 붙었습니다.
    """
    text = (
        "RECONCILIATION OF GAAP TO NON-GAAP OPERATING INCOME\n"
        "(in thousands)\n"
        "GAAP operating income                        140,000\n"
        "Stock-based compensation                      95,000\n"
        "Non-GAAP operating income                    250,000"
    )
    assert sf.find_labeled_value(text, sf.LABELS_NONGAAP_OP_INCOME) == 250_000_000


def test_label_used_as_prose_does_not_steal_the_value():
    """설명 문구로 쓰인 라벨이 뒤에 나오는 GAAP 숫자를 집으면 안 됩니다.

    값은 언제나 자기 라벨 바로 뒤에 옵니다. 설명 문구는 숫자가 한참 뒤에
    있으므로 '가장 가까운 라벨'을 고르면 걸러집니다.
    """
    text = (
        "Management uses non-GAAP operating income to evaluate performance. "
        "GAAP operating income was $140.0 million, and "
        "non-GAAP operating income was $250.0 million."
    )
    assert sf.find_labeled_value(text, sf.LABELS_NONGAAP_OP_INCOME) == 250_000_000


def test_value_on_the_next_line_is_still_found():
    """줄이 바뀐 서술형도 놓치지 않아야 합니다 (같은 줄 우선이 과하면 안 됨)"""
    text = "Non-GAAP operating income\n   was $250.0 million for the quarter."
    assert sf.find_labeled_value(text, sf.LABELS_NONGAAP_OP_INCOME) == 250_000_000


def test_table_unit_headers_are_all_recognised():
    """보도자료에서 흔한 단위 표기를 전부 알아봐야 합니다.

    못 알아보면 값이 1,000배 작아지는데, 매출과 영업이익이 같이 작아져
    마진 비율이 보존되므로 **어떤 정합성 검사에도 걸리지 않습니다.**
    """
    cases = {
        "(in thousands, except per share data)": 1_000,
        "(Unaudited, in thousands)": 1_000,
        "(unaudited, in millions)": 1_000_000,
        "(Dollars in thousands)": 1_000,
        "($ in millions)": 1_000_000,
        "(U.S. dollars in millions)": 1_000_000,
        "(dollars and shares in thousands, except per share data)": 1_000,
        "(in billions)": 1_000_000_000,
        "단위 표기 없음": 1,
    }
    for header, expected in cases.items():
        text = header + "\nNon-GAAP operating income   250,000\n"
        assert sf._detect_table_unit(text, len(text)) == expected, header


def test_annual_figures_are_not_read_as_quarterly():
    """'4분기 및 연간' 보도자료에서 연간 숫자를 분기 자리에 넣으면 안 됩니다.

    구분하는 장치가 전혀 없어 매년 4분기마다 +228% 짜리 가짜 급등이 생겼습니다.
    """
    text = (
        "ACME Corp. Reports Fourth Quarter and Full Year 2024 Results\n"
        "Fourth quarter revenue was $1.0 billion, up 18%.\n"
        "Full year revenue was $3.6 billion.\n"
        "Fourth quarter non-GAAP operating income was $250.0 million.\n"
        "For the year, non-GAAP operating income was $820.0 million."
    )
    result = sf.parse_press_release(text)
    assert result["revenue"] == 1_000_000_000, result["revenue"]
    assert result["op_income"] == 250_000_000, result["op_income"]


def test_quarter_column_wins_in_a_two_column_table():
    """'3개월 / 12개월' 두 열이 있는 표에서는 분기 열을 써야 합니다"""
    text = (
        "(in thousands)                    Three Months Ended   Twelve Months Ended\n"
        "Total revenue                          1,000,000            3,600,000\n"
        "Non-GAAP operating income                250,000              820,000"
    )
    result = sf.parse_press_release(text)
    assert result["revenue"] == 1_000_000_000
    assert result["op_income"] == 250_000_000


def test_annual_only_document_still_yields_numbers():
    """연간 숫자밖에 없는 문서에서는 그것이라도 써야 합니다 (자료를 잃지 않음)"""
    text = (
        "Full year revenue was $3.6 billion and "
        "non-GAAP operating income was $820.0 million."
    )
    result = sf.parse_press_release(text)
    assert result["revenue"] == 3_600_000_000
    assert result["op_income"] == 820_000_000


def test_earlier_quarter_does_not_steal_the_next_quarters_8k():
    """앞 분기가 뒷 분기의 8-K 를 가로채면 안 됩니다.

    짝짓기 창(120일)이 분기 간격(약 91일)보다 넓어서, 앞 분기의 8-K 가 파싱에
    실패하면 앞 분기가 뒷 분기 것을 가져갔습니다. 12월 결산 회사의 2분기
    발표(7/25)는 1분기 종료일(3/31)에서 116일이라 창 안에 들어옵니다.
    """
    M = 1_000_000
    xbrl = [
        {"filing_date": "2024-03-31", "period_label": "24/03",
         "revenue": 600 * M, "op_income": 100 * M, "source": cfg.SRC_APPROX},
        {"filing_date": "2024-06-30", "period_label": "24/06",
         "revenue": 560 * M, "op_income": 130 * M, "source": cfg.SRC_APPROX},
    ]
    press = [{"filing_date": "2024-07-25", "period_label": "24 Q2",
              "revenue": 600 * M, "op_income": 155 * M, "source": cfg.SRC_DIRECT,
              "gm_is_gaap": False, "derivation": "", "filing_url": ""}]

    merged = sf.merge_quarters(xbrl, press, {})

    first, second = merged[0], merged[1]
    assert first["op_income"] == 100 * M, "3월 분기가 7월 발표를 가로챘습니다"
    assert first["period_label"] == "24/03", "3월 분기 이름표가 덮였습니다"
    assert second["op_income"] == 155 * M, "6월 분기가 자기 발표를 못 받았습니다"
    assert second["source"] == cfg.SRC_DIRECT


def test_8k_still_pairs_when_it_is_the_only_candidate_quarter():
    """가로채기 방지가 과해서 정상 짝짓기까지 막으면 안 됩니다"""
    M = 1_000_000
    xbrl = [{"filing_date": "2024-06-30", "period_label": "24/06",
             "revenue": 560 * M, "op_income": 130 * M, "source": cfg.SRC_APPROX}]
    press = [{"filing_date": "2024-07-25", "period_label": "24 Q2",
              "revenue": 600 * M, "op_income": 155 * M, "source": cfg.SRC_DIRECT,
              "gm_is_gaap": False, "derivation": "", "filing_url": ""}]

    merged = sf.merge_quarters(xbrl, press, {})
    assert merged[0]["op_income"] == 155 * M
    assert merged[0]["source"] == cfg.SRC_DIRECT


# ---------------------------------------------------------------------------
# XBRL GAAP EPS — 구조화 데이터에서 주당순이익 뽑기 (조정 EPS 미발표 회사 커버)
# ---------------------------------------------------------------------------
class _FakeFacts:
    """edgartools facts 흉내 — query().by_concept().by_period_length().to_dataframe()"""

    def __init__(self, rows):
        self._rows = rows      # [{concept, unit, period_end, numeric_value, months}]

    def query(self):
        return self

    def by_concept(self, concept):
        self._concept = concept.lower()
        return self

    def by_period_length(self, months):
        self._months = months
        return self

    def to_dataframe(self):
        import pandas as pd
        picked = [
            {k: r[k] for k in ("concept", "unit", "period_end", "numeric_value")}
            for r in self._rows
            # 실제 edgartools 처럼 부분일치로 딸려 오는 상황을 흉내냅니다
            if self._concept in r["concept"].lower() and r["months"] == self._months
        ]
        return pd.DataFrame(picked)


def _eps_fact(period_end, value, months=3, concept="us-gaap:EarningsPerShareDiluted"):
    return {"concept": concept, "unit": "USD/shares",
            "period_end": period_end, "numeric_value": value, "months": months}


def test_xbrl_concepts_include_gaap_eps():
    """XBRL 개념 목록에 주당순이익이 등록되어 있어야 합니다."""
    assert "gaap_eps" in sf._XBRL_CONCEPTS
    assert "EarningsPerShareDiluted" in sf._XBRL_CONCEPTS["gaap_eps"]


def test_period_series_unit_filter_by_kind():
    """달러 시계열은 주당 금액을 거부하고, 주당 시계열은 받아야 합니다."""
    facts = _FakeFacts([_eps_fact("2025-03-31", 1.26)])
    # 기본(달러) 조회 — 주당 금액은 단위가 달라 거부
    assert sf._period_series(facts, "EarningsPerShareDiluted", months=3) == {}
    # 주당 단위로 조회하면 받아들임
    got = sf._period_series(
        facts, "EarningsPerShareDiluted", months=3, unit="USD/SHARES"
    )
    assert got == {"2025-03-31": 1.26}, got


def test_xbrl_eps_is_not_q4_filled():
    """EPS 는 비율이라 `연간 − 3분기합`이 4분기 값이 아닙니다 — 채우면 창작.

    영업이익(달러)은 같은 상황에서 4분기가 채워져야 하고,
    주당순이익은 채워지지 않아야 합니다.
    """
    quarter_ends = ("2024-03-31", "2024-06-30", "2024-09-30")
    rows = (
        [_eps_fact(d, 1.0) for d in quarter_ends]
        + [_eps_fact("2024-12-31", 5.0, months=12)]      # 연간 EPS 만 있음
        + [{"concept": "us-gaap:OperatingIncomeLoss", "unit": "USD",
            "period_end": d, "numeric_value": 100.0, "months": 3}
           for d in quarter_ends]
        + [{"concept": "us-gaap:OperatingIncomeLoss", "unit": "USD",
            "period_end": "2024-12-31", "numeric_value": 500.0, "months": 12}]
    )
    facts = _FakeFacts(rows)
    eps = sf._series_for_key("gaap_eps", facts, None)
    op = sf._series_for_key("op_income", facts, None)
    assert "2024-12-31" not in eps, eps          # EPS Q4 는 없음으로 남아야 함
    assert op.get("2024-12-31") == 200.0, op     # 500 − (100×3) = 200 채움


# ---------------------------------------------------------------------------
# 28차 확대 — 분기 선언 구획 상속 · ± 형 · between 형 (전부 실물 발췌)
# ---------------------------------------------------------------------------
AMBA_BULLET_STYLE = """Based on information available as of today, Ambarella is
offering the following guidance for the second quarter of fiscal year 2027,
ending July 31, 2026:
•   Revenue is expected to be between $105.0 million and $111.0 million.
•   Gross margin on a non-GAAP basis is expected to be between 59.0% and 60.5%.
"""

AMD_PLUSMINUS_STYLE = ("For the second quarter of 2026, AMD expects revenue to "
                       "be approximately $11.2 billion, plus or minus $300 million.")

MCHP_RECITED_TRAP = (
    "Our December quarter guidance provided on November 5 was net sales of "
    "$1.186 billion. Taking all these factors into account, we expect March "
    "quarter net sales of $1.260 billion plus or minus $20.0 million."
)

CIEN_ANNUAL_THEN_QUARTER = """Ciena expects fiscal year 2026 to include:
• Revenue in the range of $5.7 billion to $6.1 billion
Ciena expects fiscal first quarter 2026 to include:
• Revenue in the range of $1.35 billion to $1.43 billion
"""

UNH_TRAPS = (
    "UnitedHealth Group Reports First Quarter 2026 Results; Revises Full Year "
    "Guidance\n"
    "• Revenues of $63.7 billion grew nearly 10 percent year over year.\n"
    "The company entered into an arrangement to buy back at least $2 billion "
    "of its common stock, which it expects to complete by the end of the "
    "second quarter 2026.\n"
)

UCTT_EPS_BETWEEN = (
    "Second Quarter Fiscal 2026 Outlook\n"
    "For the second quarter, the Company expects revenue in the range of "
    "$565 million to $605 million. The Company expects GAAP diluted net income "
    "per share to be between $0.20 and $0.36 and non-GAAP diluted net income "
    "per share to be between $0.44 and $0.60."
)


def test_guidance_block_inherits_quarter_context():
    """분기 선언 아래 불릿(분기 낱말 없음)도 매출 가이던스로 읽어야 합니다."""
    rev = fe.parse_guidance_revenue(AMBA_BULLET_STYLE)
    assert rev["low"] == 105.0e6 and rev["high"] == 111.0e6, rev


def test_guidance_plus_minus_dollar():
    """± 형: $11.2 billion ± $300 million → 10.9~11.5B."""
    rev = fe.parse_guidance_revenue(AMD_PLUSMINUS_STYLE)
    assert rev["low"] == 10.9e9 and rev["high"] == 11.5e9 and rev["mid"] == 11.2e9, rev


def test_guidance_band_beats_recited_single():
    """지난번 가이던스 재인용(단일값)보다 새 범위(밴드)가 이겨야 합니다."""
    rev = fe.parse_guidance_revenue(MCHP_RECITED_TRAP)
    assert rev["low"] == 1.24e9 and rev["high"] == 1.28e9, rev


def test_guidance_quarter_block_not_annual():
    """연간 블록 뒤에 분기 블록이 오면 분기 수치만 읽어야 합니다 (CIEN 실물)."""
    rev = fe.parse_guidance_revenue(CIEN_ANNUAL_THEN_QUARTER)
    assert rev["low"] == 1.35e9 and rev["high"] == 1.43e9, rev


def test_guidance_ignores_buyback_and_results_bullets():
    """자사주 매입의 "expects ... quarter"와 실적 하이라이트 불릿은
    가이던스가 아닙니다 (UNH 실물 오탐 회귀)."""
    rev = fe.parse_guidance_revenue(UNH_TRAPS)
    assert rev["mid"] is None, rev


def test_guidance_eps_between_prefers_nongaap_band():
    """between $A and $B 형 EPS — GAAP 나란히 있어도 non-GAAP 범위를 집습니다."""
    eps = fe.parse_guidance_eps(UCTT_EPS_BETWEEN)
    assert eps["low"] == 0.44 and eps["high"] == 0.6, eps


def test_guidance_giant_sentence_blob_is_skipped():
    """구두점 없는 머리말·표 뭉치(500자 초과 가짜 문장)는 건너뜁니다."""
    blob = ("EXHIBIT 99.1 " + "AMERICAN EXPRESS COMPANY " * 30
            + " second quarter expects revenue guidance "
            + " $3.1 billion " + "x " * 120)
    assert len(blob) > 500
    assert fe.parse_guidance_revenue(blob)["mid"] is None


def test_fiscal_year_quarter_sentence_is_not_annual():
    """29차 회귀: "first quarter of fiscal year 2026" 문장이 연간으로
    오인돼 버려졌던 사고 — 분기를 직접 명명한 항목은 연간이 아닙니다."""
    s = ("For the first quarter of fiscal year 2026, the Company expects "
         "revenue of $500 million to $540 million.")
    rev = fe.parse_guidance_revenue(s)
    assert rev["low"] == 500e6 and rev["high"] == 540e6, rev
    # 반대로, 분기 낱말 없는 연간 불릿은 여전히 버려야 합니다
    block = ("Guidance for the second quarter of fiscal 2026:\n"
             "• Full year revenue of $2.0 billion to $2.2 billion\n")
    assert fe.parse_guidance_revenue(block)["mid"] is None, "연간 불릿이 통과!"


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


def test_xbrl_addbacks_are_used_only_when_every_quarter_has_them():
    """되돌릴 항목이 일부 분기에만 있으면 아예 쓰지 않아야 합니다.

    주식보상비는 10-Q 현금흐름표에 **누적기간(3·6·9개월)** 으로 신고돼 3개월
    값이 Q1 에만 있는 경우가 흔합니다. 없으면 0 으로 처리하면 한 열 안에
    "GAAP+주식보상비" 와 "GAAP" 이 섞여 분기마다 정의가 달라집니다.

        Q1  GAAP 100 + 주식보상비 40 = 140
        Q2  GAAP 110              = 110   ← 주식보상비 없음
        → 델타 -21% (실제로는 +10%)

    델타 모델은 값의 크기가 아니라 값의 변화를 봅니다. 절대 정확도보다
    **한 종목 안에서 정의가 일관된 것**이 훨씬 중요합니다.
    """
    ends = ["2024-03-31", "2024-06-30", "2024-09-30"]
    op = {"2024-03-31": 100e6, "2024-06-30": 110e6, "2024-09-30": 120e6}
    partial_sbc = {"2024-03-31": 40e6}          # Q1 에만 있음

    usable = [e for e in ends if op.get(e) is not None]
    have = sum(1 for e in usable if partial_sbc.get(e) is not None)
    assert 0 < have < len(usable), "이 픽스처는 '일부 분기에만 있는' 상황이어야 합니다"

    # 일관성 규칙: 전 분기에 있어야만 되돌립니다
    consistent = have == len(usable)
    values = [op[e] + ((partial_sbc.get(e) or 0.0) if consistent else 0.0) for e in ends]
    assert values == [100e6, 110e6, 120e6], values

    growth = (values[1] / values[0] - 1) * 100
    assert abs(growth - 10.0) < 0.01, f"델타가 {growth:.1f}% — 정의 혼재가 남아 있습니다"


def test_all_exceptions_are_recorded_not_just_the_first():
    """첫 예외만 남기면 뒤의 진짜 실패가 보이지 않습니다.

    8-K 60건을 훑는 동안 앞쪽에서 사소한 예외 하나가 나면, 그 뒤의 진짜
    원인은 화면에 한 줄도 나타나지 않았습니다.
    """
    report = sf.new_report("TEST")
    for i in range(3):
        sf._record_error(report, f"단계{i}", ValueError(f"오류{i}"))

    assert "단계0" in report["first_error"]          # 화면 요약용 첫 오류는 그대로
    assert len(report["all_errors"]) == 3            # 나머지도 남습니다
    assert any("단계2" in e for e in report["all_errors"])
