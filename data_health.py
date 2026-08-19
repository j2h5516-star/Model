"""
data_health.py — 수집물 전체의 건강검진 (108차)
================================================

**왜 또 만드나** — `data_quality.py` 가 이미 있는데?

`data_quality.py` 는 **한 분기 안에서 앞뒤가 맞나**를 봅니다
(마진이 100%를 넘나, 매출이 영업이익보다 작나…). 좋은 그물이지만,
2026-08-18 하루에 찾아낸 사고들은 **전부 그 그물을 빠져나갔습니다.**

| 사고 | 왜 안 걸렸나 |
|---|---|
| `gaap_eps_xbrl` 가 3,066행 **전부** None | 어느 한 행도 이상하지 않다. **칸이 통째로** 비었을 뿐 |
| 매출 182칸이 쓰레기 (BAC 8달러) | 8달러는 그 자체로는 "말이 되는" 숫자다 |
| XBRL 사실 1,832건이 버려짐 | 행이 만들어지기 **전에** 사라진다 |
| 심판 날짜가 분기끝인데 "발표일"로 이름 붙음 | 값은 멀쩡하다. **뜻**이 틀렸다 |
| 화면이 "약 5년" · "9.4% vs 26.8%" 라고 말함 | 데이터가 아니라 **화면**의 문제 |

공통점이 하나 있습니다 — **전부 조용했습니다.** 예외도 안 나고, 실패
카운터도 안 올라갔습니다. 아무도 안 볼 때까지 그대로 있었습니다.

그리고 결국 잡아낸 방법도 공통점이 있습니다. 전부 **"세어 본 것"** 입니다.

  ① 칸이 얼마나 차 있나 세기      → 통째로 빈 칸이 드러남
  ② 어제와 무엇이 달라졌나 세기    → 고침의 부수 피해가 드러남
  ③ 자기 이력에서 얼마나 벗어났나 → 그럴듯한 쓰레기가 드러남

이 파일은 그 셋을 **로봇이 매일 자동으로** 하게 합니다. 세션마다 사람이
손으로 세던 것을 장치로 옮기는 것입니다.

**두 가지 원칙을 지킵니다.**

  · **막지 않고 센다.** 문턱을 지어내 값을 버리면 89차(근거 없는 전망
    가드)의 실수를 되풀이합니다. 이 파일은 **값을 하나도 바꾸지 않습니다.**
  · **경보 문턱은 아직 안 정한다.** 며칠치 숫자를 쌓아 정상 범위를
    실측한 뒤에 사전 등록합니다 (헌법 2조). 지금은 **숫자만 남깁니다.**
"""

from __future__ import annotations

from statistics import median

# 건강검진 대상 칸 — 측정과 화면이 실제로 쓰는 것들
WATCHED_FIELDS = (
    "revenue",
    "adj_eps",
    "adjusted_ebitda",
    "gaap_eps",
    "op_income",
    "gross_margin_pct",
    "revenue_xbrl",
    "gaap_eps_xbrl",
    "press_matched",
)

# 자기 이력 대비 몇 배까지를 "벗어남"으로 셀 것인가.
#
# ⚠️ 이것은 **버리는 문턱이 아니라 세는 눈금**입니다. 값은 하나도 안
#    바뀝니다. 106차에 BAC 8달러(그 종목 중앙값 220억)를 찾을 때 쓴 눈금을
#    그대로 옮겨 적었습니다. 눈금이 성기면 놓치고 촘촘하면 시끄러운데,
#    **버리지 않으므로 틀려도 값이 상하지 않습니다.**
OUTLIER_FOLD = 1000.0

# 이력이 이보다 짧으면 중앙값이 의미가 없어 세지 않습니다
OUTLIER_MIN_HISTORY = 8


def fill_rates(eps: dict) -> dict:
    """칸별 채움률 — **통째로 빈 칸**을 드러냅니다.

    91차에 `gaap_eps_xbrl` 이 3,066행 전부 None 이었는데 오류 기록이
    하나도 없었습니다. 이 표가 매일 남았다면 그날 바로 보였을 것입니다.
    """
    총 = sum(len(rows) for rows in eps.values())
    out: dict = {"행": 총}
    for field in WATCHED_FIELDS:
        찬 = sum(1 for rows in eps.values() for r in rows if r.get(field) is not None)
        out[field] = {
            "찬칸": 찬,
            "비율": round(100.0 * 찬 / 총, 1) if 총 else None,
        }
    return out


def changed_cells(before: dict, after: dict, limit: int = 8) -> dict:
    """어제 수집물과 **무엇이 달라졌나** — 고침의 부수 피해를 드러냅니다.

    정상인 날은 새 발표 몇 건만 바뀝니다. 파서를 고친 날 수백~수천 칸이
    바뀌면 그 고침이 의도한 것보다 넓게 번진 것입니다. 이번 세션에서
    사람이 매번 손으로 하던 전수 대조(87·89·103차)를 로봇에게 넘깁니다.

    짝은 (종목, 발표일)로 맞춥니다. 새 종목·새 분기는 "추가"로 따로 셉니다.
    """
    def 색인(eps):
        m = {}
        for t, rows in (eps or {}).items():
            for r in rows:
                키 = (t, str(r.get("announced_date") or r.get("filing_date") or ""))
                m[키] = r
        return m

    A, B = 색인(before), 색인(after)
    바뀐칸 = 0
    종목별: dict = {}
    for 키 in A.keys() & B.keys():
        for field in WATCHED_FIELDS:
            if A[키].get(field) != B[키].get(field):
                바뀐칸 += 1
                종목별[키[0]] = 종목별.get(키[0], 0) + 1
    상위 = sorted(종목별.items(), key=lambda kv: -kv[1])[:limit]
    return {
        "맞대본 분기": len(A.keys() & B.keys()),
        "바뀐 칸": 바뀐칸,
        "사라진 분기": len(A.keys() - B.keys()),
        "새 분기": len(B.keys() - A.keys()),
        "많이 바뀐 종목": [{"종목": t, "칸": n} for t, n in 상위],
    }


def outliers(eps: dict, fields=("revenue", "op_income")) -> dict:
    """자기 이력에서 크게 벗어난 값 — **그럴듯한 쓰레기**를 드러냅니다.

    106차에 BAC 매출이 **8달러**(그 종목 중앙값 220억)로 들어와 있었는데,
    8달러는 그 자체로는 아무 검사에도 안 걸리는 "말이 되는" 숫자였습니다.
    한 종목 안에서 자기 이력과 맞대야만 드러납니다.

    ⚠️ **버리지 않습니다.** 개수와 예시만 남깁니다.
    """
    out: dict = {}
    for field in fields:
        걸림 = []
        for t, rows in (eps or {}).items():
            vals = [r[field] for r in rows
                    if isinstance(r.get(field), (int, float))]
            if len(vals) < OUTLIER_MIN_HISTORY:
                continue
            중앙 = median(vals)
            if not 중앙 or 중앙 <= 0:
                continue
            for r in rows:
                v = r.get(field)
                if not isinstance(v, (int, float)):
                    continue
                비 = v / 중앙
                if 비 < 1.0 / OUTLIER_FOLD or 비 > OUTLIER_FOLD:
                    걸림.append({
                        "종목": t,
                        "발표일": r.get("announced_date"),
                        "값": v,
                        "그 종목 중앙값": 중앙,
                    })
        걸림.sort(key=lambda x: abs(x["값"]))
        out[field] = {"건수": len(걸림), "예시": 걸림[:5]}
    return out


def report(after: dict, before: dict | None = None) -> dict:
    """세 검사를 한 덩어리로 — 로봇 기록에 그대로 담깁니다.

    before(어제 수집물)가 없으면 '어제 대비' 칸만 비웁니다. 없는 것을
    지어내지 않습니다.
    """
    out = {
        "설명": (
            "수집물 전체 건강검진 (108차). **값을 바꾸지 않고 세기만 합니다.** "
            "경보 문턱은 며칠치를 쌓아 정상 범위를 실측한 뒤 사전 등록합니다."
        ),
        "채움률": fill_rates(after),
        "이상값": outliers(after),
    }
    out["어제 대비"] = changed_cells(before, after) if before else None
    return out
