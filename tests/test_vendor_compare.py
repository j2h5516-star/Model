"""
tests/test_vendor_compare.py — 두 자 대조 검증 (75차)

이 도구의 약속:
  ① **아무것도 고치지 않는다** — 재기만 한다
  ② 뜻이 다른 칸(조정 EPS ↔ 월가 EPS)은 맞대지 않는다
  ③ 짝을 억지로 붙이지 않는다 — 못 찾으면 "짝 없음"으로 센다
  ④ 반올림 차이를 "불일치"라고 부풀리지 않는다

실행: python3 tests/test_vendor_compare.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import vendor_compare as vc  # noqa: E402


def 우리분기(filing_date, **kw):
    row = {"filing_date": filing_date, "period_label": "26 Q1",
           "gaap_eps": None, "adj_eps": None, "revenue": None,
           "gross_margin_pct": None}
    row.update(kw)
    return row


def 야후분기(period_end, **kw):
    row = {"period_end": period_end, "gaap_eps": None, "revenue": None,
           "gross_margin_pct": None}
    row.update(kw)
    return row


def 보관(**티커):
    return {"tickers": {t: {"quarters": q} for t, q in 티커.items()}}


def test_matching_quarters_agree():
    """같은 값이면 '같음'으로 세야 합니다."""
    q = {"AAA": [우리분기("2026-03-31", gaap_eps=1.20, revenue=1_000.0)]}
    v = 보관(AAA=[야후분기("2026-03-31", gaap_eps=1.20, revenue=1_000.0)])
    r = vc.compare(q, v)
    assert r["짝지은분기"] == 1 and r["짝없는분기"] == 0
    assert r["칸별"]["GAAP EPS"]["같음"] == 1
    assert r["칸별"]["GAAP EPS"]["다름"] == 0
    assert r["불일치"] == []


def test_rounding_difference_is_not_a_mismatch():
    """1센트 차이를 불일치라고 부풀리면 숫자가 거짓말을 합니다."""
    q = {"AAA": [우리분기("2026-03-31", gaap_eps=1.20)]}
    v = 보관(AAA=[야후분기("2026-03-31", gaap_eps=1.21)])
    assert vc.compare(q, v)["칸별"]["GAAP EPS"]["같음"] == 1


def test_real_mismatch_is_listed_with_both_values():
    """진짜 불일치는 **두 값을 나란히** 남겨야 사람이 판단할 수 있습니다.

    실물 예: 우리가 연간값 59.45 를 물고 있고 야후는 분기 10.81 을 줄 때.
    """
    q = {"GS": [우리분기("2021-12-31", period_label="21 Q4", gaap_eps=59.45)]}
    v = 보관(GS=[야후분기("2021-12-31", gaap_eps=10.81)])
    r = vc.compare(q, v)
    assert r["칸별"]["GAAP EPS"]["다름"] == 1
    틀린칸 = r["불일치"][0]
    assert 틀린칸["우리"] == 59.45 and 틀린칸["야후"] == 10.81
    assert 틀린칸["종목"] == "GS"


def test_quarter_end_off_by_a_few_days_still_matches():
    """결산일이 며칠 어긋나도 같은 분기입니다 (회사마다 다릅니다)."""
    q = {"AAA": [우리분기("2026-03-28", gaap_eps=1.0)]}
    v = 보관(AAA=[야후분기("2026-03-31", gaap_eps=1.0)])
    assert vc.compare(q, v)["짝지은분기"] == 1


def test_far_apart_quarters_are_not_forced_together():
    """멀리 떨어진 분기를 억지로 붙이면 안 됩니다 — '짝 없음'입니다."""
    q = {"AAA": [우리분기("2026-03-31", gaap_eps=1.0)]}
    v = 보관(AAA=[야후분기("2025-12-31", gaap_eps=9.9)])
    r = vc.compare(q, v)
    assert r["짝없는분기"] == 1 and r["짝지은분기"] == 0
    assert r["칸별"]["GAAP EPS"]["다름"] == 0, "짝도 없는데 불일치를 셌습니다"


def test_one_vendor_quarter_is_used_only_once():
    """야후 분기 하나가 우리 두 행에 겹쳐 붙으면 안 됩니다.

    같은 발표일에 행이 둘인 종목이 실제로 있습니다(67차). 하나에만
    붙어야 나머지가 '짝 없음'으로 정직하게 남습니다.
    """
    q = {"AAA": [우리분기("2026-03-31", gaap_eps=1.0),
                 우리분기("2026-03-30", gaap_eps=5.0)]}
    v = 보관(AAA=[야후분기("2026-03-31", gaap_eps=1.0)])
    r = vc.compare(q, v)
    assert r["짝지은분기"] == 1 and r["짝없는분기"] == 1


def test_adjusted_eps_is_never_compared():
    """조정 EPS 는 뜻이 달라 맞대지 않습니다 (헌법 1조).

    회사 정의 조정 EPS 와 월가 기준값을 "불일치"라 세면 숫자가
    거짓말을 합니다. 이 검사는 그 칸이 아예 없음을 확인합니다.
    """
    이름들 = [이름 for _, _, 이름 in vc.FIELDS]
    assert "조정 EPS" not in 이름들, 이름들
    assert all("조정" not in n for n in 이름들), 이름들
    q = {"AAA": [우리분기("2026-03-31", adj_eps=1.19)]}
    v = 보관(AAA=[야후분기("2026-03-31", street_eps=1.25)])
    r = vc.compare(q, v)
    assert r["불일치"] == [], r["불일치"]


def test_missing_on_one_side_is_counted_apart():
    """한쪽에만 있는 값은 '다름'이 아니라 따로 셉니다."""
    q = {"AAA": [우리분기("2026-03-31", gaap_eps=1.0, revenue=None)]}
    v = 보관(AAA=[야후분기("2026-03-31", gaap_eps=None, revenue=500.0)])
    c = vc.compare(q, v)["칸별"]
    assert c["GAAP EPS"]["우리만"] == 1 and c["GAAP EPS"]["다름"] == 0
    assert c["매출"]["야후만"] == 1 and c["매출"]["다름"] == 0


def test_compare_never_changes_the_data():
    """대조는 **읽기만** 합니다 — 한 칸도 바뀌면 안 됩니다."""
    q = {"AAA": [우리분기("2026-03-31", gaap_eps=59.45)]}
    v = 보관(AAA=[야후분기("2026-03-31", gaap_eps=10.81)])
    전 = [dict(r) for r in q["AAA"]]
    전v = [dict(r) for r in v["tickers"]["AAA"]["quarters"]]
    vc.compare(q, v)
    vc.report(q, v)
    assert q["AAA"] == 전 and v["tickers"]["AAA"]["quarters"] == 전v


def test_flagged_cells_get_a_vendor_verdict():
    """조사가 표시한 칸에서 야후가 뭐라 하는지 갈라 세야 합니다.

    이것이 이번 대조의 핵심 질문입니다 — 우리가 이상하다고 표시한 칸에서
    야후가 다른 값을 준다면 우리 쪽 결함일 가능성이 큽니다.
    """
    q = {"GS": [우리분기("2021-12-31", period_label="21 Q4", gaap_eps=59.45)],
         "AAA": [우리분기("2026-03-31", period_label="26 Q1", gaap_eps=8.0)]}
    v = 보관(GS=[야후분기("2021-12-31", gaap_eps=10.81)],
            AAA=[야후분기("2026-03-31", gaap_eps=8.0)])
    표시 = [{"종목": "GS", "칸": "gaap_eps", "라벨": "21 Q4", "값": 59.45},
          {"종목": "AAA", "칸": "gaap_eps", "라벨": "26 Q1", "값": 8.0},
          {"종목": "ZZZ", "칸": "gaap_eps", "라벨": "26 Q1", "값": 3.0}]
    r = vc.verdict_on_flagged(q, v, 표시)
    assert [x["종목"] for x in r["야후가_다름"]] == ["GS"]
    assert r["야후가_다름"][0]["야후"] == 10.81
    assert [x["종목"] for x in r["야후도_같음"]] == ["AAA"]
    assert [x["종목"] for x in r["야후에_없음"]] == ["ZZZ"]


def test_ticker_missing_from_vendor_is_skipped_not_counted():
    """야후에 없는 종목은 비교 대상이 아닙니다 (불일치가 아닙니다)."""
    q = {"AAA": [우리분기("2026-03-31", gaap_eps=1.0)]}
    r = vc.compare(q, {"tickers": {}})
    assert r["비교한종목"] == 0 and r["짝지은분기"] == 0
    assert r["칸별"]["GAAP EPS"]["다름"] == 0


def test_empty_inputs_do_not_crash():
    """재료가 비어도 무너지지 않고 '확인 못함'으로 말해야 합니다."""
    r = vc.compare({}, {})
    assert r["비교한종목"] == 0
    assert "확인 못함" in vc.report({}, {})


if __name__ == "__main__":
    tests = [
        (n, f) for n, f in sorted(globals().items())
        if n.startswith("test_") and callable(f)
    ]
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
    print(f"\n두 자 대조 검증: {passed}개 통과, {failed}개 실패")
    sys.exit(1 if failed else 0)
