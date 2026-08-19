"""
test_repo_config.py — 세션 설정·훅 검증 (110차)
================================================

실행: python3 tests/test_repo_config.py

왜 시험하나: 설정 파일이 깨지면 **아무 오류 없이** 권한·훅이 통째로
꺼집니다 (JSON 한 글자만 깨져도). 훅이 고장 나는 방향도 조용합니다 —
복구를 안 하면 매 세션이 옛 커밋에서 헤매고, 반대로 **잘못 복구하면
로컬 작업이 소리 없이 날아갑니다.** 후자가 훨씬 위험하므로 둘 다 못박습니다.
"""

import json
import os
import subprocess
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
HOOK = os.path.join(ROOT, ".claude", "hooks", "session_start.sh")


def _run_hook(workdir):
    r = subprocess.run(
        ["bash", HOOK], env={**os.environ, "MODEL_DIR": workdir},
        capture_output=True, text=True, timeout=60,
    )
    return r


def _scratch_clone(tmp):
    subprocess.run(["git", "clone", "-q", ROOT, tmp], check=True, timeout=60)
    return tmp


def test_설정파일이_유효한_JSON_이고_훅이_연결돼_있다():
    d = json.load(open(os.path.join(ROOT, ".claude", "settings.json")))
    assert d.get("permissions", {}).get("allow"), "권한 목록이 비었습니다"
    hooks = d.get("hooks", {}).get("SessionStart")
    assert hooks, "SessionStart 훅이 설정에 없습니다"
    cmd = hooks[0]["hooks"][0]["command"]
    assert "session_start.sh" in cmd
    assert os.path.exists(HOOK), "훅 스크립트 파일이 없습니다"


def test_초기화_상태면_무손실_복구한다():
    """뒤처짐>0 · 미커밋 0 · 로컬 커밋 0 — 잃을 것이 없음이 증명된 경우."""
    import shutil
    tmp = "/tmp/claude-0/hook_t1"
    shutil.rmtree(tmp, ignore_errors=True)
    _scratch_clone(tmp)
    subprocess.run(["git", "reset", "-q", "--hard", "HEAD~2"], cwd=tmp, check=True)
    r = _run_hook(tmp)
    assert r.returncode == 0, r.stderr
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=tmp,
                          capture_output=True, text=True).stdout.strip()
    remote = subprocess.run(["git", "rev-parse", "origin/main"], cwd=tmp,
                            capture_output=True, text=True).stdout.strip()
    assert head == remote, "초기화 상태인데 복구하지 않았습니다"
    assert "자동 복구" in r.stdout
    shutil.rmtree(tmp, ignore_errors=True)


def test_로컬_작업이_있으면_절대_건드리지_않는다():
    """이 시험이 지키는 것이 가장 중요합니다 — 잘못 복구하면 로컬 커밋이
    소리 없이 사라집니다. 뒤처짐 + 로컬 커밋 → 보고만 해야 합니다."""
    import shutil
    tmp = "/tmp/claude-0/hook_t2"
    shutil.rmtree(tmp, ignore_errors=True)
    _scratch_clone(tmp)
    subprocess.run(["git", "reset", "-q", "--hard", "HEAD~2"], cwd=tmp, check=True)
    open(os.path.join(tmp, "local_work.txt"), "w").write("x")
    subprocess.run(["git", "add", "local_work.txt"], cwd=tmp, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-qm", "local"], cwd=tmp, check=True)
    before = subprocess.run(["git", "rev-parse", "HEAD"], cwd=tmp,
                            capture_output=True, text=True).stdout.strip()
    r = _run_hook(tmp)
    after = subprocess.run(["git", "rev-parse", "HEAD"], cwd=tmp,
                           capture_output=True, text=True).stdout.strip()
    assert after == before, "로컬 커밋이 날아갔습니다 — 훅이 과잉 복구합니다"
    assert "건드리지 않았습니다" in r.stdout
    shutil.rmtree(tmp, ignore_errors=True)


def test_훅은_어떤_경우에도_세션을_막지_않는다():
    """저장소가 아예 없어도 종료코드 0 — 훅 실패가 세션 시작을 막으면 안 됩니다."""
    r = _run_hook("/tmp/claude-0/없는_경로_xyz")
    assert r.returncode == 0, "훅이 0이 아닌 코드로 끝나 세션을 막습니다"


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
    print(f"\n세션 설정·훅 검증: {passed}개 통과, {failed}개 실패")
    sys.exit(1 if failed else 0)
