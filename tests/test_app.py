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
    """유니버스 **전 종목**에 섹터가 있어야 합니다 (빠지면 화면에 미분류).

    종목 수는 확장할 때마다 바뀌므로 숫자를 적지 않습니다 — 79 라고
    적혀 있던 것이 401 이 되도록 아무도 못 고쳤습니다(150차-E).
    """
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
    """화면이 "채택 0개"를 정직하게 말하는지 (96차).

    ⚠️ 160차에 시험을 다시 썼습니다. 예전에는 **실제 판정 파일**을 읽어
    "채택이 0개여야 한다"고 못박았는데, 2026-09-01 에 H9 가 실제로
    채택되자 코드가 멀쩡한데도 빨간 불이 났습니다. 그것은 데이터가
    바뀐 것이지 화면이 깨진 것이 아닙니다. 시험이 지켜야 할 약속은
    "채택이 없으면 매수 근거가 아니라고 말한다" 이므로, 두 경우를
    **가짜 판정**으로 각각 확인합니다.
    """
    빈판정 = {"가설": {"H2b_신고점_첫돌파": {"판정": "미채택"}}}
    assert app.adopted_names(빈판정) == []
    html = app.summary_cards_html(50.0, [], [], [], app.adopted_names(빈판정),
                                  app.adoption_caveats(빈판정))
    assert "매수 근거 아님" in html, html


def test_채택이_생기면_얼마나_아슬아슬한지_함께_말한다():
    """160차 — "채택"이라는 글자만 띄우면 매수 근거로 읽힙니다.

    실측(2026-09-01 H9): 신호 하한 8.7 vs 기준선 상한 8.6 — 넘긴 폭이
    **0.1%p**. 앞시기 8.3% / 뒤시기 16.7% 로 **뒤시기에만** 우위였고,
    전체 표본으로는 미채택이었습니다. 헌법 4원칙이 정확히 경고하는
    모양이라, 판정은 그대로 두고 화면이 함께 말하게 합니다.
    """
    판정 = {"가설": {"H9_저평가_첫신기록": {
        "판정": "채택",
        "신규(판정)": {"신호": {"n": 217, "rate": 12.4, "ci": [8.7, 17.5]},
                   "기준선": {"n": 9514, "rate": 8.0, "ci": [7.5, 8.6]}},
        "신규_앞시기": {"n": 109, "rate": 8.3, "ci": [4.4, 15.0]},
        "신규_뒤시기": {"n": 108, "rate": 16.7, "ci": [10.8, 24.8]},
        "전체(참고)": {"판정": "미채택"},
    }}}
    c = app.adoption_caveats(판정)
    assert len(c) == 1, c
    assert c[0]["여유"] == 0.1, c            # 8.7 − 8.6
    assert c[0]["앞시기율"] == 8.3 and c[0]["뒤시기율"] == 16.7, c
    assert any("앞시기에는 우위 없음" in w for w in c[0]["주의"]), c
    assert any("전체 표본으로는 미채택" in w for w in c[0]["주의"]), c
    # 화면에 실제로 그려져야 합니다 — 만들어 놓고 안 그리면 없는 것과 같습니다
    html = app.summary_cards_html(50.0, [], [], [], app.adopted_names(판정), c)
    assert "여유 +0.1%p" in html, html
    assert "앞 8.3% → 뒤 16.7%" in html, html
    assert "앞시기에는 우위 없음" in html, html
    # 앞시기가 기준선 위로 완전히 떨어져 있으면 그 경고는 붙지 않습니다
    import copy
    판정2 = copy.deepcopy(판정)
    판정2["가설"]["H9_저평가_첫신기록"]["신규_앞시기"] = {
        "n": 109, "rate": 20.0, "ci": [15.0, 26.0]}
    판정2["가설"]["H9_저평가_첫신기록"]["전체(참고)"] = {"판정": "채택"}
    assert app.adoption_caveats(판정2)[0]["주의"] == [], app.adoption_caveats(판정2)


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




def test_이상값은_모양별로_쪼개_보여준다():
    """(136차) '영업이익 1,112건'이라는 총 건수만으로는 무엇이 망가졌는지
    알 수 없었습니다. 쪼개 보고서야 정체가 **단위 혼선**임이 드러났습니다
    (한 종목 안에서 달러와 천·백만이 섞임). 화면도 쪼개 말해야 합니다."""
    검진 = {
        "채움률": {"행": 100},
        "이상값": {
            "revenue": {"건수": 25, "종목수": 14, "예시": []},
            "op_income": {"건수": 666, "종목수": 93, "예시": [],
                          "모양별": {"이력보다 1000배 이상 작음": 499,
                                  "이력보다 1000배 이상 큼": 163,
                                  "정확히 0": 4}},
        },
        "어제 대비": None,
    }
    글 = " ".join(app.health_lines(검진))
    assert "93종목" in 글, f"몇 종목이 걸렸는지 안 보입니다: {글}"
    for 조각 in ("499칸", "163칸", "4칸", "단위가 섞인"):
        assert 조각 in 글, f"{조각} 가 화면에 없습니다: {글}"
    # 모양별이 없는 옛 기록으로도 화면이 깨지지 않아야 합니다
    검진["이상값"]["op_income"].pop("모양별")
    assert app.health_lines(검진), "옛 기록에서 화면이 비었습니다"


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


def test_요약카드는_없으면_없다고_말하고_새완성을_올린다():
    """(130차) 증권앱풍 요약 카드 — 값이 없으면 '없음', 약한 장세는 빨강,
    이번 주 새 완성 종목이 이름으로 올라온다."""
    html = app.summary_cards_html(12.4, [], [], [], [])
    assert "12%" in html and "약한 장세" in html and "mv-red" in html
    assert html.count("없음") >= 3
    html2 = app.summary_cards_html(
        30.0, [{"묶음": "헬스케어"}],
        [{"묶음": "구독SW", "완성": 7, "신호": 5}],
        [{"종목": "LITE"}, {"종목": "CRDO"}], ["H18"])
    assert "살아 있는 장세" in html2 and "헬스케어" in html2
    assert "LITE · CRDO" in html2 and "구독SW 7" in html2 and "H18" in html2


def test_신호종목판은_최신완성이_먼저오고_새완성표시가_붙는다():
    """(130차) LITE(8/19 완성)가 이격도순 정렬에 밀려 접기 속에 숨었던
    사고의 수리 — 최신 완성이 앞이고, 7일 안 완성은 새완성 True."""
    사건 = [
        {"ticker": "EXTR", "day": "2026-06-26", "이격도": 64.7, "델타": False,
         "테마": "AI-광통신네트워크"},
        {"ticker": "LITE", "day": "2026-08-19", "이격도": 48.7, "델타": True,
         "테마": "AI-광통신네트워크"},
    ]
    rows = app.recent_completion_rows(사건, "2026-08-19")
    assert rows[0]["종목"] == "LITE", rows
    assert rows[0]["새완성"] is True and rows[1]["새완성"] is False
    assert rows[0]["테마"] == "AI-광통신네트워크"


def test_두_정배열_잣대가_갈리는_종목을_센다():
    """(150차-D) 같은 앱 안에 "정배열"이 두 뜻으로 쓰입니다 — 일부러
    그렇게 두었습니다(33차·39차 따로 등록).

      · 폭  = 이평선 배열 **+ 주봉 종가 > 4주선**
      · 완성 = 이평선 **배열만**

    코드 주석에는 39차부터 적혀 있었지만 화면에는 한 글자도 없어서,
    "반도체장비 폭 0%"와 "TER·ONTO 완성"이 서로 모순처럼 보였습니다.
    이 함수는 지금 갈리는 종목을 세어 화면이 설명할 수 있게 합니다.
    """
    import sector_model as sm

    def 주가(closes):
        # 넉넉한 주 수를 만들어 52주선까지 잽니다 (하루 = 한 주 격자로 충분)
        from datetime import date, timedelta
        d = date(2020, 1, 6)
        dates = []
        for _ in closes:
            dates.append(d.isoformat())
            d += timedelta(days=7)
        return {"dates": dates, "close": list(closes)}

    # 꾸준히 올라 이평선이 정렬된 뒤, 마지막 주만 살짝 눌린 종목
    오름 = [100.0 + i * 2.0 for i in range(120)]
    눌림 = 주가(오름[:-1] + [오름[-2] - 25.0])
    # 눌리지 않고 계속 오른 종목
    그대로 = 주가(오름)

    ds = {"benchmark": "SPY",
          "tickers": ["눌린", "정상"],
          "prices": {"눌린": 눌림, "정상": 그대로,
                     "SPY": 그대로}}

    # 먼저 전제를 확인합니다 — 전제가 깨지면 이 시험은 헛돕니다
    assert sm.aligned_flags_chart(눌림)[max(sm.aligned_flags_chart(눌림))] is True, \
        "눌린 종목이 완성 잣대로도 정배열이 아닙니다 — 시험 데이터가 잘못됐습니다"
    assert sm.aligned_flags(눌림)[max(sm.aligned_flags(눌림))] is False, \
        "눌린 종목이 폭 잣대로도 정배열입니다 — 시험 데이터가 잘못됐습니다"

    got = app.gauge_gap_rows(ds)
    assert got["종목들"] == ["눌린"], got
    assert got["종목수"] == 1, got


def test_장세화면이_두_잣대의_차이를_말한다():
    """(150차-D) 셈만 하고 화면이 안 적으면 주인은 여전히 두 탭이
    모순된다고 봅니다 — 값이 담기는 것과 그려지는 것을 둘 다 봅니다."""
    root = os.path.join(os.path.dirname(__file__), "..")
    with open(os.path.join(root, "web_build.py"), encoding="utf-8") as f:
        assert "app.gauge_gap_rows(" in f.read(), \
            "웹앱 데이터에 잣대 차이가 담기지 않습니다"
    with open(os.path.join(root, "docs", "app.js"), encoding="utf-8") as f:
        js = f.read()
    assert "잣대차이" in js, "장세 화면이 잣대 차이를 그리지 않습니다"
    for 조각 in ("서로 다른 잣대", "주봉 종가", "틀린 것이 아닙니다"):
        assert 조각 in js, f"화면 설명에 '{조각}' 가 없습니다"


def test_한_종목은_관찰판에_한_줄만_나온다():
    """(150차) 정배열이 깨졌다 다시 붙기를 반복하면 같은 종목이 여러 번
    완성합니다. 그것을 그대로 세면 관찰판이 '여러 종목이 몰렸다'고
    **잘못 말합니다** — 실데이터에서 '광고 플랫폼' 묶음이 제타 한
    종목뿐인데 완성 2개로 올라 있었습니다.

    남는 것은 **가장 최근 완성**이어야 합니다 (옛 완성의 낡은 이격도가
    지금 값을 덮으면 안 됩니다).
    """
    사건 = [
        {"ticker": "ZETA", "day": "2026-06-26", "이격도": 3.2,  "델타": False},
        {"ticker": "ZETA", "day": "2026-08-14", "이격도": 50.7, "델타": True},
        {"ticker": "KMI",  "day": "2026-06-01", "이격도": 5.0,  "델타": False},
        {"ticker": "KMI",  "day": "2026-07-01", "이격도": 6.0,  "델타": False},
        {"ticker": "KMI",  "day": "2026-08-01", "이격도": 7.0,  "델타": True},
    ]
    rows = app.recent_completion_rows(사건, "2026-08-19")
    종목들 = [r["종목"] for r in rows]
    assert len(종목들) == len(set(종목들)), f"같은 종목이 두 번 나옵니다: {종목들}"
    제타 = next(r for r in rows if r["종목"] == "ZETA")
    assert 제타["완성일"] == "2026-08-14" and 제타["이격도"] == 50.7, 제타
    카이 = next(r for r in rows if r["종목"] == "KMI")
    assert 카이["완성일"] == "2026-08-01" and 카이["이격도"] == 7.0, 카이

    # 묶음 집계도 같이 고쳐져야 합니다 — 한 종목짜리 묶음이 "완성 2개"로
    # 관찰판에 오르면 안 됩니다.
    묶음 = {g["묶음"]: g for g in app.leader_watch_rows(사건, "2026-08-19")}
    광고 = 묶음.get("광고 플랫폼")
    assert 광고 is not None and 광고["완성"] == 1, (
        f"한 종목(제타)뿐인 묶음이 완성 {광고 and 광고['완성']}개로 세어집니다")


def test_이격도가_높으면_며칠_지난_완성도_앞에_온다():
    """(150차) 주인 지적 — "제타는 정배열인데 왜 화면에 없지?"

    129차에 최신 완성이 이격도순에 밀려 접기 속에 숨은 사고를 고치며
    완성일순으로 바꿨더니, 이번엔 **반대 사고**가 났습니다. 제타는
    이격도 50.7%(신호 20개 중 4위)인데 완성일이 8일 전이라 6번째로
    밀려 접기 속으로 들어갔습니다.

    차례는 ① 새 완성(7일 안) 먼저 ② 그 안에서 이격도순 — 두 사고를
    한꺼번에 막습니다.
    """
    사건 = [
        # 새 완성이지만 이격도가 낮은 것 넷 — 옛 규칙이면 이들이 앞을 다 먹습니다
        {"ticker": "AAL",  "day": "2026-08-18", "이격도": 31.0, "델타": False},
        {"ticker": "ROST", "day": "2026-08-18", "이격도": 30.6, "델타": False},
        {"ticker": "FCX",  "day": "2026-08-17", "이격도": 30.4, "델타": False},
        {"ticker": "NUE",  "day": "2026-08-17", "이격도": 32.0, "델타": False},
        # 8일 전 완성이지만 이격도가 훨씬 높은 것
        {"ticker": "ZETA", "day": "2026-08-11", "이격도": 50.7, "델타": True},
    ]
    rows = app.recent_completion_rows(사건, "2026-08-19")
    자리 = [r["종목"] for r in rows].index("ZETA")
    assert 자리 <= 4, (
        f"제타가 {자리 + 1}번째입니다 — 이격도 50.7%인데 뒤로 밀렸습니다: "
        f"{[(r['종목'], r['이격도'], r['새완성']) for r in rows]}")
    # 새 완성끼리는 이격도 높은 순
    새것 = [r for r in rows if r["새완성"]]
    assert [r["이격도"] for r in 새것] == sorted(
        (r["이격도"] for r in 새것), reverse=True), 새것


def test_홈화면은_신호종목을_여덟개까지_펼친다():
    """(150차) 제타가 6번째라 **딱 한 칸 차이로** 접혀 있었습니다.
    5칸은 좁습니다 — 8칸으로 넓혔습니다."""
    root = os.path.join(os.path.dirname(__file__), "..")
    with open(os.path.join(root, "app.py"), encoding="utf-8") as f:
        text = f.read()
    assert "_펼침 = 8" in text, "홈 화면 펼침 개수가 8이 아닙니다"
    assert "_종목판[:5]" not in text, "5칸 제한이 남아 있습니다"




def test_로빈후드행은_알약과_새완성점을_정확히_단다():
    """(131차) 종목 행 — 오른쪽 초록 알약에 +이격도%, 7일 안 완성만
    초록 점, 이격도 없으면 — 로 정직하게."""
    r = {"종목": "LITE", "묶음": "광통신", "테마": "AI-광통신네트워크",
         "완성일": "2026-08-19", "이격도": 48.7, "델타": True, "새완성": True}
    html = app.signal_row_html(r)
    assert "+48.7%" in html and "rh-pill" in html
    assert "rh-dot" in html and "델타상승" in html and "AI-광통신네트워크" in html
    r2 = {"종목": "EXTR", "묶음": "하드웨어", "테마": None,
          "완성일": "2026-06-26", "이격도": None, "델타": False, "새완성": False}
    html2 = app.signal_row_html(r2)
    assert "rh-dot" not in html2 and ">—<" in html2


def test_채택거리_줄은_막힌_가설을_숨기지_않는다():
    """(139차) 주인의 "언제부터 포착 가능한가"에 대한 답입니다. 실측은
    표본이 쌓인 11개 중 10개가 **표본을 더 모아도 못 넘는** 상태였습니다.
    좋은 소식만 적고 이 사실을 빼면 정직화 위반입니다."""
    verdict = {"가설": {
        "H_막힘": {"신규(판정)": {"신호": {"n": 200, "rate": 5.0, "ci": [2.7, 9.0]},
                              "기준선": {"n": 900, "rate": 11.0, "ci": [9.0, 13.3]}}},
        "H_가능": {"신규(판정)": {"신호": {"n": 100, "rate": 27.0, "ci": [19.3, 36.4]},
                              "기준선": {"n": 900, "rate": 22.0, "ci": [19.4, 24.9]}}},
        "H_없음": {"신규(판정)": {"신호": {"n": 0, "rate": None, "ci": [0, 0]},
                              "기준선": {"n": 0, "rate": None, "ci": [0, 0]}}},
    }}
    글 = " ".join(app.adoption_distance_lines(verdict))
    assert "못 넘음: **1개**" in 글, 글
    assert "넘을 수 있음: **1개**" in 글, 글
    assert "H_가능" in 글, "넘을 수 있는 가설의 이름과 필요 표본이 없습니다"
    # "영원히 불가능"으로 단정하지 않는다 (없는 것을 단정하지 않기)
    assert "그대로라면" in 글, 글
    # 판정 파일이 없으면 지어내지 않는다
    assert "지어내지 않습니다" in " ".join(app.adoption_distance_lines(None))



def test_표본이_0인_가설의_시점도_화면에_적는다():
    """(140차) 표본이 0인 가설은 139차 계산이 아무 말도 못 합니다. 화면이
    침묵하면 주인은 "왜 계속 표본 없음인가"를 알 길이 없습니다. 규칙에서
    바로 나오는 하한선(등록일 + 창)을 적습니다."""
    import judge
    빈판정 = {"가설": {이름: {"신규(판정)": {"신호": {"n": 0}}}
                    for 이름 in judge.hypothesis_clock()}}
    글 = " ".join(app.adoption_distance_lines(빈판정))
    assert "언제부터 생기나" in 글, 글
    assert judge.H18B_NAME in 글, "1년 창 가설이 화면에 없습니다"
    assert "2027" in 글, "1년 창인데 내년 날짜가 안 보입니다: " + 글
    assert "예측이 아니라" in 글, "하한선임을 밝히지 않았습니다"



# ---------------------------------------------------------------------------
# 조합 신호판 (154차) — H29 조합의 현재 상태 나열
# ---------------------------------------------------------------------------
def _조합_ds(델타상승=True, 급등=True, 신선=True):
    """정배열 유지 ∧ 이격 30%+ ∧ 이격 상승을 만들 수 있는 최소 자료.

    급등=False 면 완만한 상승이라 이격이 30%에 못 미칩니다.
    """
    from datetime import date as d, timedelta as td
    n = 120
    day = d.fromisoformat("2023-01-02")
    dates = []
    for _ in range(n):
        dates.append(day.isoformat()); day += td(days=7)
    if 급등:
        # 가속 상승 — 직선 상승은 막판에 이격(52주선 대비 %)이 도로
        # 줄어들어 '이격 상승' 조건을 못 만든다 (실측 98.2 < 100.0)
        closes = ([100.0] * 90 + [100.0 * 1.06 ** i for i in range(1, 31)])
    else:
        # 가속이되 작게 — 이격이 오르는 중이지만 30% 문턱에는 못 미침
        # (이격상승 조건까지 통과해야 문턱 검사만 따로 시험할 수 있다)
        closes = ([100.0] * 90 + [100.0 * 1.008 ** i for i in range(1, 31)])
    rows = []
    qday = d.fromisoformat(dates[0])
    # 신선=False 는 분기 8개(정상 간격)로 끝내 마지막 발표를 오래되게
    # 한다 — 간격을 줄이면(40일) 연속 분기 인정(55~150일)에서 통째로
    # 탈락해 신선도 검사에 닿기도 전에 빠진다 (돌연변이가 안 잡혔던 원인)
    for i in range(12 if 신선 else 8):
        v = (1.0 + i * 0.1) if 델타상승 else (5.0 - i * 0.1)
        rows.append({"announced_date": qday.isoformat(),
                     "filing_date": qday.isoformat(),
                     "period_label": f"Q{i}", "adj_eps": v})
        qday += td(days=91)
    return {"tickers": ["AAA"], "benchmark": "SPY",
            "prices": {"AAA": {"dates": dates, "close": closes},
                       "SPY": {"dates": dates, "close": [100.0] * n}},
            "quarters": {"AAA": rows}}


def test_조합신호판은_네_조건을_전부_요구한다():
    """정배열∧델타↑∧이격30%+∧이격상승 — 하나라도 빠지면 목록에 없다."""
    ds = _조합_ds()
    rows = app.combo_now_rows(ds)
    assert [r["종목"] for r in rows] == ["AAA"], rows
    assert rows[0]["이격도"] >= 30.0
    assert rows[0]["이격도"] > rows[0]["이격도_4주전"]
    # 델타 하락이면 빠진다
    assert app.combo_now_rows(_조합_ds(델타상승=False)) == []
    # 이격이 문턱에 못 미치면 빠진다
    assert app.combo_now_rows(_조합_ds(급등=False)) == []


def test_조합신호판은_이격이_줄면_뺀다():
    """이격 30%+ 라도 4주 전보다 줄었으면 조합이 아니다 — 직선 상승은
    막판에 이격이 도로 줄어드는 실측(98.2 < 100.0)을 그대로 쓴다."""
    ds = _조합_ds()
    n = len(ds["prices"]["AAA"]["close"])
    ds["prices"]["AAA"]["close"] = ([100.0] * 90
                                    + [100.0 + i * 8 for i in range(1, n - 89)])
    assert app.combo_now_rows(ds) == []


def test_조합신호판은_오래된_델타를_판단하지_않는다():
    """마지막 발표가 신선도(140일)를 넘으면 델타를 지어내지 않고 뺀다."""
    ds = _조합_ds(신선=False)   # 40일 간격 12개 → 마지막 발표가 2년 전
    assert app.combo_now_rows(ds) == []


# ---------------------------------------------------------------------------
# 성장 속도표 (162차) — "저런 표를 앞으로의 기준으로 삼아"
# ---------------------------------------------------------------------------
def _성장_eps행(values, start_year=2024):
    """분기마다 ~91일 간격으로 발표한 조정 EPS 행. 매출은 조금씩 다르게
    (같은 값이 4번 나오면 자리채움으로 보고 없음 처리되는 검사가 있음)."""
    rows = []
    for i, v in enumerate(values):
        y, m = start_year + i // 4, 1 + (i % 4) * 3
        rev = 1e9 + i * 3e7
        rows.append({"filing_date": f"{y}-{m:02d}-15",
                     "announced_date": f"{y}-{m + 1:02d}-15",
                     "period_label": f"Q{i}", "revenue": rev, "op_income": rev * 0.1,
                     "adj_eps": v, "adjusted_ebitda": None,
                     "gaap_eps": round(v * 0.9, 4), "gross_margin_pct": 50.0,
                     "gaap_eps_xbrl": None, "revenue_xbrl": None,
                     "gross_margin_pct_xbrl": None})
    return rows


def _성장_ds(eps: dict, 마지막날="2026-01-02"):
    """dataset.build 를 **실제로 거친** 미니 데이터 — 정제기가 행을 지우면
    성장표도 그 뒤를 봐야 하기 때문입니다."""
    import dataset
    from datetime import date, timedelta
    d, 날들, 종가 = date(2023, 1, 6), [], []
    while d <= date.fromisoformat(마지막날):
        날들.append(d.isoformat())
        종가.append(100.0 + len(날들))
        d += timedelta(days=7)
    snap = {"benchmark": "SPY", "tickers": ["SPY"] + list(eps),
            "eps": {"SPY": [], **eps},
            "prices": {"SPY": {"dates": 날들, "close": 종가}}}
    for t in eps:
        snap["prices"][t] = {"dates": 날들, "close": [c * 1.2 for c in 종가]}
    return dataset.build(snap)


def test_pct_change_는_전값이_0_이하면_없음():
    assert app._pct_change(4.8, 4.2) == 14.3
    assert app._pct_change(3.0, 4.0) == -25.0
    assert app._pct_change(1.0, 0.0) is None, "0 으로 나눈 비율을 만들었습니다"
    assert app._pct_change(1.0, -2.0) is None, "음수에서 온 비율은 뜻이 없습니다"
    assert app._pct_change(None, 4.0) is None and app._pct_change(4.0, None) is None


def test_성장행은_가속과_감속을_TTM_증가율로_가른다():
    """가속 = 이번 TTM 증가율 > 직전 TTM 증가율. 값이 올라도(둘 다 TTM
    신고점) 속도가 꺾이면 감속이다 — CRDO 사건(161차)의 교훈."""
    ds = _성장_ds({"가속": _성장_eps행([1, 1, 1, 1, 1, 1, 1.2, 1.6]),
                  "감속": _성장_eps행([1, 1, 1, 1, 1, 1, 1.6, 1.2])})
    가 = app.growth_row(ds, "가속")
    # TTM 4.0 → 4.2 → 4.8 : 직전 +5.0% · 이번 +14.3%
    assert 가["TTM"] == 4.8 and 가["직전TTM증가"] == 5.0 and 가["TTM증가"] == 14.3, 가
    assert 가["가속"] is True and 가["신고점"] is True, 가
    assert 가["잣대"] == "adj_eps" and 가["최근발표"] == "2025-11-15", 가
    assert 가["분기QoQ"] == 33.3 and 가["직전분기QoQ"] == 20.0, 가
    감 = app.growth_row(ds, "감속")
    # TTM 4.0 → 4.6 → 4.8 : 직전 +15.0% · 이번 +4.3% → 값은 신고점인데 감속
    assert 감["신고점"] is True and 감["가속"] is False, 감
    assert 감["TTM증가"] == 4.3 and 감["직전TTM증가"] == 15.0, 감


def test_성장행은_직전_TTM_이_0_이하면_가속을_가리지_않는다():
    """없는 값을 만들지 않습니다 — 적자에서 흑자로 돌아선 첫 분기의
    '증가율'은 뜻이 없어 None 이고 가속도 None(판단불가)입니다."""
    ds = _성장_ds({"음수": _성장_eps행([1, 1, -2, -2, -2, -2, 1, 2])})
    r = app.growth_row(ds, "음수")
    assert r["TTM"] == -1 and r["TTM증가"] is None and r["가속"] is None, r
    assert r["직전분기QoQ"] is None and r["분기QoQ"] == 100.0, r


def test_성장행은_마지막_연속_구간만_쓴다():
    """끊긴 이력을 이어붙이면 한 푼도 안 늘었는데 가속이 나옵니다(사고 7).
    마지막 연속 구간이 6분기 미만이면 줄을 만들지 않습니다."""
    앞 = _성장_eps행([1, 1, 1, 1, 1, 1, 1.2, 1.6], start_year=2021)   # 2021~2022
    뒤 = _성장_eps행([2, 2.5, 3], start_year=2025)                     # 2년 뒤 3분기
    ds = _성장_ds({"끊김": 앞 + 뒤})
    assert app.growth_row(ds, "끊김") is None, "끊긴 구간을 이어붙여 잤습니다"
    # 잣대 자체가 없으면(8분기 미만) 당연히 없음
    ds2 = _성장_ds({"짧음": _성장_eps행([1, 1, 1, 1, 1.2, 1.6, 1.7])})
    assert app.growth_row(ds2, "짧음") is None


def test_성장표는_낡은_발표를_빼고_가속_먼저_정렬한다():
    """마지막 발표가 기준일(주가 마지막 날)에서 140일 넘게 오래면 뺍니다 —
    실측으로 UTHR 2022 · PFE 2023 · ZBH 2022 가 '지금의 감속'으로 섞여
    들어왔습니다. 차례는 가속 → 판단불가 → 감속, 그 안에서 TTM증가 큰 순."""
    ds = _성장_ds({
        "가속": _성장_eps행([1, 1, 1, 1, 1, 1, 1.2, 1.6]),
        "더가속": _성장_eps행([1, 1, 1, 1, 1, 1, 1.5, 2.5]),
        "감속": _성장_eps행([1, 1, 1, 1, 1, 1, 1.6, 1.2]),
        "음수": _성장_eps행([1, 1, -2, -2, -2, -2, 1, 2]),
        "낡음": _성장_eps행([1, 1, 1, 1, 1, 1, 1.2, 1.6], start_year=2022),
    })
    표 = app.growth_table_rows(ds)
    assert [r["종목"] for r in 표] == ["더가속", "가속", "음수", "감속"], [r["종목"] for r in 표]
    assert app.growth_row(ds, "낡음") is not None, "낡음은 줄은 있되 표에서만 빠져야 합니다"
    # 기준일을 직접 주면 그 날짜로 신선도를 잽니다 — 2024-01 기준이면 낡음(2023-11)도 신선
    assert "낡음" in [r["종목"] for r in app.growth_table_rows(ds, 기준일="2024-01-15")]
    # 묶음 필터 — 시험 종목은 전부 미분류
    assert app.growth_table_rows(ds, 묶음="없는묶음") == []
    assert len(app.growth_table_rows(ds, 묶음="미분류")) == 4


def test_성장묶음_비율은_5개_미만이면_적지_않는다():
    """한 종목이 20%p 를 움직이는 표본으로 비율을 말하지 않습니다."""
    작은 = _성장_ds({"가속": _성장_eps행([1, 1, 1, 1, 1, 1, 1.2, 1.6]),
                    "감속": _성장_eps행([1, 1, 1, 1, 1, 1, 1.6, 1.2]),
                    "음수": _성장_eps행([1, 1, -2, -2, -2, -2, 1, 2])})
    [g] = app.growth_sector_rows(작은)
    assert g == {"묶음": "미분류", "가속": 1, "감속": 1, "판단불가": 1, "가속비율": None}, g

    큰 = _성장_ds({f"가속{i}": _성장_eps행([1, 1, 1, 1, 1, 1, 1.2, 1.6 + i / 10])
                  for i in range(5)} | {"감속": _성장_eps행([1, 1, 1, 1, 1, 1, 1.6, 1.2])})
    [g] = app.growth_sector_rows(큰)
    assert g["가속"] == 5 and g["감속"] == 1 and g["가속비율"] == 83.3, g


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
