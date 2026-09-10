"""
test_workflows.py — 로봇 일정표(깃허브 워크플로) 검증 (182차)
==============================================================

실행: python3 tests/test_workflows.py

왜 시험하나 — 실제로 일어난 사고:

  `collect_request.yml`(앱에서 종목을 요청하면 로봇이 수집하는 길)은
  만든 날부터 **60번의 푸시 내내 한 번도 돌지 않았습니다.** 단계 이름표를
  `id: 코드` 처럼 한글로 적었기 때문입니다. 깃허브는 이름표(id)를
  영문·숫자·밑줄·붙임표로만 받습니다.

  더 나쁜 것은 **조용하다**는 점입니다. 깃허브는 파일을 못 읽으면
  "무엇이 잘못됐다"고 적어 주지 않고, 일감이 0개인 빈 실패만 남깁니다.
  액션 목록에는 그저 빨간 X 하나가 뜨고, 그것도 푸시 때마다 생기니
  "원래 저런가 보다" 하고 지나치기 쉽습니다. 그래서 3주 가까이
  아무도 몰랐습니다.

  이 시험은 그 무리(한글 이름표·한글 환경변수·셸에서 못 쓰는 이름)를
  **커밋 전에** 잡습니다. 눈에 보이는 설명(name·주석)은 한글 그대로 씁니다 —
  막는 것은 "기계가 이름으로 읽는 자리"뿐입니다.
"""

import os
import re
import sys

뿌리 = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
일정표_폴더 = os.path.join(뿌리, ".github", "workflows")

# 깃허브가 이름표(id)로 받아 주는 모양. 첫 글자는 영문이나 밑줄.
영문이름표 = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")
# 셸(bash)이 변수 이름으로 받아 주는 모양. 환경변수 이름도 이것을 따릅니다.
셸이름 = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def 일정표들():
    """.github/workflows 안의 워크플로 파일을 (이름, 본문) 으로 돌려줍니다."""
    나온것 = []
    for 파일 in sorted(os.listdir(일정표_폴더)):
        if 파일.endswith(".yml") or 파일.endswith(".yaml"):
            경로 = os.path.join(일정표_폴더, 파일)
            나온것.append((파일, open(경로, encoding="utf-8").read()))
    assert 나온것, "워크플로 파일을 하나도 못 찾았습니다"
    return 나온것


def 들여쓰기(줄):
    return len(줄) - len(줄.lstrip(" "))


def env_이름들(본문):
    """`env:` 아래에 적힌 환경변수 이름을 모읍니다.

    `env:` 줄보다 더 깊이 들여쓴 `이름: 값` 줄만 그 블록에 속합니다.
    들여쓰기가 같아지거나 얕아지면 블록이 끝난 것입니다.
    """
    이름들 = []
    안쪽깊이 = None
    for 줄 in 본문.splitlines():
        if not 줄.strip() or 줄.lstrip().startswith("#"):
            continue
        깊이 = 들여쓰기(줄)
        if 안쪽깊이 is not None:
            if 깊이 >= 안쪽깊이:
                m = re.match(r"^\s*([^\s:#][^:]*):", 줄)
                if m:
                    이름들.append(m.group(1).strip())
                continue
            안쪽깊이 = None          # 블록이 끝났습니다
        if re.match(r"^\s*env:\s*(#.*)?$", 줄):
            안쪽깊이 = 깊이 + 1
    return 이름들


def test_단계_이름표는_영문이어야_한다():
    """`id:` 가 한글이면 깃허브가 파일 자체를 못 읽습니다 (사고 원인)."""
    for 파일, 본문 in 일정표들():
        for 번호, 줄 in enumerate(본문.splitlines(), 1):
            m = re.match(r"^\s*id:\s*(\S+)\s*$", 줄)
            if not m:
                continue
            이름표 = m.group(1).strip("\"'")
            assert 영문이름표.match(이름표), (
                f"{파일}:{번호} 이름표 '{이름표}' 는 깃허브가 못 읽습니다 "
                f"— 영문·숫자·_·- 로만 적으세요"
            )


def test_환경변수_이름은_셸이_읽을_수_있어야_한다():
    """`제목: …` 같은 한글 환경변수는 bash 가 이름으로 인정하지 않습니다."""
    for 파일, 본문 in 일정표들():
        for 이름 in env_이름들(본문):
            assert 셸이름.match(이름), (
                f"{파일} 의 환경변수 '{이름}' 은 셸이 못 읽습니다 "
                f"— 영문 대문자로 적으세요 (설명은 주석에)"
            )


def test_셸_안에서_한글_변수를_쓰지_않는다():
    """`${제목#수집:}` 같은 꼴은 bash 에서 'bad substitution' 으로 죽습니다."""
    쓰임 = re.compile(r"\$\{?([A-Za-z_가-힣][A-Za-z0-9_가-힣]*)")
    for 파일, 본문 in 일정표들():
        for 번호, 줄 in enumerate(본문.splitlines(), 1):
            if 줄.lstrip().startswith("#"):
                continue
            # ${{ ... }} 은 깃허브가 먼저 갈아 끼우는 자리라 셸과 무관합니다
            셸부분 = re.sub(r"\$\{\{.*?\}\}", "", 줄)
            for 변수 in 쓰임.findall(셸부분):
                assert 셸이름.match(변수), (
                    f"{파일}:{번호} 셸 변수 '{변수}' 에 한글이 있습니다 "
                    f"— bash 는 한글 이름을 못 씁니다"
                )


def test_워크플로마다_이름이_적혀_있다():
    """`name:` 이 없으면 액션 목록에 파일 경로가 그대로 떠 알아보기 어렵습니다."""
    for 파일, 본문 in 일정표들():
        assert re.search(r"^name:\s*\S", 본문, re.M), (
            f"{파일} 에 맨 위 name: 이 없습니다"
        )


def test_참조한_단계_이름표가_실제로_있다():
    """`steps.없는이름.outputs…` 은 조용히 빈 값이 되어 단계가 통째로 건너뛰어집니다."""
    for 파일, 본문 in 일정표들():
        있는것 = set(re.findall(r"^\s*id:\s*(\S+)\s*$", 본문, re.M))
        있는것 = {x.strip("\"'") for x in 있는것}
        for 쓴것 in set(re.findall(r"steps\.([A-Za-z0-9_-]+)\.", 본문)):
            assert 쓴것 in 있는것, (
                f"{파일} 이 steps.{쓴것} 을 쓰는데 그런 id: 가 없습니다"
            )


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
    print(f"\n로봇 일정표 검증: {passed}개 통과, {failed}개 실패")
    sys.exit(1 if failed else 0)
