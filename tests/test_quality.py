"""
test_quality.py — 숫자 검사 단계 검증
=====================================

배포 화면에 나왔던 실제 사고를 그대로 재현해, 이제는 화면까지 흘러가지 않는지 봅니다.

    최근 4개 분기 평균 영업마진 82,804,463.3%
    다음 분기 예상 영업이익 $389,938,207.9M  (최근 실제 분기는 $84.5M)
    변화율 +461,711,116.6%

실행: python3 tests/test_quality.py
"""

import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import config as cfg  # noqa: E402
import data_quality as dq  # noqa: E402
import forward_estimates as fe  # noqa: E402
from fixtures import make_quarters  # noqa: E402

M = 1_000_000


# ---------------------------------------------------------------------------
# 안전 계산
# ---------------------------------------------------------------------------
def test_margin_rejects_impossible_value():
    """영업이익이 매출보다 크면(마진 100% 초과) 마진을 돌려주면 안 됨"""
    # 실제 사고 값: 매출이 100만분의 1로 들어와 마진이 828,044배가 됨
    assert dq.safe_margin_pct(102.0, 84.5 * M) is None
    assert dq.safe_margin_pct(0, 10 * M) is None
    assert dq.safe_margin_pct(None, 10 * M) is None


def test_margin_accepts_normal_value():
    """정상 범위 마진은 그대로 계산돼야 함"""
    assert abs(dq.safe_margin_pct(300 * M, 84.5 * M) - 28.17) < 0.1


def test_growth_rejects_absurd_and_negative_base():
    """비현실적 증가율과 적자 기저는 계산하지 않아야 함"""
    assert dq.safe_growth_pct(84.5 * M, 389_938_207.9 * M) is None   # +461,711,116%
    assert dq.safe_growth_pct(-10 * M, 5 * M) is None                # 적자 기저
    assert dq.safe_growth_pct(0, 5 * M) is None
    assert abs(dq.safe_growth_pct(100 * M, 120 * M) - 20.0) < 0.01   # 정상


# ---------------------------------------------------------------------------
# 분기 검사 — "버리기 전에 먼저 판단하고, 고칠 수 있으면 고친다"
# ---------------------------------------------------------------------------
def test_quarter_with_scaled_revenue_is_repaired_not_discarded():
    """매출이 백만 단위로 들어오면 버리지 말고 고쳐서 써야 함"""
    quarter = {"period_label": "26 Q2", "revenue": 300.0, "op_income": 84.5 * M}
    verdict = dq.check_quarter(quarter)

    assert verdict["quality"] == dq.Q_FIXED, verdict
    assert verdict["revenue"] == 300.0 * M, verdict
    assert "고쳤습니다" in verdict["reasons"][0]


def test_quarter_with_unfixable_revenue_keeps_op_income():
    """매출을 고칠 수 없어도 분기를 통째로 버리지 않고 영업이익은 살려야 함"""
    quarter = {"period_label": "26 Q1", "revenue": 7.0, "op_income": 84.5 * M}
    verdict = dq.check_quarter(quarter)

    assert verdict["quality"] == dq.Q_PARTIAL, verdict
    assert verdict["revenue"] is None
    assert "매출을 계산에서 뺐습니다" in verdict["reasons"][0]


def test_quarter_without_op_income_is_unusable():
    """영업이익이 없으면 그 분기는 쓸 수 없음"""
    assert dq.check_quarter({"period_label": "X", "revenue": 100 * M})["quality"] == dq.Q_INVALID


def test_validate_quarters_records_what_it_did():
    """무엇을 고치고 무엇을 뺐는지 진단에 남겨야 함"""
    quarters = [
        {"period_label": "25 Q1", "revenue": 250 * M, "op_income": 60 * M},
        {"period_label": "25 Q2", "revenue": 270.0, "op_income": 70 * M},   # 백만 단위 → 보정
        {"period_label": "25 Q3", "revenue": 300 * M, "op_income": None},   # 제외
    ]
    report = {}
    kept = dq.validate_quarters(quarters, report)

    assert len(kept) == 2, kept
    assert report["quality_fixed"] == 1
    assert report["quality_dropped"] == 1
    assert kept[1]["revenue"] == 270.0 * M
    assert kept[1]["quality"] == dq.Q_FIXED
    assert any("고쳤습니다" in note for note in report["quality_notes"])


def test_partial_quarter_clears_dependent_fields():
    """매출을 뺐으면 매출에서 나온 값(GM%)도 함께 지워야 앞뒤가 맞음"""
    quarters = [{"period_label": "X", "revenue": 3.0, "op_income": 50 * M,
                 "gross_margin_pct": 65.0}]
    kept = dq.validate_quarters(quarters, {})

    assert kept[0]["revenue"] is None
    assert kept[0]["gross_margin_pct"] is None


# ---------------------------------------------------------------------------
# 전망 검사
# ---------------------------------------------------------------------------
def test_forward_multiple_guard():
    """한 분기 만에 10배를 넘는 전망은 쓰지 않아야 함"""
    # 실제 사고 값 — 절대 금액 자체가 비현실적이라 걸러짐
    ok, reason = dq.check_forward(389_938_207.9 * M, 84.5 * M)
    assert ok is False and reason, reason

    # 금액은 현실적이지만 최근 실적의 20배 — 배수 한도에 걸려야 함
    ok, reason = dq.check_forward(84.5 * M * 20, 84.5 * M)
    assert ok is False and "한도를 벗어납니다" in reason, reason

    ok, _ = dq.check_forward(95 * M, 84.5 * M)      # 정상 범위
    assert ok is True

    ok, _ = dq.check_forward(None, 84.5 * M)        # 전망 없음은 오류가 아님
    assert ok is True


def test_average_margin_ignores_broken_quarter():
    """망가진 분기 하나가 평균 마진을 오염시키면 안 됨"""
    quarters = [
        {"revenue": 250 * M, "op_income": 60 * M},   # 24%
        {"revenue": 102.0, "op_income": 84.5 * M},   # 망가진 분기 (828,044배)
        {"revenue": 300 * M, "op_income": 90 * M},   # 30%
    ]
    margin = fe.average_operating_margin(quarters, n=4)

    assert margin is not None
    assert 20.0 < margin < 35.0, margin


# ---------------------------------------------------------------------------
# 사고 전체 재현 (수집 → 검사 → 전망 → 예측)
# ---------------------------------------------------------------------------
def test_normal_data_is_untouched():
    """정상 데이터는 검사를 통과하며 값이 바뀌면 안 됨"""
    quarters = make_quarters([100 * M, 115 * M, 140 * M], margins=[50.0, 50.5, 51.0])
    kept = dq.validate_quarters([dict(q) for q in quarters], {})

    assert len(kept) == len(quarters)
    for before, after in zip(quarters, kept):
        assert after["op_income"] == before["op_income"]
        assert after["revenue"] == before["revenue"]
        assert after["quality"] == dq.Q_OK



# ---------------------------------------------------------------------------
# XBRL 개념·단위 필터 — 배포 사고의 진짜 원인
# ---------------------------------------------------------------------------
class _FakeFacts:
    """edgartools facts 객체 흉내 — to_dataframe() 만 있으면 됩니다."""

    def __init__(self, rows):
        self._rows = rows

    def query(self):
        return self

    def by_concept(self, concept):
        # 실제 edgartools 기본값과 같은 '부분일치 + 이름표 일치'를 흉내 냅니다
        low = concept.lower()
        self._filtered = [
            r for r in self._rows
            if low in r["concept"].lower() or low in str(r.get("label", "")).lower()
        ]
        return self

    def by_period_length(self, months):
        return self

    def to_dataframe(self):
        import pandas as pd
        return pd.DataFrame(getattr(self, "_filtered", self._rows))


def test_xbrl_rejects_other_concepts_and_units():
    """매출을 찾을 때 매출원가·비율·주당금액이 딸려 들어오면 안 됨"""
    import sec_fundamentals as sf

    facts = _FakeFacts([
        {"concept": "us-gaap:Revenues", "label": "Revenues",
         "period_end": "2025-09-30", "numeric_value": 268_000_000.0, "unit": "USD"},
        {"concept": "us-gaap:CostOfRevenue", "label": "Cost of revenues",
         "period_end": "2025-09-30", "numeric_value": 96_000_000.0, "unit": "USD"},
        {"concept": "us-gaap:ConcentrationRiskPercentageOfRevenues",
         "label": "Concentration risk percentage of revenues",
         "period_end": "2025-09-30", "numeric_value": 35.0, "unit": "pure"},
    ])
    report = {}
    series = sf._period_series(facts, "Revenues", 3, report)

    assert series == {"2025-09-30": 268_000_000.0}, series
    assert report.get("xbrl_rejected", 0) >= 2


def test_xbrl_rejects_per_share_unit():
    """주당 금액(USD/shares)이 주식보상비 총액을 밀어내면 안 됨"""
    import sec_fundamentals as sf

    facts = _FakeFacts([
        {"concept": "us-gaap:ShareBasedCompensation", "label": "Share-based compensation",
         "period_end": "2025-09-30", "numeric_value": 12_000_000.0, "unit": "USD"},
        {"concept": "us-gaap:ShareBasedCompensationArrangementWeightedAverageGrantDateFairValue",
         "label": "Weighted average grant date fair value",
         "period_end": "2025-09-30", "numeric_value": 18.42, "unit": "USD/shares"},
    ])
    series = sf._period_series(facts, "ShareBasedCompensation", 3, {})

    assert series == {"2025-09-30": 12_000_000.0}, series


def test_xbrl_ambiguous_values_are_not_guessed():
    """같은 분기에 크게 다른 값이 둘이면 아무거나 쓰지 말고 제외해야 함"""
    import sec_fundamentals as sf

    facts = _FakeFacts([
        {"concept": "us-gaap:Revenues", "label": "Revenues",
         "period_end": "2025-09-30", "numeric_value": 268_000_000.0, "unit": "USD"},
        {"concept": "us-gaap:Revenues", "label": "Revenues",
         "period_end": "2025-09-30", "numeric_value": 12_000_000.0, "unit": "USD"},
    ])
    report = {}
    series = sf._period_series(facts, "Revenues", 3, report)

    assert series == {}, series
    assert report.get("xbrl_ambiguous")


# ---------------------------------------------------------------------------
# 보도자료 퍼센트 함정
# ---------------------------------------------------------------------------
def test_percent_written_as_word_is_not_taken_as_amount():
    """'82 percent' 를 금액으로 채택하면 마진이 1억%가 됩니다"""
    import sec_fundamentals as sf

    text = "Non-GAAP gross margin was 82 percent for the quarter."
    value = sf.find_labeled_value(text, sf.LABELS_NONGAAP_GM_PCT, is_percent=True)
    assert value == 82.0, value

    # 금액을 찾을 때는 그 숫자를 쓰면 안 됩니다
    amount = sf.find_labeled_value(text, [r"non[-\s]?GAAP\s+gross\s+margin"])
    assert amount is None or amount != 82.0, amount


def test_percent_on_next_line_does_not_hijack_amount():
    """다음 줄이 '% of revenue' 로 시작해도 이번 줄 금액은 금액이어야 함"""
    import sec_fundamentals as sf

    text = "Non-GAAP operating income   45,123\n% of revenue   18.5"
    value = sf.find_labeled_value(text, sf.LABELS_NONGAAP_OP_INCOME)
    assert value == 45_123.0, value


def test_press_rejects_revenue_smaller_than_op_income():
    """매출이 영업이익보다 작게 잡히면 그 매출은 채택하지 않아야 함"""
    import sec_fundamentals as sf

    result = {"revenue": 102.0, "op_income": 84_500_000.0}
    sf._sanity_check_press(result)
    assert result["revenue"] is None
    assert result["rejected_revenue"] == 102.0


# ---------------------------------------------------------------------------
# 전망 경로
# ---------------------------------------------------------------------------
def test_annual_guidance_is_not_used_as_quarterly():
    """연간 전망을 분기 전망으로 쓰면 이익이 4배로 튑니다"""
    annual = "Full-year 2026 outlook: we expect revenue of $1,900 million to $2,000 million."
    quarterly = "Second quarter 2026 outlook: we expect revenue of $480 million to $500 million."

    assert fe.looks_annual(annual) is True
    assert fe.looks_annual(quarterly) is False


def test_average_margin_excludes_outlier_quarter():
    """한 분기만 크게 튀면 평균에서 빼야 함"""
    quarters = [
        {"revenue": 250 * M, "op_income": 45 * M},    # 18%
        {"revenue": 260 * M, "op_income": 47 * M},    # 18.1%
        {"revenue": 100 * M, "op_income": 94 * M},    # 94% ← 껍데기 잔차
        {"revenue": 280 * M, "op_income": 51 * M},    # 18.2%
    ]
    margin = fe.average_operating_margin(quarters, n=4)

    assert margin is not None and 15.0 < margin < 22.0, margin


# ---------------------------------------------------------------------------
# 점수 쪽 방어선
# ---------------------------------------------------------------------------
def test_seasonal_business_is_detected():
    """분기마다 오르내리는 사업은 계절성으로 판정해야 함"""
    labels = ["24 Q1", "24 Q2", "24 Q3", "24 Q4", "25 Q1", "25 Q2", "25 Q3", "25 Q4"]
    seasonal = [
        {"period_label": lab, "op_income": v, "revenue": v * 4}
        for lab, v in zip(labels, [100 * M, 130 * M, 90 * M, 160 * M,
                                   110 * M, 143 * M, 99 * M, 176 * M])
    ]
    steady = [
        {"period_label": lab, "op_income": 100 * M * (1.1 ** i), "revenue": 400 * M}
        for i, lab in enumerate(labels)
    ]

    assert dq.detect_seasonality(seasonal)["seasonal"] is True
    assert dq.detect_seasonality(steady)["seasonal"] is False


def test_분기번호는_이름이_아니라_기간끝으로_정한다():
    """(150차-T) 분기 **이름**은 못 믿습니다 — 기간종료일이 먼저입니다.

    이름은 보도자료 본문에서 글자로 뽑습니다. 본문이 직전 분기나 작년
    같은 분기를 함께 말하면 엉뚱한 것을 집습니다. 실데이터에서
    **652칸(8.8%)·122종목**이 그 종목 안에서조차 어긋나 있었습니다:

        SLB  '20 Q4' 라는 이름의 행 4개 — 기간끝 2021-03/06/09/12
        INTC '18 Q2' 라는 이름의 행 3개 — 기간끝 2018-03/06/12

    이 함수는 '같은 분기끼리 묶기'에만 쓰이므로, 이름이 겹치면 서로 다른
    네 분기가 한 묶음이 되어 계절성·이상값 판정이 통째로 어긋납니다.
    """
    # 이름은 거짓말을 하고, 기간끝은 사실을 말하는 행
    거짓 = {"period_label": "20 Q4", "filing_date": "2021-06-30"}
    assert dq.fiscal_quarter_of(거짓) == (2021, 2), (
        "이름('20 Q4')을 믿었습니다 — 기간끝 2021-06-30 은 2분기입니다: "
        f"{dq.fiscal_quarter_of(거짓)}")

    # SLB 실물 — 네 행이 **서로 다른** 번호를 받아야 합니다
    slb = [{"period_label": "20 Q4", "filing_date": d}
           for d in ("2021-03-31", "2021-06-30", "2021-09-30", "2021-12-31")]
    번호 = [dq.fiscal_quarter_of(q) for q in slb]
    assert len(set(번호)) == 4, f"네 분기가 한 묶음이 됐습니다: {번호}"

    # 기간끝이 아예 없을 때만 이름으로 돌아갑니다 (없는 값을 만들지 않음)
    assert dq.fiscal_quarter_of({"period_label": "25 Q3"}) == (2025, 3)
    assert dq.fiscal_quarter_of({"period_label": "25/09"}) == (2025, 3)
    assert dq.fiscal_quarter_of({}) is None
    assert dq.fiscal_quarter_of({"period_label": "아무 말"}) is None


def test_one_off_spike_is_flagged():
    """한 분기만 유별나게 튄 곳(일회성 이익·인수합병)을 찾아야 함"""
    quarters = [
        {"period_label": f"Q{i}", "op_income": v}
        for i, v in enumerate(
            [80 * M, 85 * M, 90 * M, 260 * M, 95 * M, 100 * M, 105 * M], 1
        )
    ]
    found = dq.detect_anomalies(quarters)

    # 260M 분기(0부터 세어 3번) — 튀었다 되돌아오므로 '스파이크'(데이터 오류)
    assert 3 in found["spikes"], found
    assert any("데이터 오류" in r for r in found["reasons"])


def test_step_up_is_not_treated_as_error():
    """인수합병처럼 올라가서 유지되면 '계단' — 분기를 버리지 않고 그 증가율만 뺌"""
    quarters = [
        {"period_label": f"2{4+i//4} Q{i%4+1}", "op_income": v * M}
        for i, v in enumerate([50, 55, 60, 130, 140, 150, 160])
    ]
    found = dq.detect_anomalies(quarters)

    assert 3 in found["steps"], found
    assert 3 not in found["spikes"], found
    assert any("구조 변화" in r for r in found["reasons"])


def test_latest_quarter_jump_is_pending_not_guessed():
    """가장 최근 분기가 튀면 뒤가 없어 판별 불가 — 아는 척하지 않고 유보"""
    quarters = [
        {"period_label": f"2{4+i//4} Q{i%4+1}", "op_income": v * M}
        for i, v in enumerate([80, 85, 90, 95, 260])
    ]
    found = dq.detect_anomalies(quarters)

    assert 4 in found["pending"], found
    assert any("알 수 없습니다" in r for r in found["reasons"])


# ---------------------------------------------------------------------------
# 조정 EBITDA 로만 발표하는 회사 (ZETA·TSLA·APP 형)
# ---------------------------------------------------------------------------
def test_adjusted_ebitda_is_parsed():
    """논갭 영업이익이 없으면 조정 EBITDA 라도 챙겨 와야 함"""
    import sec_fundamentals as sf

    text = (
        "Fourth Quarter 2025 Financial Results (in thousands)\n"
        "Total revenue          395,000\n"
        "Adjusted EBITDA        100,500\n"
    )
    parsed = sf.parse_press_release(text)

    assert parsed["adjusted_ebitda"] == 100_500_000, parsed
    assert parsed["op_income"] is None      # 아직 확정 아님 (감가상각비를 만나야 함)


def test_ebitda_minus_da_becomes_operating_income():
    """조정 EBITDA − 감가상각비 = 논갭 영업이익 (역산)"""
    import sec_fundamentals as sf

    row = {"period_label": "25 Q4", "op_income": 60 * M, "da": 14 * M,
           "source": cfg.SRC_APPROX}
    press = {"adjusted_ebitda": 100.5 * M, "revenue": 395 * M}
    sf._apply_press_to_row(row, press)

    assert abs(row["op_income"] - 86.5 * M) < 1, row
    assert row["source"] == cfg.SRC_DERIVED
    assert "조정 EBITDA" in row["derivation"]


def test_ebitda_guidance_becomes_forward():
    """회사가 조정 EBITDA 가이던스만 줘도 버리지 않고 전망을 만들어야 함"""
    guidance = {"revenue": 370 * M, "adjusted_ebitda": 61.5 * M,
                "gross_margin_pct": None, "opex": None, "operating_margin_pct": None}
    quarters = [{"da": 13 * M}, {"da": 14 * M}, {"da": 15 * M}, {"da": 14 * M}]

    value, basis, note = fe.forward_from_guidance(guidance, quarters)

    assert abs(value - 47.5 * M) < 1, value        # 61.5 − 14(중앙값) = 47.5
    assert basis == cfg.SRC_DERIVED                # 가이던스가 아니라 역산 (근사가 섞임)
    assert "조정 EBITDA" in note


def test_ebitda_guidance_without_depreciation_is_skipped():
    """감가상각비를 모르면 억지로 만들지 말아야 함"""
    guidance = {"revenue": 370 * M, "adjusted_ebitda": 61.5 * M,
                "gross_margin_pct": None, "opex": None, "operating_margin_pct": None}

    value, basis, _ = fe.forward_from_guidance(guidance, [{"da": None}])
    assert value is None and basis is None


def test_mixed_definition_is_detected():
    """한 종목 안에서 논갭 영업이익의 정의가 섞이면 알려야 함"""
    quarters = [
        {"period_label": "25 Q1", "op_income": 50 * M, "source": cfg.SRC_DIRECT},
        {"period_label": "25 Q2", "op_income": 55 * M, "source": cfg.SRC_APPROX},
        {"period_label": "25 Q3", "op_income": 60 * M, "source": cfg.SRC_DIRECT},
    ]
    result = dq.check_source_consistency(quarters)

    assert result["mixed"] is True
    assert result["switches"] == 2
    assert "가짜 신호" in result["reason"]

    same = [{"op_income": 1, "source": cfg.SRC_DIRECT} for _ in range(3)]
    assert dq.check_source_consistency(same)["mixed"] is False




# ---------------------------------------------------------------------------
# 매출 보정 — "진짜 단위 오류"와 "손실이 큰 정상 회사"를 가려야 합니다
# ---------------------------------------------------------------------------
def _rev_quarter(label, revenue_m, op_m):
    return {
        "period_label": label, "fiscal_quarter": 1,
        "revenue": revenue_m * M, "op_income": op_m * M,
        "source": cfg.SRC_DIRECT,
    }


def test_loss_making_company_revenue_is_not_inflated():
    """정상 적자기업의 매출을 1,000배로 부풀리면 안 됩니다.

    예전에는 '고친 뒤 마진이 범위에 들어오는가'만 봤습니다. 그런데 매출을 키우면
    마진은 언제나 0 쪽으로 움직이므로 **어떤 값이든 보정에 성공**합니다.
    그래서 매출 $20M · 영업손실 −$150M 인 바이오텍(정상적인 모습)의 매출이
    $20,000M 로 부풀려지고 "보정됨" 배지까지 달렸습니다.
    """
    quarters = [
        _rev_quarter(f"Q{i + 1}", revenue, -150)
        for i, revenue in enumerate([6, 7, 8, 9, 11, 13])
    ]
    report = {}
    kept = dq.validate_quarters(quarters, report)

    assert [q["revenue"] for q in kept] == [6 * M, 7 * M, 8 * M, 9 * M, 11 * M, 13 * M]
    assert not [n for n in report.get("quality_notes", []) if "고쳤습니다" in n]


def test_real_unit_error_is_still_repaired():
    """한 분기만 이웃과 1,000배 어긋나면 그건 진짜 단위 오류입니다"""
    quarters = [
        _rev_quarter(f"Q{i + 1}", revenue, 100)
        for i, revenue in enumerate([500, 520, 540, 560, 580])
    ]
    quarters[2]["revenue"] = 540_000.0        # 천 단위로 들어온 분기
    report = {}
    kept = dq.validate_quarters(quarters, report)

    assert kept[2]["revenue"] == 540_000_000.0
    assert [n for n in report.get("quality_notes", []) if "고쳤습니다" in n]


def test_single_quarter_cannot_vouch_for_itself():
    """분기가 하나뿐이면 자기 자신을 이웃으로 삼아 검증을 통과하면 안 됩니다"""
    kept = dq.validate_quarters(
        [{"period_label": "X", "revenue": 3.0, "op_income": 50 * M,
          "gross_margin_pct": 65.0}], {}
    )
    assert kept[0]["revenue"] is None


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
    print(f"\n숫자 검사 테스트: {passed}개 통과, {failed}개 실패")
    sys.exit(1 if failed else 0)
