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
