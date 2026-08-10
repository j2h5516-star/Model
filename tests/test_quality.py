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
import scoring  # noqa: E402
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
def test_zeta_incident_end_to_end():
    """실제 사고 데이터를 넣어도 화면용 숫자가 정상 범위여야 함"""
    import pipeline

    broken = [
        {"period_label": "25 Q3", "revenue": 240 * M, "op_income": 65.3 * M,
         "source": cfg.SRC_APPROX},
        {"period_label": "26 Q1", "revenue": 102.0, "op_income": 50.5 * M,
         "source": cfg.SRC_APPROX},          # ← 매출이 100만분의 1
        {"period_label": "26 Q2", "revenue": 290 * M, "op_income": 84.5 * M,
         "source": cfg.SRC_APPROX},
    ]

    def fake_fundamentals(ticker, use_cache=True):
        import sec_fundamentals as sf
        return list(broken), sf.new_report(ticker)

    def fake_consensus(ticker):
        return {"revenue_0q": 471 * M, "revenue_1q": 500 * M, "eps_0q": None,
                "eps_1q": None, "analysts_0q": 13, "revision": 1,
                "revision_velocity_pct": 4.5, "errors": []}

    with patch("sec_fundamentals.get_fundamentals", side_effect=fake_fundamentals), \
         patch("forward_estimates.fetch_consensus", side_effect=fake_consensus):
        bundle = pipeline.collect_one_ticker("ZETA", use_cache=False)

    quarters, forward = bundle["quarters"], bundle["forward"]

    # ① 망가진 매출은 고쳐졌거나 제외됐어야 함
    for q in quarters:
        margin = dq.safe_margin_pct(q.get("revenue"), q.get("op_income"))
        assert margin is None or -500 <= margin <= 100, q

    # ② 전망이 최근 실적의 10배를 넘지 않아야 함
    if forward.get("forward_op_income") is not None:
        assert forward["forward_op_income"] < 84.5 * M * cfg.FORWARD_MAX_MULTIPLE, forward

    # ③ 화면에 나갈 증가율이 정상 범위여야 함
    score = scoring.build_score(
        "ZETA", quarters, forward,
        {"state": cfg.S_FULL_UP, "slope": "상승", "disparity": 10.0, "rs": 20.0,
         "stage": 1, "close": 30.0, "week_change": 0.0},
    )
    detail = score["forecast_detail"]
    for key in ("next_qoq", "next2_qoq", "last_qoq"):
        value = detail.get(key)
        assert value is None or abs(value) <= cfg.GROWTH_ABS_MAX_PCT, (key, value)


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
def test_forward_score_not_full_marks_on_absurd_growth():
    """비현실적 증가율에 만점을 주면 안 됨"""
    quarters = make_quarters([84.5 * M])
    absurd = scoring.score_forward(
        quarters, {"forward_op_income": 389_938_207.9 * M, "revision": 1}
    )
    normal = scoring.score_forward(
        quarters, {"forward_op_income": 110 * M, "revision": 1}
    )
    assert absurd["score"] < normal["score"], (absurd, normal)
    assert "비현실적" in absurd["detail"], absurd["detail"]


def test_gm_driver_ignores_impossible_margin():
    """82,804,463% 같은 GM%는 판단에서 빼야 함"""
    quarters = [
        {"gross_margin_pct": 50.0}, {"gross_margin_pct": 50.5},
        {"gross_margin_pct": 82_804_463.3}, {"gross_margin_pct": 51.0},
    ]
    result = scoring.score_gm_driver(quarters)

    assert result["delta_pp"] is None or abs(result["delta_pp"]) < 100.0, result


def test_chart_qoq_uses_safe_growth():
    """차트용 QoQ 도 점수와 같은 안전 계산을 써야 함"""
    import pipeline

    quarters = [
        {"period_label": "A", "filing_date": "2025-01-01", "op_income": 100.0},
        {"period_label": "B", "filing_date": "2025-04-01", "op_income": 84.5 * M},
    ]
    frame = pipeline.quarters_to_frame(quarters)

    import pandas as pd
    assert pd.isna(frame["qoq_pct"].iloc[1]), frame["qoq_pct"].tolist()



# ---------------------------------------------------------------------------
# 계절성 · 튀는 분기 — "먼저 판단하고 적용" 원칙
# ---------------------------------------------------------------------------
def test_seasonal_business_is_detected():
    """분기마다 오르내리는 사업은 계절성으로 판정해야 함"""
    seasonal = [
        {"period_label": f"Q{i}", "op_income": v, "revenue": v * 4}
        for i, v in enumerate(
            [100 * M, 130 * M, 90 * M, 160 * M, 110 * M, 143 * M, 99 * M, 176 * M], 1
        )
    ]
    steady = [
        {"period_label": f"Q{i}", "op_income": 100 * M * (1.1 ** i), "revenue": 400 * M}
        for i in range(8)
    ]

    assert dq.detect_seasonality(seasonal)["seasonal"] is True
    assert dq.detect_seasonality(steady)["seasonal"] is False


def test_one_off_spike_is_flagged():
    """한 분기만 유별나게 튄 곳(일회성 이익·인수합병)을 찾아야 함"""
    quarters = [
        {"period_label": f"Q{i}", "op_income": v}
        for i, v in enumerate(
            [80 * M, 85 * M, 90 * M, 260 * M, 95 * M, 100 * M, 105 * M], 1
        )
    ]
    found = dq.detect_anomalies(quarters)

    assert 3 in found["indexes"], found        # 260M 분기(0부터 세어 3번)
    assert any("일회성" in r for r in found["reasons"])


def test_direction_is_withheld_after_spike():
    """튄 분기가 최근에 있으면 방향을 단정하지 말아야 함"""
    quarters = [
        {"period_label": f"Q{i}", "op_income": v, "revenue": v * 4}
        for i, v in enumerate(
            [80 * M, 85 * M, 90 * M, 95 * M, 100 * M, 260 * M], 1
        )
    ]
    checked = dq.validate_quarters(quarters, {})
    result = scoring.score_delta_acceleration(checked)

    assert result["direction"] == cfg.D_UNKNOWN, result
    assert "판정을 미룹니다" in result["detail"]


def test_single_signal_is_not_a_firm_call():
    """신호 하나만 방향을 보이면 단정하지 말고 '약한' 판정이어야 함"""
    # 증가율이 매 분기 +2%p대로 오름 — 추세는 우상향이지만 단기 문턱(3%p)에는 못 미침
    quarters = make_quarters([100 * M, 112 * M, 128 * M, 150 * M, 180 * M])
    result = scoring.score_delta_acceleration(quarters)

    assert result["direction"] == cfg.D_WEAK_ACCEL, result
    assert cfg.DELTA_RATIO[cfg.D_WEAK_ACCEL] < cfg.DELTA_RATIO[cfg.D_ACCEL]


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
