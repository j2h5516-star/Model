"""
test_screen_verify.py — 화면↔데이터 정합성 점검기 검증 (150차-N)

지키는 약속:
  · 화면이 데이터와 어긋나면 **반드시 ⛔ 로 잡는다** (여섯 가지 깨뜨림)
  · 멀쩡하면 조용하다 (거짓 경보 없음)
  · 점검기는 **읽기 전용**이다 — 어떤 파일도 고치지 않는다
  · 스킬 문서가 실제 파일과 짝이 맞는다

실행: python3 tests/test_screen_verify.py
"""

import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

import screen_verify as sv  # noqa: E402

APP = os.path.join(ROOT, "docs", "data", "app.json")


def _있나():
    return os.path.exists(APP)


def _깨고_보기(바꾸기):
    """app.json 을 잠깐 망가뜨려 ⛔ 가 뜨는지 보고 **반드시 되돌린다**."""
    백업 = tempfile.mktemp(suffix=".json")
    shutil.copy(APP, 백업)
    try:
        with open(APP, encoding="utf-8") as f:
            a = json.load(f)
        바꾸기(a)
        with open(APP, "w", encoding="utf-8") as f:
            json.dump(a, f, ensure_ascii=False)
        return [r for r in sv.화면과_데이터를_맞댄다() if r["상태"] == "⛔"]
    finally:
        shutil.copy(백업, APP)
        os.unlink(백업)


def test_멀쩡하면_조용하다():
    """거짓 경보가 나면 아무도 이 점검을 안 믿게 됩니다."""
    if not _있나():
        return
    이상 = [r for r in sv.화면과_데이터를_맞댄다() if r["상태"] == "⛔"]
    assert not 이상, f"멀쩡한 화면에 경보가 떴습니다: {이상}"


def test_신호_종목이_빠지면_잡는다():
    """(150차-B) 웹앱이 제타를 신호 아님으로 표시하던 사고의 그물."""
    if not _있나():
        return
    이상 = _깨고_보기(lambda a: a.__setitem__("신호종목", a["신호종목"][:-3]))
    assert any("신호 종목" in r["검사"] for r in 이상), 이상


def test_한_종목이_두_줄이면_잡는다():
    """(150차-B) 같은 종목의 옛 완성이 되살아나는 것."""
    if not _있나():
        return
    이상 = _깨고_보기(
        lambda a: a.__setitem__("완성전체", a["완성전체"] + a["완성전체"][:2]))
    assert any("한 종목" in r["검사"] for r in 이상), 이상


def test_기준일이_어긋나면_잡는다():
    """옛 데이터로 화면이 굳는 것 — 겉보기엔 멀쩡한 최악의 고장."""
    if not _있나():
        return
    이상 = _깨고_보기(lambda a: a.__setitem__("기준일", "2020-01-01"))
    assert any("기준일" in r["검사"] for r in 이상), 이상


def test_가설이_빠지면_잡는다():
    if not _있나():
        return
    이상 = _깨고_보기(lambda a: a.__setitem__("가설", a["가설"][:-4]))
    assert any("가설" in r["검사"] for r in 이상), 이상


def test_정직화_칸이_빠지면_잡는다():
    """(150차-C) 만들어 놓고 주인이 보는 화면에 안 닿던 사고의 그물."""
    if not _있나():
        return
    def 지우기(a):
        for h in a["가설"][:3]:
            h["채택거리"] = None
    이상 = _깨고_보기(지우기)
    assert any("채택 거리" in r["검사"] for r in 이상), 이상


def test_잣대차이가_어긋나면_잡는다():
    """(150차-D) 같은 앱이 '정배열'을 두 뜻으로 쓰던 것."""
    if not _있나():
        return
    이상 = _깨고_보기(
        lambda a: a.__setitem__("잣대차이", {"종목수": 7, "종목들": []}))
    assert any("잣대" in r["검사"] for r in 이상), 이상


def test_점검기는_아무것도_고치지_않는다():
    """읽기 전용 약속 — 돌린 뒤 파일이 한 글자도 안 바뀌어야 합니다."""
    if not _있나():
        return
    with open(APP, "rb") as f:
        전 = f.read()
    sv.화면과_데이터를_맞댄다()
    with open(APP, "rb") as f:
        후 = f.read()
    assert 전 == 후, "점검기가 화면 파일을 고쳤습니다"


def test_스킬_문서가_실제_파일과_짝이_맞는다():
    """(150차-N) 스킬이 없는 스크립트를 부르면 주인이 부를 때 죽습니다."""
    skill = os.path.join(ROOT, ".claude", "skills", "screen-verify", "SKILL.md")
    assert os.path.exists(skill), "screen-verify 스킬 문서가 없습니다"
    with open(skill, encoding="utf-8") as f:
        글 = f.read()
    assert "python3 screen_verify.py" in 글, "스킬이 점검기를 부르지 않습니다"
    assert os.path.exists(os.path.join(ROOT, "screen_verify.py")), \
        "스킬이 부르는 screen_verify.py 가 없습니다"
    # 모바일 확인 단계가 빠지면 주인이 보는 화면을 아무도 안 봅니다
    assert "412" in 글, "스킬에 모바일 412px 확인 단계가 없습니다"


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    passed = failed = 0
    for name, fn in tests:
        try:
            fn(); print(f"  ✅ {name}"); passed += 1
        except AssertionError as e:
            print(f"  ❌ {name} — {e}"); failed += 1
        except Exception as e:
            print(f"  💥 {name} — {type(e).__name__}: {e}"); failed += 1
    print(f"\n화면 정합성 점검기 검증: {passed}개 통과, {failed}개 실패")
    sys.exit(1 if failed else 0)
