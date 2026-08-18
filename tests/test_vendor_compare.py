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
           "gross_margin_pct": None,
           # 보도자료가 덮어쓰기 전의 XBRL 값 (90차)
           "gaap_eps_xbrl": None, "revenue_xbrl": None,
           "gross_margin_pct_xbrl": None}
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


def test_wanted_asks_for_the_filing_behind_a_gaap_eps_disagreement():
    """야후와 어긋난 GAAP EPS 칸은 원문을 부탁해야 합니다 (86차 CRM 실물).

    실물: CRM 2025-07-31 분기 — 우리 0.00 ↔ 야후 1.96.
    이웃 분기까지 나란히 무너져 조사(audit_data)는 못 보는 자리입니다.
    """
    q = {"CRM": [우리분기("2025-07-31", gaap_eps=0.0,
                        announced_date="2025-09-03")]}
    v = 보관(CRM=[야후분기("2025-07-31", gaap_eps=1.96)])
    목록 = vc.wanted_from_mismatch(q, v)
    assert [r["종목"] for r in 목록] == ["CRM"]
    assert 목록[0]["발표일"] == "2025-09-03"      # 분기끝이 아니라 **발표일**
    assert "1.96" in 목록[0]["이유"]


def test_wanted_ignores_gross_margin_because_the_gap_is_a_definition_gap():
    """매출총이익률 차이는 결함이 아니라 정의 차이라 부탁하지 않습니다.

    실물(86차): CSCO 우리 67.5(회사 발표 논갭) ↔ 야후 63.2(갭 계산).
    이걸 부탁 목록에 넣으면 멀쩡한 공시가 자리를 다 차지합니다.
    """
    q = {"CSCO": [우리분기("2025-07-26", gross_margin_pct=67.5,
                         announced_date="2025-08-13")]}
    v = 보관(CSCO=[야후분기("2025-07-26", gross_margin_pct=63.2)])
    r = vc.compare(q, v)
    assert r["칸별"]["매출총이익률"]["다름"] == 1      # 재기는 한다
    assert vc.wanted_from_mismatch(q, v) == []      # 부탁은 안 한다


def test_wanted_ranks_under_reads_as_high_as_over_reads():
    """우리가 **작게** 읽은 칸도 앞자리에 와야 합니다 (86차에 실제로 당함).

    자리는 유한한데 '우리÷야후' 큰 순으로만 줄을 세우면, 우리가 작게 읽은
    칸(CRM 0.00 ↔ 야후 1.96)은 배수가 0 이라 맨 뒤로 밀립니다.
    정작 원문이 가장 급한 칸이 그것이었습니다.
    """
    q = {
        "OVER": [우리분기("2026-03-31", gaap_eps=4.00,
                        announced_date="2026-04-20")],   # 4배 크게 읽음
        "UNDER": [우리분기("2026-03-31", gaap_eps=0.00,
                         announced_date="2026-04-21")],  # 통째로 놓침
        "SMALL": [우리분기("2026-03-31", gaap_eps=1.30,
                         announced_date="2026-04-22")],  # 살짝 어긋남
    }
    v = 보관(OVER=[야후분기("2026-03-31", gaap_eps=1.00)],
           UNDER=[야후분기("2026-03-31", gaap_eps=1.96)],
           SMALL=[야후분기("2026-03-31", gaap_eps=1.00)])
    이름 = [r["종목"] for r in vc.wanted_from_mismatch(q, v, limit=2)]
    assert "UNDER" in 이름, f"작게 읽은 칸이 밀렸습니다: {이름}"
    assert "SMALL" not in 이름, f"살짝 어긋난 칸이 앞자리를 먹었습니다: {이름}"


def test_wanted_does_not_let_one_ticker_eat_the_whole_list():
    """한 종목이 목록을 다 먹으면 안 됩니다 (86차 실물: 단위 차이 한 종류가
    자리 30칸 중 24칸을 차지했습니다). 같은 회사는 형식이 같아 여러 건을
    봐도 배우는 게 없습니다."""
    q = {"NFLX": [우리분기(f"2025-0{i}-30", gaap_eps=9.9,
                         announced_date=f"2025-0{i}-30") for i in range(1, 6)],
         "OTHER": [우리분기("2025-06-30", gaap_eps=8.8,
                          announced_date="2025-06-30")]}
    v = 보관(NFLX=[야후분기(f"2025-0{i}-30", gaap_eps=1.0) for i in range(1, 6)],
           OTHER=[야후분기("2025-06-30", gaap_eps=1.0)])
    목록 = vc.wanted_from_mismatch(q, v)
    assert sum(r["종목"] == "NFLX" for r in 목록) == vc._PER_TICKER_MAX
    assert any(r["종목"] == "OTHER" for r in 목록), "다른 종목이 밀려났습니다"


def test_wanted_skips_rows_with_no_announcement_date():
    """발표일이 없으면 어느 공시인지 짚을 수 없어 담지 않습니다."""
    q = {"AAA": [우리분기("2026-03-31", gaap_eps=9.9, announced_date=None)]}
    v = 보관(AAA=[야후분기("2026-03-31", gaap_eps=1.0)])
    assert vc.compare(q, v)["칸별"]["GAAP EPS"]["다름"] == 1
    assert vc.wanted_from_mismatch(q, v) == []


def test_wanted_stays_silent_when_the_two_rulers_agree():
    """두 자가 같으면 부탁할 것이 없습니다 (조용해야 정상)."""
    q = {"AAA": [우리분기("2026-03-31", gaap_eps=1.20, revenue=1_000.0,
                        announced_date="2026-04-20")]}
    v = 보관(AAA=[야후분기("2026-03-31", gaap_eps=1.20, revenue=1_000.0)])
    assert vc.wanted_from_mismatch(q, v) == []


def test_duel_counts_who_the_referee_agrees_with():
    """같은 칸을 두 경로가 읽었을 때 누가 맞았는지 세야 합니다 (90차).

    실물 모양: 보도자료가 **전년 열**을 물어 1.56 을 넣었고,
    XBRL 은 기간 태그가 붙어 있어 1.96 을 냈습니다. 야후도 1.96.
    """
    q = {"CRM": [우리분기("2025-07-31", gaap_eps=1.56,
                        gaap_eps_xbrl=1.96)]}
    v = 보관(CRM=[야후분기("2025-07-31", gaap_eps=1.96)])
    c = vc.duel(q, v)["칸별"]["GAAP EPS"]
    assert c["둘다읽음"] == 1
    assert c["XBRL만맞음"] == 1
    assert c["보도자료만맞음"] == 0 and c["둘다맞음"] == 0


def test_duel_counts_the_press_win_too():
    """반대 경우도 세야 합니다 — 한쪽 편을 들면 안 됩니다."""
    q = {"AAA": [우리분기("2026-03-31", gaap_eps=1.20, gaap_eps_xbrl=9.99)]}
    v = 보관(AAA=[야후분기("2026-03-31", gaap_eps=1.20)])
    c = vc.duel(q, v)["칸별"]["GAAP EPS"]
    assert c["보도자료만맞음"] == 1 and c["XBRL만맞음"] == 0


def test_duel_does_not_score_a_quarter_the_referee_cannot_judge():
    """야후가 그 칸을 모르면 승부를 세지 않아야 합니다.

    '둘 다 없음'을 무승부로 세면 숫자가 거짓말을 합니다.
    """
    q = {"AAA": [우리분기("2026-03-31", gaap_eps=1.20, gaap_eps_xbrl=1.20)]}
    v = 보관(AAA=[야후분기("2026-03-31")])          # 야후에 값 없음
    c = vc.duel(q, v)["칸별"]["GAAP EPS"]
    assert c["둘다읽음"] == 0 and c["둘다맞음"] == 0


def test_duel_separates_rows_where_only_one_path_read_anything():
    """한쪽만 값이 있는 칸은 **승부가 아니라 따로** 세야 합니다."""
    q = {"AAA": [우리분기("2026-03-31", gaap_eps=1.20, gaap_eps_xbrl=None)],
         "BBB": [우리분기("2026-03-31", gaap_eps=None, gaap_eps_xbrl=1.20)]}
    v = 보관(AAA=[야후분기("2026-03-31", gaap_eps=1.20)],
           BBB=[야후분기("2026-03-31", gaap_eps=1.20)])
    c = vc.duel(q, v)["칸별"]["GAAP EPS"]
    assert c["둘다읽음"] == 0
    assert c["보도자료만있음"] == 1 and c["XBRL만있음"] == 1


def test_duel_report_speaks_plainly_when_there_is_nothing_to_compare():
    """XBRL 값을 아직 안 담은 스냅샷이면 그렇게 말해야 합니다."""
    q = {"AAA": [우리분기("2026-03-31", gaap_eps=1.20)]}
    v = 보관(AAA=[야후분기("2026-03-31", gaap_eps=1.20)])
    assert "맞대 볼 칸이 없습니다" in vc.duel_report(q, v)


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
