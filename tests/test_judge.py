"""
test_judge.py — 자동 판정 검증 · v3 5단계
==========================================

11차 사전 등록의 채택 기준이 그대로 적용되는지 확인합니다:
  · 채택 = 신호 윌슨 하한 > 기준선 윌슨 상한 (완전 분리)
  · 신호 n<10 이면 "판정 불가" — 억지 결론 금지
  · 판정 표본 = 신규 종목만 (발견 29종목 제외)
  · H5b·H6 는 게이지 판단 불가 사건을 표본에서 제외

실행: python3 tests/test_judge.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import config as cfg  # noqa: E402
import judge  # noqa: E402


def event(ticker="NEWCO", announced="2025-01-01", excess=0.0,
          new_high=False, streak=0, h5=False, h5b=False, yardstick="adj_eps"):
    return {"ticker": ticker, "announced": announced, "excess": excess,
            "new_high": new_high, "newhigh_streak": streak,
            "h5": h5, "h5b": h5b, "잣대": yardstick}


# ---------------------------------------------------------------------------
# 윌슨 구간 — 알려진 성질로 검산
# ---------------------------------------------------------------------------
def test_wilson_known_properties():
    low, high = judge.wilson_interval(0, 100)
    assert low == 0.0 and high < 5.0                 # 0/100 → 0 근처
    low, high = judge.wilson_interval(100, 100)
    assert high > 99.9 and low > 95.0                # 100/100 → 100 근처
    low, high = judge.wilson_interval(5, 10)
    assert low < 50.0 < high                         # 5/10 → 50 을 품는 넓은 구간
    assert judge.wilson_interval(0, 0) == (0.0, 0.0)


# ---------------------------------------------------------------------------
# 채택 기준 — 분리·겹침·표본 부족
# ---------------------------------------------------------------------------
def test_adoption_requires_separation():
    """신호가 압도적으로 좋고 표본이 충분하면 채택."""
    # 신호 20건 전부 폭등, 기준선(신호 포함 100건)은 20% 근처
    signal = [event(excess=50.0) for _ in range(20)]
    rest = [event(excess=0.0) for _ in range(80)]
    result = judge._judge(signal, signal + rest)
    assert result["판정"] == "채택", result


def test_overlap_is_not_adopted():
    """구간이 겹치면 미채택 — 우연과 구분 안 되는 우위는 믿지 않습니다."""
    signal = [event(excess=50.0 if i % 4 == 0 else 0.0) for i in range(20)]
    rest = [event(excess=50.0 if i % 5 == 0 else 0.0) for i in range(80)]
    result = judge._judge(signal, signal + rest)
    assert result["판정"] == "미채택", result


def test_small_sample_is_undecidable():
    """신호 n<10 이면 아무리 좋아 보여도 '판정 불가'."""
    signal = [event(excess=50.0) for _ in range(9)]
    rest = [event(excess=0.0) for _ in range(80)]
    result = judge._judge(signal, signal + rest)
    assert result["판정"] == "판정 불가", result


# ---------------------------------------------------------------------------
# 표본 분리 — 신규(판정) vs 전체(참고)
# ---------------------------------------------------------------------------
def test_discovery_tickers_are_excluded_from_judgment_sample():
    discovery = sorted(cfg.MEASURE_DISCOVERY_TICKERS)[0]
    events = (
        [event(ticker=discovery, excess=50.0) for _ in range(30)]
        + [event(ticker="NEWCO", excess=0.0) for _ in range(30)]
    )
    result = judge.run(events)
    assert result["표본"]["신규(판정)"] == 30
    assert result["표본"]["전체(참고)"] == 60


def test_h6_uses_only_gauge_decidable_events():
    """H5b 판단 불가(None) 사건은 H5b·H6 표본에서 빠져야 합니다."""
    decidable = [event(h5b=True, streak=1, excess=50.0) for _ in range(5)]
    warmup = [event(h5b=None, streak=1, excess=50.0) for _ in range(20)]
    result = judge.run(decidable + warmup)
    h6 = result["가설"]["H6_결합_H5bxH2b"]["신규(판정)"]
    assert h6["기준선"]["n"] == 5, h6            # 워밍업 20건 제외
    assert h6["판정"] == "판정 불가"             # 신호 5건 < 10


def test_time_halves_are_reported():
    events = [event(announced=f"2025-0{1 + i % 9}-01", new_high=(i % 2 == 0),
                    excess=25.0 if i % 2 == 0 else 0.0) for i in range(40)]
    result = judge.run(events)
    h2 = result["가설"]["H2_신고점"]
    assert "신규_앞시기" in h2 and "신규_뒤시기" in h2
    assert h2["신규_앞시기"]["n"] + h2["신규_뒤시기"]["n"] == 20 + 20 - 20
    # (앞 20 + 뒤 20 사건 중 신호만 세므로 합계는 신호 사건 수 20)


def test_verdict_covers_all_registered_hypotheses():
    result = judge.run([event() for _ in range(12)])
    for name in ("H2_신고점", "H2b_신고점_첫돌파", "H5_실적폭_고정20",
                 "H5b_실적폭_중앙값", "H6_결합_H5bxH2b",
                 "H7_EBITDA_첫돌파", "H8_GAAPEPS_첫돌파"):
        assert name in result["가설"], name
        assert result["가설"][name]["판정"] in ("채택", "미채택", "판정 불가")


def test_ladder_groups_have_separate_baselines():
    """12차 등록: H7 의 기준선은 EBITDA 잣대 그룹만, H2b 는 조정 EPS 그룹만."""
    adj = [event(excess=0.0) for _ in range(30)]
    ebitda = [event(yardstick="adjusted_ebitda", streak=1 if i < 12 else 0,
                    excess=50.0 if i < 12 else 0.0) for i in range(15)]
    result = judge.run(adj + ebitda)
    h7 = result["가설"]["H7_EBITDA_첫돌파"]["신규(판정)"]
    assert h7["기준선"]["n"] == 15, h7          # EBITDA 그룹만
    assert h7["신호"]["n"] == 12, h7
    h2b = result["가설"]["H2b_신고점_첫돌파"]["신규(판정)"]
    assert h2b["기준선"]["n"] == 30, h2b        # 조정 EPS 그룹만


def test_h10_op_income_pool_is_separate():
    """H10 (23차 등록): 논갭 영업이익 사건은 별도 목록 — H2~H9 오염 금지."""
    ladder = [event(excess=0.0) for _ in range(30)]
    for e in ladder: e["below52"] = False
    op_sig = [event(streak=1, excess=50.0, yardstick="op_income")
              for _ in range(12)]
    for e in op_sig: e["below52"] = True
    op_rest = [event(excess=0.0, yardstick="op_income") for _ in range(30)]
    for e in op_rest: e["below52"] = False
    op_unknown = [event(excess=50.0, streak=1, yardstick="op_income")
                  for _ in range(5)]
    for e in op_unknown: e["below52"] = None
    result = judge.run(ladder, op_events=op_sig + op_rest + op_unknown)
    h10 = result["가설"][judge.H10_NAME]["신규(판정)"]
    assert h10["기준선"]["n"] == 42, h10          # 판단 불가 5건 제외, 사다리 30건 불포함
    assert h10["신호"]["n"] == 12, h10
    assert result["가설"][judge.H10_NAME]["판정"] == "채택"
    # 반대 방향 오염도 금지: H9 표본에 op_income 사건이 섞이면 안 된다
    h9 = result["가설"]["H9_저평가_첫신기록"]["신규(판정)"]
    assert h9["기준선"]["n"] == 30, h9
    # op_events 를 안 주면 H10 은 아예 안 나온다
    assert judge.H10_NAME not in judge.run(ladder)["가설"]


def test_h9_undervalued_first_breakout():
    """H9 (21차 등록): 첫 신기록 ∧ 52주선 아래. 표본은 판단 가능 사건만."""
    sig = [event(streak=1, excess=50.0) for _ in range(12)]
    for e in sig: e["below52"] = True
    above = [event(streak=1, excess=0.0) for _ in range(10)]
    for e in above: e["below52"] = False
    unknown = [event(excess=0.0) for _ in range(20)]
    for e in unknown: e["below52"] = None
    rest = [event(excess=0.0) for _ in range(30)]
    for e in rest: e["below52"] = False
    result = judge.run(sig + above + unknown + rest)
    h9 = result["가설"]["H9_저평가_첫신기록"]["신규(판정)"]
    assert h9["기준선"]["n"] == 52, h9        # 판단 불가 20건 제외
    assert h9["신호"]["n"] == 12, h9
    assert result["가설"]["H9_저평가_첫신기록"]["판정"] in ("채택", "미채택")



def test_leadership_records_facts_without_forcing_a_verdict():
    """H19~H21 (44차 등록): 표본이 국면 단위라 n<10 이면 '판정 불가'.

    44차 ⑤에서 **데이터를 보기 전에** 적은 대로, 국면 수가 적다고 문턱을
    낮추거나 표본 단위를 주(週)로 바꿔 n 을 부풀리지 않습니다.
    """
    timeline = [{"주": "2026-08-14", "주도": "A", "점수": 55.0,
                 "완성수": 5, "델타폭": 60.0}]
    switches = [{"주": "2025-01-03", "이전": "B", "이후": "A", "성공": True},
                {"주": "2025-06-06", "이전": "A", "이후": "C", "성공": False},
                {"주": "2026-01-02", "이전": "C", "이후": "A", "성공": None}]
    inflections = [{"주": "2025-03-07", "묶음": "A", "성공": True}]
    out = judge.judge_leadership(timeline, switches, inflections)
    h20 = out[judge.H20_NAME]
    assert h20["n"] == 2, h20            # 표적을 못 잰 사건은 분모에서 빠진다
    assert h20["성공"] == 1 and h20["판정"] == "판정 불가", h20
    assert out[judge.H19_NAME]["현재_주도"] == "A"
    assert out[judge.H19_NAME]["판정"] == "판정 불가"
    assert out[judge.H21_NAME]["n"] == 1


def test_leadership_can_adopt_only_with_enough_episodes():
    """국면이 10건 이상 쌓이고 성공률 하한이 50%를 넘어야 채택입니다."""
    switches = [{"성공": True} for _ in range(12)]
    out = judge.judge_leadership([], switches, [])
    assert out[judge.H20_NAME]["판정"] == "채택", out[judge.H20_NAME]
    # 12건 중 7건 성공 = 점추정 58.3% 로 절반을 넘지만, 윌슨 **하한**은
    # 32% 라 채택이 아닙니다. 점추정으로 판정하면 여기서 틀립니다.
    seven = [{"성공": i < 7} for i in range(12)]
    out2 = judge.judge_leadership([], seven, [])
    h20 = out2[judge.H20_NAME]
    assert h20["rate"] > 50.0, h20                 # 점추정은 절반을 넘는다
    assert h20["ci"][0] < 50.0, h20                # 하한은 못 넘는다
    assert h20["판정"] == "미채택", h20


# ---------------------------------------------------------------------------
# 52차 감사 수리 ⑤ — 판정 파일에 코드 판번호를 남긴다
# ---------------------------------------------------------------------------
def test_verdict_records_code_revision():
    """판정 결과에 지금 코드의 git 판번호가 들어 있어야 합니다.

    없으면 나중에 "이 판정이 어느 판 코드로 나온 것인가"를 되짚을 수
    없습니다 (52차 감사의 실제 사고).
    """
    out = judge.run([event(ticker=f"T{i}") for i in range(12)])
    assert "code_rev" in out, "판정에 코드 판번호가 없습니다"
    assert out["code_rev"] == judge.code_revision(), out["code_rev"]
    assert out["code_rev"], "코드 판번호가 빈 값입니다"


def test_code_revision_never_invents_a_value():
    """git 을 못 부르면 지어내지 말고 '알수없음' 이어야 합니다.

    '지어내지 않는다'는 실제로 실패시켜 봐야 확인됩니다 — 성공한 결과만
    보면 가짜 해시를 돌려주는 코드도 통과합니다.
    """
    import subprocess
    real_run = subprocess.run

    class Failed:                       # git 이 오류로 끝난 척
        returncode = 128
        stdout = ""

    try:
        subprocess.run = lambda *a, **k: Failed()
        assert judge.code_revision() == "알수없음", "git 실패인데 값을 지어냈습니다"

        subprocess.run = lambda *a, **k: type("R", (), {"returncode": 0,
                                                        "stdout": "  \n"})()
        assert judge.code_revision() == "알수없음", "빈 출력인데 값을 지어냈습니다"

        def boom(*a, **k):
            raise FileNotFoundError("git 이 없는 환경")
        subprocess.run = boom
        assert judge.code_revision() == "알수없음", "git 이 없는데 값을 지어냈습니다"
    finally:
        subprocess.run = real_run

    # 정상일 때는 짧은 해시 모양이어야 합니다
    got = judge.code_revision()
    assert isinstance(got, str) and got, got
    if got != "알수없음":
        assert all(c in "0123456789abcdef" for c in got), got
        assert 6 <= len(got) <= 12, got


def test_H22_신고점폭은_등록일_뒤만_판정한다():
    """H22·H22b (109차 등록) — **탐색 표본으로 판정하면 안 됩니다.**

    ⚠️ 문턱 5%·20% 는 109차 탐색 표를 **보고 골랐습니다**. 그 표본으로
    판정하면 그것이 바로 헌법 2조가 막는 사후 맞추기입니다. 그래서
    등록일(2026-08-19) **뒤**의 발표만 판정 표본이고, 그 전 것은
    '탐색표본(참고)'로 따로 적습니다.

    이 시험이 없으면 누가 등록일 가드를 지워도 조용히 통과합니다 —
    오히려 표본이 커져 **채택이 나올 수도** 있어 더 위험합니다.
    """
    def 사건(날, 폭, 초과):
        return {"ticker": "AA", "잣대": "adj_eps", "announced": 날,
                "신고점폭": 폭, "excess": 초과}

    옛 = [사건("2026-01-01", 50.0, 99.0) for _ in range(30)]      # 탐색 구간, 전부 폭등
    새 = [사건("2026-09-01", 50.0, -5.0) for _ in range(12)]      # 등록 뒤, 전부 실패
    r = judge.judge_newhigh_margin(옛 + 새)

    for 이름 in ("H22_신고점폭_5", "H22b_신고점폭_20"):
        d = r[이름]
        assert d["등록일"] == "2026-08-19"
        # 판정은 **등록일 뒤** 것만 본다 → 전부 실패했으므로 채택될 수 없다
        assert d["신규(판정)"]["신호"]["n"] == 12, d["신규(판정)"]
        assert d["판정"] != "채택", f"{이름}: 탐색 표본이 판정에 샜습니다"
        # 옛 것은 버리지 않고 참고로 남긴다
        assert d["탐색표본(참고)"]["신호"]["n"] == 30


def test_H22_폭을_못_재는_발표는_표본에서_뺀다():
    """직전 정점이 음수면 "몇 % 성장"이라는 말이 성립하지 않습니다.
    측정 장치가 없음(None)으로 두고, 판정도 그 발표를 세지 않습니다 —
    억지로 0으로 채우면 신호가 아닌 것이 신호 그룹에 섞입니다."""
    events = [
        {"ticker": "AA", "잣대": "adj_eps", "announced": "2026-09-01",
         "신고점폭": None, "excess": 99.0},
        {"ticker": "AA", "잣대": "adj_eps", "announced": "2026-09-02",
         "신고점폭": 30.0, "excess": 99.0},
    ]
    r = judge.judge_newhigh_margin(events)
    assert r["H22_신고점폭_5"]["신규(판정)"]["기준선"]["n"] == 1, "폭 없는 발표가 섞였습니다"


def test_H23_깊은게이지는_등록일_뒤만_판정한다():
    """H23 (116차 등록) — 100차 깊이 표를 보고 만든 가설이므로 그 표본으로
    판정하면 사후 맞추기입니다. 등록일 뒤의 발표만 판정 표본이고,
    깊은 게이지를 판단 못 한 발표(None)는 표본에서 뺍니다."""
    def 사건(날, 켜짐, 초과):
        return {"ticker": "AA", "잣대": "adj_eps", "announced": 날,
                "h5b_깊은": 켜짐, "excess": 초과}

    옛 = [사건("2026-01-01", True, 99.0) for _ in range(30)]     # 탐색 구간, 전부 폭등
    새 = [사건("2026-09-01", True, -5.0) for _ in range(12)]     # 등록 뒤, 전부 실패
    판단불가 = [사건("2026-09-02", None, 99.0)]                   # 워밍업 부족 등
    r = judge.judge_deep_gauge(옛 + 새 + 판단불가)

    d = r["H23_실적폭_중앙값_깊은게이지"]
    assert d["등록일"] == "2026-08-19"
    assert d["신규(판정)"]["신호"]["n"] == 12, d["신규(판정)"]
    assert d["신규(판정)"]["기준선"]["n"] == 12, "판단 불가 발표가 표본에 샜습니다"
    assert d["판정"] != "채택", "탐색 표본이 판정에 샜습니다"
    assert d["탐색표본(참고)"]["신호"]["n"] == 30




def test_H24_장세조건부_첫돌파는_등록일_뒤만_판정한다():
    """H24 (121차 등록) — 띠 20~60% 는 탐색 표를 보고 골랐으므로 그 표본으로
    판정하면 사후 맞추기입니다. 등록일 뒤의 발표만 판정 표본이고,
    장세폭을 판단 못 한 발표(None)는 표본에서 뺍니다."""
    def 사건(날, 폭, streak, 초과):
        return {"ticker": "AA", "잣대": "adj_eps", "announced": 날,
                "장세폭": 폭, "newhigh_streak": streak, "excess": 초과}

    옛 = [사건("2026-01-01", 30.0, 1, 99.0) for _ in range(30)]   # 탐색 구간, 전부 폭등
    새 = [사건("2026-09-01", 30.0, 1, -5.0) for _ in range(12)]   # 등록 뒤, 전부 실패
    판단불가 = [사건("2026-09-02", None, 1, 99.0)]                 # 장세폭 없음
    r = judge.judge_regime_breakout(옛 + 새 + 판단불가)

    d = r["H24_장세조건부_첫돌파"]
    assert d["등록일"] == "2026-08-20"
    assert d["장세폭_띠"] == [20.0, 60.0]
    assert d["신규(판정)"]["신호"]["n"] == 12, d["신규(판정)"]
    assert d["신규(판정)"]["기준선"]["n"] == 12, "판단 불가 발표가 표본에 샜습니다"
    assert d["판정"] != "채택", "탐색 표본이 판정에 샜습니다"
    assert d["탐색표본(참고)"]["신호"]["n"] == 30


def test_H24_띠의_경계는_하한포함_상한제외():
    """띠 [20, 60): 20.0 은 신호, 60.0 과 19.9 는 신호가 아닙니다.
    첫돌파가 아니면(streak≠1) 띠 안이어도 신호가 아닙니다."""
    def 사건(폭, streak):
        return {"ticker": "AA", "잣대": "adj_eps", "announced": "2026-09-01",
                "장세폭": 폭, "newhigh_streak": streak, "excess": 0.0}

    표본 = ([사건(20.0, 1), 사건(59.9, 1)]          # 신호 2
            + [사건(60.0, 1), 사건(19.9, 1)]        # 띠 밖 — 신호 아님
            + [사건(30.0, 2), 사건(30.0, 0)]        # 첫돌파 아님
            + [사건(30.0, 1) for _ in range(6)])    # 신호 6 (n 채우기)
    r = judge.judge_regime_breakout(표본)
    d = r["H24_장세조건부_첫돌파"]["신규(판정)"]
    assert d["신호"]["n"] == 8, d["신호"]
    assert d["기준선"]["n"] == 12




def test_H25_런업큰상회는_등록일_뒤만_판정한다():
    """H25 (124차 등록) — 문턱 15%·20%p 는 탐색 표를 보고 골랐으므로 그
    표본으로 판정하면 사후 맞추기입니다. 런업·서프율을 판단 못 한 발표는
    각 가설의 표본에서 빠집니다."""
    def 사건(날, 런업, 서프, 초과):
        return {"ticker": "AA", "잣대": "adj_eps", "announced": 날,
                "런업": 런업, "서프율": 서프, "excess": 초과}

    옛 = [사건("2026-01-01", 30.0, 20.0, 99.0) for _ in range(30)]
    새 = [사건("2026-09-01", 30.0, 20.0, -5.0) for _ in range(12)]
    서프없음 = [사건("2026-09-02", 30.0, None, 99.0)]   # H25 제외, H25b 포함
    런업없음 = [사건("2026-09-03", None, 20.0, 99.0)]   # 둘 다 제외
    r = judge.judge_momentum_beat(옛 + 새 + 서프없음 + 런업없음)

    h25 = r["H25_런업_큰상회"]
    assert h25["등록일"] == "2026-08-21"
    assert h25["신규(판정)"]["신호"]["n"] == 12, h25["신규(판정)"]
    assert h25["신규(판정)"]["기준선"]["n"] == 12, "판단 불가 발표가 샜습니다"
    assert h25["판정"] != "채택", "탐색 표본이 판정에 샜습니다"
    assert h25["탐색표본(참고)"]["신호"]["n"] == 30

    h25b = r["H25b_런업단독"]
    assert h25b["신규(판정)"]["기준선"]["n"] == 13, "서프 없는 발표는 H25b 에 들어가야 합니다"


def test_H25_문턱은_경계값을_포함한다():
    """런업 20.0·서프율 15.0 정확히 그 값이면 신호(이상 조건)입니다.
    19.9·14.9 는 신호가 아닙니다."""
    def 사건(런업, 서프):
        return {"ticker": "AA", "잣대": "adj_eps", "announced": "2026-09-01",
                "런업": 런업, "서프율": 서프, "excess": 0.0}
    표본 = ([사건(20.0, 15.0)] + [사건(19.9, 15.0)] + [사건(20.0, 14.9)]
            + [사건(50.0, 40.0) for _ in range(9)])
    r = judge.judge_momentum_beat(표본)
    d = r["H25_런업_큰상회"]["신규(판정)"]
    assert d["신호"]["n"] == 10, d["신호"]
    assert d["기준선"]["n"] == 12




def test_H18b_1년표적은_등록일_뒤만_판정한다():
    """H18b (126차 등록) — H18 과 같은 신호(이격도 30%+), 표적만 1년.
    1년 창이 안 끝난 사건(초과250 없음)은 표본에서 빠지고,
    탐색 구간 완성은 참고로만 셉니다."""
    def 사건(날, 이격, 초과250):
        return {"ticker": "AA", "day": 날, "이격도": 이격, "초과250": 초과250}

    옛 = [사건("2026-01-01", 40.0, 99.0) for _ in range(30)]     # 탐색, 전부 대박
    새 = [사건("2026-09-01", 40.0, -5.0) for _ in range(12)]     # 등록 뒤, 전부 실패
    미완 = [사건("2026-09-02", 40.0, None)]                       # 창 안 끝남
    r = judge.judge_completion_gap_1y(옛 + 새 + 미완, "2026-08-21", 30.0)

    d = r["H18b_완성이격도_1년"]
    assert d["등록일"] == "2026-08-21"
    assert d["신규(판정)"]["신호"]["n"] == 12, d["신규(판정)"]
    assert d["판정"] != "채택", "탐색 표본이 판정에 샜습니다"
    assert d["탐색표본(참고)"]["신호"]["n"] == 30


# ---------------------------------------------------------------------------
# 채택까지 얼마나 남았나 (139차) — 새 기준이 아니라 기존 기준의 산수
# ---------------------------------------------------------------------------
def test_폭등률이_기준선_상한_아래면_표본으로는_못_넘는다():
    """등록 기준은 '신호 윌슨 하한 > 기준선 상한'이고, 하한은 표본이 늘어도
    **폭등률 위로는 못 올라갑니다.** 그러니 폭등률 자체가 기준선 상한보다
    낮으면 표본을 아무리 모아도 못 넘습니다 — 기다린다고 되는 일이 아닙니다.

    실측(2026-08-21 판정 파일): 표본이 쌓인 11개 중 **10개가 이 상태**."""
    assert judge.needed_sample(rate=11.6, baseline_high=12.3, start_n=1177) is None
    assert judge.needed_sample(rate=12.3, baseline_high=12.3, start_n=100) is None


def test_폭등률이_높으면_필요한_표본_수를_계산한다():
    """실물 H11b: 폭등률 27.0% · 기준선 상한 24.9% · 지금 n=100 →
    n≈1,684. 계산한 표본에서 하한이 실제로 상한을 넘는지 확인합니다."""
    필요 = judge.needed_sample(rate=27.0, baseline_high=24.9, start_n=100)
    assert 필요 and 필요 > 100, 필요
    낮, _ = judge.wilson_interval(round(27.0 / 100 * 필요), 필요)
    assert 낮 > 24.9, f"계산한 표본인데 하한 {낮}이 상한을 못 넘습니다"
    # 한 단계 작은 표본에서는 아직 못 넘어야 합니다 (최소값이어야 하므로)
    작게 = int(필요 / 1.1)
    낮2, _ = judge.wilson_interval(round(27.0 / 100 * 작게), 작게)
    assert 낮2 <= 24.9, f"더 작은 표본({작게})에서도 넘습니다 — 최소가 아닙니다"


def test_채택거리는_표본_없는_가설을_지어내지_않는다():
    """사전 등록만 하고 아직 신호가 없는 가설은 '표본 없음'으로 둡니다."""
    verdict = {"가설": {
        "H_없음": {"신규(판정)": {"신호": {"n": 0, "rate": None, "ci": [0, 0]},
                              "기준선": {"n": 0, "rate": None, "ci": [0, 0]}}},
        "H_막힘": {"신규(판정)": {"신호": {"n": 200, "rate": 5.0, "ci": [2.7, 9.0]},
                              "기준선": {"n": 900, "rate": 11.0, "ci": [9.0, 13.3]}}},
    }}
    행 = {r["가설"]: r for r in judge.adoption_distance(verdict)}
    assert 행["H_없음"]["상태"] == "표본 없음", 행["H_없음"]
    assert 행["H_없음"]["필요표본"] is None
    assert "못 넘음" in 행["H_막힘"]["상태"], 행["H_막힘"]


def test_이미_넘은_가설은_그렇게_말한다():
    """하한이 이미 상한 위면 '필요 표본'을 말할 것이 아니라 넘었다고 해야."""
    verdict = {"가설": {"H_좋음": {"신규(판정)": {
        "신호": {"n": 300, "rate": 40.0, "ci": [34.7, 45.6]},
        "기준선": {"n": 900, "rate": 20.0, "ci": [17.5, 22.8]}}}}}
    행 = judge.adoption_distance(verdict)[0]
    assert "이미" in 행["상태"], 행



def test_표본이_0인_가설의_첫_표본_가능일은_등록일_더하기_창():
    """(140차) 등록된 23개 중 12개는 아직 표본이 0입니다. 139차의 계산은
    그 가설들에 대해 아무 말도 못 합니다. 그런데 첫 표본이 나올 수 있는
    가장 이른 날은 산수로 정해져 있습니다 — 사전 등록 규율상 **등록일
    뒤의 새 신호만** 세고, 표적을 재려면 **창이 끝나야** 하기 때문입니다.

    특히 1년 창인 H18b(2026-08-21 등록)는 **2027년 하반기 전에는 첫
    표본조차 없습니다.** 화면이 이 사실을 말하지 않으면 주인은 "왜 계속
    표본 없음인가"를 알 길이 없습니다."""
    빈판정 = {"가설": {이름: {"신규(판정)": {"신호": {"n": 0}}}
                    for 이름 in judge.hypothesis_clock()}}
    행 = {r["가설"]: r for r in judge.first_verdict_floor(빈판정)}
    assert judge.H18B_NAME in 행, 행
    r = 행[judge.H18B_NAME]
    assert r["창_거래일"] == judge._TRADING_DAYS_PER_YEAR, r
    assert r["가장이른날"] > "2027-06", f"1년 창인데 너무 이릅니다: {r}"
    # 60거래일 가설은 석 달쯤 뒤
    짧은 = 행[judge.H24_NAME]
    assert "2026-11" in 짧은["가장이른날"], 짧은
    assert 짧은["등록일"] == judge.H24_START_DAY


def test_이미_표본이_있는_가설은_첫_표본_예상에_넣지_않는다():
    """표본이 이미 있으면 '언제부터 생기나'는 무의미합니다 — 그건
    139차의 '얼마나 더 필요한가'가 답할 몫입니다."""
    이름 = judge.H24_NAME
    있음 = {"가설": {이름: {"신규(판정)": {"신호": {"n": 42}}}}}
    assert 이름 not in {r["가설"] for r in judge.first_verdict_floor(있음)}


def test_등록일을_모르는_가설은_날짜를_지어내지_않는다():
    """등록일 상수를 못 찾은 가설(H19·H21)은 **말하지 않습니다** —
    날짜를 지어내는 것보다 말하지 않는 편이 안전합니다 (헌법 1조)."""
    시계 = judge.hypothesis_clock()
    assert judge.H19_NAME not in 시계, "등록일 상수가 없는데 표에 넣었습니다"
    assert judge.H21_NAME not in 시계
    빈 = {"가설": {judge.H19_NAME: {"신규(판정)": {"신호": {"n": 0}}}}}
    이름들 = {r["가설"] for r in judge.first_verdict_floor(빈)}
    assert judge.H19_NAME not in 이름들, "등록일을 모르는데 날짜를 말했습니다"
    assert judge.H21_NAME not in 이름들



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
    print(f"\n자동 판정 검증: {passed}개 통과, {failed}개 실패")
    sys.exit(1 if failed else 0)
