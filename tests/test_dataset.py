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
import pathlib
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


def test_impossible_gross_margin_is_dropped():
    """이익률은 100%를 넘을 수 없습니다 (매출총이익 ≤ 매출) — 69차."""
    for bad in (101.0, 250.0, -150.0):
        snap = make_snapshot(eps={"AAA": [quarter_row(gross_margin_pct=bad)]})
        result = dataset.build(snap)
        assert result["quarters"]["AAA"][0]["gross_margin_pct"] is None, bad
        assert any("매출총이익률" in n for n in result["notes"]), bad


def test_normal_gross_margin_passes():
    """정상 범위(적자 마진 포함)는 그대로 통과해야 합니다."""
    for ok in (63.4, 0.0, 100.0, -30.0, -100.0):
        snap = make_snapshot(eps={"AAA": [quarter_row(gross_margin_pct=ok)]})
        row = dataset.build(snap)["quarters"]["AAA"][0]
        assert row["gross_margin_pct"] == ok, ok


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



def test_yearly_values_hidden_behind_each_other_are_all_dropped():
    """오염이 오염을 가려 주는 것을 뚫어야 합니다 (73차, 실물 VZ·GS).

    VZ 는 **1월 발표 행마다 연간 EPS** 가 들어와 있습니다 (분기 실제는
    1.2 안팎인데 4.7~5.2). 검사는 "직전 4분기 합과 거의 같은가"를 보는데,
    그 직전 4분기 안에 앞의 연간값이 끼어 있으면 합이 8.4 로 부풀어
    "합과 다르다"고 판정돼 뒤의 연간값이 살아남습니다.

    그래서 한 칸 지울 때마다 처음부터 다시 재야 합니다. 한 번만 재는
    코드로 되돌리면 뒤의 두 칸(4.80 · 4.80)이 살아남아 이 검사가
    빨간 불이 됩니다.
    """
    분기 = [1.18, 1.22, 1.19, 1.21]        # 네 분기 합이 정확히 4.80
    값들 = (분기 + [4.80]                    # ← 연간값 ①
            + [1.18, 1.22, 1.19] + [4.80]   # ← 연간값 ② (①에 가려짐)
            + [1.21, 1.18, 1.22] + [4.80])  # ← 연간값 ③ (②에 가려짐)
    def 날짜(i):
        """13개 행이 필요하므로 해를 넘겨 가며 날짜를 만듭니다."""
        return f"{2023 + i // 12}-{i % 12 + 1:02d}-28"

    rows = [quarter_row(filing_date=날짜(i), announced_date=날짜(i),
                        # 매출·날짜는 분기마다 달라야 합니다 (자리채움 가드)
                        revenue=1_000_000.0 + i * 7_000,
                        period_label=f"Q{i}", adj_eps=v)
            for i, v in enumerate(값들)]

    result = dataset.build(make_snapshot(eps={"AAA": rows}))
    kept = [r["adj_eps"] for r in result["quarters"]["AAA"]]

    assert 4.80 not in kept, f"연간값이 살아남았습니다: {kept}"
    assert kept.count(None) == 3, f"세 칸 모두 지워져야 합니다: {kept}"
    assert len([v for v in kept if v is not None]) == 10, kept
    누적메모 = [n for n in result["notes"] if "누적" in n]
    assert len(누적메모) == 3, 누적메모


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


def test_액면분할_환산_정분할():
    """분할 전 주당 값은 나눠지고, 분할 후·주당 아닌 칸은 그대로 (112차).

    실물 근거: SMCI 우리 22.09 = 야후 소급 2.20 × 10.04 (111차). 분할 전후를
    그대로 이으면 TTM 추세에 가짜 급락이 생깁니다. 강한 의심만 14종목.
    """
    snap = {
        "benchmark": "SPY",
        "tickers": ["AA"],
        "prices": {"SPY": {"dates": ["2024-01-02"], "close": [100.0]}},
        "eps": {"AA": [
            {"filing_date": "2024-03-31", "announced_date": "2024-05-01",
             "adj_eps": 20.0, "gaap_eps": 18.0, "revenue": 5.0e9},   # 분할 전
            {"filing_date": "2024-12-31", "announced_date": "2025-02-01",
             "adj_eps": 2.1, "gaap_eps": 1.9, "revenue": 5.2e9},     # 분할 후
        ]},
    }
    ds = dataset.build(snap, splits={"AA": [("2024-10-01", 10.0)]})
    앞, 뒤 = ds["quarters"]["AA"]
    assert abs(앞["adj_eps"] - 2.0) < 1e-9, "분할 전 조정 EPS 가 ÷10 되지 않았습니다"
    assert abs(앞["gaap_eps"] - 1.8) < 1e-9
    assert 앞["revenue"] == 5.0e9, "매출은 주식 수와 무관 — 건드리면 안 됩니다"
    assert 뒤["adj_eps"] == 2.1, "분할 후 값을 건드렸습니다"
    assert any("액면분할 환산" in n for n in ds["notes"]), "환산 내역이 notes 에 없습니다"


def test_액면분할_환산_역분할과_겹분할():
    """역분할(비율<1)은 값이 커지고, 분할 두 번은 비율이 곱해집니다."""
    snap = {
        "benchmark": "SPY",
        "tickers": ["BB"],
        "prices": {"SPY": {"dates": ["2024-01-02"], "close": [100.0]}},
        "eps": {"BB": [
            {"filing_date": "2019-03-31", "announced_date": "2019-05-01",
             "adj_eps": 1.0},                                # 두 분할 모두 앞
            {"filing_date": "2021-03-31", "announced_date": "2021-05-01",
             "adj_eps": 1.0},                                # 역분할만 앞
        ]},
    }
    # 2020: 5:1 정분할 · 2022: 1:8 역분할(비율 0.125)
    ds = dataset.build(snap, splits={"BB": [("2020-06-01", 5.0),
                                            ("2022-06-01", 0.125)]})
    r1, r2 = ds["quarters"]["BB"]
    assert abs(r1["adj_eps"] - 1.0 / (5.0 * 0.125)) < 1e-9, r1   # ÷0.625 = ×1.6
    assert abs(r2["adj_eps"] - 1.0 / 0.125) < 1e-9, r2           # ×8


def test_분할기록_없으면_예전과_완전히_같다():
    """로봇이 아직 splits 를 안 모았으면 환산도 없어야 합니다 —
    없는 것을 지어내지 않습니다."""
    snap = {
        "benchmark": "SPY",
        "tickers": ["CC"],
        "prices": {"SPY": {"dates": ["2024-01-02"], "close": [100.0]}},
        "eps": {"CC": [{"filing_date": "2024-03-31", "adj_eps": 20.0}]},
    }
    with_none = dataset.build(snap)
    with_empty = dataset.build(snap, splits={})
    assert with_none["quarters"]["CC"][0]["adj_eps"] == 20.0
    assert with_empty["quarters"]["CC"][0]["adj_eps"] == 20.0


def test_load_splits_는_없거나_이상한_기록을_거른다():
    import json as _json
    import os as _os
    tmp = "/tmp/claude-0/vendor_splits_test.json"
    _json.dump({"tickers": {
        "AA": {"splits": [{"date": "2024-10-01", "ratio": 10.0},
                          {"date": "2020-06-01", "ratio": 5.0}]},
        "BB": {"splits": [{"date": "이상함", "ratio": 10.0},   # 날짜 불량
                          {"date": "2024-01-01", "ratio": 1.0},  # 무의미
                          {"date": "2024-01-01", "ratio": -2}]},  # 불량
        "CC": {},
    }}, open(tmp, "w"))
    out = dataset.load_splits(tmp)
    assert out == {"AA": [("2020-06-01", 5.0), ("2024-10-01", 10.0)]}, out
    assert dataset.load_splits("/tmp/claude-0/없는파일.json") == {}
    _os.remove(tmp)


# ---------------------------------------------------------------------------
# 발표일 되찾기 (137차) — 값은 있는데 날짜가 없어 통째로 빠지던 분기
# ---------------------------------------------------------------------------
def _발표일_스냅(rows):
    return {
        "benchmark": "SPY",
        "tickers": ["AA"],
        "prices": {"SPY": {"dates": ["2024-01-02"], "close": [100.0]}},
        "eps": {"AA": rows},
    }


def test_발표일이_비면_바깥자_발표일로_채운다():
    """실측: 9,466행 중 2,336행에 발표일이 없고, 그중 785행은 잣대 값이
    멀쩡히 있었습니다. 측정은 발표일로 창을 잡으므로 그 분기들은 실적이
    있는데도 한 번도 세어지지 않았습니다."""
    snap = _발표일_스냅([{"filing_date": "2024-03-31", "adj_eps": 1.0}])
    ds = dataset.build(snap, announcements={"AA": ["2024-04-25"]})
    행 = ds["quarters"]["AA"][0]
    assert 행["announced_date"] == "2024-04-25", 행
    assert 행["_발표일출처"] == "야후", "채운 사실을 숨기면 안 됩니다"
    assert any("발표일이 비어" in n for n in ds["notes"]), ds["notes"]


def test_분기끝을_발표일로_쓰지_않는다():
    """`filing_date` 는 접수일이 아니라 **분기 종료일**입니다. 둘 다 있는
    7,130행으로 재 보니 발표는 분기끝보다 **중앙 30일** 뒤였습니다.
    분기끝을 발표일로 쓰면 뉴스가 나오기 한 달 전에 사는 셈이 되어 모든
    가설이 부풀려집니다 (108차 "값은 멀쩡한데 뜻이 틀렸다"의 재현)."""
    snap = _발표일_스냅([{"filing_date": "2024-03-31", "adj_eps": 1.0}])
    ds = dataset.build(snap, announcements={})      # 바깥 자 기록 없음
    행 = ds["quarters"]["AA"][0]
    assert not 행.get("announced_date"), \
        f"분기끝을 발표일로 써 버렸습니다: {행.get('announced_date')}"


def test_우리_발표일은_덮어쓰지_않는다():
    """바깥 자와 우리가 다를 때(실측 256행) 우리 값을 덮으면 대조가
    불가능해집니다. 빈 칸만 채웁니다."""
    snap = _발표일_스냅([{"filing_date": "2024-03-31", "adj_eps": 1.0,
                       "announced_date": "2024-04-20"}])
    ds = dataset.build(snap, announcements={"AA": ["2024-04-25"]})
    행 = ds["quarters"]["AA"][0]
    assert 행["announced_date"] == "2024-04-20", 행
    assert "_발표일출처" not in 행


def test_창_밖이거나_후보가_여럿이면_채우지_않는다():
    """창 0~75일은 실측 분포에서 골랐습니다(발표 지연 99% 58일 · 최대 117).
    90일을 넘기면 **다음 분기 발표**가 창에 들어와 애매해집니다."""
    snap = _발표일_스냅([{"filing_date": "2024-03-31", "adj_eps": 1.0}])
    멀다 = dataset.build(snap, announcements={"AA": ["2024-07-30"]})   # 121일 뒤
    assert not 멀다["quarters"]["AA"][0].get("announced_date")
    여럿 = dataset.build(snap, announcements={"AA": ["2024-04-25", "2024-05-20"]})
    assert not 여럿["quarters"]["AA"][0].get("announced_date"), \
        "후보가 둘인데 하나를 골랐습니다 — 모르는 것은 모르는 채로 둡니다"


def test_이미_쓰인_발표일은_다시_쓰지_않는다():
    """회사는 한 날에 두 분기를 발표하지 않습니다. 같은 날이 두 행에
    붙으면 둘 중 하나는 틀린 것입니다 — 가드가 없을 때 실제로 16건이
    겹쳤습니다(137차 실측)."""
    snap = _발표일_스냅([
        {"filing_date": "2024-03-31", "adj_eps": 1.0,
         "announced_date": "2024-05-10"},
        {"filing_date": "2024-04-30", "adj_eps": 1.1},   # 창 안에 같은 날뿐
    ])
    ds = dataset.build(snap, announcements={"AA": ["2024-05-10"]})
    둘째 = ds["quarters"]["AA"][1]
    assert not 둘째.get("announced_date"), \
        f"이미 쓰인 발표일을 또 붙였습니다: {둘째.get('announced_date')}"


def test_load_announcements_는_뜻이_확인된_날짜만_읽는다():
    """`날짜뜻` 이 "발표일" 인 기록만 씁니다 — 뜻이 다른 날짜를 발표일로
    쓰는 것이 108차에 겪은 사고 그 자체입니다."""
    import json as _json
    tmp = "/tmp/claude-0/vendor_ann_test.json"
    os.makedirs(os.path.dirname(tmp), exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as f:
        _json.dump({"tickers": {
            "AA": {"announcements": [
                {"announced_date": "2024-04-25", "날짜뜻": "발표일"},
                {"announced_date": "2024-03-31", "날짜뜻": "분기끝"},
                {"announced_date": "2024-05-01"},                  # 뜻 없음
                {"날짜뜻": "발표일"},                                # 날짜 없음
            ]},
            "BB": {"announcements": []},
        }}, f, ensure_ascii=False)
    읽음 = dataset.load_announcements(tmp)
    assert 읽음 == {"AA": ["2024-04-25"]}, 읽음
    assert dataset.load_announcements("/없는/파일.json") == {}



def test_분기끝에서_너무_가깝거나_먼_발표일은_버린다():
    """(138차) 우리가 읽은 발표일 7,130행 중 236행이 바깥 자와 어긋났고,
    어긋날 때는 **우리 쪽이 틀린 경우가 훨씬 많았습니다**(상식 범위 안:
    우리 146/236 · 야후 216/236). 실적과 무관한 8-K 를 집은 것으로
    보입니다. 창을 잘못된 날에서 시작하면 그 사건의 수익률은 다른 기간을
    잰 숫자가 되어 모든 가설에 잡음으로 들어갑니다."""
    for 발표, 설명 in (("2024-04-02", "분기끝 2일 뒤 — 결산 전"),
                     ("2024-07-20", "분기끝 111일 뒤 — 다음 분기 지남")):
        snap = _발표일_스냅([{"filing_date": "2024-03-31", "adj_eps": 1.0,
                           "announced_date": 발표}])
        ds = dataset.build(snap, announcements={})
        assert not ds["quarters"]["AA"][0].get("announced_date"), \
            f"{설명} 인데 그대로 뒀습니다"
        assert any("상식 범위" in n for n in ds["notes"]), ds["notes"]


def test_상식_범위_안의_발표일은_건드리지_않는다():
    """범위 10~70일은 **바깥 자가 확인해 준 6,874행**의 분포에서 골랐습니다
    (0.5% = 10일 · 중앙 30일 · 99.5% = 58일). 평범한 발표를 버리면
    표본만 줄고 얻는 것이 없습니다."""
    snap = _발표일_스냅([{"filing_date": "2024-03-31", "adj_eps": 1.0,
                       "announced_date": "2024-04-30"}])      # 30일 뒤
    ds = dataset.build(snap, announcements={})
    assert ds["quarters"]["AA"][0]["announced_date"] == "2024-04-30"
    assert not any("상식 범위" in n for n in ds["notes"]), ds["notes"]


def test_버린_발표일은_바깥자_기록으로_되채워진다():
    """버리기만 하면 표본이 줄 뿐입니다. 버린 자리를 137차 되찾기가 채워야
    비로소 **수리**가 됩니다. 실행 출력: 128행을 버려 122행이 되채워지고
    6행만 날짜 없이 남았으며, 어긋나던 90행이 바로잡혔습니다."""
    snap = _발표일_스냅([{"filing_date": "2024-03-31", "adj_eps": 1.0,
                       "announced_date": "2024-07-20"}])      # 111일 — 버려짐
    ds = dataset.build(snap, announcements={"AA": ["2024-04-25"]})
    행 = ds["quarters"]["AA"][0]
    assert 행["announced_date"] == "2024-04-25", f"되채우지 못했습니다: {행}"
    assert 행["_발표일출처"] == "야후"



# ---------------------------------------------------------------------------
# 자가 없는 행의 단위 — **증명된 배수가 있을 때만** 고친다 (150차-W)
# ---------------------------------------------------------------------------
def _행(끝, 매출, 영업익, xbrl=None, 배수=None):
    r = {"filing_date": 끝, "period_label": 끝[:7], "revenue": 매출,
         "op_income": 영업익}
    if xbrl is not None:
        r["op_income_xbrl"] = xbrl
    if 배수 is not None:
        r["unit_scale_fixed"] = 배수
    return r


def _크리도_모양():
    """XBRL 자가 ×1,000 을 정한 분기 셋 + 자가 없는 구멍 메움 행 하나."""
    return [
        _행("2025-02-01", 135_002_000, 42_384_000, xbrl=42_384_000, 배수=1000),
        _행("2025-05-03", 170_025_000, 62_523),          # 자 없음 — 고칠 대상
        _행("2025-08-02", 223_074_000, 96_197_000, xbrl=88_000_000),
        _행("2025-11-01", 268_027_000, 124_120_000, xbrl=124_120_000, 배수=1000),
        _행("2026-01-31", 407_012_000, 201_782_000, xbrl=201_782_000, 배수=1000),
    ]


def test_증명된_배수가_있으면_자없는_행도_고친다():
    """실물 CRDO 2025-05-03 — 구멍을 메워 만든 행이라 XBRL 자가 없습니다.

    그런데 같은 종목 세 분기에서 XBRL 자가 이미 ×1,000 을 정해 뒀습니다.
    표가 천 단위면 그 표에서 나온 모든 값이 천 단위입니다.
    """
    rows = _크리도_모양()
    메모 = []
    고침 = dataset._단위를_이웃으로_고치기("CRDO", rows, 메모)
    assert 고침.get("2025-05-03") == 1000, f"안 고쳤습니다: {고침}"
    고친행 = next(r for r in rows if r["filing_date"] == "2025-05-03")
    assert 고친행["op_income"] == 62_523_000, 고친행
    assert 메모 and "150차-W" in 메모[0]


def test_증명된_배수가_없으면_아무것도_안_한다():
    """XBRL 자가 배수를 정한 적이 없으면 근거가 없습니다 — 그냥 버립니다."""
    rows = [dict(r) for r in _크리도_모양()]
    for r in rows:
        r.pop("unit_scale_fixed", None)      # 증명 자취를 지운다
    메모 = []
    assert dataset._단위를_이웃으로_고치기("CRDO", rows, 메모) == {}, "근거 없이 고쳤습니다"
    assert not 메모


def test_증명된_배수가_한_분기뿐이면_안_고친다():
    """한 번은 우연일 수 있습니다 — 두 분기 이상이어야 합니다."""
    rows = [dict(r) for r in _크리도_모양()]
    본 = 0
    for r in rows:
        if r.get("unit_scale_fixed"):
            본 += 1
            if 본 > 1:
                r.pop("unit_scale_fixed")
    assert dataset._단위를_이웃으로_고치기("CRDO", rows, []) == {}, \
        "증명이 한 분기뿐인데 고쳤습니다"


def test_증명된_배수가_엇갈리면_안_고친다():
    """×1,000 과 ×100만이 섞여 있으면 어느 쪽인지 알 수 없습니다.

    ⚠️ **정직하게**: 이 조건을 꺼도 값이 틀어지지는 않습니다. 배수가
    1,000배씩 떨어져 있어 엉뚱한 배수를 고르면 **띠 검사에 걸려**
    아무것도 안 하기 때문입니다(돌연변이로 확인). 즉 이 조건이 지키는
    것은 '틀린 값'이 아니라 **'근거 없이 고치지 않음'** 입니다.
    그래도 남겨 둡니다 — 띠가 언젠가 넓어지면 그때는 이 조건이 막습니다.
    """
    rows = [dict(r) for r in _크리도_모양()]
    for r in rows:
        if r.get("unit_scale_fixed"):
            r["unit_scale_fixed"] = 1_000_000
            break
    assert dataset._단위를_이웃으로_고치기("CRDO", rows, []) == {}, \
        "배수가 엇갈리는데 하나를 골랐습니다"


def test_엉뚱한_배수는_어차피_띠에_걸린다():
    """'배수 후보가 1,000배씩 떨어져 있다'는 성질을 직접 확인합니다.

    이것이 깨지면 위 시험이 조용히 헛돌기 시작합니다.
    """
    매출, 값 = 170_025_000.0, 62_523.0
    아래, 위 = 0.009, 0.95              # 크리도의 성한 분기 마진 띠
    맞는 = [s for s in (1_000, 1_000_000, 1_000_000_000)
           if 아래 <= abs(값 * s / 매출) <= 위]
    assert 맞는 == [1_000], f"띠 안에 드는 배수가 {맞는} 입니다 — 하나여야 합니다"


def test_진짜_손익분기_분기는_띠_밖이면_안_고친다():
    """마진이 진짜로 0.05% 인 회사를 1,000배 부풀리면 안 됩니다.

    ×배수를 곱해도 그 종목의 성한 분기 마진 띠 안에 안 들어오면
    그대로 둡니다 — 버려질지언정 틀린 값을 만들지 않습니다.
    """
    rows = _크리도_모양()
    for r in rows:
        if r["filing_date"] == "2025-05-03":
            r["revenue"] = 700_000_000_000.0
            r["op_income"] = 50_000.0        # ×1000 해도 0.007%
    고침 = dataset._단위를_이웃으로_고치기("CRDO", rows, [])
    assert "2025-05-03" not in 고침, f"띠 밖인데 고쳤습니다: {고침}"


def test_XBRL_자가_본_행은_여기서_건드리지_않는다():
    """XBRL 자가 **보고도 못 정한** 행은 그대로 둡니다.

    150차-V 의 XBRL 자가 그 행을 이미 봤는데 배수를 못 정했다면,
    그것은 '모르겠다'는 독립된 판단입니다. 종목 단위 배수로 덮어쓰면
    그 판단을 무시하는 것이 됩니다.

    (성한 값인 행은 조건 ③ 이 이미 거르므로, 여기서는 **자가 있으면서
    값도 수상한** 행으로 조건 ② 만 따로 시험합니다.)
    """
    rows = _크리도_모양()
    rows.append(_행("2024-11-02", 72_034_000, 8_256, xbrl=999_999_999))
    dataset._단위를_이웃으로_고치기("CRDO", rows, [])
    수상한행 = next(r for r in rows if r["filing_date"] == "2024-11-02")
    assert 수상한행["op_income"] == 8_256, (
        "XBRL 자가 못 정한 행을 종목 배수로 덮어썼습니다: "
        f"{수상한행['op_income']}")


# ---------------------------------------------------------------------------
# 이웃과 자릿수가 어긋난 매출 — 고치거나 버린다 (150차-Y)
# ---------------------------------------------------------------------------
def _매출행(끝, 매출):
    return {"filing_date": 끝, "period_label": 끝[:7], "revenue": 매출}


def test_자릿수_어긋난_매출을_이웃으로_고친다():
    """실물 HD 26 Q2 — 매출이 **47,861 달러**로 적혀 있었습니다.

    실제는 478억. XBRL 에 그 분기 매출이 없어 보도자료 값이 단위 없이
    들어왔습니다. 전수로 26행 · 15종목이 이런 상태였습니다.
    """
    rows = [_매출행("2025-05-04", 39_000_000_000.0),
            _매출행("2025-08-03", 43_000_000_000.0),
            _매출행("2025-11-02", 40_000_000_000.0),
            _매출행("2026-02-01", 39_500_000_000.0),
            _매출행("2026-08-18", 47_861.0)]          # ← 깨진 행
    메모 = []
    r = dataset._매출_자릿수_바로잡기("HD", rows, 메모)
    assert r == {"고침": 1, "버림": 0}, r
    깨졌던 = next(x for x in rows if x["filing_date"] == "2026-08-18")
    assert 깨졌던["revenue"] == 47_861_000_000.0, 깨졌던
    assert 깨졌던["revenue_scale_fixed"] == 1_000_000
    assert 메모 and "150차-Y" in 메모[0]


def test_어떤_단위로도_안_맞으면_버린다():
    """실물 MRK 16 Q3 매출 228 · MS 18 Q2 매출 1,000,000.

    어떤 배수를 곱해도 이웃 자릿수에 안 맞습니다. 지금처럼 두면
    **틀린 값이 조용히 통과합니다** — 없음이 안전합니다.
    """
    rows = [_매출행("2025-03-31", 10_000_000_000.0),
            _매출행("2025-06-30", 10_200_000_000.0),
            _매출행("2025-09-30", 228.0),             # ← 어떤 배수도 안 맞음
            _매출행("2025-12-31", 10_400_000_000.0),
            _매출행("2026-03-31", 10_100_000_000.0)]
    메모 = []
    r = dataset._매출_자릿수_바로잡기("MRK", rows, 메모)
    assert r == {"고침": 0, "버림": 1}, r
    깨진 = next(x for x in rows if x["filing_date"] == "2025-09-30")
    assert 깨진["revenue"] is None, 깨진
    assert 메모 and "없음 처리" in 메모[0]


def test_멀쩡한_매출은_건드리지_않는다():
    """빠르게 크는 회사(크리도: 13M → 437M)를 오해하면 안 됩니다."""
    rows = [_매출행("2024-04-27", 60_782_000.0),
            _매출행("2024-11-02", 72_034_000.0),
            _매출행("2025-02-01", 135_002_000.0),
            _매출행("2025-08-02", 223_074_000.0),
            _매출행("2026-05-02", 437_003_000.0)]
    앞 = [r["revenue"] for r in rows]
    assert dataset._매출_자릿수_바로잡기("CRDO", rows, []) == {"고침": 0, "버림": 0}
    assert [r["revenue"] for r in rows] == 앞, "멀쩡한 값을 건드렸습니다"


def test_견줄_성한_행이_모자라면_아무것도_안_한다():
    """(150차-Y 의 한계) 골드만삭스는 **모든 행**이 백만 단위로 들어와
    있어 견줄 성한 행이 없습니다. 그럴 때는 판단하지 않습니다 —
    지어내지 않는 쪽입니다. **미해결로 남깁니다.**
    """
    rows = [_매출행("2025-03-31", 10_707.0), _매출행("2025-06-30", 10_120.0),
            _매출행("2025-09-30", 10_115.0), _매출행("2026-03-31", 12_738.0)]
    앞 = [r["revenue"] for r in rows]
    assert dataset._매출_자릿수_바로잡기("GS", rows, []) == {"고침": 0, "버림": 0}
    assert [r["revenue"] for r in rows] == 앞


def test_빠르게_크는_회사를_전체_중앙값으로_오해하지_않는다():
    """(150차-Y) **가까운 분기**로 재는 이유입니다.

    전체 중앙값으로 재면, 100배 넘게 큰 회사의 **초창기 분기**가
    "자릿수가 깨졌다"로 몰려 버려집니다. 초창기 매출은 그때 이웃과
    자릿수가 맞으므로 멀쩡한 값입니다.
    """
    rows = [_매출행("2023-03-31", 5_000_000.0),
            _매출행("2023-06-30", 6_000_000.0),
            _매출행("2023-09-30", 8_000_000.0),
            _매출행("2025-03-31", 700_000_000.0),
            _매출행("2025-06-30", 800_000_000.0),
            _매출행("2025-09-30", 900_000_000.0),
            _매출행("2025-12-31", 1_000_000_000.0)]
    앞 = [r["revenue"] for r in rows]
    r = dataset._매출_자릿수_바로잡기("ZZGROW", rows, [])
    assert r == {"고침": 0, "버림": 0}, (
        f"빠르게 크는 회사의 초창기 분기를 건드렸습니다: {r}")
    assert [x["revenue"] for x in rows] == 앞


def test_매출_배수도_둘_이상_맞을_수_없다():
    """'딱 하나만 맞을 때 고친다'가 **구조적으로** 지켜지는지 봅니다.

    띠는 중앙값의 1/3 ~ 3배(폭 9배)이고 배수 후보는 1,000배씩
    떨어져 있으므로 둘이 동시에 들어올 수 없습니다. 이 성질이 깨지면
    위 시험들이 조용히 헛돌기 시작합니다.
    """
    for 중앙 in (1e7, 1.3e10, 4.4e8):
        for v in (228.0, 3_414.0, 47_861.0, 1_000_000.0, 15_520.0):
            맞는 = [s for s in (1_000, 1_000_000, 1_000_000_000)
                   if 중앙 / 3.0 <= v * s <= 중앙 * 3.0]
            assert len(맞는) <= 1, (
                f"중앙 {중앙:,.0f} · 값 {v:,.0f} 에서 배수가 {맞는} 로 여럿입니다")


# ---------------------------------------------------------------------------
# 4분기 자리에 들어온 연간값 — 독립된 자로 가리기 (150차-AO)
# ---------------------------------------------------------------------------

def _연말행(분기끝, gaap, 연간, adj=None, ebitda=None):
    return {"filing_date": 분기끝, "announced_date": None,
            "period_label": 분기끝[:7], "gaap_eps": gaap,
            "gaap_eps_annual_xbrl": 연간, "adj_eps": adj,
            "adjusted_ebitda": ebitda, "revenue": 1.0e9}


def test_4분기_자리의_연간값을_XBRL_연간과_대조해_버린다():
    """실물 SHW — 4분기 자리에 **연간값**이 들어와 있었습니다 (150차-AD).

    앞선 검사(`_drop_cumulative_values`)는 "직전 4분기 합"을 기준으로 쓰는데
    그 합이 이미 오염되면 무력해집니다. 150차-AK 에서 회계 항등식으로
    풀어 보려다 **원리적으로 안 된다**는 것을 실측으로 확인했고, 가르려면
    **식 밖에서 온 값**이 필요했습니다 — XBRL 의 12개월 GAAP EPS 입니다.

    같은 행의 조정 EPS·조정 EBITDA 도 함께 버립니다. GAAP 이 연간값으로
    확증되면 그 행의 다른 잣대도 **같은 표에서 온** 연간값입니다.
    """
    rows = [_연말행("2024-12-31", 10.55, 10.55, adj=11.33, ebitda=5.0e8)]
    notes = []
    dataset._연간값이_4분기_자리에_있으면_버린다("SHW", rows, notes)
    assert rows[0]["gaap_eps"] is None, rows[0]
    assert rows[0]["adj_eps"] is None, "같은 표에서 온 조정 EPS 도 버려야 합니다"
    assert rows[0]["adjusted_ebitda"] is None, rows[0]
    assert notes and "150차-AO" in notes[0], notes


def test_진짜_4분기값은_남긴다():
    """4분기 값이 연간값과 **다르면** 그것은 진짜 분기값입니다.

    150차-AK 의 항등식 규칙은 이것을 못 가려 AMZN 2017(성수기) · TSLA 2023
    (일회성 세금 환입) · SNAP 2024(광고업)의 진짜 값을 죽였고, 그래서
    버렸습니다. 이 자는 가릅니다 — 그것이 채택 근거입니다.
    """
    실물 = [
        ("AMZN", 3.75, 6.15),    # 연말 성수기
        ("TSLA", 2.27, 4.30),    # 일회성 이연법인세 환입
        ("SNAP", 0.01, -0.42),   # 광고업 4분기
    ]
    for 종목, 분기, 연간 in 실물:
        rows = [_연말행("2024-12-31", 분기, 연간, adj=0.5)]
        dataset._연간값이_4분기_자리에_있으면_버린다(종목, rows, [])
        assert rows[0]["gaap_eps"] == 분기, f"{종목} 진짜 4분기값이 죽었습니다"
        assert rows[0]["adj_eps"] == 0.5, 종목


def test_자가_없으면_손대지_않는다():
    """XBRL 연간값이 없는 행(결산일이 아닌 분기)은 판단할 수 없습니다.

    자가 없는데 버리면 그것은 재지 않고 버리는 것입니다 (헌법 1조).
    """
    rows = [_연말행("2024-09-30", 10.55, None, adj=11.33)]
    dataset._연간값이_4분기_자리에_있으면_버린다("SHW", rows, [])
    assert rows[0]["gaap_eps"] == 10.55 and rows[0]["adj_eps"] == 11.33


def test_연간값_검사가_누적값_검사보다_먼저_돈다():
    """순서가 뒤바뀌면 **자로 쓸 값이 먼저 사라집니다.**

    누적값 검사는 GAAP EPS 를 곧잘 잡아 없음으로 만드는데, 그러면 연간값
    검사가 대조할 GAAP 이 없어 조정 EPS(연간값)가 그대로 살아남습니다.
    150차-AD 가 "GAAP 은 잡히는데 조정 EPS 만 빠져나간다"고 적은 그 현상의
    정체가 이 순서였습니다 — 실측으로 확인하고 순서를 바꿨습니다.
    """
    소스 = pathlib.Path(dataset.__file__).read_text()
    연간자리 = 소스.find("_연간값이_4분기_자리에_있으면_버린다(ticker, kept, notes)")
    누적자리 = 소스.find("_drop_cumulative_values(ticker, kept, notes)")
    assert 연간자리 != -1 and 누적자리 != -1, "두 검사 호출을 못 찾았습니다"
    assert 연간자리 < 누적자리, (
        "연간값 검사가 누적값 검사보다 **뒤에** 있습니다 — 자로 쓸 GAAP 이 "
        "먼저 지워져 조정 EPS 의 연간값을 못 잡습니다")


# ---------------------------------------------------------------------------
# 161차 — 빠르게 자라는 회사를 골라 죽이던 덫
# ---------------------------------------------------------------------------

def _성장행(i, value, revenue, **더):
    """분기마다 대략 두 배씩 자라는 회사의 행. 매출은 XBRL 과 같게 둡니다.

    영업이익은 매출에 비례시킵니다 — 고정값으로 두면 매출이 커질수록
    마진이 0에 수렴해 **다른 검사**(단위 착오)가 울립니다.
    """
    row = quarter_row(
        filing_date=f"{2024 + i // 4}-{(i % 4) * 3 + 1:02d}-28",
        announced_date=f"{2024 + i // 4}-{(i % 4) * 3 + 2:02d}-28",
        period_label=f"Q{i}", adj_eps=value, gaap_eps=round(value * 0.75, 4),
        revenue=revenue, revenue_xbrl=revenue, op_income=revenue * 0.2)
    row.update(더)
    return row


def test_빠르게_자란_분기는_XBRL로_확인되면_살아남는다():
    """161차 — 실물 CRDO. 조정 EPS 0.25·0.52·1.07·1.16 이 **수집은 제대로
    됐는데** 정제기가 "연간 누적값"으로 오인해 지웠고, 그 탓에 CRDO 는
    2024-12 이후 줄곧 판단 불가였다(주인이 보는 화면에서 통째로 빠짐).

    산수로는 가릴 수 없다: 이익이 분기마다 비율 r 로 자라면 직전 4분기
    합은 v·(1/r+1/r²+1/r³+1/r⁴) 이고, r ≈ 2 에서 그 값이 v 와 거의 같다.
    그래서 **식 밖에서 온 자**(XBRL 3개월 매출)로 분기 행임을 확인한다.
    """
    값들 = [0.07, 0.13, 0.25, 0.50, 1.00, 2.00]      # 분기마다 두 배
    매출 = [60e6, 110e6, 210e6, 410e6, 800e6, 1600e6]
    rows = [_성장행(i, v, r) for i, (v, r) in enumerate(zip(값들, 매출))]
    result = dataset.build(make_snapshot(eps={"AAA": rows}))
    kept = [r["adj_eps"] for r in result["quarters"]["AAA"]]
    assert kept == 값들, f"빠르게 자란 진짜 분기 값이 지워졌습니다: {kept}"
    assert not [n for n in result["notes"] if "누적" in n], result["notes"]


def test_매출은_분기인데_EPS가_연간이면_면제하지_않는다():
    """161차 — 매출 일치만으로 면제하면 **진짜 연간값**을 살려 둔다.

    실측으로 "매출은 분기인데 GAAP EPS 는 XBRL 연간값과 같은" 행이
    **157개** 있었다(NXPI 23·24·25 Q4 · SWKS 21·22 Q4 등). 그래서 면제
    조건에 "XBRL 연간 EPS 와 같지 않을 것"을 함께 건다.

    ⚠️ 이 조건은 **함수를 직접 불러** 확인한다. 통합으로 확인하려 했더니
    앞단의 `_연간값이_4분기_자리에_있으면_버린다` 가 먼저 잡아 버려서,
    정작 이 조건이 일하는지는 드러나지 않았다(시험이 헛돌 뻔했다).
    """
    분기행 = {"revenue": 100e6, "revenue_xbrl": 100e6,
             "gaap_eps": 0.80, "gaap_eps_annual_xbrl": 2.51}
    assert dataset._분기행임이_XBRL로_확인됨(분기행) is True, 분기행

    연간행 = dict(분기행, gaap_eps=2.51)      # GAAP 이 XBRL 연간값과 같다
    assert dataset._분기행임이_XBRL로_확인됨(연간행) is False, 연간행

    # 매출이 XBRL 3개월 값과 다르면 애초에 분기 행이 아니다
    누적행 = dict(분기행, revenue=380e6)
    assert dataset._분기행임이_XBRL로_확인됨(누적행) is False, 누적행

    # 견줄 XBRL 값이 없으면 "확인 못 함" — 면제하지 않는다
    assert dataset._분기행임이_XBRL로_확인됨(
        {"revenue": 100e6, "gaap_eps": 0.80}) is False

    # 1~3분기는 XBRL 연간 칸이 원래 없다 — 그때는 매출 자만으로 통과
    assert dataset._분기행임이_XBRL로_확인됨(
        {"revenue": 100e6, "revenue_xbrl": 100e6, "gaap_eps": 0.80}) is True


def test_XBRL_자가_없으면_예전처럼_버린다():
    """161차 — 확인할 자가 하나도 없으면 버린다. "없음"이 "틀림"보다
    안전하다는 원칙은 그대로다(헌법 1조). 52차·73차가 막던 사고가
    되돌아오면 안 된다."""
    값들 = [0.07, 0.13, 0.25, 0.50, 1.00, 2.00]
    매출 = [60e6, 110e6, 210e6, 410e6, 800e6, 1600e6]
    rows = [_성장행(i, v, r) for i, (v, r) in enumerate(zip(값들, 매출))]
    for row in rows:
        row.pop("revenue_xbrl", None)          # XBRL 자를 통째로 없앤다
    kept = [r["adj_eps"] for r in
            dataset.build(make_snapshot(eps={"AAA": rows}))["quarters"]["AAA"]]
    assert None in kept, f"확인할 자가 없는데도 전부 살렸습니다: {kept}"


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
