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
    """**딴 곳에 복사해** 망가뜨린 뒤, 점검기가 그 파일을 보게 합니다.

    ⚠️ 150차-AQ 에 방식을 바꿨습니다. 예전에는 **진짜 app.json 을 덮어쓰고**
       finally 에서 되돌렸는데, 시험이 도는 도중에 프로세스가 죽으면
       (시간 초과·Ctrl-C·컨테이너 재시작) 되돌리기가 실행되지 않아
       **저장소의 화면 데이터가 망가진 채로 남습니다.** 실제로 그 일이
       일어나 다음 시험이 "완성 목록 135줄 · 서로 다른 종목 133개" 라는
       거짓 경보를 냈고, 원인을 찾는 데 시간을 썼습니다.
       이제는 진짜 파일을 **건드리지 않습니다.**
    """
    임시 = tempfile.mktemp(suffix=".json")
    본디 = sv.APP_JSON
    try:
        with open(APP, encoding="utf-8") as f:
            a = json.load(f)
        바꾸기(a)
        with open(임시, "w", encoding="utf-8") as f:
            json.dump(a, f, ensure_ascii=False)
        sv.APP_JSON = 임시
        return [r for r in sv.화면과_데이터를_맞댄다() if r["상태"] == "⛔"]
    finally:
        sv.APP_JSON = 본디
        if os.path.exists(임시):
            os.unlink(임시)


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


def test_어긋난_분기이름을_센다():
    """(150차-T) 분기 **이름**이 그 종목 안에서 앞뒤가 안 맞는 칸 세기.

    회계연도가 달력과 어긋나는 회사도 **어긋난 폭은 일정**하므로,
    일정하면 세지 않고 들쭉날쭉할 때만 셉니다.
    """
    def 행(라벨, 끝):
        return {"period_label": 라벨, "filing_date": 끝}

    # ① 달력 결산 — 이름과 기간끝이 딱 맞음 → 0건
    멀쩡 = {"quarters": {"AAA": [
        행("24 Q1", "2024-03-31"), 행("24 Q2", "2024-06-30"),
        행("24 Q3", "2024-09-30"), 행("24 Q4", "2024-12-31"),
        행("25 Q1", "2025-03-31")]}}
    assert sv._어긋난라벨_세기(멀쩡)["어긋남"] == 0, "멀쩡한 이름을 잡았습니다"

    # ② 4월 결산(CRDO 형) — 이름이 늘 2칸 밀려 있지만 **일정**함 → 0건
    밀림 = {"quarters": {"BBB": [
        행("24 Q1", "2023-07-31"), 행("24 Q2", "2023-10-31"),
        행("24 Q3", "2024-01-31"), 행("24 Q4", "2024-04-30"),
        행("25 Q1", "2024-07-31")]}}
    assert sv._어긋난라벨_세기(밀림)["어긋남"] == 0, (
        "회계연도가 달력과 다른 회사를 오탐했습니다 — "
        "폭이 일정하면 이름은 맞는 것입니다")

    # ③ SLB 실물 — 같은 이름이 네 분기에 붙음 → 다수(1개)를 뺀 3건
    거짓 = {"quarters": {"CCC": [
        행("20 Q4", "2021-03-31"), 행("20 Q4", "2021-06-30"),
        행("20 Q4", "2021-09-30"), 행("20 Q4", "2021-12-31")]}}
    r = sv._어긋난라벨_세기(거짓)
    assert r["어긋남"] == 3 and r["종목"] == 1, (
        f"네 분기에 같은 이름이 붙었는데 못 잡았습니다: {r}")

    # ④ 점검 목록에 실제로 실려 있는가 (글자만 있으면 헛돕니다)
    with open(os.path.join(ROOT, "screen_verify.py"), encoding="utf-8") as f:
        코드 = f.read()
    assert "_어긋난라벨_세기(ds)" in 코드, \
        "세는 함수를 만들고 점검 목록에 안 실었습니다"


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


def test_자릿수_깨진_매출을_센다():
    """(150차-Y) 고침이 실제로 일하는지 **매일** 확인하는 자리입니다.

    실물: GS 분기 매출 15,520 달러(실제 155억) · XOM 198 · MRK 228 —
    26행·15종목. 고침을 넣었으니 정상 상태에서는 **0** 이어야 합니다.
    """
    def 행(매출):
        return {"revenue": 매출}

    멀쩡 = {"quarters": {"AAA": [행(1e10), 행(1.1e10), 행(1.2e10), 행(1.05e10)]}}
    assert _sv_세기(멀쩡)["행"] == 0, "멀쩡한 매출을 잡았습니다"

    깨짐 = {"quarters": {"GS": [행(1e10), 행(1.1e10), 행(1.2e10), 행(1.05e10),
                              행(15_520.0)]}}
    r = _sv_세기(깨짐)
    assert r["행"] == 1 and r["종목들"] == ["GS"], r

    # 성한 행이 4개 미만이면 견줄 수가 없으므로 세지 않습니다
    모자람 = {"quarters": {"BBB": [행(1e10), 행(1.1e10), 행(15_520.0)]}}
    assert _sv_세기(모자람)["행"] == 0, "견줄 행이 모자란데 셌습니다"

    # (150차-AX) **크게 자란 회사**를 통째로 잡으면 안 됩니다.
    #
    # 이름표는 "가까운 분기의 1/100 미만"이라고 말하는데 코드는 종목
    # **전체 중앙값**과 견주고 있었습니다. 실물 ALNY 는 매출이 38M →
    # 1,291M 로 34배 자랐고, 전체 중앙값(224.8M)과 견주니 2018년 분기
    # (2.069M)가 1/100 밑으로 떨어져 ⛔ 가 났습니다. 앞뒤 이웃은
    # 21~33M 이라 자릿수가 어긋난 것이 아니라 그 시절이 작았을 뿐입니다.
    #
    # ⚠️ 시험 표본은 **실물 ALNY 그대로**를 씁니다. 처음에 스무 개만
    #    추려 넣었더니 중앙값이 178M 으로 낮아져, **옛 자로도 안 걸리는**
    #    표본이 되어 시험이 헛돌았습니다(돌연변이로 확인). 실물은 35개에
    #    중앙값 259M 이라 옛 자로는 반드시 걸립니다.
    자람 = {"quarters": {"ALNY": [행(v * 1e6) for v in (
        38, 22, 30, 2.069, 21, 33, 45, 70, 72, 99, 104, 126, 164, 178,
        221, 188, 259, 213, 225, 264, 335, 319, 319, 751, 440, 494,
        660, 501, 593, 594, 774, 1249, 1097, 1167, 1291)]}}
    assert _sv_세기(자람)["행"] == 0, (
        "크게 자란 회사의 초창기 분기를 자릿수 오류로 잡았습니다")

    # 그래도 **한 행만 단위가 어긋난 것**은 이웃과 견줘 그대로 잡힙니다
    자람깨짐 = {"quarters": {"ALNY": [행(v * 1e6) for v in (
        38, 22, 30, 21, 33, 45, 70, 99, 126, 178, 213,
        264, 319, 494, 660, 594, 1249, 1.167, 1167, 1291)]}}
    assert _sv_세기(자람깨짐)["행"] == 1, (
        "한 행만 1000배 어긋난 것을 놓쳤습니다")

    # (150차-AC) **종목 전체**가 어긋난 경우 — 고칠 수는 없어도 말은 해야
    # 합니다. 실물 GS: 전 분기가 백만 단위(15,520 = 실제 155억).
    통째 = {"quarters": {"GS": [행(10_707.0), 행(10_120.0),
                              행(10_115.0), 행(12_738.0)]}}
    r2 = _sv_세기(통째)
    assert r2["통째수상"] == ["GS"], (
        f"종목 전체가 어긋난 것을 못 잡았습니다: {r2}")
    assert r2["행"] == 0, "견줄 데가 없는데 행으로도 셌습니다"
    # 멀쩡한 종목은 여기에 안 들어와야 합니다
    assert _sv_세기(멀쩡)["통째수상"] == [], _sv_세기(멀쩡)

    with open(os.path.join(ROOT, "screen_verify.py"), encoding="utf-8") as f:
        코드2 = f.read()
    assert '적기("종목 전체 매출"' in 코드2, \
        "종목 전체 수상을 점검 항목으로 안 내보냅니다 — 화면이 조용하면 믿습니다"

    # 점검 목록에 실제로 실려 있는가 (글자만 있으면 헛돕니다)
    with open(os.path.join(ROOT, "screen_verify.py"), encoding="utf-8") as f:
        코드 = f.read()
    assert "_자릿수깨진_매출_세기(ds)" in 코드, \
        "세는 함수를 만들고 점검 목록에 안 실었습니다"


def _sv_세기(ds):
    return sv._자릿수깨진_매출_세기(ds)


def test_빈칸사유를_갈래별로_센다():
    """빈칸이 왜 비었는지 **갈래를 갈라** 세어야 합니다 (150차-AM).

    주인 지시(2026-08-24) — "빈칸이 너무 많다". 세어 보니 대부분은
    우리가 못 가져온 것이 아니라 **가져올 것이 없는 것**이었습니다.
    셋을 갈라 보이지 않으면 주인은 고칠 수 없는 것을 고치라고 하거나,
    고칠 수 있는 것을 못 보고 지나갑니다.
    """
    ds = {"quarters": {
        # 조정 EPS 를 **내는** 회사
        "AAA": [
            {"filing_date": "2024-03-31", "announced_date": "2024-04-20",
             "press_matched": True, "adj_eps": 1.0},           # 있음
            {"filing_date": "2024-06-30", "announced_date": "2024-07-20",
             "press_matched": True, "adj_eps": None},          # 회사 미발표
            {"filing_date": "2024-09-30", "announced_date": "2024-10-20",
             "press_matched": False, "adj_eps": None},         # 못 붙임 · 냄
            {"filing_date": "2023-12-31", "announced_date": None,
             "press_matched": False, "adj_eps": None},         # 발표 기록 없음
            {"filing_date": "2024-12-31", "announced_date": "2025-01-20",
             "press_matched": None, "adj_eps": None},          # 구멍 메움
        ],
        # 조정 EPS 를 **아예 안 내는** 회사
        "BBB": [
            {"filing_date": "2024-03-31", "announced_date": "2024-04-20",
             "press_matched": False, "adj_eps": None},         # 못 붙임 · 안 냄
        ],
    }}
    셈 = sv._빈칸사유_세기(ds)
    assert 셈["전체"] == 6, 셈
    assert 셈["있음"] == 1, 셈
    assert 셈["회사미발표"] == 1, 셈
    assert 셈["짝없음_냄"] == 1, 셈
    assert 셈["짝없음_안냄"] == 1, 셈
    assert 셈["그시절없음"] == 1, 셈
    assert 셈["구멍메움"] == 1, 셈


def test_빈칸사유는_회사가_내는지로_일감을_가른다():
    """8-K 를 못 붙인 것이라도, **그 회사가 애초에 안 내면** 일감이 아닙니다.

    붙여도 값이 안 나오는 것을 일감으로 세면, 영원히 안 줄어드는 숫자를
    쳐다보게 됩니다 — 0 이 될 수 없는 경보는 곧 무시됩니다.
    """
    def 만들기(다른분기에_값이_있나):
        rows = [{"filing_date": "2024-09-30", "announced_date": "2024-10-20",
                 "press_matched": False, "adj_eps": None}]
        if 다른분기에_값이_있나:
            rows.append({"filing_date": "2024-06-30",
                         "announced_date": "2024-07-20",
                         "press_matched": True, "adj_eps": 2.0})
        return {"quarters": {"AAA": rows}}

    낸다 = sv._빈칸사유_세기(만들기(True))
    안낸다 = sv._빈칸사유_세기(만들기(False))
    assert 낸다["짝없음_냄"] == 1 and 낸다["짝없음_안냄"] == 0, 낸다
    assert 안낸다["짝없음_안냄"] == 1 and 안낸다["짝없음_냄"] == 0, 안낸다


def test_빈칸사유가_화면_점검_목록에_실린다():
    """세기만 하고 보고하지 않으면 아무도 안 보는 칸이 됩니다 (150차-C)."""
    if not _있나():
        return
    이름들 = [r["검사"] for r in sv.화면과_데이터를_맞댄다()]
    assert "주 잣대 빈칸 사유" in 이름들, 이름들


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
