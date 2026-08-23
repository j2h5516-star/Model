"""
test_collect_one.py — 한 종목 즉시 수집 검증 (150차-O, 주인 요청)

가장 중요한 약속:
  · **판정 표본(snapshot.json)을 절대 건드리지 않는다** — 궁금해서 부른
    종목을 표본에 넣으면 "결과를 보고 종목을 고르는 것"이 되어 사전
    등록이 무너집니다(헌법 2조).
  · 종목 코드가 아닌 입력은 **거절**한다 (아무 글자나 로봇을 돌리지 않음).
  · 받아 온 종목은 화면이 "요청 수집분"이라고 **먼저 말한다**.

실행: python3 tests/test_collect_one.py
"""

import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

import collect_one as co  # noqa: E402


def test_종목코드가_아니면_거절한다():
    """아무 글자나 받으면 엉뚱한 요청이 로봇을 돌립니다."""
    for 나쁜것 in ("", "   ", "NVDA; rm -rf /", "TOOOOLONGCODE",
                 "한글", "AA BB", "<script>"):
        assert co.종목코드_정리(나쁜것) is None, f"{나쁜것!r} 를 통과시켰습니다"
    assert co.종목코드_정리(" nvda ") == "NVDA"
    assert co.종목코드_정리("BRK.B") == "BRK.B"


def test_판정표본을_건드리지_않는다():
    """(150차-O) 가장 중요한 약속 — 코드와 워크플로 양쪽에서 못박습니다."""
    with open(os.path.join(ROOT, "collect_one.py"), encoding="utf-8") as f:
        코드 = f.read()
    assert "snapshot.json" not in 코드.split('"""', 2)[2], (
        "collect_one 이 판정 표본 파일을 만집니다")
    assert "data/lookup" in 코드 or "LOOKUP_DIR" in 코드

    wf = os.path.join(ROOT, ".github", "workflows", "collect_one.yml")
    with open(wf, encoding="utf-8") as f:
        글 = f.read()
    # 커밋 대상에 data/measure 가 있으면 표본이 오염됩니다
    커밋줄 = [l for l in 글.splitlines() if l.strip().startswith("git add")]
    assert 커밋줄, "워크플로에 git add 가 없습니다"
    for l in 커밋줄:
        assert "data/measure" not in l, (
            f"워크플로가 판정 표본을 커밋합니다: {l.strip()}")


def test_화면자료는_요청수집이라고_말한다():
    """안 적으면 다른 종목과 같은 무게로 읽힙니다."""
    수집 = {"종목": "ZZTEST", "성공": False, "eps": [], "prices": {}}
    assert co.화면자료_만들기(수집, progress=lambda *a: None) is None, \
        "실패한 수집으로 화면 자료를 만들면 안 됩니다"

    with open(os.path.join(ROOT, "collect_one.py"), encoding="utf-8") as f:
        코드 = f.read()
    assert '"요청수집"' in 코드 and '"안내"' in 코드
    assert "판정 표본에는 쓰이지 않습니다" in 코드

    with open(os.path.join(ROOT, "docs", "app.js"), encoding="utf-8") as f:
        js = f.read()
    assert "d.요청수집" in js, "화면이 요청 수집분 표시를 안 그립니다"


def test_검색이_자료있는_종목_전부를_훑는다():
    """(150차-O) 정배열도 아니고 최근 완성도 없는 종목은 목록에 아예
    없어서 **검색해도 안 나왔습니다** — "자료가 없다"로 오해합니다."""
    with open(os.path.join(ROOT, "web_build.py"), encoding="utf-8") as f:
        wb = f.read()
    assert '"종목목록": sorted(ds["quarters"])' in wb, \
        "웹앱 자료에 종목 목록이 없습니다 — 검색이 일부만 훑습니다"
    with open(os.path.join(ROOT, "docs", "app.js"), encoding="utf-8") as f:
        js = f.read()
    assert "a.종목목록" in js, "검색 화면이 종목 목록을 안 씁니다"
    assert "못찾음판(" in js, "없는 종목을 쳤을 때 안내가 없습니다"
    # 안내가 실제로 뜨는 자리에 배선됐는가 (글자만 있으면 헛돕니다 — 150차-J·L)
    assert "판.innerHTML = 없다 ? 못찾음판(말)" in js, \
        "못찾음판이 검색 결과에 배선되지 않았습니다"


def test_워크플로가_실제로_스크립트를_부른다():
    wf = os.path.join(ROOT, ".github", "workflows", "collect_one.yml")
    with open(wf, encoding="utf-8") as f:
        글 = f.read()
    assert "python collect_one.py" in 글, "워크플로가 수집기를 안 부릅니다"
    assert "workflow_dispatch" in 글, "손으로 실행할 수 없습니다"
    # 정기 수집과 같은 그룹이면 서로를 막습니다
    assert "group: collect-one" in 글, \
        "정기 수집과 같은 concurrency 그룹이면 서로를 막습니다"


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
    print(f"\n한 종목 즉시 수집 검증: {passed}개 통과, {failed}개 실패")
    sys.exit(1 if failed else 0)
