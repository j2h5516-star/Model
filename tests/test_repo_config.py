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
sys.path.insert(0, ROOT)      # config 를 불러오기 위해 (122차 유니버스 시험)


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




# ---------------------------------------------------------------------------
# 유니버스 정합성 (122차 4차 확장 — 205종목)
# ---------------------------------------------------------------------------
def test_유니버스는_중복없이_전부_분류돼_있다():
    """종목을 넣고 분류표에 넣는 것을 잊으면 화면이 '미분류'로 말없이
    떨어지고, 묶음 층이 비면 주도 계산에서 통째로 빠집니다. 확장 때마다
    사람이 눈으로 확인하지 않도록 시험이 대신 셉니다."""
    import config as cfg

    중복 = [t for t in set(cfg.TICKERS) if cfg.TICKERS.count(t) > 1]
    assert not 중복, f"종목이 두 번 들어갔습니다: {중복}"

    빠진섹터 = [t for t in cfg.TICKERS if t not in cfg.SECTORS]
    assert not 빠진섹터, f"업종 분류가 없는 종목: {빠진섹터}"

    빠진묶음 = [t for t in cfg.TICKERS if t not in cfg.GROUPS]
    assert not 빠진묶음, f"사이클 묶음이 없는 종목: {빠진묶음}"

    빠진층 = sorted({g for g in cfg.GROUPS.values()
                   if g not in cfg.GROUP_LAYERS})
    assert not 빠진층, f"층이 없는 묶음: {빠진층}"


def test_발견종목은_유니버스_안에_있고_판정에서_빠진다():
    """발견 29종목은 판정 표본에서 빼기로 7차에 등록했습니다. 확장을
    하다가 이 목록의 종목을 유니버스에서 빼면 등록이 깨집니다."""
    import config as cfg

    없는것 = [t for t in cfg.MEASURE_DISCOVERY_TICKERS
            if t not in cfg.TICKERS]
    assert not 없는것, f"발견 종목이 유니버스에서 사라졌습니다: {없는것}"
    assert len(cfg.MEASURE_DISCOVERY_TICKERS) == 29




# ---------------------------------------------------------------------------
# 웹앱 배선 (132차) — 로봇이 매일 웹앱 데이터를 만들고 커밋하는가
# ---------------------------------------------------------------------------
def test_로봇이_웹앱_데이터를_만들고_커밋한다():
    """배선이 빠지면 웹앱은 **조용히 옛 데이터로 굳습니다** — 화면은 멀쩡해
    보이는데 며칠 전 숫자를 보여 주는 최악의 고장입니다. 그래서 못박습니다."""
    path = os.path.join(ROOT, ".github", "workflows", "collect.yml")
    with open(path, encoding="utf-8") as f:
        text = f.read()
    assert "python web_build.py" in text, "웹앱 데이터 만들기 단계가 없습니다"
    assert "git add data/measure docs/data" in text, \
        "커밋 대상에 docs/data 가 빠졌습니다"
    # 충돌 재커밋 경로에서도 웹앱 데이터를 지키는가 (2026-08-14 사고의 교훈)
    재커밋 = text.split("push 거부")[-1]   # 마지막 조각 = 실제 재커밋 코드
    assert "web_backup" in 재커밋, "충돌 재커밋 때 웹앱 데이터를 잃습니다"


def test_시간예산이_워크플로_한도보다_넉넉히_작다():
    """(150차-G) 예산이 깃허브 한도를 넘으면 런이 **일하는 중에 죽습니다**
    — 그날 수집이 통째로 버려집니다(94차에 적은 "표본을 늘리려다 있던
    표본을 깎는 최악").

    예산은 8-K 훑기 구간만 재고, 그 뒤에 주가 수집·판정·웹앱 빌더가
    더 붙습니다. 그래서 한도가 예산보다 **넉넉히** 커야 합니다.
    """
    import re
    import config as cfg
    path = os.path.join(ROOT, ".github", "workflows", "collect.yml")
    with open(path, encoding="utf-8") as f:
        text = f.read()
    m = re.search(r"^\s*timeout-minutes:\s*(\d+)", text, re.M)
    assert m, "워크플로에 timeout-minutes 가 없습니다"
    한도 = int(m.group(1))
    예산 = cfg.COLLECT_BUDGET_MINUTES
    assert 예산 < 한도, (
        f"시간 예산 {예산}분이 워크플로 한도 {한도}분 이상입니다 — "
        "런이 일하는 중에 잘립니다")
    여유 = 한도 - 예산
    assert 여유 >= 60, (
        f"예산 {예산}분과 한도 {한도}분의 여유가 {여유}분뿐입니다 — "
        "8-K 훑기 뒤에 주가·판정·웹앱 빌더가 더 걸립니다 "
        "(08-22 실측: 훑기 뒤 단계에 약 20분)")


def test_워크플로_주석이_일꾼수를_거꾸로_말하지_않는다():
    """(150차-G) 워크플로 주석이 일꾼을 3 으로 못박고 있었는데 **코드는
    이미 6** 이었습니다. 142차에 올리고 148차에 초당 0.28요청(허용 10의
    3%)으로 안전을 확인했는데 주석만 반대로 말하고 있었습니다.

    주석이 코드와 반대면 다음 사람이 그 주석을 믿고 되돌립니다.
    """
    import re
    import config as cfg
    path = os.path.join(ROOT, ".github", "workflows", "collect.yml")
    with open(path, encoding="utf-8") as f:
        text = f.read()
    # "일꾼 수는 N 그대로" 처럼 지금 값을 못박는 문구가 있다면 실제와 맞아야
    for m in re.finditer(r"일꾼 수는\s*(\d+)\s*(?:개\s*)?그대로", text):
        assert int(m.group(1)) == cfg.COLLECT_WORKERS, (
            f"워크플로 주석은 일꾼 {m.group(1)}이라 하는데 "
            f"config 는 {cfg.COLLECT_WORKERS} 입니다")


def test_설계도가_실제_모듈을_전부_적고_있다():
    """(150차-I) `설계도.md` 는 새 세션이 **모듈 경계를 배우는** 문서인데,
    실제로 있는 파이썬 파일 **14개가 빠져** 있었습니다(sector_model ·
    leadership · vendor_feed · vendor_compare · web_build · model_verify
    등). 새 세션이 시스템을 절반만 보게 됩니다.

    문서는 **아무도 안 보면 반드시 썩습니다** — 시험이 대신 봅니다.
    새 파일을 지으면 설계도에도 적어야 통과합니다.
    """
    import re
    with open(os.path.join(ROOT, "설계도.md"), encoding="utf-8") as f:
        적힌것 = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*\.py", f.read()))
    실제 = {f for f in os.listdir(ROOT) if f.endswith(".py")}
    빠짐 = sorted(실제 - 적힌것)
    assert not 빠짐, (
        f"설계도.md 에 안 적힌 모듈: {빠짐} — 새 세션은 이 부품들이 "
        "없는 줄 압니다")
    유령 = sorted(적힌것 - 실제)
    assert not 유령, (
        f"설계도.md 가 없는 파일을 말합니다: {유령} — 지워졌는데 "
        "문서만 남았습니다")


def test_웹앱_화면_파일이_그대로_있다():
    """index/style/app.js 중 하나만 빠져도 화면이 통째로 죽습니다."""
    for name in ("index.html", "style.css", "app.js", "manifest.json"):
        p = os.path.join(ROOT, "docs", name)
        assert os.path.exists(p), f"docs/{name} 이 없습니다"
    with open(os.path.join(ROOT, "docs", "app.js"), encoding="utf-8") as f:
        js = f.read()
    assert "data/app.json" in js, "웹앱이 데이터 경로를 잃었습니다"

    # 외부 인터넷에서 무엇을 **받아오면** 안 됩니다 (오프라인·차단 환경 대비).
    #
    # ⚠️ 원래는 "http" 글자가 있기만 해도 실패였는데, 150차-O 에서 **사람이
    #    눌러야 열리는 링크**(없는 종목을 수집하러 가는 GitHub 실행 화면)를
    #    넣자 걸렸습니다. 규칙을 약화하는 대신 **뜻을 정확히** 나눕니다 —
    #    금지해야 할 것은 "화면이 스스로 바깥에서 받아오는 것"이고,
    #    사람이 누르는 <a href> 는 아무것도 안 받아옵니다.
    받아오기 = ("fetch(", "XMLHttpRequest", "importScripts",
              "<script src", "<link rel", "@import", "new Image(",
              "navigator.sendBeacon")
    for 표 in 받아오기:
        if 표 in js:
            자리 = js.index(표)
            토막 = js[max(0, 자리 - 80):자리 + 120]
            assert "http://" not in 토막 and "https://" not in 토막, (
                f"웹앱이 바깥에서 받아옵니다({표}) — 자기 파일만 읽어야 합니다:"
                f" …{토막.strip()[:160]}…")
    # 링크로 나가는 바깥 주소는 **이 저장소의 깃허브**만 허용합니다
    import re
    바깥 = set(re.findall(r"https?://[A-Za-z0-9./_-]+", js))
    허용 = {u for u in 바깥 if u.startswith("https://github.com/j2h5516-star/Model")}
    남은 = 바깥 - 허용
    assert not 남은, f"허용되지 않은 바깥 주소: {sorted(남은)}"


def test_실행스위치_뒤에_갇힌_시험이_없다():
    """(150차-V) **두 번째로 같은 사고가 났습니다.**

    시험 파일은 맨 아래 `if __name__ == "__main__":` 안에서 `globals()` 를
    훑어 자기 시험을 돌립니다. 그 블록보다 **아래**에 적힌 시험 함수는
    그 시점에 아직 만들어지지 않았고, 블록이 `sys.exit` 로 끝나므로
    **영원히 안 돕니다.** 그런데 초록불은 그대로 뜹니다.

    · 1차: 실행 스위치 뒤에 적혀 한 번도 안 돈 시험 **12개** (인수인계 4장)
    · 2차: `test_parsing.py` 에 **8개** — 그중 2개는 제가 만든 것도
      아닌, 예전부터 잠들어 있던 것이었습니다 (150차-V).

    사람이 조심해서 될 일이 아닙니다. 기계가 막습니다.

    ⚠️ **이 검사 자체에도 결함이 있었습니다** (150차-AE). 처음에는
    `글.find('if __name__ ==')` 로 스위치를 찾았는데, 그 글자가 바로 이
    설명글 안에도 들어 있어서 **설명글을 스위치로 착각**했습니다. 그래서
    이 함수 뒤에 시험을 하나 더 넣자 가짜 경보가 떴습니다.
    지금은 **줄 맨 앞에서 시작하는 것**만 스위치로 봅니다 — 설명글 안의
    인용은 들여쓰기가 있어 걸리지 않습니다.
    """
    import re

    d = os.path.join(ROOT, "tests")
    갇힘 = []
    # 줄 **맨 앞**(들여쓰기 없음)에서 시작하는 것만 진짜 실행 스위치입니다
    스위치 = re.compile(r"^if __name__ ==", re.M)
    for f in sorted(os.listdir(d)):
        if not (f.startswith("test_") and f.endswith(".py")):
            continue
        with open(os.path.join(d, f), encoding="utf-8") as fh:
            글 = fh.read()
        m = 스위치.search(글)
        if m is None:
            갇힘.append(f"{f}: 실행 스위치가 아예 없습니다")
            continue
        i = m.start()
        뒤 = re.findall(r"^def (test_\w+)", 글[i:], re.M)
        if 뒤:
            갇힘.append(f"{f}: 스위치 뒤에 {len(뒤)}개 — {뒤}")
    assert not 갇힘, (
        "실행 스위치 **뒤**에 적혀 한 번도 돌지 않는 시험이 있습니다. "
        "그 블록을 파일 맨 끝으로 옮기세요:\n  " + "\n  ".join(갇힘))


def test_공시원문_캐시는_시간초과에도_저장된다():
    """(150차-AE) **스스로 못 빠져나오는 덫**이었습니다.

    2026-08-23 런이 300분 한도에 걸려 취소되자, `actions/cache@v4` 의
    저장 단계가 통째로 건너뛰어졌습니다(그 액션은 성공했을 때만
    저장합니다). 그래서 4시간 26분 동안 받은 새 151종목 공시 원문이
    전부 버려졌습니다. 다음 날도 캐시가 비어 있으니 똑같이 4시간 반이
    걸리고 또 취소됩니다 — 영원히 반복됩니다.

    그래서 복원(restore)과 저장(save)을 나누고, 저장에 always 조건을
    답니다. 이 한 줄이 덫을 끊습니다.
    """
    import re
    path = os.path.join(ROOT, ".github", "workflows", "collect.yml")
    with open(path, encoding="utf-8") as f:
        글 = f.read()

    assert "actions/cache/restore@v4" in 글, "캐시 복원 단계가 없습니다"
    assert "actions/cache/save@v4" in 글, (
        "캐시 **저장** 단계가 없습니다 — 시간 초과 시 받은 원문이 버려집니다")
    # 통짜 액션은 성공했을 때만 저장하므로 다시 쓰면 안 됩니다
    assert not re.search(r"uses:\s*actions/cache@v4", 글), (
        "actions/cache 통짜를 다시 쓰고 있습니다 — 취소되면 저장이 "
        "건너뛰어집니다 (150차-AE 의 사고)")

    # 저장 단계에 always 조건이 실제로 붙어 있는가
    자리 = 글.index("actions/cache/save@v4")
    앞토막 = 글[max(0, 자리 - 400):자리]
    assert "if: always()" in 앞토막, (
        "캐시 저장에 always 조건이 없습니다 — 취소·실패하면 저장이 "
        "건너뛰어져 다음 런도 똑같이 시간 초과합니다")

    # 저장이 **수집 실행 뒤**에 와야 합니다 (앞이면 받기 전에 저장)
    assert 글.index("python collect_job.py") < 자리, (
        "캐시 저장이 수집 실행보다 앞에 있습니다 — 받기 전에 저장합니다")


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
