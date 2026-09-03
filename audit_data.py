"""
audit_data.py — 재료(수집 데이터) 오염 전수조사 (73차)
=====================================================

하는 일:
  손질을 마친 분기표(dataset.build 의 결과)를 훑어, **아직 남아 있는
  오염 의심 칸**을 세어 보여 줍니다. **아무것도 지우지 않습니다.**

왜 지우지 않는가 (65차 §④ 에 등록한 방침 "표시부터, 지우지 말고"):
  지울 규칙을 만들려면 문턱이 필요한데, 문턱을 잘못 잡으면 **진짜 실적을
  지웁니다.** 66차에서 실제로 겪었습니다 — "결산 분기 값이 크면 연간값"
  이라는 그럴듯한 규칙이 회계연도가 앞서가는 회사(NVDA·MRVL·SNOW) 307칸을
  지울 뻔했습니다. 73차에도 같은 함정을 다시 만났습니다:

    · FSLR 이 12-31 행마다 7.74 · 12.02 · 14.21 → **연간 EPS 오염**
    · ABNB 가 09-30 행마다 20억 달러 → **진짜 성수기 실적**(3분기)

  둘 다 "해마다 같은 자리에서 평소의 3~4배"라는 모양이 똑같아서, 어떤
  배수를 고르든 갈라낼 수 없었습니다. 그래서 **자동 삭제를 포기하고
  세어서 보여 주기**로 합니다. 지우지 않으므로 진짜 실적을 잃지 않고,
  세어서 보여 주므로 재료가 얼마나 더러운지를 감추지도 않습니다.

무엇을 세나 (셋 다 문턱 해석을 함께 적습니다):
  ⑴ 같은 기간 라벨이 여러 행에    — 파서가 분기를 못 읽어 앞 라벨을 되쓴 것
  ⑵ 혼자 튀었다가 돌아오는 칸     — 앞·뒤 이웃보다 몇 배 큰 값
  ⑶ 같은 값이 되풀이되는 사다리 칸 — 연간값·자리채움이 여러 분기에 복사된 것

⑵ 의 배수 3배는 **삭제 기준이 아니라 표시 기준**입니다. 성장하는 회사는
걸리지 않습니다(이웃과만 비교하므로). 지우지 않으니 틀려도 잃는 것이
없고, 크기를 가늠하는 데만 씁니다.
"""

from __future__ import annotations

import re
from collections import defaultdict

# 잣대 사다리 3칸 (measure_engine.yardstick_of 와 같은 순서)
LADDER_FIELDS = ("adj_eps", "adjusted_ebitda", "gaap_eps")

# 표시 기준 (삭제 기준이 아닙니다 — 위 설명 참조)
SPIKE_RATIO = 3.0        # 앞·뒤 이웃 중 큰 쪽의 몇 배부터 표시할까
REPEAT_MIN = 3           # 같은 값이 몇 번 되풀이되면 표시할까
MIN_SERIES = 5           # 이만큼도 값이 없는 종목은 비교 자체가 무의미


def duplicate_labels(quarters: dict) -> list[dict]:
    """같은 기간 라벨이 여러 행에 붙은 종목 목록.

    측정은 발표일(announced_date)로 하므로 계산이 틀리지는 않습니다.
    다만 **파서가 분기를 못 읽었다는 표시**이고, 그런 종목은 다른
    칸에서도 잘못 읽었을 가능성이 큽니다. 그래서 셉니다.
    """
    out = []
    for ticker, rows in sorted(quarters.items()):
        by_label: dict[str, int] = defaultdict(int)
        for row in rows:
            label = row.get("period_label")
            if label and any(row.get(f) is not None for f in LADDER_FIELDS):
                by_label[label] += 1
        겹친행 = sum(n for n in by_label.values() if n > 1)
        if 겹친행:
            out.append({
                "종목": ticker,
                "겹친라벨": sum(1 for n in by_label.values() if n > 1),
                "걸린행": 겹친행,
                "전체행": len(rows),
            })
    out.sort(key=lambda r: -r["걸린행"])
    return out


def spike_cells(quarters: dict, ratio: float = SPIKE_RATIO) -> list[dict]:
    """앞·뒤 이웃보다 `ratio` 배 이상 큰 칸 (혼자 튀었다가 돌아오는 값).

    **왜 종목 전체 중앙값이 아니라 바로 옆 분기와 비교하는가**:
    전체 중앙값과 비교하면 CRDO(0.05 → 1.16)·NVDA·PLTR 같은 **진짜
    성장**이 상위를 채워 버립니다(73차 실측). 성장은 이웃과 가깝고,
    오염은 이웃 둘 다에서 멉니다. 그래서 이웃하고만 비교합니다.

    앞뒤 이웃이 **둘 다** 있는 칸만 봅니다 — 맨 앞·맨 뒤 칸은 판단하지
    않습니다(모르는 것은 모른다고 둡니다).
    """
    out = []
    for ticker, rows in sorted(quarters.items()):
        for field in LADDER_FIELDS:
            series = [(i, r[field]) for i, r in enumerate(rows)
                      if r.get(field) is not None]
            if len(series) < MIN_SERIES:
                continue
            for pos in range(1, len(series) - 1):
                index, value = series[pos]
                앞, 뒤 = series[pos - 1][1], series[pos + 1][1]
                이웃 = max(abs(앞), abs(뒤))
                if 이웃 <= 0:
                    continue
                if abs(value) >= 이웃 * ratio:
                    out.append({
                        "종목": ticker,
                        "칸": field,
                        "라벨": rows[index].get("period_label"),
                        "발표일": rows[index].get("announced_date"),
                        "앞": 앞,
                        "값": value,
                        "뒤": 뒤,
                        "배수": abs(value) / 이웃,
                    })
    out.sort(key=lambda r: -r["배수"])
    return out


def repeated_ladder_values(quarters: dict,
                           min_repeat: int = REPEAT_MIN) -> list[dict]:
    """같은 값이 `min_repeat` 번 이상 되풀이되는 사다리 칸.

    매출에는 이미 같은 검사가 있습니다(_drop_repeated_revenue). 사다리
    칸에도 같은 일이 일어납니다 — 실물 DELL 7.99·7.98·8.38, NRG 조정
    EBITDA 37.25억이 세 분기 연속. 분기 이익이 소수점 둘째 자리까지
    똑같이 되풀이될 확률은 사실상 없습니다.

    0 은 세지 않습니다 — 적자 직전 회사의 EPS 0.00 은 진짜로 되풀이됩니다.
    """
    out = []
    for ticker, rows in sorted(quarters.items()):
        for field in LADDER_FIELDS:
            values = [r[field] for r in rows if r.get(field) is not None]
            if len(values) < MIN_SERIES:
                continue
            셈: dict[float, int] = defaultdict(int)
            for v in values:
                셈[round(v, 2)] += 1
            for value, n in 셈.items():
                if n >= min_repeat and value != 0:
                    out.append({
                        "종목": ticker,
                        "칸": field,
                        "값": value,
                        "횟수": n,
                        "전체": len(values),
                    })
    out.sort(key=lambda r: -r["횟수"])
    return out


def summary(quarters: dict) -> dict:
    """세 검사의 합계 — 계기판 한 줄로 쓰기 위한 요약."""
    labels = duplicate_labels(quarters)
    spikes = spike_cells(quarters)
    repeats = repeated_ladder_values(quarters)
    검사한칸 = 0
    for rows in quarters.values():
        for field in LADDER_FIELDS:
            n = sum(1 for r in rows if r.get(field) is not None)
            if n >= MIN_SERIES:
                검사한칸 += max(n - 2, 0)    # 앞뒤 이웃이 있는 칸만
    return {
        "라벨겹침_종목": len(labels),
        "라벨겹침_행": sum(r["걸린행"] for r in labels),
        "튄칸": len(spikes),
        "검사한칸": 검사한칸,
        "튄칸_비율": (len(spikes) / 검사한칸 * 100.0) if 검사한칸 else None,
        "되풀이_묶음": len(repeats),
        "되풀이_칸": sum(r["횟수"] for r in repeats),
        "전체종목": len(quarters),
    }


# 매출총이익률이 한 종목 안에서 크게 오르내리는 것 (77·78차)
# ---------------------------------------------------------------------------
# 야후와 대조해 보니 매출총이익률이 44% 어긋났고, 실물(AMBA)로 원인을
# 확인했습니다 — 각주의 "두 이익률의 **차이** 1.4%" 를 이익률로 읽고
# 있었습니다. WMT·DELL 은 원문이 없어 원인을 확인 못 했습니다.
#
# 매출총이익률은 회사의 **체질**이라 분기마다 몇 %p 안에서 움직입니다.
# 한 종목 안에서 60% ↔ 2% 를 오간다면 **둘 중 하나는 틀린 값**입니다.
#
# ⚠️ 처음엔 "종목 중앙값에서 멀면 이상치"로 재려 했는데 **거꾸로 나왔습니다** —
#    AMBA 는 틀린 값(2% 대)이 더 많아 중앙값이 3.9% 가 되고, 그러면 진짜
#    값(63%)이 이상치로 찍힙니다. 어느 쪽이 옳은지 우리는 모릅니다.
#    그래서 **어느 쪽이 틀렸는지 정하지 않고**, "이 종목은 폭이 너무 넓다"는
#    사실만 말하고 **양 끝 원문을 둘 다** 부탁합니다. 원문을 봐야 압니다.
GM_SPAN_PCT = 20.0     # 한 종목 안 최대-최소가 이만큼(%p)을 넘으면 표시


def gross_margin_unstable(quarters: dict,
                          span: float = GM_SPAN_PCT) -> list[dict]:
    """매출총이익률 폭이 너무 넓은 종목과, 그 **양 끝** 행.

    어느 쪽이 틀렸는지 정하지 않습니다 — 양 끝을 둘 다 돌려줍니다.
    **아무것도 지우지 않습니다.**
    """
    out = []
    for ticker, rows in sorted(quarters.items()):
        있는행 = [r for r in rows if r.get("gross_margin_pct") is not None]
        if len(있는행) < MIN_SERIES:
            continue
        차례 = sorted(있는행, key=lambda r: r["gross_margin_pct"])
        폭 = 차례[-1]["gross_margin_pct"] - 차례[0]["gross_margin_pct"]
        if 폭 < span:
            continue
        for row, 쪽 in ((차례[0], "가장 낮음"), (차례[-1], "가장 높음")):
            out.append({
                "종목": ticker,
                "칸": "gross_margin_pct",
                "라벨": row.get("period_label"),
                "발표일": row.get("announced_date"),
                "값": row["gross_margin_pct"],
                "폭": round(폭, 1),
                "쪽": 쪽,
            })
    out.sort(key=lambda r: -r["폭"])
    return out


def wanted_raw_filings(quarters: dict, limit: int = 60) -> list[dict]:
    """로봇에게 "이 공시 원문은 꼭 가져와 달라"고 부탁할 목록.

    **왜 필요한가** — 지금 막혀 있는 곳이 정확히 여기입니다.
    VZ 5.18 · GS 59.45 · IPGP 99.10 같은 칸이 왜 그렇게 들어왔는지 알려면
    **그 보도자료 원문**을 봐야 하는데, 개발 환경은 SEC 가 막혀 있어 받을
    수 없습니다. 지금 보관되는 원문은 "잣대값을 하나도 못 읽은" 실패
    공시뿐이라, **잘못 읽은** 공시는 한 건도 남지 않습니다.

    그래서 조사가 찾아낸 칸의 (종목, 발표일)을 목록으로 적어 저장소에
    커밋하면, 다음 로봇 실행이 그 공시만 골라 원문을 담아 옵니다.
    다음 세션은 그 실물로 파서를 고칠 수 있습니다.

    발표일이 없는 행은 담지 않습니다 — 어느 공시인지 짚을 수 없습니다.
    """
    out = []
    for cell in spike_cells(quarters):
        if not cell.get("발표일"):
            continue
        out.append({
            "종목": cell["종목"],
            "발표일": cell["발표일"],
            "칸": cell["칸"],
            "값": cell["값"],
            "배수": round(cell["배수"], 1),
            "이유": "이웃 분기보다 크게 튐",
        })
        if len(out) >= limit // 2:
            break
    # 매출총이익률 이상치도 절반쯤 담습니다 (78차) — 이 칸의 원인을 아직
    # 원문으로 확인 못 한 종목이 있습니다(WMT·DELL).
    담긴종목 = {r["종목"] for r in out}
    for cell in gross_margin_unstable(quarters):
        if not cell.get("발표일"):
            continue
        out.append({
            "종목": cell["종목"],
            "발표일": cell["발표일"],
            "칸": cell["칸"],
            "값": cell["값"],
            "배수": None,
            "이유": f"매출총이익률 폭이 {cell['폭']}%p 로 넓음 "
                  f"({cell['쪽']} 쪽 — 어느 쪽이 틀렸는지는 원문을 봐야 압니다)",
        })
        담긴종목.add(cell["종목"])
        if len(out) >= limit:
            break
    return out


def one_line(quarters: dict) -> str:
    """계기판에 그대로 띄울 한 줄 (모바일 폭에서도 읽히도록 짧게)."""
    s = summary(quarters)
    비율 = "확인 못함" if s["튄칸_비율"] is None else f"{s['튄칸_비율']:.1f}%"
    return (f"재료 상태 — 이웃보다 {SPIKE_RATIO:g}배 튄 칸 {s['튄칸']}개"
            f"({비율}) · 같은 값 되풀이 {s['되풀이_칸']}칸 · "
            f"기간 라벨 겹침 {s['라벨겹침_행']}행({s['라벨겹침_종목']}종목). "
            "지우지 않고 세기만 합니다 — 지울 규칙을 만들면 진짜 실적까지 "
            "지운다는 것을 66차·73차에 실측했습니다.")


# ---------------------------------------------------------------------------
# 실행 방법:  python3 audit_data.py
#   조사 결과를 화면에 찍고, 로봇에게 부탁할 원문 목록을 파일로 적습니다.
# ---------------------------------------------------------------------------
WANTED_PATH = "data/measure/wanted_raw.json"
WANTED_LIMIT = 95      # 틀린 값(바깥 자·월가·이웃 튐)에게 주는 자리 (150차: 60→95)
HOLE_QUOTA = 15        # 잣대 구멍에게 **따로 떼어 두는** 자리 (135차, 아래 설명)
FRESH_HOLE_QUOTA = 12  # 그중 **최신 구멍**에게 다시 떼어 두는 자리 (150차-K)
                       # — "구멍 많은 종목부터"로만 뽑으니 15칸이 전부
                       #   2017~2021년 것이었고 최근 1년 구멍은 0건이었습니다.
                       #   최신 구멍은 지금의 TTM·판정을 막으므로 더 급합니다.

# 150차 — 재료별 몫. 실측해 보니 "월가와 갈린 칸" 재료가 **자기 몫에서
# 이미 잘려** 있었습니다:
#   · 월가 4분기 합과 2% 안에 드는 칸(= 연간값을 분기값 칸에 넣은 것,
#     산술로 확정된 **우리 결함**)이 39건인데, 재료가 20건만 내놓아
#     19건이 목록에 닿지도 못했습니다.
#   · 그 20건은 다시 앞자리 60칸을 먹어, 이웃 튐 재료 60건 중 11건만
#     실렸습니다.
# 135차에 구멍에게 배운 것과 같은 병이라 같은 약을 씁니다 — 재료마다
# **제 몫을 먼저 떼어** 놓습니다. 원문 부탁은 로봇이 이미 내려받은 글을
# 저장만 하는 것이라(sec_fundamentals._is_wanted_raw) 접속이 늘지 않고,
# 늘어나는 것은 저장 용량뿐입니다(건당 약 34KB).
MISMATCH_QUOTA = 30    # 바깥 자(야후)와 어긋난 칸
STREET_QUOTA = 40      # 월가 발표 EPS 와 갈린 조정 EPS 칸 (연간값 모양이 앞)
NEIGHBOR_QUOTA = 25    # 이웃 분기와 어긋난 칸(우리끼리 비교)


# 177차 — **이미 받아 둔 원문은 다시 부탁하지 않습니다**
# ---------------------------------------------------------------------------
# 2026-09-03 실측: 부탁 목록 119건 중 **105건(88%)의 원문이 이미 저장소에
# 있었습니다.** 목록은 날마다 똑같은 규칙으로 다시 만들어지므로, 한 번
# 앞자리를 차지한 공시는 원문을 받은 뒤에도 계속 그 자리에 남습니다.
# 그래서 새 후보가 들어갈 자리가 하루 14칸뿐이었습니다.
#
# 실물 피해: ZETA 는 잣대(조정 EBITDA) 구멍이 12칸인데 두 갈래
# (구멍 많은 순 · 최신 구멍 순) 어디에도 못 듭니다 — 앞은 2017~2020년
# 구멍이 많은 종목이, 뒤는 2026년 구멍 종목이 차지하고, ZETA 의 최신
# 구멍(2025-05-01)은 그 가운데에 끼어 영원히 밀립니다.
#
# 부탁 목록은 **원문을 저장만** 하게 하는 것이라(값·사건·판정에 닿지
# 않습니다) 자리 나누기를 바꾸는 것은 표본을 바꾸지 않습니다.
RAW_DIR = "data/measure/raw"
_RAW_NAME_RE = re.compile(r"^(.+?)_(\d{4}-\d{2}-\d{2})(?:_[^_]+)?\.txt$")


def 이미_받은_원문(raw_dir: str | None = None) -> set[tuple[str, str]]:
    """저장소에 원문이 있는 (종목, 발표일) 짝. 폴더가 없으면 빈 집합.

    기본값을 인자 자리에 박지 않고 **부를 때** RAW_DIR 을 읽습니다 —
    박아 두면 시험이 폴더를 바꿔도 옛 경로를 계속 봅니다(실제로 겪음).
    """
    import os

    본 = set()
    try:
        이름들 = os.listdir(raw_dir or RAW_DIR)
    except OSError:
        return 본
    for name in 이름들:
        m = _RAW_NAME_RE.match(name)
        if m:
            본.add((m.group(1), m.group(2)))
    return 본


def 받은것_빼기(목록: list[dict], 받음: set[tuple[str, str]]) -> list[dict]:
    """이미 원문이 있는 부탁을 뺍니다 — 자리를 새 후보에게 넘깁니다."""
    return [r for r in 목록 or []
            if (r.get("종목"), str(r.get("발표일") or "")[:10]) not in 받음]


def merge_wanted(*목록들: list[dict], limit: int = 60) -> list[dict]:
    """여러 곳에서 모은 부탁 목록을 하나로 합칩니다 (앞엣것이 우선).

    같은 공시를 두 번 적어도 로봇은 한 번만 담아 오므로, (종목, 발표일)이
    겹치면 **먼저 온 것만** 남깁니다. 86차부터 두 곳에서 옵니다 —
    바깥 자(야후)와 어긋난 칸, 그리고 이웃과 어긋난 칸(조사).
    야후 쪽을 앞에 두는 이유: 그쪽은 **바깥 증거**라 우리 착각이 섞일
    여지가 적습니다.
    """
    본 = set()
    out = []
    for 목록 in 목록들:
        for row in 목록 or []:
            열쇠 = (row.get("종목"), str(row.get("발표일") or "")[:10])
            if not 열쇠[0] or not 열쇠[1] or 열쇠 in 본:
                continue
            본.add(열쇠)
            out.append(row)
            if len(out) >= limit:
                return out
    return out


def write_wanted(quarters: dict, path: str = WANTED_PATH,
                 extra: list[dict] | None = None,
                 tail: list[dict] | None = None,
                 받음: set | None = None) -> int:
    """로봇이 읽을 '원문 부탁 목록'을 파일로 적고, 건수를 돌려줍니다.

    extra 로 바깥 자(야후)와 어긋난 칸을 함께 넘기면 앞자리에 놓습니다.
    """
    import json

    # 순서 = 바깥 자 → 이웃과 튄 칸 → **맨 뒤에 잣대 구멍**(135차).
    # 틀린 값이 먼저, 없는 값이 나중 (헌법 1조).
    #
    # ⚠️ 처음엔 그냥 맨 뒤에 붙였는데, 실데이터로 재 보니 **한 칸도 못
    #    들어갔습니다** — 틀린 값 후보만 110건이라 60칸을 먼저 다 채웁니다.
    #    그러면 이 배관은 영원히 안 도는 코드가 됩니다(134차에 고친 것과
    #    똑같은 병). 그래서 구멍에게 **따로 자리 몇 칸**(HOLE_QUOTA)을
    #    떼어 줍니다. 앞자리는 그대로 틀린 값이 가지므로 우선순위는
    #    지켜지고, 배관은 실제로 돕니다.
    #
    # tail 은 이미 재료별로 제 몫만큼 잘라서 들어옵니다(refresh_wanted 참조).
    # 이웃 튐도 **제 몫만큼 잘라서** 넣습니다 (150차). 안 자르면 앞 재료가
    # 남은 자리를 다 먹거나, 반대로 이웃 튐이 뒤 재료를 굶깁니다.
    # 이웃 튐 재료도 이미 받은 원문을 거른 뒤 몫만큼 자릅니다 (177차).
    if 받음 is None:
        받음 = 이미_받은_원문()
    앞 = merge_wanted(extra or [],
                     받은것_빼기(wanted_raw_filings(quarters, limit=400),
                              받음)[:NEIGHBOR_QUOTA],
                     limit=WANTED_LIMIT)
    목록 = merge_wanted(앞, tail or [],
                      limit=(WANTED_LIMIT + SCALE_QUOTA
                             + FRESH_HOLE_QUOTA + HOLE_QUOTA))
    with open(path, "w", encoding="utf-8") as f:
        json.dump({
            "설명": ("'원문이 필요한 공시' 목록입니다. 세 곳에서 옵니다 — "
                   "바깥 자(야후)와 어긋난 칸(vendor_compare), "
                   "월가 발표 EPS 와 갈린 조정 EPS 칸(115차 배관), "
                   "이웃 분기와 어긋난 칸(audit_data). "
                   "수집 로봇은 이 목록에 있는 공시는 값을 읽었더라도 원문을 "
                   "함께 담아 옵니다 — 잘못 읽은 값의 원인을 다음 세션이 "
                   "실물로 확인할 수 있게 하려는 것입니다. 값은 지우지 않습니다."),
            "목록": 목록,
        }, f, ensure_ascii=False, indent=1)
    return len(목록)


# 잣대 구멍 (135차) — **값이 아예 없는** 칸도 원문을 부탁합니다.
#
# 133~134차에 만든 재료 셋은 전부 "틀린 값"을 찾습니다(야후와 어긋남 ·
# 월가와 갈림 · 이웃과 튐). 그런데 ZETA 를 보다가 네 번째 종류가 드러
# 났습니다 — **파서가 아무 값도 못 읽어 칸이 빈 것**입니다. 틀린 값이
# 아니라 없는 값이라 어느 그물에도 안 걸립니다.
#
#   실물 ZETA(잣대 조정 EBITDA): 2021-11 다음이 2024-09 — 그 사이 12개
#   분기가 통째로 비어 있어 **델타를 잴 수 없습니다**(화면의 "—").
#   전 종목 실측: 잣대가 있는 구간 **안쪽**의 빈 칸 408개
#   (UCTT 23 · AMBA 23 · ROKU 21 · EL 19 · BA 18 · ZETA 12 …).
#
# 우선순위를 **맨 뒤**에 둡니다. 헌법 1조가 "없음은 안전하고 틀림은
# 위험하다"고 못박았으므로, 틀린 값을 먼저 고치고 남는 자리에만 구멍을
# 채웁니다. 틀린 값이 줄면 자연히 구멍 차례가 옵니다 — 사람이 순서를
# 다시 정할 필요가 없습니다.
_HOLE_PER_TICKER_MAX = 2      # 한 종목이 목록을 다 차지하지 않게


def wanted_from_holes(quarters: dict, limit: int = 60,
                      newest_first: bool = False) -> list[dict]:
    """잣대 값이 **구간 안쪽에서 빈** 칸의 원문을 부탁합니다.

    앞뒤 바깥(아직 상장 전·아직 발표 전)의 빈칸은 정상이므로 세지
    않습니다 — 값이 있는 첫 분기와 마지막 분기 **사이**만 봅니다.

    `newest_first=True` 면 **최신 구멍부터** 고릅니다 (150차-K).
    왜 따로 필요한가 — 기본 규칙("구멍 많은 종목부터")으로 15칸을
    채워 보니 **전부 2017~2021년 것**이었고 최근 1년 구멍은 0건이었습니다.
    최신 구멍은 영원히 차례가 안 옵니다. 그런데 최신 구멍이 더 급합니다:

      · 한 분기가 비면 그 분기가 들어가는 **TTM 네 칸이 전부 막힙니다**
        — 즉 지금 화면의 실적 이력과 **지금 판정**을 막습니다.
      · 2018년 구멍은 이미 끝난 측정에만 걸립니다.

    실물: 주인이 화면 맨 위에서 보는 신호 1·2위 LITE·ZETA 의 최신
    분기가 비어 있는데(LITE 25Q4·26Q4 · ZETA 24Q4·25Q1), 구멍이
    1~2개뿐이라 순위에서 밀려 부탁 목록에 **한 건도 못 들어갔습니다**.
    """
    import measure_engine as _me

    # ① 종목마다 구간 안쪽 빈 칸을 **전부** 셉니다 (자리는 아직 안 나눔)
    모음: list[tuple[str, str, list[str]]] = []     # (종목, 잣대, 빈 날짜들)
    for ticker in sorted(quarters):
        rows = quarters.get(ticker) or []
        잣대 = _me.yardstick_of(rows)
        if not 잣대:
            continue
        있는 = [r for r in rows if r.get(잣대) is not None]
        if len(있는) < 4:
            continue
        첫, 끝 = 있는[0].get("announced_date"), 있는[-1].get("announced_date")
        if not 첫 or not 끝:
            continue
        # ⚠️ 원래는 `첫 < 날 < 끝` 이었습니다 — **값이 있는 마지막 분기
        #    뒤**의 빈칸을 "아직 발표 전"으로 보고 뺐습니다. 그런데
        #    **발표일이 있는 행은 이미 발표된 것**입니다. 못 읽었을 뿐입니다.
        #    실측(150차-K): 그렇게 가려진 꼬리 구멍이 **197건 · 29종목**이고,
        #    최신 15건이 전부 2026년 7~8월 — 즉 **가장 최근 분기**였습니다.
        #    가장 최근 분기가 비면 지금의 TTM·신고점·첫돌파가 통째로 막힙니다.
        #    (주인이 화면에서 LITE 26Q4 가 "—"인 것을 보고 물어 발견.)
        #    앞쪽 바깥(값이 처음 나오기 전)은 그대로 뺍니다 — 상장 전이거나
        #    그 지표를 아직 안 내던 때라 정상입니다.
        빈칸 = [str(r["announced_date"])[:10] for r in rows
                if r.get("announced_date") and r.get(잣대) is None
                and 첫 < r["announced_date"]]
        if 빈칸:
            모음.append((ticker, 잣대, 빈칸))

    # ② **구멍이 많은 종목부터**. 알파벳 순으로 자르면 앞 글자 종목만
    #    영원히 서비스받고 UCTT·ZETA 는 차례가 오지 않습니다(이 목록은
    #    날마다 똑같이 다시 만들어지므로 순서 편향이 굳어집니다).
    #    다만 이 규칙만으로는 **최신 구멍이 영원히 밀립니다** — 그래서
    #    newest_first 갈래를 따로 둡니다(150차-K, 위 설명).
    if newest_first:
        # 종목별로 **가장 최근 구멍**을 대표로 삼아 늘어놓습니다.
        모음.sort(key=lambda t: (max(t[2]), t[0]), reverse=True)
        모음 = [(티, 잣, sorted(빈, reverse=True)) for 티, 잣, 빈 in 모음]
    else:
        모음.sort(key=lambda t: (-len(t[2]), t[0]))

    out: list[dict] = []
    for ticker, 잣대, 빈칸 in 모음:
        for 날 in 빈칸[:_HOLE_PER_TICKER_MAX]:
            out.append({
                "종목": ticker,
                "발표일": 날,
                "칸": {"adj_eps": "조정 EPS", "adjusted_ebitda": "조정 EBITDA",
                      "gaap_eps": "GAAP EPS"}.get(잣대, 잣대),
                "값": None,
                "배수": None,
                "이유": f"잣대({잣대}) 칸이 비어 있음 — 파서가 못 읽은 것으로 "
                      f"보임 (이 종목 빈 칸 {len(빈칸)}개)",
            })
        if len(out) >= limit:
            break
    return out[:limit]


# 자릿수 어긋남 (136차) — **손질을 통과했는데도** 단위가 다른 칸.
#
# 136차에 건강검진의 부호 결함을 고치고 나서, 남은 경보를 모양별로
# 쪼개 봤더니 정체가 드러났습니다 — 쓰레기가 아니라 **단위 혼선**입니다.
# 한 종목 안에서 어떤 분기는 달러, 어떤 분기는 천/백만 단위입니다.
#
#   실물 MU 영업이익: 694 ↔ 17.4억 (백만 단위 분기가 섞임)
#   실물 TTMI:        68,209 ↔ 54.8억 (천 단위 분기가 섞임)
#   실물 HD 매출 2026-08-18: 47,861 ↔ 4.1조 (**가장 최근 분기**가 백만 단위)
#   영업이익 666칸 · 93종목 / 매출 25칸
#
# 손질(dataset)이 상당수를 이미 지웁니다(마진이 상식 밖이면 없음 처리).
# 그래도 **통과해 남는 칸**이 있고, 그 칸은 화면에 틀린 숫자로 뜹니다.
# 고치려면 그 보도자료 표의 단위 머리글을 봐야 하므로 원문을 부탁합니다.
# **여기서도 값은 고치지 않습니다** — 환산 규칙을 원문 없이 지어내면
# 66·73차의 실수를 되풀이합니다.
SCALE_FOLD = 1000.0           # 이력 중앙값의 몇 배부터 자릿수 어긋남으로 볼까
SCALE_MIN_HISTORY = 8
_SCALE_PER_TICKER_MAX = 2
SCALE_QUOTA = 10              # 부탁 목록에서 이 재료에 떼어 주는 자리


def wanted_from_scale(quarters: dict, limit: int = 40) -> list[dict]:
    """손질을 통과했는데도 자기 이력과 **자릿수**가 어긋난 칸.

    크기(절댓값)끼리만 견줍니다 — 적자는 자릿수 문제가 아닙니다
    (건강검진의 부호 결함과 같은 함정, 136차).
    """
    from statistics import median

    모음: list[tuple[str, int, list[dict]]] = []
    for ticker in sorted(quarters):
        rows = quarters.get(ticker) or []
        걸린: list[dict] = []
        for field in ("revenue", "op_income"):
            크기들 = [abs(r[field]) for r in rows
                     if isinstance(r.get(field), (int, float))]
            if len(크기들) < SCALE_MIN_HISTORY:
                continue
            중앙 = median(크기들)
            if not 중앙 or 중앙 <= 0:
                continue
            for r in rows:
                v = r.get(field)
                날 = r.get("announced_date")
                if not isinstance(v, (int, float)) or not 날 or v == 0:
                    continue
                비 = abs(v) / 중앙
                if 비 > SCALE_FOLD or 비 < 1.0 / SCALE_FOLD:
                    걸린.append({
                        "종목": ticker,
                        "발표일": str(날)[:10],
                        "칸": {"revenue": "매출",
                              "op_income": "영업이익"}.get(field, field),
                        "값": v,
                        "배수": round(비, 1) if 비 > 1 else round(1.0 / 비, 1),
                        "이유": f"{field} {v:,.0f} 가 이 종목 이력 중앙값 "
                              f"{중앙:,.0f} 과 자릿수가 다름 — 보도자료 표의 "
                              f"단위(천·백만)를 못 읽은 것으로 보임",
                    })
        if 걸린:
            모음.append((ticker, len(걸린), 걸린))

    # 많이 어긋난 종목부터 (135차 구멍과 같은 이유 — 알파벳 순은 편향)
    모음.sort(key=lambda t: (-t[1], t[0]))
    out: list[dict] = []
    for _ticker, _n, 걸린 in 모음:
        out.extend(걸린[:_SCALE_PER_TICKER_MAX])
        if len(out) >= limit:
            break
    return out[:limit]


def 이번런에_담은_원문(files: dict | None) -> set[tuple[str, str]]:
    """이번 런이 **아직 디스크에 안 쓴** 원문의 (종목, 발표일) 짝 (178차).

    로봇은 원문을 `files` 사전에 모아 두었다가 런 **맨 끝**에 커밋합니다.
    그런데 부탁 목록 갱신은 그보다 앞에서 돌면서 디스크만 봅니다. 그래서
    **이번 런이 방금 담아 온 원문을 모른 채** 목록을 적어, 같은 공시를
    한 번 더 부탁했습니다 — 목록이 늘 한 런씩 뒤처졌습니다.

    실측(런 #68, 2026-09-03): 새로 적은 부탁 120건 중 **104건**이 그 런에서
    이미 담아 온 것이었습니다. 즉 다음 런의 자리 120칸 중 16칸만 새 일이었습니다.
    """
    본 = set()
    for path in (files or {}):
        name = str(path).rsplit("/", 1)[-1]
        m = _RAW_NAME_RE.match(name)
        if m:
            본.add((m.group(1), m.group(2)))
    return 본


def refresh_wanted(quarters: dict, vendor: dict | None,
                   path: str = WANTED_PATH, progress=print,
                   files: dict | None = None) -> int:
    """부탁 목록을 다시 적습니다 — **로봇이 매일 부르는 자리** (134차).

    115차에 만든 배관은 사람이 audit_data.py 를 직접 돌릴 때만 돌았습니다.
    그래서 4차 확장(250종목)으로 들어온 새 90종목의 오염 공시가 **한 건도
    목록에 못 들어갔습니다**(133차 감사에서 실측: 새 종목 0건). 같은 코드를
    로봇도 부를 수 있게 함수로 뺐습니다 — 규칙은 하나도 바꾸지 않았습니다.
    """
    # 이미 원문을 받아 둔 공시는 **자르기 전에** 뺍니다 (177차). 잘라낸
    # 뒤에 빼면 그 자리가 빈 채로 남아, 새 후보가 여전히 못 들어옵니다.
    # 디스크에 있는 것 + **이번 런이 방금 담은 것**(178차) 둘 다 뺍니다.
    받음 = 이미_받은_원문() | 이번런에_담은_원문(files)
    바깥: list[dict] = []
    if vendor:
        try:
            import vendor_compare as _vc
            # 후보를 넉넉히 받아 **거른 뒤에** 몫만큼 자릅니다 — 그래야
            # 이미 받은 원문이 그 재료의 몫을 빈자리로 잡아먹지 않습니다.
            바깥 = 받은것_빼기(
                       _vc.wanted_from_mismatch(quarters, vendor, limit=400),
                       받음)[:MISMATCH_QUOTA] + \
                  받은것_빼기(
                       _vc.wanted_from_street(quarters, vendor, limit=400),
                       받음)[:STREET_QUOTA]
        except Exception as exc:      # 부탁 목록이 실패해도 수집은 계속
            progress(f"⚠️ 부탁 목록 재료 실패: {type(exc).__name__}: {str(exc)[:120]}")
    # 재료마다 **제 몫만큼 먼저 잘라서** 넘깁니다. 안 그러면 앞 재료가
    # 뒤 재료의 자리를 먹어 뒤쪽 배관이 영원히 안 돕니다 (135차에 실측한
    # 함정 — 구멍이 0건이었습니다).
    자릿수 = 받은것_빼기(wanted_from_scale(quarters), 받음)[:SCALE_QUOTA]
    # 최신 구멍을 **먼저** 놓습니다 (150차-K). merge_wanted 가 (종목,발표일)
    # 로 겹침을 지우므로 아래 '구멍'과 중복돼도 한 번만 실립니다.
    신선구멍 = 받은것_빼기(
        wanted_from_holes(quarters, newest_first=True, limit=400), 받음
    )[:FRESH_HOLE_QUOTA]
    구멍 = 받은것_빼기(wanted_from_holes(quarters, limit=400), 받음)[:HOLE_QUOTA]
    count = write_wanted(quarters, path=path, 받음=받음,
                         extra=바깥, tail=자릿수 + 신선구멍 + 구멍)
    progress(f"원문 부탁 목록 {count}건 갱신 "
             f"(이미 받은 원문 {len(받음)}건은 뺌 · "
             f"바깥 자 재료 {len(바깥)}건 · 자릿수 어긋남 {len(자릿수)}건 · "
             f"최신 구멍 {len(신선구멍)}건 · 잣대 구멍 후보 {len(구멍)}건)")
    return count


if __name__ == "__main__":
    import dataset

    _ds = dataset.build(dataset.load())
    _q = _ds["quarters"]
    print(one_line(_q))
    print()
    for _row in spike_cells(_q)[:20]:
        print(f"  {_row['배수']:6.1f}배 {_row['종목']:6s} {_row['칸']:16s} "
              f"{str(_row['라벨']):8s} {_row['앞']:>12,.2f} → "
              f"{_row['값']:>12,.2f} → {_row['뒤']:>12,.2f}")
    print()
    # 바깥 자(야후)와 어긋난 칸도 함께 부탁합니다 (86차) — 조사는 이웃과
    # 비교하므로 CRM 처럼 여러 분기가 나란히 무너지면 못 봅니다.
    _바깥 = []
    try:
        import json as _json

        import vendor_compare as _vc

        with open("data/measure/vendor.json", encoding="utf-8") as _f:
            _야후 = _json.load(_f)
        _바깥 = _vc.wanted_from_mismatch(_q, _야후)
        print(f"바깥 자(야후)와 어긋난 칸 {len(_바깥)}건을 목록 앞자리에 둡니다.")
        # 월가 발표 EPS 와 갈린 조정 EPS 칸 (115차 배관 — 111차 실측이 근거).
        # 조정 EPS 는 XBRL 이 못 지키는 유일한 잣대 칸이라, 이 심판이
        # 만든 의심 목록만이 원문 감사로 가는 길입니다.
        _조정 = _vc.wanted_from_street(_q, _야후)
        연간 = sum(1 for r in _조정 if "연간값 모양" in r["이유"])
        print(f"월가와 갈린 조정 EPS {len(_조정)}건(연간값 모양 {연간}건)을 그 뒤에 둡니다.")
        _바깥 = _바깥 + _조정
    except (OSError, ValueError) as _e:
        print(f"야후 파일을 못 읽어 바깥 자 없이 적습니다: {_e}")
    print(f"원문 부탁 목록 {write_wanted(_q, extra=_바깥)}건 → {WANTED_PATH}")
