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


def test_수집이_실제로_분기를_돌려준다():
    """(150차-S) **이 시험이 없어서 한 번도 안 되는 코드를 커밋했습니다.**

    무슨 일이 있었나: `collect_job.collect_fundamentals()` 가 분기 자료를
    돌려준다고 **짐작하고** 썼습니다. 그 함수는 진단 보고만 돌려주고
    분기는 버립니다. 그래서 워크플로가 언제나 "분기 0개 → 실패"로
    끝났습니다. 실제로 깃허브에서 돌려 보고서야 알았습니다.

    그때 있던 시험들은 **입력 검사·글자 있나**만 봤지 수집 함수를
    **한 번도 부르지 않았습니다.** 여기서 실제로 부릅니다 — SEC 는
    안 두드리고 가짜로 갈아끼워서.
    """
    import config as cfg
    import market_data as md
    import sec_fundamentals as sf
    import fixtures as fx

    분기 = fx.make_quarters([10, 12, 14, 16, 18, 21, 25, 30, 36, 43, 52, 62])
    for i, q in enumerate(분기):
        q["adj_eps"] = 0.10 + 0.02 * i
        q["announced_date"] = q["filing_date"]
    주가 = fx.trending_daily(900)

    옛것 = (sf.get_fundamentals, sf._ensure_identity, md.fetch_daily_data)
    try:
        sf._ensure_identity = lambda *a, **k: None
        sf.get_fundamentals = lambda t, *a, **k: (분기, {})
        md.fetch_daily_data = lambda ts, *a, **k: (
            {t: 주가 for t in ts}, [])
        수집 = co.한종목_수집("ZZTEST", progress=lambda *a: None)
    finally:
        sf.get_fundamentals, sf._ensure_identity, md.fetch_daily_data = 옛것

    assert 수집["eps"], "분기를 하나도 못 받았습니다 — 수집 경로가 끊겼습니다"
    assert len(수집["eps"]) == len(분기), \
        f"분기 {len(분기)}개를 넣었는데 {len(수집['eps'])}개가 나왔습니다"
    assert "adj_eps" in 수집["eps"][0], \
        f"판정 표본과 칸 이름이 다릅니다: {sorted(수집['eps'][0])[:6]}"

    # 주가는 **판정 표본과 같은 모양**이어야 dataset 이 읽습니다
    for t in ("ZZTEST", cfg.BENCHMARK):
        p = 수집["prices"].get(t)
        assert isinstance(p, dict) and p.get("dates") and p.get("close"), \
            f"{t} 주가 모양이 판정 표본과 다릅니다: {type(p).__name__}"
    assert 수집["성공"] is True, 수집["말"]

    # 그리고 이 수집물로 **화면 자료가 실제로 만들어져야** 합니다
    화면 = co.화면자료_만들기(수집, progress=lambda *a: None)
    assert 화면 is not None, "수집은 됐는데 화면 자료를 못 만들었습니다"
    assert 화면.get("요청수집") is True and 화면.get("안내"), 화면.keys()


def test_단위진단이_창밖의_표기를_찾아낸다():
    """(150차-U) 주인 질문 — "값이 왜 비지? 어떻게 가져오지?"

    영업이익·EBITDA **1,800칸**이 "단위 착오 의심"으로 버려지고 있습니다
    (138종목). 매출은 단위를 제대로 읽는데 영업이익은 못 읽습니다.
    `_detect_table_unit` 은 숫자 **앞쪽 3,000자**만 훑고, 못 찾으면
    1(단위 없음)로 봅니다.

    **짐작하지 말고 원문에서 봐야 합니다.** 이 진단기가 그것을 봅니다:
    표기가 문서에 아예 없는 것인지, 있는데 창 밖이라 못 본 것인지.
    """
    import json
    import shutil
    import tempfile
    import config as cfg

    폴더 = tempfile.mkdtemp()
    옛 = cfg.RAW8K_CACHE_DIR
    try:
        cfg.RAW8K_CACHE_DIR = 폴더
        os.makedirs(os.path.join(폴더, "ZZTEST"))
        # 단위 표기는 **맨 위 한 번**, 영업이익 줄은 4,000자 뒤 — 실물 모양
        본문 = ("(in thousands)\nTotal revenue      437,003\n"
              + ("filler line for spacing\n" * 200)
              + "Non-GAAP operating income      216,722\n")
        assert len(본문) > 3000, "시험 지문이 창(3,000자)보다 짧으면 헛돕니다"
        with open(os.path.join(폴더, "ZZTEST", "a.json"), "w",
                  encoding="utf-8") as f:
            json.dump({"v": 1, "text": 본문}, f)
        r = co.단위진단("ZZTEST", progress=lambda *a: None)
    finally:
        cfg.RAW8K_CACHE_DIR = 옛
        shutil.rmtree(폴더, ignore_errors=True)

    assert r["본 문서"] == 1, r
    assert r["요약"]["매출만 단위 찾음"] == 1, (
        f"매출은 단위를 찾고 영업이익은 못 찾은 상태를 못 잡았습니다: {r['요약']}")
    사례 = r["사례"][0]
    assert 사례["매출_단위배수"] == 1000 and 사례["영업이익_단위배수"] == 1, 사례
    assert 사례["가장가까운표기"] == "thousand", 사례
    assert 사례["창_밖인가"] is True, (
        f"표기가 창(3,000자) 밖인데 그렇게 말하지 않았습니다: "
        f"거리 {사례['그 표기까지_글자수']}")
    assert "216,722" in 사례["영업이익_둘레"], 사례["영업이익_둘레"]


def test_요청수집_파일이_깃에_올라간다():
    """(150차-S) 파일을 만들고도 `.gitignore` 에 걸려 **사라졌습니다.**

    실행 로그 그대로: `The following paths are ignored by one of your
    .gitignore files: data/lookup` → `변경 없음 — 커밋 생략`.
    만들어도 저장소에 안 올라가면 주인 휴대폰에는 영원히 안 뜹니다.
    """
    import subprocess

    for 경로 in ("data/lookup/ZZTEST.json", "docs/data/t/ZZTEST.json"):
        r = subprocess.run(["git", "check-ignore", "-q", 경로],
                           cwd=ROOT, capture_output=True)
        assert r.returncode != 0, (
            f"{경로} 가 .gitignore 에 걸려 커밋되지 않습니다 — "
            "워크플로가 만들고도 버립니다")

    wf = os.path.join(ROOT, ".github", "workflows", "collect_one.yml")
    with open(wf, encoding="utf-8") as f:
        글 = f.read()
    커밋줄 = " ".join(l for l in 글.splitlines()
                   if l.strip().startswith("git add"))
    for 폴더 in ("data/lookup", "docs/data/t"):
        assert 폴더 in 커밋줄, f"워크플로가 {폴더} 를 커밋하지 않습니다"


def test_워크플로가_실제로_스크립트를_부른다():
    wf = os.path.join(ROOT, ".github", "workflows", "collect_one.yml")
    with open(wf, encoding="utf-8") as f:
        글 = f.read()
    assert "python collect_one.py" in 글, "워크플로가 수집기를 안 부릅니다"
    assert "workflow_dispatch" in 글, "손으로 실행할 수 없습니다"
    # 정기 수집과 같은 그룹이면 서로를 막습니다
    assert "group: collect-one" in 글, \
        "정기 수집과 같은 concurrency 그룹이면 서로를 막습니다"


def test_빠진분기_진단이_XBRL_시계열을_읽는다():
    """(150차-X) 분기가 통째로 없는 이유를 XBRL 원자료에서 봅니다.

    실측: 빠진 분기의 **61.7%(92건)가 Q4** 이고, 골드만삭스는 12월
    결산 분기가 아홉 해 내리 없습니다. 코드에는 `연간 − (1+2+3분기)` 로
    채우는 길이 있는데(`_fill_missing_q4`) 왜 안 통하는지 **짐작하지
    말고 원자료에서** 봅니다.

    ⚠️ 이 시험은 **SEC 를 두드리지 않습니다.** 가짜 edgar 를 끼워
    넣습니다 — 진짜로 두드리면 개발 환경에서 막혀 시험이 몇 분씩
    멈춥니다(실제로 겪었습니다).
    """
    import types
    import sec_fundamentals as sf

    가짜facts = object()
    가짜edgar = types.ModuleType("edgar")

    class _가짜회사:
        def __init__(self, t):
            self.t = t

        def get_facts(self):
            return 가짜facts

    가짜edgar.Company = _가짜회사

    삼개월 = {"2025-03-31": 10.0, "2025-06-30": 11.0, "2025-09-30": 12.0}
    연간 = {"2025-12-31": 50.0}          # 12월 분기가 없고 연간만 있음

    옛모듈 = sys.modules.get("edgar")
    옛것 = (sf._ensure_identity, sf._quarterly_series, sf._annual_series,
          sf.fetch_xbrl_approximation)
    try:
        sys.modules["edgar"] = 가짜edgar
        sf._ensure_identity = lambda *a, **k: None
        sf._quarterly_series = lambda f, c, r=None, unit="USD": (
            dict(삼개월) if c == "OperatingIncomeLoss" else {})
        sf._annual_series = lambda f, c, r=None, unit="USD": (
            dict(연간) if c == "OperatingIncomeLoss" else {})
        sf.fetch_xbrl_approximation = lambda t, s=None, r=None: [
            {"filing_date": d} for d in sorted(삼개월)]
        r = co.빠진분기_진단("ZZTEST", progress=lambda *a: None)
    finally:
        (sf._ensure_identity, sf._quarterly_series, sf._annual_series,
         sf.fetch_xbrl_approximation) = 옛것
        if 옛모듈 is None:
            sys.modules.pop("edgar", None)
        else:
            sys.modules["edgar"] = 옛모듈

    op = r["묶음"]["op_income"]
    assert op["3개월 값"] == 3 and op["연간 값"] == 1, op
    assert op["채워서 새로 생긴 분기"] == 1, (
        f"연간에서 4분기를 못 채웠습니다: {op}")
    assert "2025-12-31" in op["새로 생긴 것 보기"], op
    # gaap_eps 는 **일부러** 안 채웁니다 (주식 수가 분기마다 달라 뺄셈이 안 됨)
    assert r["묶음"]["gaap_eps"]["gaap_eps 는 일부러 안 채움"] is True

    with open(os.path.join(ROOT, "collect_one.py"), encoding="utf-8") as f:
        코드 = f.read()
    assert "구멍 = 빠진분기_진단(ticker)" in 코드, \
        "진단을 만들고 실행 경로에 안 붙였습니다 (글자만 있으면 헛돕니다)"
    assert '"빠진분기진단": 구멍진단' in 코드, \
        "진단 결과를 파일에 안 적습니다 — 남지 않으면 볼 수 없습니다"


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
