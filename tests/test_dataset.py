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
