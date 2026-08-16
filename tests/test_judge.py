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
