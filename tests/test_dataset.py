"""
test_dataset.py — 데이터 계층(dataset.py) 검증 · v3 4단계
=========================================================

데이터 계층의 약속을 검사합니다 (설계도.md ①):
  · 검사에서 탈락한 값은 고치지 않고 "없음"으로 둔다
  · 시간순으로 정렬한다
  · 원본(snapshot)은 절대 바꾸지 않고, 파일도 쓰지 않는다
  · 무엇을 버렸는지 전부 notes 에 남긴다
  · 기준지수가 없으면 조용히 넘어가지 않고 즉시 실패한다

마지막 시험은 저장소의 **실물 snapshot** 으로 통행 규칙이 실제로
지켜지는지 확인합니다 (로봇이 매일 덮어쓰므로, 특정 값이 아니라
불변 조건만 검사합니다).

실행: python3 tests/test_dataset.py
"""

import copy
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import config as cfg  # noqa: E402
import dataset  # noqa: E402


def make_snapshot(**overrides) -> dict:
    """시험용 최소 snapshot. 기준지수(SPY) 가격은 기본으로 넣어 둡니다."""
    snap = {
        "saved_at": "2026-08-12T23:33:00+00:00",
        "tickers": ["AAA"],
        "benchmark": "SPY",
        "eps": {"AAA": []},
        "prices": {
            "SPY": {"dates": ["2026-08-11", "2026-08-12"], "close": [500.0, 501.0]},
        },
    }
    snap.update(overrides)
    return snap


def quarter_row(**overrides) -> dict:
    row = {
        "filing_date": "2026-06-30",
        "announced_date": "2026-08-05",
        "period_label": "26 Q4",
        "revenue": 1_000_000.0,
        "op_income": 100_000.0,
        "adj_eps": 1.25,
        "adjusted_ebitda": None,
        "gaap_eps": 1.00,
        "source": "직접공시",
        "guid_eps_low": None,
        "guid_eps_high": None,
        "guid_eps_mid": None,
    }
    row.update(overrides)
    return row



def test_repeated_revenue_is_placeholder_not_measurement():
    """한 종목에 똑같은 매출값이 4분기 이상 나오면 자리채움입니다 (68차).

    실물: OXY 매출 1 이 23분기 중 20개, BAC 매출 2,000,000 이 24분기 중
    17개, COF 매출 **-5,000,000**(음수!)이 8개.

    매출은 잣대가 아니지만 **다른 가드들의 잣대**입니다 — 형제 행 잔해
    판정(100배)·마진 검사·EBITDA 단위 검사가 다 매출을 기준으로 씁니다.
    가짜 매출이 통행하면 그 가드들이 틀린 기준으로 판단합니다.
    """
    rows = [quarter_row(filing_date=f"2024-{m:02d}-28",
                        announced_date=f"2024-{m:02d}-28",
                        period_label=f"Q{m}", revenue=2_000_000.0)
            for m in (1, 4, 7, 10)]
    result = dataset.build(make_snapshot(eps={"AAA": rows}))
    kept = result["quarters"]["AAA"]
    assert all(r["revenue"] is None for r in kept), kept
    assert any("자리채움" in n for n in result["notes"]), result["notes"]


def test_varying_revenue_is_left_alone():
    """분기마다 다른 진짜 매출은 건드리면 안 됩니다."""
    rows = [quarter_row(filing_date=f"2024-{m:02d}-28",
                        announced_date=f"2024-{m:02d}-28",
                        period_label=f"Q{m}",
                        revenue=1_000_000.0 + m * 7_000)
            for m in (1, 4, 7, 10)]
    kept = dataset.build(make_snapshot(eps={"AAA": rows}))["quarters"]["AAA"]
    assert all(r["revenue"] is not None for r in kept), kept


def test_three_repeats_are_not_enough():
    """3개까지는 우연일 수 있으므로 건드리지 않습니다 (문턱은 4).

    한쪽으로만 막으면 반대로 넘어집니다 — 너무 예민하면 진짜 매출을
    지웁니다.
    """
    rows = [quarter_row(filing_date=f"2024-{m:02d}-28",
                        announced_date=f"2024-{m:02d}-28",
                        period_label=f"Q{m}", revenue=2_000_000.0)
            for m in (1, 4, 7)]
    rows.append(quarter_row(filing_date="2024-11-28",
                            announced_date="2024-11-28",
                            period_label="Q11", revenue=3_100_000.0))
    kept = dataset.build(make_snapshot(eps={"AAA": rows}))["quarters"]["AAA"]
    assert all(r["revenue"] is not None for r in kept), kept


# ---------------------------------------------------------------------------
# 67차 — 같은 발표일에 두 행 (배당금이 EPS 자리에 들어온 실물)
# ---------------------------------------------------------------------------
def test_same_day_siblings_are_both_dropped():
    """한 발표일에 잣대값 행이 둘이면 **둘 다** 버립니다.

    실물(JPM 2025-04-11): gaap_eps 1.4 와 5.08 이 같은 날에 함께 있습니다.
    1.4 는 분기 **배당금**이고(1.15→1.25→1.40→1.50 인상 일정과 일치),
    매출도 22.6 으로 말이 안 됩니다(실제 17,653 백만). 두 행이 번갈아
    남아 이익 시계열이 톱니가 됐습니다.

    "큰 쪽이 진짜"로 고르지 않는 이유: 크기 규칙은 54차에 **진짜 실적
    하락 71건을 지운** 사고를 냈습니다. 매출로도 못 가릅니다 — 실물에서
    **가짜 행 매출이 더 큰** 경우가 있습니다(20.3 vs 13.0).
    """
    snap = make_snapshot(eps={"AAA": [
        quarter_row(filing_date="2026-03-31", announced_date="2026-04-11",
                    period_label="26 Q1", gaap_eps=5.08, adj_eps=None,
                    revenue=13.0),
        quarter_row(filing_date="2026-03-30", announced_date="2026-04-11",
                    period_label="25 Q4", gaap_eps=1.40, adj_eps=None,
                    revenue=22.6),
    ]})
    result = dataset.build(snap)
    rows = result["quarters"]["AAA"]
    assert len(rows) == 2, "행 자체는 남습니다 (이력이므로)"
    assert all(r["gaap_eps"] is None for r in rows), rows
    assert any("같은 발표일" in n for n in result["notes"]), result["notes"]


def test_single_row_per_day_is_untouched():
    """하루에 한 행이면 건드리지 않습니다 — 멀쩡한 값을 지우면 안 됩니다."""
    snap = make_snapshot(eps={"AAA": [
        quarter_row(filing_date="2026-03-31", announced_date="2026-04-11",
                    gaap_eps=5.08, adj_eps=1.25, revenue=17_653_000_000.0),
        quarter_row(filing_date="2026-06-30", announced_date="2026-07-11",
                    gaap_eps=5.25, adj_eps=1.30, revenue=17_701_000_000.0),
    ]})
    rows = dataset.build(snap)["quarters"]["AAA"]
    assert [r["gaap_eps"] for r in rows] == [5.08, 5.25], rows
    assert [r["adj_eps"] for r in rows] == [1.25, 1.30], rows


def test_rows_without_announced_date_are_not_grouped():
    """발표일이 없는 행끼리는 같은 날로 묶으면 안 됩니다.

    None 을 하나의 날짜처럼 묶으면 발표일 없는 행이 서로를 지웁니다.
    """
    # 매출도 무너뜨려 둡니다 — 안 그러면 매출 검사에서 먼저 걸러져
    # 이 시험이 "발표일로 묶는가"를 가리지 못합니다 (가짜 초록불).
    snap = make_snapshot(eps={"AAA": [
        quarter_row(filing_date="2026-03-31", announced_date=None,
                    revenue=13.0, gaap_eps=5.08),
        quarter_row(filing_date="2026-06-30", announced_date=None,
                    revenue=22.6, gaap_eps=5.25),
    ]})
    rows = dataset.build(snap)["quarters"]["AAA"]
    assert [r["gaap_eps"] for r in rows] == [5.08, 5.25], rows


def test_same_day_drop_is_recorded_not_silent():
    """조용히 버리지 않습니다 — 무엇을 왜 버렸는지 notes 에 남깁니다."""
    snap = make_snapshot(eps={"AAA": [
        quarter_row(announced_date="2026-04-11", period_label="A",
                    revenue=13.0, gaap_eps=5.0),
        quarter_row(announced_date="2026-04-11", period_label="B",
                    revenue=22.6, gaap_eps=1.4),
    ]})
    notes = [n for n in dataset.build(snap)["notes"] if "가릴 수 없어" in n]
    assert len(notes) == 2, notes
    assert any("A" in n for n in notes) and any("B" in n for n in notes), notes


def test_one_good_revenue_sibling_is_left_to_the_ratio_rule():
    """형제 중 매출이 멀쩡한 행이 있으면 이 가드는 손대지 않습니다.

    그 경우는 기존 100배 규칙의 영역이고, 54차가 "비슷한 크기면 둘 다
    남긴다"고 이미 정해 뒀습니다. 새 가드가 그 결정을 덮어쓰면 안 됩니다.
    """
    snap = make_snapshot(eps={"AAA": [
        quarter_row(filing_date="2024-03-31", period_label="24 Q1",
                    announced_date="2024-04-12", revenue=17_653.0,
                    adj_eps=None, gaap_eps=4.45),
        quarter_row(filing_date="2023-12-31", period_label="23 Q4",
                    announced_date="2024-04-12", revenue=900.0,
                    adj_eps=None, gaap_eps=1.15),
    ]})
    rows = {r["period_label"]: r for r in dataset.build(snap)["quarters"]["AAA"]}
    assert rows["24 Q1"]["gaap_eps"] == 4.45, rows
    assert rows["23 Q4"]["gaap_eps"] == 1.15, rows


# ---------------------------------------------------------------------------
# 값 검사 — 탈락은 "없음"으로, 고치지 않는다
# ---------------------------------------------------------------------------
def test_absurd_per_share_value_becomes_none():
    """주당 금액 상한을 넘는 값(실물 오류 202.0)은 없음 처리 + 기록."""
    snap = make_snapshot(eps={"AAA": [quarter_row(adj_eps=202.0)]})
    result = dataset.build(snap)
    row = result["quarters"]["AAA"][0]
    assert row["adj_eps"] is None, row["adj_eps"]
    assert row["gaap_eps"] == 1.00          # 멀쩡한 값은 그대로
    assert any("202.0" in n for n in result["notes"]), result["notes"]


def test_tiny_revenue_is_dropped():
    """자릿수가 무너진 매출(실물: APP 21Q4 rev=3.55달러)은 없음 처리.

    주당 금액·백만 단위 착오가 매출 칸에 들어온 실측 3건 —
    이 유니버스에 분기 매출 1만 달러 미만 회사는 없습니다.
    """
    snap = make_snapshot(eps={"AAA": [quarter_row(revenue=3.55)]})
    result = dataset.build(snap)
    assert result["quarters"]["AAA"][0]["revenue"] is None
    assert any("3.55" in n for n in result["notes"])


def test_normal_large_eps_passes():
    """검증된 정상 최대값(SNDK 39.25)은 상한 안이므로 통과해야 합니다."""
    snap = make_snapshot(eps={"AAA": [quarter_row(adj_eps=39.25)]})
    assert dataset.build(snap)["quarters"]["AAA"][0]["adj_eps"] == 39.25


def test_op_income_unit_mismatch_is_dropped():
    """영업이익 단위 미환산(25차 감사 실물: CRDO 216,722 ↔ 매출 4.37억)은
    없음 처리. 보도자료 표가 '천 달러' 단위인데 달러로 저장된 값은
    매출(달러 확정) 대비 마진이 0.1% 미만으로 나타납니다."""
    snap = make_snapshot(eps={"AAA": [
        quarter_row(revenue=437_003_000.0, op_income=216_722.0),   # 천 달러 착오
        quarter_row(filing_date="2026-03-31", period_label="26 Q3",
                    revenue=430_949_000.0, op_income=-2.0),        # 주당값 오인
        quarter_row(filing_date="2025-12-31", period_label="26 Q2",
                    revenue=1_168_179_000.0, op_income=1_100_000_000.0),  # 마진 94%
    ]})
    result = dataset.build(snap)
    rows = result["quarters"]["AAA"]
    assert all(r["op_income"] is None for r in rows), rows
    assert sum("단위" in n or "마진" in n for n in result["notes"]) >= 3


def test_real_op_income_passes_unit_guard():
    """정상 마진(10%)과 근소 적자(-3%)는 그대로 통과해야 합니다."""
    snap = make_snapshot(eps={"AAA": [
        quarter_row(revenue=1_000_000_000.0, op_income=100_000_000.0),
        quarter_row(filing_date="2026-03-31", period_label="26 Q3",
                    revenue=1_000_000_000.0, op_income=-30_000_000.0),
    ]})
    rows = dataset.build(snap)["quarters"]["AAA"]
    assert rows[0]["op_income"] == -30_000_000.0     # 정렬 후 첫 행 = 26 Q3
    assert rows[1]["op_income"] == 100_000_000.0


def test_adjusted_ebitda_unit_mismatch_is_dropped():
    """조정 EBITDA 단위 미환산(46차 감사 실물 85건)은 없음 처리.

    가장 아픈 예: CIEN 25 Q3 157,962,000 → 25 Q4 **205,536**.
    저장값으로는 99.9% 급감이지만 실제로는 205,536천 달러(2.06억)로
    **증가**입니다. 이 오염이 광통신 묶음의 이익 델타를 통째로 뒤집어
    주도섹터 판정을 틀리게 만들고 있었습니다.
    """
    snap = make_snapshot(eps={"AAA": [
        quarter_row(revenue=1_047_000_000.0, adjusted_ebitda=205_536.0),   # 천 달러 착오
        quarter_row(filing_date="2026-03-31", period_label="26 Q3",
                    revenue=1_000_000_000.0, adjusted_ebitda=1_200_000_000.0),  # 매출 초과
    ]})
    result = dataset.build(snap)
    rows = result["quarters"]["AAA"]
    assert all(r["adjusted_ebitda"] is None for r in rows), rows
    assert sum("EBITDA" in n for n in result["notes"]) >= 2, result["notes"]


def test_real_adjusted_ebitda_passes_unit_guard():
    """정상 EBITDA 마진(20%)과 소폭 적자는 그대로 통과해야 합니다."""
    snap = make_snapshot(eps={"AAA": [
        quarter_row(revenue=1_000_000_000.0, adjusted_ebitda=200_000_000.0),
        quarter_row(filing_date="2026-03-31", period_label="26 Q3",
                    revenue=1_000_000_000.0, adjusted_ebitda=-50_000_000.0),
    ]})
    rows = dataset.build(snap)["quarters"]["AAA"]
    assert rows[0]["adjusted_ebitda"] == -50_000_000.0
    assert rows[1]["adjusted_ebitda"] == 200_000_000.0



def test_cumulative_value_in_quarterly_slot_is_dropped():
    """누적(YTD·연간)값이 분기 칸에 들어온 것은 없음 처리 (52차 감사).

    실물 QCOM: 분기 EPS 가 2.4~2.6 인데 2024-09-29 마감 행만 10.22 —
    직전 4분기 합 9.77 과 거의 같다. 보도자료의 '연간' 칸을 문 것이다.
    이런 값은 그 분기에 가짜 급등, 다음 분기에 가짜 급락을 한 쌍 만들어
    이익 델타를 통째로 뒤집는다.
    """
    rows = []
    day = 1
    for i, value in enumerate([2.4, 2.5, 2.4, 2.6, 2.5, 2.5, 10.22, 2.6]):
        rows.append(quarter_row(
            filing_date=f"2024-{(i % 12) + 1:02d}-28",
            announced_date=f"2024-{(i % 12) + 1:02d}-28",
            # 매출은 분기마다 달라야 합니다 — 똑같으면 자리채움으로 걸립니다
            revenue=1_000_000.0 + i * 1_000,
            period_label=f"Q{i}", adj_eps=value))
    result = dataset.build(make_snapshot(eps={"AAA": rows}))
    kept = [r["adj_eps"] for r in result["quarters"]["AAA"]]
    assert 10.22 not in kept, kept
    assert kept.count(2.5) == 3 and 2.6 in kept, kept   # 정상값은 그대로
    assert any("누적" in n for n in result["notes"]), result["notes"]


def test_growth_quarter_is_not_mistaken_for_cumulative():
    """빠르게 크는 회사의 정상 분기를 누적으로 오인하면 안 됩니다.

    직전 4분기 합과 **닮지 않으면** 건드리지 않습니다.
    """
    rows = []
    for i, value in enumerate([0.10, 0.15, 0.22, 0.33, 0.50, 0.75, 1.10, 1.60]):
        rows.append(quarter_row(
            filing_date=f"2024-{(i % 12) + 1:02d}-28",
            announced_date=f"2024-{(i % 12) + 1:02d}-28",
            # 매출은 분기마다 달라야 합니다 — 똑같으면 자리채움으로 걸립니다
            revenue=1_000_000.0 + i * 1_000,
            period_label=f"Q{i}", adj_eps=value))
    kept = [r["adj_eps"] for r in
            dataset.build(make_snapshot(eps={"AAA": rows}))["quarters"]["AAA"]]
    assert kept == [0.10, 0.15, 0.22, 0.33, 0.50, 0.75, 1.10, 1.60], kept


def test_loss_quarter_is_never_treated_as_cumulative():
    """적자(음수) 분기는 누적값일 수 없으므로 건드리지 않습니다."""
    rows = []
    for i, value in enumerate([2.0, 2.1, 2.0, 2.2, 2.1, 2.0, -8.3, 2.1]):
        rows.append(quarter_row(
            filing_date=f"2024-{(i % 12) + 1:02d}-28",
            announced_date=f"2024-{(i % 12) + 1:02d}-28",
            # 매출은 분기마다 달라야 합니다 — 똑같으면 자리채움으로 걸립니다
            revenue=1_000_000.0 + i * 1_000,
            period_label=f"Q{i}", adj_eps=value))
    kept = [r["adj_eps"] for r in
            dataset.build(make_snapshot(eps={"AAA": rows}))["quarters"]["AAA"]]
    assert -8.3 in kept, kept


def test_cumulative_guard_uses_only_past_values():
    """판정에 쓰는 것은 그 행보다 **앞선** 값들뿐 — 미래를 보지 않습니다.

    앞부분이 똑같은 두 자료를 넣되 **뒤에만** 아주 큰 값을 붙입니다.
    미래까지 보고 중앙값을 내면 그 큰 값이 중앙값을 끌어올려 앞의 누적값이
    살아남습니다. 앞엣값의 운명이 뒤엣값에 좌우되면 미래 엿보기입니다.
    """
    head = [1.0, 1.0, 1.0, 1.0, 4.2]      # 4.2 = 직전 4분기 합 4.0 과 거의 같음

    def build(tail):
        rows = [quarter_row(filing_date=f"2024-{(i % 12) + 1:02d}-28",
                            period_label=f"Q{i}", adj_eps=v)
                for i, v in enumerate(head + tail)]
        got = dataset.build(make_snapshot(eps={"AAA": rows}))["quarters"]["AAA"]
        return [r["adj_eps"] for r in got][:len(head)]

    assert build([])[-1] is None, "누적값 4.2 를 못 잡았습니다"
    assert build([50.0, 50.0, 50.0, 50.0])[-1] is None, (
        "뒤에 붙은 큰 값이 앞엣값 판정을 바꿨습니다 — 미래를 보고 있습니다")



def test_parse_debris_row_is_dropped():
    """같은 발표일의 형제 행보다 매출이 100배 작으면 파싱 잔해로 보고 버립니다.

    실물 JPM(원문 확증): 같은 발표일에
      · 매출 17,653 · gaap_eps 4.45  ← 진짜 분기 실적
      · 매출     19.3 · gaap_eps 1.15  ← **분기 배당금**이 EPS 칸에 들어옴
    이 잔해가 남으면 JPM 델타 9쌍이 전부 '하락'으로 읽힙니다.
    """
    snap = make_snapshot(eps={"AAA": [
        quarter_row(filing_date="2024-03-31", period_label="24 Q1",
                    announced_date="2024-04-12", revenue=17_653.0,
                    adj_eps=None, gaap_eps=4.45),
        quarter_row(filing_date="2023-09-30", period_label="23 Q3",
                    announced_date="2024-04-12", revenue=19.3,
                    adj_eps=None, gaap_eps=1.15),
    ]})
    result = dataset.build(snap)
    rows = {r["period_label"]: r for r in result["quarters"]["AAA"]}
    assert rows["24 Q1"]["gaap_eps"] == 4.45, rows       # 진짜는 남는다
    assert rows["23 Q3"]["gaap_eps"] is None, rows       # 잔해는 버린다
    assert any("잔해" in n for n in result["notes"]), result["notes"]


def test_millions_revenue_rows_are_not_mistaken_for_debris():
    """매출을 **백만 달러 단위**로 적는 회사의 정상 행을 지우면 안 됩니다.

    '매출이 작다'만으로 자르면 AMD 4,313 · MCHP 1,649 같은 정상 행 172칸이
    함께 지워집니다. 형제 행이 없으면 건드리지 않습니다.
    """
    snap = make_snapshot(eps={"AAA": [
        quarter_row(filing_date="2024-03-31", period_label="24 Q1",
                    announced_date="2024-04-30", revenue=4_313.0,
                    adj_eps=0.73, gaap_eps=0.75),
        quarter_row(filing_date="2024-06-30", period_label="24 Q2",
                    announced_date="2024-07-30", revenue=5_887.0,
                    adj_eps=1.13, gaap_eps=0.56),
    ]})
    rows = dataset.build(snap)["quarters"]["AAA"]
    assert [r["adj_eps"] for r in rows] == [0.73, 1.13], rows
    assert [r["gaap_eps"] for r in rows] == [0.75, 0.56], rows


def test_sibling_rows_of_similar_size_are_both_kept():
    """형제 행이라도 매출 차이가 100배 미만이면 둘 다 남깁니다."""
    snap = make_snapshot(eps={"AAA": [
        quarter_row(filing_date="2024-03-31", period_label="24 Q1",
                    announced_date="2024-04-12", revenue=17_653.0, gaap_eps=4.45),
        quarter_row(filing_date="2023-12-31", period_label="23 Q4",
                    announced_date="2024-04-12", revenue=900.0, gaap_eps=1.15),
    ]})
    rows = dataset.build(snap)["quarters"]["AAA"]
    assert all(r["gaap_eps"] is not None for r in rows), rows


def test_internal_revenue_marker_does_not_leak():
    """잔해 판정에 쓴 내부 표시는 밖으로 나가면 안 됩니다."""
    snap = make_snapshot(eps={"AAA": [quarter_row(revenue=3.55)]})
    for row in dataset.build(snap)["quarters"]["AAA"]:
        assert "_raw_revenue" not in row, row

def test_non_number_becomes_none():
    """숫자가 아닌 값(NaN·문자)은 고치지 않고 없음 처리합니다."""
    snap = make_snapshot(
        eps={"AAA": [quarter_row(revenue=float("nan"), gaap_eps="1.0")]}
    )
    row = dataset.build(snap)["quarters"]["AAA"][0]
    assert row["revenue"] is None
    assert row["gaap_eps"] is None
    assert row["adj_eps"] == 1.25


def test_row_without_filing_date_is_dropped():
    """시간축(filing_date)이 없는 행은 놓을 자리가 없으므로 버리고 기록합니다."""
    snap = make_snapshot(
        eps={"AAA": [quarter_row(filing_date=None), quarter_row()]}
    )
    result = dataset.build(snap)
    assert len(result["quarters"]["AAA"]) == 1
    assert any("filing_date" in n for n in result["notes"])


def test_bad_announced_date_becomes_none_but_row_survives():
    """발표일 형식이 이상하면 발표일만 없음 — 분기 이력으로는 남습니다."""
    snap = make_snapshot(eps={"AAA": [quarter_row(announced_date="이상한값")]})
    result = dataset.build(snap)
    row = result["quarters"]["AAA"][0]
    assert row["announced_date"] is None
    assert row["adj_eps"] == 1.25


def test_quarters_are_sorted_by_filing_date():
    """분기 행은 시간순으로 정렬되어 나옵니다."""
    snap = make_snapshot(eps={"AAA": [
        quarter_row(filing_date="2026-06-30", period_label="26 Q4"),
        quarter_row(filing_date="2025-06-30", period_label="25 Q4"),
    ]})
    rows = dataset.build(snap)["quarters"]["AAA"]
    assert [r["filing_date"] for r in rows] == ["2025-06-30", "2026-06-30"]


# ---------------------------------------------------------------------------
# 가격 검사
# ---------------------------------------------------------------------------
def test_prices_sorted_deduped_and_positive_only():
    """가격은 정렬·중복 제거되고, 0 이하·숫자 아님은 점 단위로 버립니다."""
    snap = make_snapshot(prices={
        "SPY": {"dates": ["2026-08-12", "2026-08-11"], "close": [501.0, 500.0]},
        "AAA": {
            "dates": ["2026-08-12", "2026-08-11", "2026-08-12", "2026-08-13"],
            "close": [10.0, -5.0, 11.0, float("nan")],
        },
    })
    result = dataset.build(snap)
    assert result["prices"]["SPY"]["dates"] == ["2026-08-11", "2026-08-12"]
    aaa = result["prices"]["AAA"]
    # -5.0 과 NaN 은 버려지고, 중복된 8-12 는 뒤의 값(11.0)만 남습니다
    assert aaa["dates"] == ["2026-08-12"], aaa
    assert aaa["close"] == [11.0], aaa


def test_mismatched_price_table_is_dropped_whole():
    """날짜·종가 개수가 어긋난 종목은 가격 전체를 버리고 기록합니다."""
    snap = make_snapshot(prices={
        "SPY": {"dates": ["2026-08-11", "2026-08-12"], "close": [500.0, 501.0]},
        "AAA": {"dates": ["2026-08-11", "2026-08-12"], "close": [10.0]},
    })
    result = dataset.build(snap)
    assert "AAA" not in result["prices"]
    assert any("통째로" in n for n in result["notes"])


def test_missing_benchmark_raises():
    """기준지수(SPY) 가격이 없으면 즉시 실패해야 합니다 — 조용히 진행 금지."""
    snap = make_snapshot(prices={})
    try:
        dataset.build(snap)
    except ValueError as e:
        assert "SPY" in str(e)
    else:
        raise AssertionError("기준지수 없는 재료가 조용히 통과했습니다")


# ---------------------------------------------------------------------------
# 원본 불변
# ---------------------------------------------------------------------------
def test_original_snapshot_is_not_mutated():
    """build 는 snapshot 원본을 절대 바꾸지 않습니다."""
    snap = make_snapshot(eps={"AAA": [quarter_row(adj_eps=202.0)]})
    before = copy.deepcopy(snap)
    dataset.build(snap)
    assert snap == before, "원본 snapshot 이 바뀌었습니다"


# ---------------------------------------------------------------------------
# 실물 snapshot 통행 검사 (로봇이 매일 덮어쓰므로 불변 조건만)
# ---------------------------------------------------------------------------
def test_real_snapshot_passes_all_invariants():
    path = os.path.join(cfg.MEASURE_DIR, "snapshot.json")
    if not os.path.exists(path):
        print("    (실물 snapshot 없음 — 이 검사는 건너뜀)")
        return
    result = dataset.build(dataset.load(path))

    assert result["benchmark"] in result["prices"]
    for ticker, rows in result["quarters"].items():
        dates = [r["filing_date"] for r in rows]
        assert dates == sorted(dates), f"{ticker} 분기 정렬 실패"
        for r in rows:
            for field in dataset._PER_SHARE_FIELDS:
                v = r.get(field)
                assert v is None or abs(v) <= dataset.PER_SHARE_ABS_LIMIT, (
                    f"{ticker} {r['period_label']} {field}={v}"
                )
    for ticker, series in result["prices"].items():
        assert series["dates"] == sorted(series["dates"]), f"{ticker} 가격 정렬 실패"
        assert len(series["dates"]) == len(set(series["dates"])), f"{ticker} 중복 날짜"
        assert all(c > 0 for c in series["close"]), f"{ticker} 0 이하 종가"


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
    print(f"\n데이터 계층 검증: {passed}개 통과, {failed}개 실패")
    sys.exit(1 if failed else 0)
