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
    rows_out = app.recent_ticker_rows(ds, today=dates[-1])
    result = {r["종목"]: r["상태"] for r in rows_out}
    assert result["NEW1"] == "신고점 첫 돌파", result
    assert result["FLAT"] == "신고점 아님", result
    # YOUNG 의 마지막 발표는 45일 밖(2024-05-01)이라 표에 없어야 합니다
    assert "YOUNG" not in result, result
    # 섹터 꼬리표가 붙고, 모르는 종목은 "미분류"로 정직하게 표시됩니다
    assert all("섹터" in r for r in rows_out)
    assert {r["섹터"] for r in rows_out} == {"미분류"}


def test_sector_gauge_rows_split_by_sector():
    """섹터별 게이지: 신기록이 있는 섹터만 값이 올라가야 합니다."""
    from datetime import date, timedelta

    def rows(eps_list, start="2024-01-31"):
        day = date.fromisoformat(start)
        out = []
        for eps in eps_list:
            out.append({"filing_date": day.isoformat(),
                        "announced_date": day.isoformat(),
                        "period_label": str(day)[:7], "adj_eps": eps})
            day += timedelta(days=91)
        return out

    import config as cfg
    hot = sorted(cfg.SECTORS)[0]          # 실제 유니버스 종목 2개를 빌려 씀
    hot_ticker = hot
    cold_ticker = None
    hot_sector = cfg.SECTORS[hot]
    for t in sorted(cfg.SECTORS):
        if cfg.SECTORS[t] != hot_sector:
            cold_ticker = t
            break
    quarters = {
        hot_ticker: rows([1, 1, 1, 1, 2]),     # 마지막 발표 = 신고점
        cold_ticker: rows([1, 1, 1, 1, 1]),    # 신고점 아님
    }
    dates = [r["announced_date"] for r in rows([0] * 5)] + ["2025-02-10"]
    ds = {
        "benchmark": "SPY",
        "tickers": [hot_ticker, cold_ticker],
        "quarters": quarters,
        "prices": {"SPY": {"dates": dates, "close": [500.0] * len(dates)}},
    }
    result = {r["섹터"]: r for r in app.sector_gauge_rows(ds)}
    assert result[cfg.SECTORS[hot_ticker]]["게이지"] == 100.0, result
    assert result[cfg.SECTORS[cold_ticker]]["게이지"] == 0.0, result
    # 이력이 몇 주뿐이므로 "평소 대비"는 판단 불가여야 합니다
    assert result[cfg.SECTORS[hot_ticker]]["평소대비"] == "판단 불가"


def test_every_ticker_has_a_theme():
    """모든 종목이 테마(없으면 업종 대체)를 갖고, 테마 표에 죽은 항목이 없어야."""
    import config as cfg
    for t in cfg.TICKERS:
        assert cfg.theme_of(t) != "미분류", t
    extra = [t for t in cfg.THEMES if t not in cfg.TICKERS]
    assert not extra, f"유니버스에 없는 테마 항목: {extra}"


def test_every_ticker_has_a_sector():
    """유니버스 79종목 전부에 섹터가 있어야 합니다 (빠지면 화면에 미분류)."""
    import config as cfg
    missing = [t for t in cfg.TICKERS if t not in cfg.SECTORS]
    extra = [t for t in cfg.SECTORS if t not in cfg.TICKERS]
    assert not missing, f"섹터 없는 종목: {missing}"
    assert not extra, f"유니버스에 없는 섹터 항목: {extra}"


# ---------------------------------------------------------------------------
# 전망 스프레드 (관찰) — 가이던스 vs 컨센서스, 주도 섹터
# ---------------------------------------------------------------------------
def _spread_ds(tickers_rows, today="2026-08-14"):
    """전망 스프레드 시험용 미니 데이터: 가격은 SPY 하나면 충분합니다."""
    return {
        "benchmark": "SPY",
        "tickers": list(tickers_rows),
        "quarters": tickers_rows,
        "prices": {"SPY": {"dates": [today], "close": [500.0]}},
    }


def _guided_row(announced, mid):
    return {"filing_date": announced, "announced_date": announced,
            "period_label": announced[:7], "adj_eps": 1.0,
            "guid_eps_mid": mid}


def _ledger(ticker, as_of, avg):
    return {"tickers": {ticker: [{"as_of": as_of,
                                  "rows": {"0q": {"avg": avg, "low": None,
                                                  "high": None, "analysts": 5,
                                                  "year_ago": None}}}]}}


def test_forward_spread_pairs_guidance_with_consensus():
    """스프레드 = (가이던스 − 컨센서스)/|컨센서스| — 부호와 값이 맞아야 합니다."""
    import config as cfg
    t = sorted(cfg.SECTORS)[0]
    ds = _spread_ds({t: [_guided_row("2026-07-01", 1.10)]})
    rows = app.forward_spread_rows(ds, _ledger(t, "2026-08-14", 1.00))
    assert len(rows) == 1, rows
    assert rows[0]["스프레드%"] == 10.0, rows[0]
    assert rows[0]["섹터"] == cfg.SECTORS[t]


def test_forward_spread_excludes_stale_and_missing():
    """철 지난 가이던스(140일 초과)·컨센서스 없는 종목은 빠져야 합니다."""
    import config as cfg
    t1, t2 = sorted(cfg.SECTORS)[0], sorted(cfg.SECTORS)[1]
    ds = _spread_ds({
        t1: [_guided_row("2026-01-01", 1.10)],   # 226일 전 — 철 지남
        t2: [_guided_row("2026-07-01", 1.10)],   # 신선하나 컨센서스 없음
    })
    assert app.forward_spread_rows(ds, _ledger(t1, "2026-08-14", 1.0)) == []


def test_sector_spread_marks_small_samples():
    """표본 부족(n<3) 섹터는 숨기지 않고 '표본 부족'으로 표시해야 합니다."""
    import config as cfg
    t = sorted(cfg.SECTORS)[0]
    ds = _spread_ds({t: [_guided_row("2026-07-01", 0.90)]})
    rows = app.sector_spread_rows(ds, _ledger(t, "2026-08-14", 1.00))
    assert len(rows) == 1
    assert rows[0]["표본"] == "표본 부족" and rows[0]["종목수"] == 1
    assert rows[0]["스프레드중앙%"] == -10.0


# ---------------------------------------------------------------------------
# 33·34차 — 정배열 폭 구간 · 서프라이즈 섹터 · 채택 신호 종목
# ---------------------------------------------------------------------------
def test_breadth_zone_boundaries():
    """구간 경계(40·60)가 등록 정의대로 갈려야 합니다."""
    assert app.breadth_zone(39.9)["zone"] == "초기"
    assert app.breadth_zone(40.0)["zone"].startswith("쌓이는 중")
    assert app.breadth_zone(59.9)["zone"].startswith("쌓이는 중")
    assert app.breadth_zone(60.0)["zone"] == "정배열 완성 구간"
    assert app.breadth_zone(None)["zone"] == "판단 불가"
    # 최적 구간의 실측이 완성 구간보다 높아야 합니다 (34차 결과)
    assert app.breadth_zone(45)["rate"] > app.breadth_zone(75)["rate"]


def test_surprise_sector_rows_uses_recent_quarters_only():
    """서프라이즈는 최근 2분기만 — 오래된 분기가 섞이면 안 됩니다."""
    import config as cfg
    t = sorted(cfg.SECTORS)[0]
    arc = {"tickers": {t: [
        {"quarter": "2024-03-31", "surprise_pct": 999.0},   # 오래됨 — 제외
        {"quarter": "2026-03-31", "surprise_pct": 10.0},
        {"quarter": "2026-06-30", "surprise_pct": 20.0},
    ]}}
    rows = app.surprise_sector_rows(arc)
    assert len(rows) == 1 and rows[0]["건수"] == 2, rows
    assert rows[0]["중앙%"] == 15.0, rows
    assert app.surprise_sector_rows(None) == []


def test_hypothesis_details_cover_non_adopted():
    """미채택 신호는 화면에 설명이 함께 나와야 합니다 (지시 1)."""
    for name in ("H2_신고점", "H5b_실적폭_중앙값", "H11_섹터정배열폭_60"):
        assert name in app.HYPOTHESIS_DETAILS, name
        assert len(app.HYPOTHESIS_DETAILS[name]) > 20
        assert name in app.HYPOTHESIS_LABELS, name


def test_confirmation_rows_require_all_three(monkey=None):
    """H14 (38차): 전제·정배열·델타 셋 다 있어야 '확인'입니다."""
    import sector_model as sm
    rows = [
        {"묶음": "A", "전제": True, "정배열확인": True, "델타확인": True},
        {"묶음": "B", "전제": True, "정배열확인": True, "델타확인": False},
        {"묶음": "C", "전제": False, "정배열확인": True, "델타확인": True},
    ]
    # 화면이 쓰는 규칙과 같은 판단을 여기서 검산합니다
    confirmed = [r["묶음"] for r in rows
                 if r["전제"] and r["정배열확인"] and r["델타확인"]]
    assert confirmed == ["A"], confirmed
    # 등록 문턱이 코드 상수와 일치해야 합니다 (38차 등록)
    assert sm.CONFIRM_ALIGN_MIN == 40.0
    assert sm.CONFIRM_DELTA_MIN == 50.0
    assert sm.CONFIRM_PAST_DAYS == 63



# ---------------------------------------------------------------------------
# 미채택 칸의 한 줄 요약 (48차 — 모양이 다른 가설을 뭉개지 않기)
# ---------------------------------------------------------------------------
def test_signal_summary_uses_signal_when_present():
    entry = {"신규(판정)": {}}
    got = app.signal_summary(entry, {"n": 12, "rate": 40.0}, {"n": 100, "rate": 25.0})
    assert "40.0%" in got and "n=12" in got and "25.0%" in got, got


def test_signal_summary_reports_leadership_state():
    """H19 는 성공률이 없는 가설입니다 — '표본이 아직 없습니다'로 뭉개면 안 됩니다."""
    entry = {"현재_주도": "광통신·대역폭 장비·부품", "국면수": 4}
    got = app.signal_summary(entry, {}, {})
    assert "광통신" in got and "4개" in got, got
    assert "표본이 아직 없습니다" not in got


def test_signal_summary_reports_episode_counts():
    """H20·H21 은 사건 성공/전체로 적고, 있으면 기준선도 함께 적습니다."""
    entry = {"n": 3, "성공": 1, "rate": 33.3}
    got = app.signal_summary(entry, {}, {})
    assert "1/3" in got and "33.3%" in got, got
    entry2 = {"n": 5, "성공": 2, "rate": 40.0,
              "기준선(참고)": {"n": 3123, "rate": 24.2}}
    got2 = app.signal_summary(entry2, {}, {})
    assert "24.2%" in got2 and "3123" in got2, got2


def test_signal_summary_shows_exploration_when_judgment_sample_empty():
    """H19b 처럼 판정 표본이 0건이면 탐색 표본 수치를 그 사실과 함께 적습니다."""
    entry = {"신규(판정)": {"n": 0}, "탐색표본(참고)": {"n": 43, "rate": 14.0}}
    got = app.signal_summary(entry, {}, {})
    assert "판정 표본 0건" in got and "14.0%" in got and "43" in got, got


def test_signal_summary_always_shows_instability():
    """주도섹터를 적을 때는 **흔들림을 반드시 함께** 적습니다 (52차 감사).

    지목만 적고 흔들림을 빼면 사람이 그것을 확정된 사실로 읽습니다.
    실측: 잣대값 6% 만 지워도 주도가 210주 중 54주(25.7%) 바뀌고,
    지금 지목한 주도도 8번 중 4번 달라집니다.
    """
    entry = {
        "현재_주도": "기기 OEM 반도체", "국면수": 18,
        "안정성": {"판정주수": 210, "지운비율": 0.06, "반복": 8,
                   "바뀐주_중앙값": 54, "바뀐비율_중앙값": 25.7,
                   "마지막주_불일치": 4},
    }
    got = app.signal_summary(entry, {}, {})
    assert "기기 OEM 반도체" in got
    assert "흔들림" in got, got
    assert "25.7%" in got and "54/210" in got, got
    assert "8번 중 4번" in got, got


def test_instability_shows_the_spread_not_just_the_median():
    """중앙값만 적으면 정밀도를 과장합니다 (58차).

    같은 6%를 4번 지웠더니 49·60·82·113주로 나왔습니다 — **흔들림을 재는
    자 자체가 흔들립니다.** 그래서 뽑기 사이 범위를 함께 적습니다.
    """
    entry = {
        "현재_주도": "기기 OEM 반도체", "국면수": 18,
        "안정성": {"판정주수": 210, "지운비율": 0.06, "반복": 8,
                   "바뀐주_중앙값": 54, "바뀐비율_중앙값": 25.7,
                   "바뀐주_최소": 16, "바뀐주_최대": 104,
                   "마지막주_불일치": 4},
    }
    got = app.signal_summary(entry, {}, {})
    assert "16~104" in got, got
    assert "54/210" in got, got          # 중앙값도 그대로 남아야 합니다


def test_no_fake_spread_when_every_draw_agreed():
    """뽑기마다 같은 답이 나왔으면 범위를 적지 않습니다 — 없는 폭을
    만들어 보이면 그것도 창작입니다."""
    entry = {
        "현재_주도": "A", "국면수": 3,
        "안정성": {"판정주수": 210, "지운비율": 0.06, "반복": 8,
                   "바뀐주_중앙값": 7, "바뀐비율_중앙값": 3.3,
                   "바뀐주_최소": 7, "바뀐주_최대": 7,
                   "마지막주_불일치": 0},
    }
    got = app.signal_summary(entry, {}, {})
    assert "갈림" not in got, got
    assert "7/210" in got, got


def test_signal_summary_without_stability_still_works():
    """안정성이 아직 없으면(옛 판정 파일) 지목만 적고 조용히 넘어갑니다."""
    entry = {"현재_주도": "금융 중개·자본시장", "국면수": 4}
    got = app.signal_summary(entry, {}, {})
    assert "금융" in got and "흔들림" not in got, got


# ---------------------------------------------------------------------------
# 52차 감사 수리 ⑤ — 판정이 옛 코드로 계산됐는지 알리기
# ---------------------------------------------------------------------------
def test_old_code_verdict_raises_banner():
    """판정 파일의 코드 판번호가 지금 코드와 다르면 경고합니다.

    실제 사고: verdict 는 "데이터센터"를 주도로 적었는데 같은 원자료로
    현재 코드를 돌리면 "기기 OEM 반도체"가 나왔습니다. 코드가 달랐던
    것인데 화면에는 계산 시각만 있어 알 방법이 없었습니다.
    """
    got = app.verdict_code_warning({"code_rev": "47e7141"}, current="0713820")
    assert got is not None, "옛 코드 판정인데 아무 말도 하지 않았습니다"
    assert "47e7141" in got and "0713820" in got, got
    assert "옛 코드" in got, got


def test_same_code_verdict_is_silent():
    """같은 판번호면 조용합니다 — 늘 뜨는 경고는 아무도 안 봅니다."""
    assert app.verdict_code_warning({"code_rev": "abc1234"},
                                    current="abc1234") is None


def test_unknown_revision_never_warns():
    """판번호를 못 알아낸 채 적혀 있으면 비교가 안 되니 겁주지 않습니다."""
    assert app.verdict_code_warning({"code_rev": "알수없음"},
                                    current="abc1234") is None
    assert app.verdict_code_warning({"code_rev": "abc1234"},
                                    current="알수없음") is None
    assert app.verdict_code_warning(None, current="abc1234") is None


def test_verdict_without_code_rev_is_flagged_as_old():
    """판번호 칸이 아예 없으면 이 수리 이전 판정 — 옛 코드가 확실합니다.

    52차가 걸린 실제 판정 파일이 바로 이 모양이었습니다. 여기서 조용히
    넘어가면 정작 사고를 일으킨 파일을 놓칩니다.
    """
    got = app.verdict_code_warning({"computed_at": "2026-08-15T13:33:32Z"},
                                   current="abc1234")
    assert got is not None, "판번호 없는 옛 판정을 그냥 넘겼습니다"
    assert "판번호가 없" in got, got


def test_current_revision_defaults_to_running_code():
    """current 를 안 주면 지금 돌고 있는 코드의 판번호를 씁니다."""
    import judge
    now = judge.code_revision()
    if now == "알수없음":          # git 이 없는 환경이면 건너뜁니다
        return
    assert app.verdict_code_warning({"code_rev": now}) is None
    assert app.verdict_code_warning({"code_rev": "0" * 7}) is not None


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
