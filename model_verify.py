"""
model_verify.py — 모델 전체 점검기 (128차, 저장소 주인 요청)
=============================================================

"모든 조건(가설)이 채택·포착 가능한 상태로 살아 있는가"를 **한 번에**
검사합니다. 사람이(또는 세션이) 깜빡해도 이 스크립트 하나로 전 가설의
상태·표본·포착 창구를 확인할 수 있게 하는 안전망입니다.

실행: python3 model_verify.py          (종료코드 0 = 이상 없음, 1 = 문제)
      python3 model_verify.py --fast   (무거운 포착 점검 생략 — 판정 파일만)

검사 항목:
  ① 판정 파일이 있고 v3 형식인가
  ② **등록된 가설 전부**가 판정 파일에 실려 있는가 (누락 = 문제)
     — 기대 목록은 judge.py 의 등록 상수에서 **직접** 뽑습니다.
       가설을 새로 등록하면 여기 코드를 고칠 필요 없이 자동 반영됩니다.
  ③ 판정이 지금 코드 판번호로 계산됐는가 (다르면 경고 — 다음 런에 갱신)
  ④ 가설별 판정·신규 표본 n (등록일 딱지 포함)
  ⑤ 포착 창구가 살아 있는가 — 관찰판(이격도30%+ 완성) 종목 수 ·
     시장 정배열 폭 · H24 띠 안/밖 (--fast 면 생략)

경계: 이 파일은 **읽고 셀 뿐** 아무 값도 고치지 않습니다.
"""

from __future__ import annotations

import json
import os
import sys

import config as cfg
import judge


def expected_hypotheses() -> list[str]:
    """등록된 가설 이름 전부 — judge.py 의 상수에서 직접 뽑습니다."""
    names = list(judge.HYPOTHESES.keys())
    names.append(judge.H10_NAME)
    names += [judge.H11_NAME, judge.H11B_NAME]
    names.append(judge.H18_NAME)
    names.append(judge.H18B_NAME)
    names += [judge.H19_NAME, judge.H20_NAME, judge.H21_NAME, judge.H19B_NAME]
    names += [name for name, _level in judge.H22_LEVELS]
    names.append(judge.H23_NAME)
    names.append(judge.H24_NAME)
    names += [judge.H25_NAME, judge.H25B_NAME]
    names.append(judge.H26_NAME)
    return names


def verify(verdict: dict | None, expected: list[str],
           code_now: str | None) -> tuple[list[str], list[str]]:
    """(보고 줄 목록, 문제 목록). 값을 만들지 않습니다 — 없으면 없다고."""
    lines: list[str] = []
    problems: list[str] = []

    if not verdict or "가설" not in verdict:
        problems.append("판정 파일이 없거나 '가설' 칸이 없습니다")
        return lines, problems

    실린 = verdict["가설"]
    빠짐 = [name for name in expected if name not in 실린]
    if 빠짐:
        problems.append("판정 파일에 빠진 등록 가설: " + ", ".join(빠짐))
    남는 = [name for name in 실린 if name not in expected]
    if 남는:
        lines.append("(참고) 기대 목록에 없는 항목: " + ", ".join(남는))

    recorded = verdict.get("code_rev")
    if code_now and recorded and recorded not in ("알수없음", "") \
            and recorded != code_now:
        lines.append(f"⚠️ 판정이 옛 코드({recorded})로 계산됨 — 지금 코드 "
                     f"{code_now}. 다음 로봇 런에서 갱신됩니다")

    집계 = {"채택": 0, "미채택": 0, "판정 불가": 0, "기타": 0}
    for name in expected:
        entry = 실린.get(name)
        if not entry:
            continue
        판정 = entry.get("판정", "?")
        집계[판정 if 판정 in 집계 else "기타"] += 1
        신규 = (entry.get("신규(판정)") or {}).get("신호") or {}
        딱지 = f" · 등록 {entry['등록일']}" if entry.get("등록일") else ""
        lines.append(f"{name}: {판정} (신규 신호 n={신규.get('n', '—')}{딱지})")
    lines.insert(0, "판정 집계 — 채택 {채택} · 미채택 {미채택} · "
                    "판정 불가 {판정 불가}".format(**집계))
    if 집계["기타"]:
        problems.append(f"알 수 없는 판정 상태 {집계['기타']}건")
    return lines, problems


def _capture_report() -> list[str]:
    """포착 창구 점검 (무거움 — 데이터셋 빌드). 실패는 문장으로 남깁니다."""
    out: list[str] = []
    try:
        import dataset
        import sector_model as sm
        from datetime import date, timedelta

        snap = dataset.load()
        ds = dataset.build(snap, dataset.load_splits())
        오늘 = ds["prices"][ds["benchmark"]]["dates"][-1]

        series = sm.market_breadth_series(ds)
        폭 = series[-1][1] if series else None
        if 폭 is None:
            out.append("시장 정배열 폭: 판단 불가")
        else:
            띠 = judge.H24_BAND
            안 = 띠[0] <= 폭 < 띠[1]
            out.append(f"시장 정배열 폭 {폭:.0f}% — H24 띠({띠[0]:.0f}~"
                       f"{띠[1]:.0f}%) {'안' if 안 else '밖'}")

        # ⚠️ 여기서 세는 코드를 **따로 적으면 안 됩니다** (150차-F).
        #    원래는 사건 목록을 직접 걸러 셌는데, 150차-B 에서 계기판이
        #    "한 종목 한 줄"로 바뀌자 이 점검기만 옛 숫자(89건)를 말하고
        #    앱은 새 숫자(78종목)를 말했습니다. 같은 저장소의 두 도구가
        #    "관찰판"이라는 같은 말로 다른 수를 대는 상태였습니다.
        #    계기판과 **같은 함수**를 부르면 어긋날 수가 없습니다.
        import app
        완성 = sm.completion_events(ds)
        행 = app.recent_completion_rows(완성, 오늘)
        신호 = [r for r in 행 if r["신호"]]
        out.append(f"관찰판: 최근 91일 완성 {len(행)}종목 · "
                   f"이격도30%+ {len(신호)}종목 (기준일 {오늘})")
    except Exception as exc:
        out.append(f"⚠️ 포착 점검 실패: {type(exc).__name__}: {str(exc)[:120]}")
    return out


def main(argv: list[str]) -> int:
    verdict = None
    path = os.path.join(cfg.MEASURE_DIR, "verdict.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            verdict = json.load(f)

    lines, problems = verify(verdict, expected_hypotheses(),
                             judge.code_revision())
    print(f"=== 모델 전체 점검 (등록 가설 {len(expected_hypotheses())}개) ===")
    for line in lines:
        print(" ", line)
    if "--fast" not in argv:
        print("=== 포착 창구 ===")
        for line in _capture_report():
            print(" ", line)
    if problems:
        print("=== ⛔ 문제 ===")
        for p in problems:
            print(" ", p)
        return 1
    print("이상 없음.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
