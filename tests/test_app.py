"""
test_app.py — 계기판 도우미 검증 · v3 6단계
============================================

계기판의 순수 도우미(화면 없이 계산되는 부분)를 검사합니다:
  · v2 유물 verdict 를 새 판정으로 착각하지 않는가
  · "채택된 신호 없음"이 정직하게 나오는가
  · 정직화 문구에 과거 실측·기준선·판정 상태가 들어가는가
  · 최근 발표 표가 사실만 담는가

실행: python3 tests/test_app.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import app  # noqa: E402


def v3_verdict(judgment="미채택"):
    entry = {
        "신규(판정)": {
            "신호": {"n": 49, "rate": 34.7, "ci": [22.9, 48.7]},
            "기준선": {"n": 662, "rate": 19.0, "ci": [16.2, 22.2]},
            "판정": judgment,
        },
        "판정": judgment,
    }
    return {
        "computed_at": "2026-08-14T09:40:00+00:00",
        "가설": {name: dict(entry) for name in app.V3_HYPOTHESES},
    }


def test_v2_relic_is_detected():
    """v2 유물(가설 이름이 다름)은 새 판정으로 인정하면 안 됩니다."""
    relic = {"computed_at": "x", "가설": {"H2_신고점": {}, "H5_실적폭_ON": {}}}
    assert not app.verdict_is_v3(relic)
    assert not app.verdict_is_v3(None)
    assert app.verdict_is_v3(v3_verdict())


def test_no_adopted_signals_reported_honestly():
    assert app.adopted_names(v3_verdict("미채택")) == []
    adopted = app.adopted_names(v3_verdict("채택"))
    assert len(adopted) == len(app.V3_HYPOTHESES)


def test_honesty_note_contains_measured_numbers():
    """정직화 문구: 과거 실측 %·기준선 %·판정 상태가 모두 들어가야 합니다."""
    note = app.hypothesis_note(v3_verdict("미채택"), "H2b_신고점_첫돌파")
    assert "34.7%" in note and "19.0%" in note and "미채택" in note, note


def test_honesty_note_without_verdict():
    assert "판정 대기" in app.hypothesis_note(None, "H2b_신고점_첫돌파")


def test_recent_rows_report_facts_only():
    """최근 발표 표: 첫 돌파/연속/아님/판단 불가를 사실로 구분합니다."""
    def rows(eps_list, start="2024-01-31"):
        from datetime import date, timedelta
        day = date.fromisoformat(start)
        out = []
        for eps in eps_list:
            out.append({"filing_date": day.isoformat(),
                        "announced_date": day.isoformat(),
                        "period_label": str(day)[:7], "adj_eps": eps})
            day += timedelta(days=91)
        return out

    quarters = {
        "NEW1": rows([1, 1, 1, 1, 2]),          # 마지막 발표 = 첫 돌파
        "FLAT": rows([1, 1, 1, 1, 1]),          # 마지막 발표 = 신고점 아님
        "YOUNG": rows([1, 1]),                  # TTM 못 채움 = 판단 불가
    }
    dates = [r["announced_date"] for r in rows([0] * 5)]
    ds = {
        "benchmark": "SPY",
        "tickers": list(quarters),
        "quarters": quarters,
        "prices": {"SPY": {"dates": [dates[-1]], "close": [500.0]}},
    }
    result = {r["종목"]: r["상태"] for r in app.recent_ticker_rows(ds, today=dates[-1])}
    assert result["NEW1"] == "신고점 첫 돌파", result
    assert result["FLAT"] == "신고점 아님", result
    # YOUNG 의 마지막 발표는 45일 밖(2024-05-01)이라 표에 없어야 합니다
    assert "YOUNG" not in result, result


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
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
    print(f"\n계기판 도우미 검증: {passed}개 통과, {failed}개 실패")
    sys.exit(1 if failed else 0)
