"""
vendor_compare.py — 두 자를 맞대 보기 (75차 §④ 가 잴 것)
=========================================================

하는 일:
  우리 파서가 읽은 값(snapshot.json)과 데이터 회사에서 받아 온 값
  (vendor.json)을 같은 분기끼리 맞대어 **얼마나 다른지 셉니다.**
  **아무것도 고치지 않습니다** — 재기만 합니다.

왜 이 방식인가:
  74차에서 배운 것 — 짐작으로 본선을 바꾸면 안 됩니다. "야후가 더
  정확할 것 같다"는 느낌이 아니라 **불일치율 숫자**를 보고 정합니다.
  73차 조사가 "튀었다"고 표시한 칸에서 야후가 정상값을 준다면 그건
  갈아탈 근거이고, 야후도 같이 틀렸다면 헛수고를 안 하게 됩니다.

무엇을 맞대나 (**같은 뜻의 칸만**):
  · GAAP EPS      우리 gaap_eps        ↔ 야후 Diluted EPS
  · 매출          우리 revenue          ↔ 야후 Total Revenue
  · 매출총이익률   우리 gross_margin_pct ↔ 야후 (매출총이익 ÷ 매출)

⚠️ **조정 EPS 는 맞대지 않습니다.** 우리 것은 회사가 정의한 값이고
   야후 `street_eps` 는 월가 기준값이라 **뜻이 다릅니다.** 뜻이 다른
   둘을 "불일치"라 세면 숫자가 거짓말을 합니다 (헌법 1조).
   대신 따로 "얼마나 벌어지는가"만 참고로 봅니다.

분기 짝짓기:
  우리 행의 filing_date(분기 종료일)와 야후 period_end 를 맞춥니다.
  회사마다 결산일이 며칠씩 어긋나므로 **±7일**까지 같은 분기로 봅니다.
  짝을 못 찾은 행은 "짝 없음"으로 세고, **억지로 붙이지 않습니다.**
"""

from __future__ import annotations

import json
from datetime import date, datetime

# 분기 종료일이 며칠까지 어긋나도 같은 분기로 볼 것인가
MATCH_DAYS = 7

# 두 값이 "같다"고 볼 상대 오차 (반올림·단위 표기 차이를 흡수)
#   EPS 는 소수 둘째 자리까지 발표되므로 절대 오차도 함께 봅니다.
REL_TOLERANCE = 0.02          # 2%
EPS_ABS_TOLERANCE = 0.01      # 1센트

FIELDS = (
    ("gaap_eps", "gaap_eps", "GAAP EPS"),
    ("revenue", "revenue", "매출"),
    ("gross_margin_pct", "gross_margin_pct", "매출총이익률"),
)


def _day(text) -> date | None:
    try:
        return datetime.strptime(str(text)[:10], "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def _same(field: str, ours, theirs) -> bool:
    """두 값이 같다고 볼 만한가 (반올림 차이는 같은 것으로)."""
    if ours is None or theirs is None:
        return False
    if field == "gaap_eps" and abs(ours - theirs) <= EPS_ABS_TOLERANCE:
        return True
    큰쪽 = max(abs(ours), abs(theirs))
    if 큰쪽 == 0:
        return ours == theirs
    return abs(ours - theirs) / 큰쪽 <= REL_TOLERANCE


def pair_quarters(our_rows: list[dict],
                  their_rows: list[dict]) -> list[tuple[dict, dict | None]]:
    """우리 분기 행마다 가장 가까운 야후 분기를 붙입니다 (±MATCH_DAYS).

    억지로 붙이지 않습니다 — 범위를 벗어나면 None 입니다.
    한 야후 분기가 두 번 쓰이지 않도록 한 번 쓰면 뺍니다.
    """
    남은 = [(_day(r.get("period_end")), r) for r in their_rows or []]
    남은 = [(d, r) for d, r in 남은 if d is not None]
    out = []
    for row in our_rows or []:
        우리날 = _day(row.get("filing_date"))
        짝, 거리, 자리 = None, None, None
        if 우리날 is not None:
            for i, (그날, 그행) in enumerate(남은):
                d = abs((그날 - 우리날).days)
                if d <= MATCH_DAYS and (거리 is None or d < 거리):
                    짝, 거리, 자리 = 그행, d, i
            if 자리 is not None:
                남은.pop(자리)
        out.append((row, 짝))
    return out


def compare(quarters: dict, vendor: dict) -> dict:
    """전 종목을 맞대어 칸별 일치·불일치를 셉니다. 아무것도 고치지 않습니다."""
    티커표 = (vendor or {}).get("tickers") or {}
    셈 = {이름: {"둘다있음": 0, "같음": 0, "다름": 0,
               "우리만": 0, "야후만": 0} for _, _, 이름 in FIELDS}
    불일치: list[dict] = []
    짝없음 = 짝있음 = 0
    비교종목 = 0

    for ticker, rows in sorted((quarters or {}).items()):
        그쪽 = 티커표.get(ticker)
        if not 그쪽:
            continue
        비교종목 += 1
        for 우리행, 그행 in pair_quarters(rows, 그쪽.get("quarters")):
            if 그행 is None:
                짝없음 += 1
                continue
            짝있음 += 1
            for 우리칸, 그칸, 이름 in FIELDS:
                a, b = 우리행.get(우리칸), 그행.get(그칸)
                if a is None and b is None:
                    continue
                if a is None:
                    셈[이름]["야후만"] += 1
                    continue
                if b is None:
                    셈[이름]["우리만"] += 1
                    continue
                셈[이름]["둘다있음"] += 1
                if _same(우리칸, a, b):
                    셈[이름]["같음"] += 1
                else:
                    셈[이름]["다름"] += 1
                    불일치.append({
                        "종목": ticker, "칸": 이름,
                        "분기": 우리행.get("period_label"),
                        "분기끝": 우리행.get("filing_date"),
                        "발표일": 우리행.get("announced_date"),
                        "우리": a, "야후": b,
                        "배수": (abs(a) / abs(b)) if b else None,
                    })

    불일치.sort(key=lambda r: -(r["배수"] or 0))
    return {"비교한종목": 비교종목, "짝지은분기": 짝있음, "짝없는분기": 짝없음,
            "칸별": 셈, "불일치": 불일치}


def verdict_on_flagged(quarters: dict, vendor: dict,
                       flagged: list[dict]) -> dict:
    """조사(73차)가 튀었다고 표시한 칸에서 **야후는 뭐라 하는가**.

    이것이 이번 대조의 핵심 질문입니다. 우리가 이상하다고 표시한 칸에서
    야후가 다른(정상적인) 값을 준다면 → 우리 쪽 결함일 가능성이 큽니다.
    야후도 같은 값을 준다면 → 그 값이 진짜이거나 둘 다 같은 원본을
    잘못 읽은 것입니다.

    GAAP EPS 칸만 봅니다 — 조정 EPS 는 뜻이 달라 맞댈 수 없습니다.
    """
    티커표 = (vendor or {}).get("tickers") or {}
    결과 = {"야후가_다름": [], "야후도_같음": [], "야후에_없음": []}
    for cell in flagged or []:
        if cell.get("칸") != "gaap_eps":
            continue
        ticker = cell["종목"]
        그쪽 = 티커표.get(ticker)
        우리행 = None
        for row in (quarters or {}).get(ticker) or []:
            if row.get("period_label") == cell.get("라벨") and \
                    row.get("gaap_eps") == cell.get("값"):
                우리행 = row
                break
        if 우리행 is None or not 그쪽:
            결과["야후에_없음"].append(cell)
            continue
        그행 = pair_quarters([우리행], 그쪽.get("quarters"))[0][1]
        if 그행 is None or 그행.get("gaap_eps") is None:
            결과["야후에_없음"].append(cell)
        elif _same("gaap_eps", 우리행["gaap_eps"], 그행["gaap_eps"]):
            결과["야후도_같음"].append({**cell, "야후": 그행["gaap_eps"]})
        else:
            결과["야후가_다름"].append({**cell, "야후": 그행["gaap_eps"]})
    return 결과


# 원문을 부탁할 때 맞대는 칸 (86차)
# ---------------------------------------------------------------------------
# **뜻이 똑같은 칸만** 씁니다. GAAP EPS 와 매출은 회계 규정이 정한 값이라
# 우리와 야후가 다르면 둘 중 하나가 틀린 것입니다.
#
# 매출총이익률은 뺍니다 — 여기서 갈리는 것은 대개 **결함이 아니라 정의
# 차이**입니다. 실측(86차): CSCO 67.5 ↔ 야후 63.2 · BILL 84.9 ↔ 야후 81.2 —
# 우리는 회사가 발표한 논갭 이익률을 읽고 야후는 갭 기준으로 계산합니다.
# 이걸 "다름"으로 세어 원문을 부탁하면 멀쩡한 칸이 목록을 가득 채웁니다.
_MISMATCH_FIELDS = ("GAAP EPS", "매출")

# 한 종목에서 몇 건까지 부탁할 것인가
# ---------------------------------------------------------------------------
# 한 회사는 보도자료 **형식이 같습니다.** 같은 회사 다섯 분기를 부탁해도
# 보이는 것은 같은 형식이라 배우는 게 없는데, 목록 자리는 다섯 칸 먹습니다.
# 실측(86차): 자리 30칸 중 24칸을 단위 차이 한 종류가 차지했습니다
# (AMD·HD·TGT·NFLX·TER — 우리는 천/백만 단위, 야후는 원 단위).
# 2건인 이유: 4분기 발표문은 연간 표가 붙어 형식이 달라서 한 건으로는
# 모자랍니다. 로봇의 종목당 보관 자리도 12칸뿐입니다.
_PER_TICKER_MAX = 2


def wanted_from_mismatch(quarters: dict, vendor: dict,
                         limit: int = 30) -> list[dict]:
    """야후와 어긋난 칸의 원문을 로봇에게 부탁할 목록으로 바꿉니다.

    **왜 이게 필요한가** (86차에 실물로 겪은 일)
      80~85차 수리 묶음을 로봇이 처음 반영했더니, 고쳐진 칸도 많았지만
      CRM 처럼 **멀쩡하던 값이 무너진 칸**도 나왔습니다
      (GAAP EPS 우리 0.00 ↔ 야후 1.96). 원인을 알려면 그 보도자료
      원문을 봐야 하는데, 개발 환경은 SEC 가 막혀 있습니다.

      73차의 조사(audit_data)는 **이웃 분기와 비교**해 튄 칸을 찾으므로,
      CRM 처럼 여러 분기가 **나란히** 무너지면 튐이 없어 못 봅니다.
      야후라는 바깥 자는 그걸 봅니다. 그래서 조사가 못 보는 자리를
      이 목록이 메웁니다.

    아무것도 고치지 않습니다 — **원문을 달라고 적을 뿐**입니다.
    발표일이 없는 행은 담지 않습니다 (어느 공시인지 짚을 수 없음).
    """
    r = compare(quarters, vendor)

    # ⚠️ compare() 의 불일치 목록은 **배수(우리÷야후) 큰 순**입니다. 그대로
    #    쓰면 우리가 **작게** 읽은 칸이 맨 뒤로 밀립니다 — 86차에 실제로
    #    당했습니다: CRM(우리 0.00 ↔ 야후 1.96)은 배수가 0 이라 30건 안에
    #    못 들어왔는데, 정작 원문이 가장 급한 칸이 그것이었습니다.
    #    그래서 여기서는 **어느 쪽으로 벌어졌든** 벌어진 폭으로 다시 셉니다.
    def 벌어진폭(cell) -> float:
        a, b = abs(cell["우리"] or 0.0), abs(cell["야후"] or 0.0)
        큰쪽, 작은쪽 = max(a, b), min(a, b)
        if 큰쪽 == 0:
            return 0.0
        if 작은쪽 == 0:
            return float("inf")       # 한쪽이 0 이면 가장 크게 벌어진 것
        return 큰쪽 / 작은쪽

    골라낸 = sorted(
        (c for c in r["불일치"]
         if c["칸"] in _MISMATCH_FIELDS and c.get("발표일")),
        key=벌어진폭, reverse=True,
    )

    out = []
    종목별 = {}
    for cell in 골라낸:
        종목 = cell["종목"]
        if 종목별.get(종목, 0) >= _PER_TICKER_MAX:
            continue
        종목별[종목] = 종목별.get(종목, 0) + 1
        out.append({
            "종목": 종목,
            "발표일": str(cell["발표일"])[:10],
            "칸": cell["칸"],
            "값": cell["우리"],
            "배수": round(cell["배수"], 1) if cell["배수"] else None,
            "이유": f"야후는 {cell['야후']:,.2f} 라고 함 (우리 {cell['우리']:,.2f})",
        })
        if len(out) >= limit:
            break
    return out


# ---------------------------------------------------------------------------
# 월가 발표 EPS ↔ 회사 조정 EPS (111차 측정 → 115차 배관)
# ---------------------------------------------------------------------------
# 이 파일 머리말의 "조정 EPS 는 맞대지 않습니다"는 그대로 유효합니다 —
# 뜻이 다른 둘을 **불일치율**로 세면 숫자가 거짓말을 합니다.
#
# 여기서 하는 일은 다릅니다. 111차 실측으로 월가 "발표 EPS"의 정체가
# 드러났습니다: 발표일로 짝지은 3,132칸 중 72.4%가 1.5센트 안에서 회사
# 조정 EPS 와 일치하고 **차이의 중앙값은 정확히 0** — 월가는 대부분 회사
# 조정 EPS 를 그대로 받아 적습니다. 그러므로 크게 갈린 꼬리는 높은
# 확률로 **어느 한쪽의 결함**이고, 가장 크게 갈린 6곳을 산술로 확인하니
# 우리 쪽 연간값 오염이었습니다 (FDX 17.80 = 직전 4분기 월가 합 17.81 ·
# AXP 15.38 = 15.39, 각 0.1% 차).
#
# 조정 EPS 는 XBRL 이 못 지키는 유일한 잣대 칸입니다(XBRL 에 그 값이
# 없음). 그래서 이 심판이 만든 의심 목록을 `부탁_` 원문 요청으로 이어,
# 다음 세션이 실물 보도자료로 원인을 확인합니다. **값은 지우지 않습니다.**

# 발표일 짝짓기 허용 오차 — 장 마감 후 발표는 8-K 제출이 다음 날로
# 넘어가기도 합니다. 분기 발표는 약 90일 간격이라 3일이면 안전합니다.
STREET_MATCH_DAYS = 3

# 111차와 같은 기준: 1.5센트 안이면 같은 값으로 봅니다.
STREET_EPS_TOLERANCE = 0.015

# "연간값 모양" 판정 — 우리 값이 그 발표일까지의 월가 4개 분기 합과
# 2% 안이면 연간값 오염으로 표시합니다 (FDX·AXP 는 0.1% 차였습니다).
ANNUAL_SHAPE_REL = 0.02


def street_mismatch(quarters: dict, vendor: dict) -> list[dict]:
    """회사 조정 EPS 와 월가 발표 EPS 가 크게 갈린 칸을 셉니다.

    아무것도 고치지 않습니다 — 갈린 칸을 적기만 합니다.

    짝짓기는 **발표일**로만 합니다. vendor.json 의 announcements 에는
    날짜 뜻이 두 가지 섞여 있습니다 (105차 ⑥에서 실측한 함정):
      · 날짜뜻 "발표일"  — get_earnings_dates 경로. 짝짓기에 씁니다.
      · 날짜뜻 "분기끝"  — earnings_history 예비 경로. 발표일이 아니므로
                          여기서는 **쓰지 않습니다** (억지로 붙이지 않음).
    """
    티커표 = (vendor or {}).get("tickers") or {}
    out: list[dict] = []
    for ticker, rows in sorted((quarters or {}).items()):
        발표들 = [
            (_day(a.get("announced_date")), a)
            for a in (티커표.get(ticker) or {}).get("announcements") or []
            if a.get("날짜뜻") == "발표일" and a.get("street_eps") is not None
        ]
        발표들 = sorted(((d, a) for d, a in 발표들 if d is not None),
                      key=lambda 짝: 짝[0])
        if not 발표들:
            continue
        남은 = list(range(len(발표들)))
        for row in rows or []:
            ours = row.get("adj_eps")
            우리날 = _day(row.get("announced_date"))
            if ours is None or 우리날 is None:
                continue
            자리, 거리 = None, None
            for i in 남은:
                d = abs((발표들[i][0] - 우리날).days)
                if d <= STREET_MATCH_DAYS and (거리 is None or d < 거리):
                    자리, 거리 = i, d
            if 자리 is None:
                continue
            남은.remove(자리)          # 한 발표를 두 번 쓰지 않습니다
            street = 발표들[자리][1]["street_eps"]
            if abs(ours - street) <= STREET_EPS_TOLERANCE:
                continue
            # 연간값 모양인가 — 그 발표를 포함한 월가 4개 분기의 합과 비교
            사합 = None
            if 자리 >= 3:
                넷 = [발표들[j][1]["street_eps"] for j in range(자리 - 3, 자리 + 1)]
                if all(v is not None for v in 넷):
                    사합 = sum(넷)
            연간모양 = (사합 is not None and 사합 != 0
                    and abs(ours - 사합) / abs(사합) <= ANNUAL_SHAPE_REL)
            out.append({
                "종목": ticker,
                "발표일": str(row.get("announced_date"))[:10],
                "우리": ours,
                "월가": street,
                "사합": round(사합, 2) if 사합 is not None else None,
                "연간모양": 연간모양,
            })
    return out


def wanted_from_street(quarters: dict, vendor: dict,
                       limit: int = 20) -> list[dict]:
    """월가와 크게 갈린 조정 EPS 칸을 `부탁_` 원문 요청으로 바꿉니다.

    차례 정하기 — 자리(limit)가 귀하므로:
      ① **연간값 모양이 앞** — 우리 결함일 확률이 가장 높은 칸입니다
        (111차에서 산술로 확정된 종류). 정의 차이는 이 모양이 안 나옵니다.
      ② 그다음은 벌어진 폭 순 — 어느 쪽으로 벌어졌든
        (wanted_from_mismatch 가 86차 CRM 에서 배운 것과 같은 이유).
    종목당 2건 제한(_PER_TICKER_MAX)도 같은 이유로 같습니다.
    """
    def 벌어진폭(cell) -> float:
        a, b = abs(cell["우리"]), abs(cell["월가"])
        큰쪽, 작은쪽 = max(a, b), min(a, b)
        if 큰쪽 == 0:
            return 0.0
        if 작은쪽 == 0:
            return float("inf")
        return 큰쪽 / 작은쪽

    골라낸 = sorted(street_mismatch(quarters, vendor),
                 key=lambda c: (not c["연간모양"], -벌어진폭(c)))
    out = []
    종목별: dict[str, int] = {}
    for cell in 골라낸:
        종목 = cell["종목"]
        if 종목별.get(종목, 0) >= _PER_TICKER_MAX:
            continue
        종목별[종목] = 종목별.get(종목, 0) + 1
        이유 = f"월가는 {cell['월가']:,.2f} 라고 함 (우리 {cell['우리']:,.2f})"
        if cell["연간모양"]:
            이유 += (f" — 연간값 모양: 우리 값이 월가 4분기 합"
                   f" {cell['사합']:,.2f} 과 2% 안")
        out.append({
            "종목": 종목,
            "발표일": cell["발표일"],
            "칸": "조정 EPS",
            "값": cell["우리"],
            "배수": None,
            "이유": 이유,
        })
        if len(out) >= limit:
            break
    return out


def duel(quarters: dict, vendor: dict) -> dict:
    """**같은 칸을 두 경로가 각각 읽었을 때 누가 맞았나** (90차).

    우리 스냅샷에는 같은 칸의 값이 둘 있습니다:
      · `gaap_eps`       보도자료 파서가 읽은 값 (없으면 XBRL 값이 그대로)
      · `gaap_eps_xbrl`  보도자료가 덮어쓰기 **전**의 XBRL 값

    야후를 심판으로 세워 칸별로 셉니다. 이 숫자가 나와야
    "어느 쪽을 본선으로 삼을까"가 짐작이 아니라 사실이 됩니다.

    ⚠️ 아무것도 고치지 않습니다 — 세기만 합니다.
    ⚠️ 야후에 값이 없거나 짝을 못 찾은 분기는 아예 세지 않습니다.
       "둘 다 없음"을 무승부라고 부르면 숫자가 거짓말을 합니다.
    """
    티커표 = (vendor or {}).get("tickers") or {}
    결과 = {이름: {"둘다읽음": 0, "둘다맞음": 0, "보도자료만맞음": 0,
                "XBRL만맞음": 0, "둘다틀림": 0,
                "보도자료만있음": 0, "XBRL만있음": 0}
          for _, _, 이름 in FIELDS}
    사례: list[dict] = []

    for ticker, rows in sorted((quarters or {}).items()):
        그쪽 = 티커표.get(ticker)
        if not 그쪽:
            continue
        for 우리행, 그행 in pair_quarters(rows, 그쪽.get("quarters")):
            if 그행 is None:
                continue
            for 우리칸, 그칸, 이름 in FIELDS:
                심판 = 그행.get(그칸)
                if 심판 is None:
                    continue          # 심판이 모르면 승부를 못 가립니다
                보도 = 우리행.get(우리칸)
                엑스 = 우리행.get(f"{우리칸}_xbrl")
                칸 = 결과[이름]
                if 보도 is None and 엑스 is None:
                    continue
                if 엑스 is None:
                    칸["보도자료만있음"] += 1
                    continue
                if 보도 is None:
                    칸["XBRL만있음"] += 1
                    continue
                칸["둘다읽음"] += 1
                보도맞 = _same(우리칸, 보도, 심판)
                엑스맞 = _same(우리칸, 엑스, 심판)
                if 보도맞 and 엑스맞:
                    칸["둘다맞음"] += 1
                elif 보도맞:
                    칸["보도자료만맞음"] += 1
                elif 엑스맞:
                    칸["XBRL만맞음"] += 1
                    사례.append({"종목": ticker, "칸": 이름,
                                "분기끝": 우리행.get("filing_date"),
                                "보도자료": 보도, "XBRL": 엑스, "야후": 심판})
                else:
                    칸["둘다틀림"] += 1
    return {"칸별": 결과, "XBRL만맞은칸": 사례}


def duel_report(quarters: dict, vendor: dict, limit: int = 15) -> str:
    """사람이 읽을 한 덩어리 (90차 — 재고 뒤집기의 판단 근거)."""
    d = duel(quarters, vendor)
    줄 = ["같은 칸을 두 경로가 읽었을 때 — 야후가 심판 (90차)"]
    for _, _, 이름 in FIELDS:
        c = d["칸별"][이름]
        둘 = c["둘다읽음"]
        갈린 = c["보도자료만맞음"] + c["XBRL만맞음"]
        if 둘 == 0:
            줄.append(f"  {이름:8s} 맞대 볼 칸이 없습니다 "
                      f"(XBRL 값을 아직 안 담았을 수 있습니다)")
            continue
        승 = (f"{c['XBRL만맞음']}:{c['보도자료만맞음']}" if 갈린 else "갈린 칸 없음")
        줄.append(
            f"  {이름:8s} 둘 다 읽음 {둘:4d} · 둘 다 맞음 {c['둘다맞음']:4d} · "
            f"둘 다 틀림 {c['둘다틀림']:4d}\n"
            f"           갈린 칸 {갈린:4d} — XBRL:보도자료 = {승}   "
            f"(한쪽만 있음: XBRL {c['XBRL만있음']} · 보도자료 {c['보도자료만있음']})"
        )
    if d["XBRL만맞은칸"]:
        줄.append("\n  XBRL 만 맞은 칸 (보도자료가 틀린 자리):")
        for row in d["XBRL만맞은칸"][:limit]:
            줄.append(f"    {row['종목']:6s} {str(row['분기끝'])[:10]:11s} "
                      f"{row['칸']:8s} 보도자료 {row['보도자료']:>14,.2f} · "
                      f"XBRL {row['XBRL']:>14,.2f} (야후 {row['야후']:>14,.2f})")
    return "\n".join(줄)


def report(quarters: dict, vendor: dict, flagged: list[dict] | None = None,
           limit: int = 25) -> str:
    """사람이 읽을 한 덩어리 (화면·기록용)."""
    r = compare(quarters, vendor)
    줄 = [f"두 자 대조 — 종목 {r['비교한종목']}개 · "
         f"짝지은 분기 {r['짝지은분기']}개 (짝 못 찾음 {r['짝없는분기']}개)"]
    for _, _, 이름 in FIELDS:
        c = r["칸별"][이름]
        둘다 = c["둘다있음"]
        비율 = f"{c['같음'] / 둘다 * 100:.1f}%" if 둘다 else "확인 못함"
        줄.append(f"  {이름:8s} 둘 다 있음 {둘다:4d} · 같음 {c['같음']:4d}"
                  f"({비율}) · 다름 {c['다름']:4d} · "
                  f"우리만 {c['우리만']:4d} · 야후만 {c['야후만']:4d}")
    if r["불일치"]:
        줄.append("\n  가장 크게 어긋난 칸:")
        for row in r["불일치"][:limit]:
            줄.append(f"    {row['종목']:6s} {str(row['분기']):8s} {row['칸']:8s} "
                      f"우리 {row['우리']:>14,.2f} ↔ 야후 {row['야후']:>14,.2f}")
    if flagged:
        v = verdict_on_flagged(quarters, vendor, flagged)
        줄.append(f"\n  조사가 표시한 칸(GAAP EPS)에서: "
                  f"야후가 다름 {len(v['야후가_다름'])} · "
                  f"야후도 같음 {len(v['야후도_같음'])} · "
                  f"야후에 없음 {len(v['야후에_없음'])}")
        for row in v["야후가_다름"][:limit]:
            줄.append(f"    {row['종목']:6s} {str(row['라벨']):8s} "
                      f"우리 {row['값']:>10,.2f} ↔ 야후 {row['야후']:>10,.2f}")
    return "\n".join(줄)


if __name__ == "__main__":
    import audit_data
    import dataset

    ds = dataset.build(dataset.load())
    try:
        with open("data/measure/vendor.json", encoding="utf-8") as f:
            vendor = json.load(f)
    except OSError:
        raise SystemExit(
            "data/measure/vendor.json 이 아직 없습니다 — 로봇이 두 번째 자를 "
            "한 번은 받아 와야 대조할 수 있습니다 (75차)."
        )
    print(report(ds["quarters"], vendor,
                 audit_data.spike_cells(ds["quarters"])))
