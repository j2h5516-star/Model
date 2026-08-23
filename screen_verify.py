"""screen_verify.py — 화면과 데이터의 **정합성** 점검기 (150차-N, 주인 요청)

왜 필요한가 (전부 실제로 터진 일입니다):

  · 150차-B  웹앱이 제타를 "신호 아님"으로 표시했습니다. 낡은 6월 완성이
             8월 완성을 덮어써서입니다. **화면이 거짓을 말했습니다.**
  · 150차-C  "채택까지 얼마나 남았나"가 계기판에만 있고 웹앱에는 한
             글자도 없었습니다. 주인은 휴대폰만 쓰는데.
  · 150차-D  같은 앱이 "정배열"을 두 뜻으로 쓰면서 그 말을 안 했습니다.
  · 150차-F  점검기와 앱이 "관찰판"을 다른 수로 셌습니다(89 vs 78).
  · 150차-J  가설 11개가 전부 "표본 없음"이라고만 말했습니다.
  · 150차-K  주인이 "실적이 왜 빈칸이지?"라고 물어 꼬리 구멍 197건이
             그물에 안 보이던 것이 드러났습니다.

**공통점**: 계산은 멀쩡한데 **화면이 다른 말을 하거나 아무 말도 안
했습니다.** 시험은 코드를 지키지만 "웹앱에 실제로 실린 값"과 "지금
데이터로 다시 계산한 값"이 같은지는 아무도 안 봤습니다. 이 점검기가
그것을 봅니다.

실행:  python3 screen_verify.py            (종료코드 0 = 이상 없음)
       python3 screen_verify.py --json     (기계용)

**읽기 전용입니다** — 어떤 값도 고치지 않습니다.
"""

from __future__ import annotations

import json
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

WEB = os.path.join(ROOT, "docs", "data")
APP_JSON = os.path.join(WEB, "app.json")
TICKER_DIR = os.path.join(WEB, "t")


def _load(path: str):
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def 화면과_데이터를_맞댄다() -> list[dict]:
    """웹앱 파일에 실린 값 vs 지금 데이터로 다시 잰 값.

    각 항목: {"검사", "상태"("이상 없음"/"⛔"/"확인 못함"), "말"}
    **값을 고치지 않습니다** — 다른 곳을 찾아 적을 뿐입니다.
    """
    import app
    import dataset
    import judge
    import measure_engine as me
    import sector_model as sm
    import config as cfg

    결과: list[dict] = []

    def 적기(검사: str, 이상: bool, 말: str, 확인못함: bool = False):
        결과.append({"검사": 검사,
                     "상태": "확인 못함" if 확인못함 else ("⛔" if 이상 else "이상 없음"),
                     "말": 말})

    a = _load(APP_JSON)
    if a is None:
        적기("웹앱 파일", False, f"{APP_JSON} 이 없습니다 — 아직 안 만든 것", 확인못함=True)
        return 결과

    snap_path = os.path.join(cfg.MEASURE_DIR, "snapshot.json")
    snap = _load(snap_path)
    if snap is None:
        적기("수집물", False, "snapshot.json 이 없습니다", 확인못함=True)
        return 결과
    ds = dataset.build(snap)
    verdict = _load(os.path.join(cfg.MEASURE_DIR, "verdict.json"))

    # ── ① 기준일이 같은가 ────────────────────────────────────────────
    오늘 = ds["prices"][ds["benchmark"]]["dates"][-1]
    적기("기준일", a.get("기준일") != 오늘,
        f"웹앱 {a.get('기준일')} · 데이터 {오늘}")

    # ── ② 종목 수가 같은가 ──────────────────────────────────────────
    적기("종목 수", a.get("종목수") != len(ds["tickers"]),
        f"웹앱 {a.get('종목수')} · 데이터 {len(ds['tickers'])}")

    # ── ③ 신호 종목이 같은가 (150차-B 의 사고) ──────────────────────
    완성 = sm.completion_events(ds)
    행 = app.recent_completion_rows(완성, 오늘)
    지금신호 = {r["종목"] for r in 행 if r["신호"]}
    웹신호 = {r["종목"] for r in (a.get("신호종목") or [])}
    빠짐 = sorted(지금신호 - 웹신호)
    더함 = sorted(웹신호 - 지금신호)
    적기("신호 종목", bool(빠짐 or 더함),
        f"웹앱 {len(웹신호)}개 · 지금 {len(지금신호)}개"
        + (f" · 웹앱에 빠진 것 {빠짐}" if 빠짐 else "")
        + (f" · 웹앱에만 있는 것 {더함}" if 더함 else ""))

    # ── ④ 한 종목이 두 줄로 나오지 않는가 (150차-B) ─────────────────
    이름들 = [r["종목"] for r in (a.get("완성전체") or [])]
    적기("한 종목 한 줄", len(이름들) != len(set(이름들)),
        f"완성 목록 {len(이름들)}줄 · 서로 다른 종목 {len(set(이름들))}개")

    # ── ⑤ 종목별 파일의 '신호'가 목록과 어긋나지 않는가 (150차-B) ───
    어긋남 = []
    for r in (a.get("신호종목") or []):
        d = _load(os.path.join(TICKER_DIR, f"{r['종목']}.json"))
        if d is None:
            어긋남.append(f"{r['종목']}(파일 없음)")
        elif not d.get("신호"):
            어긋남.append(f"{r['종목']}(목록은 신호, 상세는 아님)")
    적기("목록 vs 종목상세", bool(어긋남),
        "어긋난 종목: " + ", ".join(어긋남) if 어긋남 else "모두 일치")

    # ── ⑥ 등록 가설이 전부 실렸는가 (150차-J 와 같은 자리) ──────────
    실린 = {h["이름"] for h in (a.get("가설") or [])}
    기대 = set(judge.expected_hypotheses()) if hasattr(judge, "expected_hypotheses") \
        else set()
    if not 기대:
        import model_verify as mv
        기대 = set(mv.expected_hypotheses())
    적기("가설 수", bool(기대 - 실린),
        f"웹앱 {len(실린)}개 · 등록 {len(기대)}개"
        + (f" · 빠짐 {sorted(기대 - 실린)}" if 기대 - 실린 else ""))

    # ── ⑦ 정직화 칸이 화면 자료에 있는가 (150차-C·J) ────────────────
    빈칸 = []
    for h in (a.get("가설") or []):
        if h.get("채택거리") is None:
            빈칸.append(h["이름"])
    적기("채택 거리", bool(빈칸),
        f"채택 거리가 없는 가설 {len(빈칸)}개" + (f" {빈칸[:5]}" if 빈칸 else ""))

    # ── ⑧ 두 '정배열' 잣대 차이가 실렸는가 (150차-D) ────────────────
    차 = a.get("잣대차이")
    지금차 = app.gauge_gap_rows(ds)
    적기("두 정배열 잣대 차이",
        (차 or {}).get("종목수") != 지금차["종목수"],
        f"웹앱 {(차 or {}).get('종목수')}개 · 지금 {지금차['종목수']}개")

    # ── ⑨ TTM 이 비었는데 델타는 나오는 종목 (150차-K·L) ────────────
    #      고장이 아니라 **빠진 분기** 때문이다. 세어서 보고만 한다.
    빠진분기 = _빠진분기_세기(ds)
    적기("빠진 분기", False,
        f"발표일이 130일 넘게 벌어진 곳 {빠진분기['구간']}건 · "
        f"{빠진분기['종목']}종목 · 빠진 분기 추정 {빠진분기['분기']}개 "
        "(원인 미규명 — 150차-K)", 확인못함=(빠진분기["구간"] > 0))

    # ── ⑩ 끊긴 이력 너머로 잰 신고점폭 (150차-K) ────────────────────
    끊김 = _끊긴폭_세기(ds)
    적기("끊김 너머 신고점폭", False,
        f"폭이 계산된 {끊김['전체']}칸 중 직전 발표에 TTM 이 없던 것 "
        f"{끊김['끊김']}칸" + (f" (최대 {끊김['최대']:,.0f}% — {끊김['종목']})"
                            if 끊김["끊김"] else ""),
        확인못함=(끊김["끊김"] > 0))

    # ── ⑪ 판정 파일이 지금 코드로 계산됐는가 ────────────────────────
    if verdict:
        적기("판정 코드", False,
            f"판정 code_rev {verdict.get('code_rev')} "
            "(다음 로봇 런에서 갱신됩니다)")

    return 결과


def _빠진분기_세기(ds: dict) -> dict:
    """발표일이 정상 분기 간격(약 91일)의 1.4배를 넘게 벌어진 곳."""
    from datetime import date
    구간 = 분기 = 0
    종목 = set()
    for t, rows in (ds.get("quarters") or {}).items():
        날 = sorted(r["announced_date"] for r in rows if r.get("announced_date"))
        for i in range(1, len(날)):
            g = (date.fromisoformat(str(날[i])[:10])
                 - date.fromisoformat(str(날[i - 1])[:10])).days
            if g > 130:
                구간 += 1
                분기 += max(1, round(g / 91) - 1)
                종목.add(t)
    return {"구간": 구간, "분기": 분기, "종목": len(종목)}


def _끊긴폭_세기(ds: dict) -> dict:
    """신고점폭이 **TTM 이 없는 구간을 건너뛰어** 계산된 칸.

    인수인계 4장 7번("끊긴 이력을 이어붙이지 마라")이 금지한 모양입니다.
    실물: CRDO 2026-06-01 폭 3,700% — 직전 정점 0.09 와 2년 떨어져 있음.
    """
    import measure_engine as me
    전체 = 끊김 = 0
    최대 = 0.0
    종목 = None
    for t, rows in (ds.get("quarters") or {}).items():
        잣 = me.yardstick_of(rows)
        if not 잣:
            continue
        앞ttm = None
        for s in me.earnings_states(rows, field=잣):
            폭 = s.get("신고점폭")
            if 폭 is not None:
                전체 += 1
                if 앞ttm is None:
                    끊김 += 1
                    if 폭 > 최대:
                        최대, 종목 = 폭, f"{t} {s['announced']}"
            앞ttm = s.get("ttm")
    return {"전체": 전체, "끊김": 끊김, "최대": 최대, "종목": 종목}


def main(argv: list[str]) -> int:
    결과 = 화면과_데이터를_맞댄다()
    if "--json" in argv:
        print(json.dumps(결과, ensure_ascii=False, indent=1))
    else:
        print("=== 화면 ↔ 데이터 정합성 점검 ===")
        for r in 결과:
            표 = {"이상 없음": "  ", "⛔": "⛔", "확인 못함": "⚠️"}[r["상태"]]
            print(f" {표} {r['검사']:18s} {r['말']}")
        이상 = [r for r in 결과 if r["상태"] == "⛔"]
        살필 = [r for r in 결과 if r["상태"] == "확인 못함"]
        print()
        if 이상:
            print(f"⛔ 화면이 데이터와 어긋납니다 — {len(이상)}건. 고치세요.")
        elif 살필:
            print(f"이상 없음 (살펴볼 것 {len(살필)}건 — 알려진 미해결이면 그대로).")
        else:
            print("이상 없음.")
    return 1 if any(r["상태"] == "⛔" for r in 결과) else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
