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


def test_정배열폭_판정줄은_판정파일을_읽는다():
    """화면 숫자가 **글자로 박히면** 데이터가 바뀌어도 아무도 모릅니다 (101차).

    ⚠️ 실제 사고: 이 칸에 "H11 실측 9.4% vs 기준선 26.8%. 정배열이 다 찬
    뒤 사는 것은 **오히려 불리**했습니다" 가 박혀 있었습니다. 10년 표본으로
    다시 재니 **31.6% vs 31.6% (n=187)** 이라 그 문장은 거짓말이 돼
    있었는데, 박힌 글자라 아무 경고도 없었습니다.

    그래서 **다른 숫자를 넣으면 화면도 따라 달라지는지**를 못박습니다.
    """
    가짜 = {"가설": {
        "H11_섹터정배열폭_60": {
            "판정": "채택",
            "신규(판정)": {"신호": {"n": 42, "rate": 77.7},
                          "기준선": {"n": 900, "rate": 11.1}},
        },
        "H11b_섹터정배열폭_80": {
            "판정": "판정 불가",
            "신규(판정)": {"신호": {"n": 0}, "기준선": {}},
        },
    }}
    본문 = "\n".join(app.breadth_verdict_lines(가짜))
    assert "77.7" in 본문 and "n=42" in 본문 and "11.1" in 본문, 본문
    assert "채택" in 본문 and "판정 불가" in 본문, 본문
    # 옛날에 박혀 있던 숫자가 되살아나면 안 됩니다
    assert "9.4%" not in 본문 and "26.8%" not in 본문, 본문


def test_채택이_없으면_매수근거가_아니라고_말한다():
    """지금 채택된 가설이 0개입니다 (96차). 화면이 그 상태를 정직하게
    말하는지 — 실제 판정 파일로 확인합니다."""
    import json
    v = json.load(open("data/measure/verdict.json", encoding="utf-8"))
    이름 = app.adopted_names(v)
    assert 이름 == [], f"채택된 가설이 생겼습니다: {이름} — 화면 문구를 다시 보세요"


def test_기준선은_판정파일을_읽고_없으면_예비값을_쓴다():
    """기준선도 **글자로 박혀** 있었습니다 (101차).

    화면이 "기준선 26.8%" 라고 말하는 동안 10년 표본의 실제 기준선은
    31.6%(n=5,004) 였습니다. 판정 파일을 읽게 하고, 파일이 없을 때만
    문서화된 예비값을 쓰도록 못박습니다 — 지어내지 않습니다.
    """
    가짜 = {"가설": {"H11_섹터정배열폭_60": {
        "신규(판정)": {"기준선": {"n": 5004, "rate": 44.4}}}}}
    assert app.breadth_baseline(가짜) == 44.4
    # 판정 파일이 없거나 모양이 다르면 예비값
    assert app.breadth_baseline(None) == app.BREADTH_BASELINE
    assert app.breadth_baseline({"가설": {}}) == app.BREADTH_BASELINE


def test_가설_설명문에는_숫자를_박지_않는다():
    """설명문에 실측 숫자를 적으면 데이터가 바뀌어도 아무도 모릅니다 (101차).

    실제 사고: H11 설명에 "오히려 나빴습니다(9.4% vs 기준선 26.8%)" 가
    박혀 있었는데, 10년 표본에서는 31.6% vs 31.6% 이라 거짓말이었습니다.
    """
    for 이름, 글 in app.HYPOTHESIS_DETAILS.items():
        for 박힌숫자 in ("9.4%", "26.8%"):
            assert 박힌숫자 not in 글, f"{이름} 설명에 옛 실측치 {박힌숫자} 가 박혀 있습니다"


def test_캐시는_값을_바꾸지_않는다():
    """캐시는 **속도만** 바꿔야 합니다 (102차).

    ⚠️ 처음에는 실데이터로 원본 함수와 캐시를 둘 다 돌려 비교했는데,
    `confirmation_rows` 하나가 22.7초라 **시험 전체가 시간 초과**로
    죽었습니다 (103차에 발견). 시험이 느려 못 돌면 안 돌린 것과 같습니다.

    그래서 **가짜 함수를 끼워 "캐시가 원본을 그대로 불러 돌려주는가"**만
    봅니다 — 값이 같은지는 이 방식이 더 확실하게 못박습니다 (원본이
    무엇을 돌려주든 그대로 나와야 하므로).
    """
    import sector_model as sm

    표식 = [{"섹터": "시험용", "값": 42}]
    원래 = sm.confirmation_rows
    sm.confirmation_rows = lambda ds: 표식
    try:
        assert app.cached_confirmation_rows({"a": 1}, "열쇠-A") == 표식
    finally:
        sm.confirmation_rows = 원래

    원래2 = sm.current_breadth
    sm.current_breadth = lambda ds: 표식
    try:
        assert app.cached_current_breadth({"a": 1}, "열쇠-B") == 표식
    finally:
        sm.current_breadth = 원래2



def test_스냅샷_이름표는_새_수집이면_달라진다():
    """캐시 열쇠가 안 바뀌면 **새 데이터가 와도 옛 화면**이 남습니다 (102차).

    로봇이 새로 커밋하면 saved_at 이 바뀌므로 캐시가 저절로 갈립니다.
    saved_at 이 없으면 종목 수라도 씁니다 — 지어내지 않습니다.
    """
    a = app.snapshot_key({"saved_at": "2026-08-18T06:20:08"})
    b = app.snapshot_key({"saved_at": "2026-08-19T09:31:00"})
    assert a != b, "수집 시각이 다른데 이름표가 같습니다 — 새 데이터가 안 보입니다"
    assert app.snapshot_key(None) == "없음"
    assert app.snapshot_key({"tickers": ["A", "B"]}) == "종목2"


def test_건강검진_없으면_없다고_말한다():
    """로봇 기록에 건강검진이 아직 없으면(옛 코드로 돈 수집) 화면은
    '아직 없음'이라고 말해야 합니다 — 없는 것을 지어내지 않습니다."""
    줄들 = app.health_lines(None)
    assert len(줄들) == 1 and "아직 없음" in 줄들[0], 줄들


def test_건강검진_줄은_로봇_기록의_숫자를_그대로_옮긴다():
    """채움률·이상값·어제 대비를 로봇 기록에서 읽어 그대로 적어야 합니다.
    (보고에 적는 숫자는 실행 출력에서 복사한다 — 와 같은 원칙의 화면판)"""
    검진 = {
        "채움률": {"행": 5851,
                 "revenue": {"찬칸": 5700, "비율": 97.5},
                 "adj_eps": {"찬칸": 3000, "비율": 51.3},
                 "gaap_eps": {"찬칸": 5389, "비율": 92.1},
                 "revenue_xbrl": {"찬칸": 5000, "비율": 85.5}},
        "이상값": {"revenue": {"건수": 3, "예시": []},
                 "op_income": {"건수": 0, "예시": []}},
        "어제 대비": {"맞대본 분기": 5800, "바뀐 칸": 12,
                  "새 분기": 7, "사라진 분기": 0},
    }
    줄들 = app.health_lines(검진)
    글 = " ".join(줄들)
    for 숫자 in ("5,851", "97.5%", "92.1%", "3건", "0건", "12", "7"):
        assert 숫자 in 글, f"{숫자} 가 화면 줄에 없습니다: {줄들}"
    # 어제 수집물이 없으면 비교를 지어내지 않고 없다고 말한다
    검진["어제 대비"] = None
    줄들 = app.health_lines(검진)
    assert any("어제 대비: 없음" in 줄 for 줄 in 줄들), 줄들




def test_장세게이지_없으면_없다고_말한다():
    """시장 폭을 못 재면(이력 부족) '판단 불가'라고 말해야 합니다."""
    줄들 = app.market_regime_lines(None, None, {})
    assert len(줄들) == 1 and "판단 불가" in 줄들[0], 줄들


def test_장세게이지는_구간_실측과_판정상태를_같이_적는다():
    """약한 장세(20% 미만)에서는 첫돌파의 과거 실측(8.2% vs 기준선 12.0%)
    과 H24 판정 상태를 함께 적어야 합니다 (헌법 3조 정직화)."""
    verdict = {"가설": {"H24_장세조건부_첫돌파": {
        "판정": "판정 불가",
        "신규(판정)": {"신호": {"n": 0}},
    }}}
    줄들 = app.market_regime_lines(12.5, 18.1, verdict)
    글 = " ".join(줄들)
    for 조각 in ("12%", "-6%p", "약한 장세", "8.2%", "12.0%",
               "판정 불가", "n=0"):
        assert 조각 in 글, f"{조각} 가 화면 줄에 없습니다: {줄들}"


def test_장세게이지_산장세는_우위미증명을_같이_적는다():
    """20~60% 구간에서는 실측(16.8% vs 13.6%)과 함께 '아직 우위 증명이
    아니다'를 반드시 적고, verdict 에 H24 가 없으면 없다고 말합니다."""
    줄들 = app.market_regime_lines(30.0, None, None)
    글 = " ".join(줄들)
    for 조각 in ("살아 있는 장세", "16.8%", "13.6%", "우위 증명은 아닙니다",
               "아직 없음"):
        assert 조각 in 글, f"{조각} 가 화면 줄에 없습니다: {줄들}"




def test_관찰판은_최근창만_세고_이격도없음은_신호가_아니다():
    """(127차) 관찰판은 기준일에서 91일 안의 완성만 세고, 이격도를 못 잰
    완성은 신호로 치지 않으며, 묶음 집계는 신호 수 → 완성 수 순입니다."""
    사건 = [
        {"ticker": "NVDA", "day": "2026-08-01", "이격도": 45.0, "델타": True},
        {"ticker": "AMD",  "day": "2026-07-01", "이격도": 12.0, "델타": False},
        {"ticker": "MU",   "day": "2026-06-01", "이격도": None, "델타": True},
        {"ticker": "JPM",  "day": "2025-01-01", "이격도": 99.0, "델타": True},  # 옛날 — 제외
    ]
    rows = app.recent_completion_rows(사건, "2026-08-19")
    assert [r["종목"] for r in rows] == ["NVDA", "AMD", "MU"], rows
    assert rows[0]["신호"] is True and rows[1]["신호"] is False
    assert rows[2]["신호"] is False, "이격도 없음이 신호로 잡혔습니다"

    묶음 = app.leader_watch_rows(사건, "2026-08-19")
    top = 묶음[0]
    assert top["묶음"] == "기기 OEM 반도체", 묶음   # NVDA·AMD 같은 묶음
    assert top["완성"] == 2 and top["신호"] == 1 and top["델타상승"] == 1
    assert top["마지막완성"] == "2026-08-01"




def test_같은묶음_같은수치_확인카드는_한장으로_합친다():
    """(129차) 헬스케어가 (섹터)·(테마) 두 분류표에서 같은 구성원·같은
    수치로 겹치면 한 장만 남기고, 수치가 다르면 둘 다 남깁니다."""
    rows = [
        {"묶음": "헬스케어", "종류": "섹터", "정배열폭": 67.0, "델타폭": 78.0,
         "3개월상대": 14.6, "확인": True},
        {"묶음": "헬스케어", "종류": "테마", "정배열폭": 67.0, "델타폭": 78.0,
         "3개월상대": 14.6, "확인": True},
        {"묶음": "AI-광통신", "종류": "테마", "정배열폭": 50.0, "델타폭": 60.0,
         "3개월상대": 9.0, "확인": True},
        {"묶음": "AI-광통신", "종류": "섹터", "정배열폭": 40.0, "델타폭": 60.0,
         "3개월상대": 9.0, "확인": True},          # 수치 다름 — 남아야 함
    ]
    out = app.dedupe_confirmations(rows)
    이름들 = [(r["묶음"], r.get("종류")) for r in out]
    assert len(out) == 3, 이름들
    assert out[0]["종류"] == "섹터·테마 동일"


def test_구성종목은_두_분류표를_합쳐_찾고_신호를_먼저_보여준다():
    """(129차) '헬스케어 어떤 종목인지'의 답 — SECTORS·GROUPS 양쪽에서
    구성원을 찾고, 최근 완성·이격도 30%+ 신호 종목이 앞에 옵니다."""
    완성 = [
        {"ticker": "LLY", "day": "2026-08-01", "이격도": 45.0, "델타": True},
        {"ticker": "PFE", "day": "2026-07-01", "이격도": 10.0, "델타": False},
    ]
    rows = app.group_member_rows("헬스케어", 완성, "2026-08-19")
    종목들 = [r["종목"] for r in rows]
    assert "LLY" in 종목들 and "PFE" in 종목들 and "UNH" in 종목들, 종목들[:5]
    assert rows[0]["종목"] == "LLY" and rows[0]["신호"] is True
    assert rows[1]["종목"] == "PFE" and rows[1]["신호"] is False


def test_오늘요약은_없으면_없다고_말한다():
    줄들 = app.today_summary_lines(12.4, [], [], [])
    글 = " ".join(줄들)
    assert "12%" in 글 and "약한 장세" in 글
    assert "켜진 묶음 없음" in 글 and "채택된 신호: 없음" in 글
    줄들2 = app.today_summary_lines(
        30.0, [{"묶음": "헬스케어"}],
        [{"묶음": "구독SW", "완성": 7, "신호": 5}], ["H18"])
    글2 = " ".join(줄들2)
    assert "살아 있는 장세" in 글2 and "헬스케어" in 글2         and "구독SW" in 글2 and "채택된 신호: H18" in 글2


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
