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
    #
    # 151차 — "**아직 안 실림**"의 두 갈래를 가릅니다. 새 가설을 등록한
    # 날은 마지막 로봇 런이 등록보다 **앞**이라 웹앱에 없는 것이 정상인데,
    # 그것까지 ⛔ 로 울리면 등록하는 날마다 거짓 경보가 납니다(실제로
    # H27·H28 등록날 시험이 그렇게 깨졌습니다). 마지막 수집 시각이 그
    # 가설의 등록일보다 앞이면 "다음 런 대기"(확인 못함)로 두고, 등록일을
    # 지나서 돈 런에도 없으면 그때가 진짜 배선 사고(⛔)입니다.
    실린 = {h["이름"] for h in (a.get("가설") or [])}
    기대 = set(judge.expected_hypotheses()) if hasattr(judge, "expected_hypotheses") \
        else set()
    if not 기대:
        import model_verify as mv
        기대 = set(mv.expected_hypotheses())
    시계 = judge.hypothesis_clock()
    수집시각 = str(a.get("수집") or "")[:10]
    말썽, 대기 = [], []
    for 이름 in sorted(기대 - 실린):
        등록일 = (시계.get(이름) or (None,))[0]
        if 등록일 and 수집시각 and 수집시각 <= 등록일:
            대기.append(이름)
        else:
            말썽.append(이름)
    적기("가설 수", bool(말썽),
        f"웹앱 {len(실린)}개 · 등록 {len(기대)}개"
        + (f" · 빠짐 {말썽}" if 말썽 else "")
        + (f" · 다음 런 대기 {대기}" if 대기 else ""),
        확인못함=bool(대기) and not 말썽)

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

    # ── ⑧b 조합 신호(H29 상태판, 154차)가 지금 값과 같은가 ──────────
    #      화면에 새 칸을 추가하면 여기에도 검사를 추가한다(150차-C 규칙).
    #      ⚠️ 재계산은 화면 빌더와 **같은 조립**(액면분할 보정 포함)으로
    #      해야 한다 — 위의 ds 는 보정 없이 지어져 CRWD 가 갈렸다(실측).
    #      snap 을 다시 읽는 이유: build 가 입력을 제자리에서 바꿀 수
    #      있어 이미 쓴 snap 을 재사용하면 이중 보정 위험이 있다.
    조합 = (a.get("조합신호") or {}).get("종목들")
    지금조합 = app.combo_now_rows(
        dataset.build(_load(snap_path), splits=dataset.load_splits()))
    화면조합 = sorted(r["종목"] for r in (조합 or []))
    실조합 = sorted(r["종목"] for r in 지금조합)
    적기("조합 신호", 조합 is None or 화면조합 != 실조합,
        f"웹앱 {len(화면조합)}개 · 지금 {len(실조합)}개"
        + ("" if 화면조합 == 실조합 else
           f" · 어긋남 {sorted(set(화면조합) ^ set(실조합))[:6]}"))

    # ── ⑨ TTM 이 비었는데 델타는 나오는 종목 (150차-K·L) ────────────
    #      고장이 아니라 **빠진 분기** 때문이다. 세어서 보고만 한다.
    빠진분기 = _빠진분기_세기(ds)
    적기("빠진 분기", False,
        f"발표일이 130일 넘게 벌어진 곳 {빠진분기['구간']}건 · "
        f"{빠진분기['종목']}종목 · 빠진 분기 추정 {빠진분기['분기']}개 "
        "(157차: 은행은 XBRL 매출 계열이 없어 원리적으로 못 채움)",
        확인못함=(빠진분기["구간"] > 0))

    # ── ⑨b 한 칸도 못 모은 종목 (159차) ────────────────────────────
    #      실측: 149차에 새로 넣은 6종목(ZI·CFLT·DFS·HOLX·X·HES)이 8일
    #      내내 "회사를 못 찾음"으로 **한 건도** 수집되지 않았는데, 매일
    #      점검 어디에도 그 사실이 뜨지 않았다. 유니버스에 이름만 있고
    #      값은 없는 종목은 조용히 측정에서 빠진다 — 인수로 사라진 회사가
    #      이렇게 빠지면 **살아남은 것만 재는 편향**이 생긴다.
    빈종목 = sorted(t for t in ds["tickers"]
                  if not (ds.get("quarters") or {}).get(t))
    적기("빈 종목", False,
        f"실적이 한 칸도 없는 종목 {len(빈종목)}개"
        + (f" — {', '.join(빈종목[:12])}" if 빈종목 else ""),
        확인못함=bool(빈종목))

    # ── ⑩ 끊긴 이력 너머로 잰 신고점폭 (150차-K) ────────────────────
    끊김 = _끊긴폭_세기(ds)
    적기("끊김 너머 신고점폭", False,
        f"폭이 계산된 {끊김['전체']}칸 중 직전 발표에 TTM 이 없던 것 "
        f"{끊김['끊김']}칸" + (f" (최대 {끊김['최대']:,.0f}% — {끊김['종목']})"
                            if 끊김["끊김"] else ""),
        확인못함=(끊김["끊김"] > 0))

    # ── ⑬ 매출 자릿수가 이웃과 어긋난 행이 남아 있는가 (150차-Y) ────
    #      실물: GS 분기 매출 15,520 달러(실제 155억) · XOM 198 · MRK 228.
    #      26행·15종목이 이랬습니다. 고침을 넣었으니 **0이어야** 합니다.
    #      0이 아니면 새로 생긴 것이므로 파고듭니다.
    깨진매출 = _자릿수깨진_매출_세기(ds)
    적기("매출 자릿수", 깨진매출["행"] > 0,
        f"가까운 분기의 1/100 미만인 매출 {깨진매출['행']}행"
        + (f" · {깨진매출['종목들']}" if 깨진매출["행"] else " (150차-Y 고침 뒤 0이 정상)"))

    # ── ⑭ **종목 전체**의 매출이 수상한 곳 (150차-AC) ──────────────
    #      150차-Y 는 "그 종목의 성한 분기"와 견주므로, **모든 분기가 다
    #      어긋난 종목**은 견줄 데가 없어 손을 못 댑니다. 실물: GS 는
    #      전 분기가 백만 단위로 들어와 있습니다(15,520 = 실제 155억).
    #      고칠 수 없어도 **말은 해야** 합니다 — 화면이 조용하면 주인은
    #      그 숫자를 믿습니다.
    적기("종목 전체 매출", False,
        f"분기 최대 매출이 500만 달러 미만인 종목 {len(깨진매출['통째수상'])}개"
        + (f" {깨진매출['통째수상']} — 고칠 자가 없어 미해결(150차-AC)"
           if 깨진매출["통째수상"] else ""),
        확인못함=bool(깨진매출["통째수상"]))

    # ── ⑫ 분기 **이름**이 그 종목 안에서 어긋나지 않는가 (150차-T) ──
    #      이름은 보도자료 글자에서 뽑아 못 믿습니다. 계산에는 안 쓰지만
    #      (기간끝을 씁니다) 화면에 이름을 띄우므로 세어서 보고합니다.
    라벨 = _어긋난라벨_세기(ds)
    적기("분기 이름 어긋남", False,
        f"이름이 있는 {라벨['검사']}칸 중 그 종목의 다수 규칙과 어긋난 것 "
        f"{라벨['어긋남']}칸 · {라벨['종목']}종목 "
        "(계산에는 안 쓰입니다 — 기간끝으로 잽니다)",
        확인못함=(라벨["어긋남"] > 0))

    # ── ⑮ **주 잣대 빈칸의 사유**를 갈래로 세어 보고한다 (150차-AM) ─
    #      주인 지시(2026-08-24): "빈칸이 너무 많다."
    #      세어 보니 대부분은 **가져올 것이 없는 것**이었습니다. 지금까지
    #      세 종류의 없음이 화면에서 똑같이 보여 판단할 수가 없었습니다.
    #      규격은 `데이터규격.md` 6장에 못박았습니다.
    사유 = _빈칸사유_세기(ds)
    적기("주 잣대 빈칸 사유", False,
        f"조정 EPS {사유['전체']}칸 — 있음 {사유['있음']} · "
        f"회사 미발표 {사유['회사미발표']} · "
        f"발표 기록 없음 {사유['그시절없음']} · "
        f"못 붙였고 그 회사는 안 냄 {사유['짝없음_안냄']} · "
        f"**못 붙였고 그 회사는 냄 {사유['짝없음_냄']}** ← 일감 · "
        f"구멍메움 {사유['구멍메움']} (데이터규격.md 6장)")

    # ── ⑪ 판정 파일이 지금 코드로 계산됐는가 ────────────────────────
    if verdict:
        적기("판정 코드", False,
            f"판정 code_rev {verdict.get('code_rev')} "
            "(다음 로봇 런에서 갱신됩니다)")

    return 결과


def _빈칸사유_세기(ds: dict) -> dict:
    """주 잣대(조정 EPS)의 빈칸을 **사유별로** 셉니다 (150차-AM).

    왜 필요한가: 주인이 "빈칸이 너무 많다"고 했는데, 세어 보니 빈칸의
    대부분은 **우리가 못 가져온 것이 아니라 가져올 것이 없는 것**이었습니다.

      · 상장 전 분기 — 8-K 실적 발표가 **존재하지 않습니다**. XBRL 에만
        과거가 있습니다(상장 서류에 재무가 실리므로). 실물 CRDO 2021년.
      · 회사 미발표 — 조정 EPS 는 논갭이라 XBRL 태그가 없고, 회사가
        안 내면 영원히 없음입니다 (실물 MU).
      · 못 가져옴 — 8-K 를 못 찾았거나 못 읽은 것. **여기만 우리 일감**입니다.

    셋을 갈라 보이지 않으면 주인은 고칠 수 없는 것을 고치라고 하거나,
    고칠 수 있는 것을 못 보고 지나칩니다. 화면이 사유를 말해야 합니다.
    """
    갈래 = {"전체": 0, "있음": 0, "그시절없음": 0, "회사미발표": 0,
          "짝없음_냄": 0, "짝없음_안냄": 0, "구멍메움": 0}
    for _t, rows in (ds.get("quarters") or {}).items():
        # 이 회사가 조정 EPS 를 **내는 회사인가** — 다른 분기에 하나라도
        # 있으면 냅니다. 안 내는 회사는 8-K 를 붙여도 값이 안 나오므로
        # 우리 일감이 아닙니다.
        낸다 = any(r.get("adj_eps") is not None for r in rows)
        발표들 = [r.get("announced_date") for r in rows if r.get("announced_date")]
        첫발표 = min(발표들)[:10] if 발표들 else None
        for r in rows:
            갈래["전체"] += 1
            if r.get("adj_eps") is not None:
                갈래["있음"] += 1
                continue
            분기끝 = str(r.get("filing_date") or "")
            붙음 = r.get("press_matched")
            if 첫발표 and 분기끝 and 분기끝 < 첫발표 and not r.get("announced_date"):
                갈래["그시절없음"] += 1
            elif 붙음 is True:
                갈래["회사미발표"] += 1
            elif 붙음 is False:
                갈래["짝없음_냄" if 낸다 else "짝없음_안냄"] += 1
            else:
                갈래["구멍메움"] += 1
    return 갈래


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


_이웃창 = 4      # 앞뒤 몇 분기까지를 "가까운 분기"로 볼 것인가 (150차-AX)


def _자릿수깨진_매출_세기(ds: dict) -> dict:
    """정제 **뒤**에도 이웃과 자릿수가 100배 넘게 어긋난 매출 세기 (150차-Y).

    150차-Y 의 고침이 실제로 일하고 있는지 매일 확인하는 자리입니다.
    고침이 어떤 이유로 꺼지거나 새 모양이 들어오면 여기서 드러납니다.
    """
    import statistics
    행 = 0
    종목 = set()
    통째수상 = []
    for t, rows in (ds.get("quarters") or {}).items():
        값들 = [r["revenue"] for r in rows
              if isinstance(r.get("revenue"), (int, float)) and r["revenue"] > 0]
        # **종목 전체**가 어긋난 경우 (150차-AC) — 견줄 성한 행이 없어
        # 150차-Y 가 손을 못 댑니다. 고칠 수는 없어도 세어서 말합니다.
        if 값들 and max(값들) < 5_000_000:
            통째수상.append(t)
        # ⚠️ **가까운 분기와** 견줍니다 (150차-AX).
        #
        #    처음에는 종목 **전체 중앙값**과 견줬는데, 이름표는 "가까운
        #    분기의 1/100 미만"이라고 말하면서 코드는 전체를 보고 있었습니다.
        #    말과 코드가 달랐고, 그 탓에 **크게 자란 회사가 통째로** 걸렸습니다.
        #    실물 ALNY: 매출이 38M → 1,291M 로 34배 자란 회사입니다.
        #    전체 중앙값은 224.8M 이라 2018년 분기(2.069M)가 1/100 밑으로
        #    떨어져 ⛔ 가 났는데, **앞뒤 이웃은 21~33M** 이라 자릿수가
        #    어긋난 것이 아니라 그냥 그 시절이 작았던 것입니다.
        #
        #    150차-Y 가 잡으려던 것(GS 15,520 = 실제 155억 같은 **한 행만**
        #    단위가 어긋난 것)은 이웃과 견줘도 그대로 걸립니다.
        for i, r in enumerate(rows):
            v = r.get("revenue")
            if not isinstance(v, (int, float)) or v <= 0:
                continue
            이웃 = [x["revenue"] for j, x in enumerate(rows)
                  if j != i and abs(j - i) <= _이웃창
                  and isinstance(x.get("revenue"), (int, float))
                  and x["revenue"] > 1_000_000]
            if len(이웃) < 4:
                continue
            if v < statistics.median(이웃) / 100.0:
                행 += 1
                종목.add(t)
    return {"행": 행, "종목들": sorted(종목)[:8], "통째수상": sorted(통째수상)[:8]}


def _어긋난라벨_세기(ds: dict) -> dict:
    """분기 이름("25 Q3")이 그 종목 안에서 앞뒤가 안 맞는 칸을 셉니다.

    어떻게 재나: 회계연도가 달력과 어긋나는 회사(예: CRDO 는 4월 결산)도
    **어긋난 폭은 일정**합니다. 그래서 종목마다 "이름의 분기번호 −
    기간끝의 달력분기" 를 모아, **가장 많은 값이 그 종목의 규칙**이라고
    보고 거기서 벗어난 칸을 셉니다.

    실물(150차-T): SLB 는 '20 Q4' 라는 이름이 붙은 행이 **4개**였습니다 —
    기간끝은 2021-03/06/09/12 로 서로 다른 네 분기였습니다.
    """
    import re
    from collections import Counter

    LAB = re.compile(r"(\d{2,4})\s*Q([1-4])", re.I)
    검사 = 어긋남 = 0
    종목 = set()
    for t, rows in (ds.get("quarters") or {}).items():
        차이 = []
        for r in rows:
            m = LAB.search(str(r.get("period_label") or ""))
            날 = re.search(r"(\d{4})-(\d{2})",
                          str(r.get("period_end") or r.get("filing_date") or ""))
            if not m or not 날:
                continue
            달분기 = (int(날.group(2)) - 1) // 3 + 1
            차이.append((int(m.group(2)) - 달분기) % 4)
        if len(차이) < 4:               # 표본이 적으면 '다수'를 말할 수 없음
            continue
        검사 += len(차이)
        주류 = Counter(차이).most_common(1)[0][0]
        틀린 = sum(1 for d in 차이 if d != 주류)
        if 틀린:
            어긋남 += 틀린
            종목.add(t)
    return {"검사": 검사, "어긋남": 어긋남, "종목": len(종목)}


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
