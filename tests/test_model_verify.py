"""
test_model_verify.py — 모델 전체 점검기 검증 (128차)
====================================================

지키는 약속:
  · 등록 가설이 판정 파일에서 **빠지면 반드시 문제**로 잡는다 (누락 안전망)
  · 옛 코드로 계산된 판정은 경고 줄을 남긴다
  · 기대 목록은 judge 상수에서 자동으로 나온다 — 새 등록이 자동 반영

실행: python3 tests/test_model_verify.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import judge  # noqa: E402
import model_verify as mv  # noqa: E402


def _verdict(names, code_rev="abc1234"):
    return {"code_rev": code_rev,
            "가설": {n: {"판정": "판정 불가",
                       "신규(판정)": {"신호": {"n": 0}}} for n in names}}


def test_등록가설이_빠지면_문제로_잡는다():
    expected = mv.expected_hypotheses()
    빠뜨림 = expected[:-2]                      # 마지막 2개 누락
    _, problems = mv.verify(_verdict(빠뜨림), expected, "abc1234")
    assert problems and "빠진 등록 가설" in problems[0], problems
    assert expected[-1] in problems[0] and expected[-2] in problems[0]


def test_전부_실려있으면_문제없음_옛코드는_경고줄():
    expected = mv.expected_hypotheses()
    lines, problems = mv.verify(_verdict(expected, "old0000"),
                                expected, "new1111")
    assert problems == [], problems
    assert any("옛 코드" in ln for ln in lines), lines


def test_기대목록은_judge_상수에서_나온다():
    expected = mv.expected_hypotheses()
    assert judge.H25B_NAME in expected
    assert judge.H18B_NAME in expected
    assert len(expected) == len(set(expected)), "기대 목록에 중복"


def test_점검기는_관찰판을_계기판과_같은_함수로_센다():
    """(150차-F) 점검기가 세는 코드를 **따로 적어** 두는 바람에,
    150차-B 에서 계기판이 "한 종목 한 줄"로 바뀌자 점검기만 옛 숫자를
    말했습니다 — 앱은 78종목, 점검기는 89건. 같은 저장소의 두 도구가
    "관찰판"이라는 같은 말로 다른 수를 대고 있었습니다.

    숫자를 시험에 박지 않습니다(다음 수집이면 바뀝니다). **같은 함수를
    부르는가**를 봅니다 — 그러면 어긋날 수가 없습니다.
    """
    root = os.path.join(os.path.dirname(__file__), "..")
    with open(os.path.join(root, "model_verify.py"), encoding="utf-8") as f:
        text = f.read()
    assert "app.recent_completion_rows(" in text, \
        "점검기가 관찰판을 계기판과 다른 방법으로 셉니다 — 두 도구가 갈립니다"
    # 옛 방식(사건 목록을 직접 거르기)이 되살아나지 않았는지
    assert 'e["이격도"] >= sm.H18_GAP_MIN' not in text, \
        "사건을 직접 거르는 옛 셈법이 남아 있습니다 (종목 중복이 다시 세어집니다)"


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
    print(f"\n모델 점검기 검증: {passed}개 통과, {failed}개 실패")
    sys.exit(1 if failed else 0)
