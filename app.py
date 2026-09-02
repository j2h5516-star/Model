"""
app.py — 계기판 (v3 6단계, 설계도.md ④)
=========================================

로봇이 커밋한 데이터(verdict.json · snapshot.json · robot_log.json)를
**읽어서 그대로** 보여 줍니다.

경계 (설계도.md 3장):
  · 자체 계산·점수·추천을 만들지 않습니다 (v1 붕괴의 원인 — 사고 1)
  · 신호를 판단처럼 보이게 하지 않습니다. 모든 상태 옆에 그 상태의
    **과거 실측 폭등률·기준선·판정 상태**를 나란히 적습니다 (정직화 —
    전략.md 2장 제3조)
  · 채택된 신호가 없으면 "채택된 신호 없음"을 그대로 표시합니다

화면 검증: 모바일 폭 412px 실렌더에서 글자 잘림·열 가림 없음을
확인하고 커밋합니다 (CLAUDE.md 2장).

실행: streamlit run app.py
"""

from __future__ import annotations

import json
import os
from datetime import date, timedelta

import audit_data
import config as cfg
import dataset
import sector_model as sm
import judge
import measure_engine as me

# 화면에 "최근 발표"로 보여 줄 기간 (표시용 — 측정 규칙 아님)
RECENT_DAYS = 45

# v3 판정 장치(11차 등록)가 쓰는 가설 이름 — verdict.json 이 이 이름들을
# 갖고 있지 않으면 v2 유물이므로 화면에 그렇게 밝힙니다.
V3_HYPOTHESES = (
    "H2_신고점", "H2b_신고점_첫돌파", "H5_실적폭_고정20",
    "H5b_실적폭_중앙값", "H6_결합_H5bxH2b",
)
# 화면 이름은 쉬운 한국어로, 괄호 안 H번호는 등록 문서와 잇는 꼬리표입니다.
HYPOTHESIS_LABELS = {
    "H2_신고점": "이익 신기록 돌파 (H2)",
    "H2b_신고점_첫돌파": "이익 첫 신기록 돌파 (H2b)",
    "H5_실적폭_고정20": "시장 게이지 20% 고정 문턱 (H5)",
    "H5b_실적폭_중앙값": "시장 게이지 — 평소보다 높음 (H5b)",
    "H6_결합_H5bxH2b": "시장 좋음 × 첫 신기록 (H6)",
    "H7_EBITDA_첫돌파": "EBITDA 첫 신기록 (H7)",
    "H8_GAAPEPS_첫돌파": "GAAP EPS 첫 신기록 (H8)",
    "H9_저평가_첫신기록": "첫 신기록 × 주가 52주선 아래 (H9)",
    "H10_논갭영업이익_저평가_첫신기록": "영업이익 첫 신기록 × 52주선 아래 (H10)",
    "H11_섹터정배열폭_60": "섹터 정배열 폭 60% 첫 돌파 (H11)",
    "H11b_섹터정배열폭_80": "섹터 정배열 폭 80% 첫 돌파 (H11b)",
    "H18_완성시_52주선이격도": "정배열 완성 × 52주선 이격도 30%+ (H18)",
    "H19_주도섹터_판정": "주도섹터 판정 — 완성 무리 × 델타 (H19)",
    "H19b_주도섹터_완성후확인": "주도섹터 — 완성 후 확인형 (H19b)",
    "H20_주도섹터_전환": "주도섹터 전환 (H20)",
    "H21_주도섹터_분기점": "주도섹터 분기점 — 델타 꺾임 (H21)",
    "H31_가속_첫돌파": "첫 신기록 × 이익 가속 (H31)",
    "H32_감속_런업_회피": "달린 뒤 감속 — 줄여야 하나 (H32, 회피)",
    "H32b_가속_런업": "달린 뒤에도 가속 — 들고 가나 (H32b)",
    "H33_런업_변동폭": "달린 종목은 크게 움직이나 — 비중 크기용 (H33)",
}

# 미채택·대기 신호를 화면에서 쉬운 말로 풀어 주는 설명 (지시 1의 이행).
# "무엇을 재봤는가 / 왜 안 쓰는가"를 한 줄씩 적습니다.
HYPOTHESIS_DETAILS = {
    "H2_신고점": (
        "이익(TTM)이 과거 최고를 넘은 발표를 사면 이기는가? — 신기록 자체만으로는 "
        "아무 발표나 산 것과 차이가 없었습니다."),
    "H2b_신고점_첫돌파": (
        "그중에서도 **처음** 넘은 발표만 골라도 되는가? — 연속 돌파보다 낫지만 "
        "기준선과 갈라질 만큼은 아니었습니다."),
    "H5_실적폭_고정20": (
        "시장 전체에서 신기록이 20% 이상 나오는 '좋은 장세'에 사면 되는가? — "
        "고정 문턱으로는 갈라지지 않았습니다."),
    "H5b_실적폭_중앙값": (
        "장세가 '그 시장의 평소보다' 좋을 때 사면 되는가? — 한때 채택됐다가 "
        "데이터가 늘자 미채택으로 뒤집혔습니다 (장치가 정직하게 작동한 사례)."),
    "H6_결합_H5bxH2b": (
        "좋은 장세 × 첫 신기록을 겹치면 되는가? — 두 조건을 다 만족하는 발표가 "
        "아직 10건이 안 돼 판정을 미루고 있습니다."),
    "H7_EBITDA_첫돌파": (
        "조정 EPS 를 발표하지 않는 회사는 EBITDA 로 같은 것을 봅니다 — "
        "해당 회사가 적어 표본이 아직 부족합니다."),
    "H8_GAAPEPS_첫돌파": (
        "EBITDA 도 없으면 GAAP EPS 로 봅니다 — 기준선과 갈라지지 않았습니다."),
    # ⚠️ 이 설명들에는 숫자를 적지 않습니다 (101차). 예전에는
    #    "오히려 나빴습니다(9.4% vs 기준선 26.8%)" 처럼 **글자로 박혀**
    #    있었는데, 10년 표본으로 다시 재니 31.6% vs 31.6% 이라 그 문장이
    #    거짓말이 돼 있었습니다. 실제 숫자는 바로 옆의 판정 줄이
    #    판정 파일에서 읽어 보여 줍니다.
    "H11_섹터정배열폭_60": (
        "섹터의 주가가 무리로 정배열되면(60% 돌파) 그 뒤 1년이 좋은가? — "
        "다 오른 뒤에 사는 셈이라 늦을 수 있다는 물음입니다."),
    "H11b_섹터정배열폭_80": (
        "더 강한 합의(80%)면 다른가?"),
    "H18_완성시_52주선이격도": (
        "주봉 정배열이 **차트에서 막 완성된** 그 주에, 주가가 52주선 위로 "
        "30% 이상 떠 있으면 다른가? — 지난 자료를 뒤져 찾아낸 후보라 "
        "**그 자료로는 판정하지 않습니다**(2026-08-15 등록, 그 뒤에 새로 "
        "생기는 완성만 셉니다). 첫 판정은 빨라야 2026년 11월입니다. "
        "참고로 지난 자료에서는 30%+ 가 28.6%, 전체가 13.1% 였습니다."),
    "H19_주도섹터_판정": (
        "같은 묶음에서 최근 한 분기 안에 정배열을 완성한 종목이 3개 이상이고, "
        "그 종목들의 30% 이상이 완성했고, 완성 종목의 과반이 이익 델타 상승을 "
        "동반했는가? — 셋을 다 넘는 묶음 중 점수가 가장 높은 것을 주도로 봅니다. "
        "**국면 단위라 표본이 4개뿐이라 채택할 수 없습니다.** 지금 지목된 묶음도 "
        "델타를 못 재는 종목이 섞여 있어 그대로 믿으면 안 됩니다."),
    "H19b_주도섹터_완성후확인": (
        "정배열 완성과 이익 델타가 **동시에** 오는 게 아니라, 완성이 먼저이고 "
        "델타는 다음 실적에서 확인되는 것 아닌가? — 실제로 광통신은 완성 뒤 "
        "5~61일에 델타가 돌아섰습니다. 그래서 '확인이 선 주'를 신호로 삼는 판을 "
        "따로 잽니다. **지난 자료에서는 신호 14.0%로 기준선 24.2%보다 나빴습니다.** "
        "그 자료로는 판정하지 않으며(2026-08-15 등록), 앞으로 생기는 확인만 셉니다."),
    "H20_주도섹터_전환": (
        "주도섹터는 **스스로 내려오지 않습니다**. 정배열이 깨져도, 다른 묶음이 "
        "위 세 조건을 다 넘고 현 주도의 최근 최고점을 넘을 때만 바뀝니다. "
        "그렇지 않으면 '기존 추세 유지'입니다. — 전환이 3건뿐이라 판정 불가입니다."),
    "H21_주도섹터_분기점": (
        "주도섹터 안에서 이익 델타가 오른 종목 비율이 그 국면 최고 대비 20%p "
        "이상 꺾인 첫 주를 '분기점'으로 봅니다. 그 뒤 상승 힘이 빠지는가? — "
        "3건 중 1건만 맞았고, 표본이 3건이라 판정 불가입니다."),
}


# ---------------------------------------------------------------------------
# 순수 도우미 — 화면 없이도 시험할 수 있게 분리
# ---------------------------------------------------------------------------
def load_json(name: str) -> dict | None:
    path = os.path.join(cfg.MEASURE_DIR, name)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def verdict_is_v3(verdict: dict | None) -> bool:
    """verdict.json 이 11차 등록의 새 판정인지 (아니면 v2 유물인지)."""
    if not verdict or "가설" not in verdict:
        return False
    return all(name in verdict["가설"] for name in V3_HYPOTHESES)


def verdict_code_warning(verdict: dict | None,
                         current: str | None = None) -> str | None:
    """판정이 **지금 코드와 다른 판**으로 계산됐으면 경고 문장, 아니면 None.

    52차 감사가 찾아낸 사고: verdict.json 은 주도 섹터를 "데이터센터"로
    적고 있었지만, 같은 원자료(snapshot)로 현재 코드를 돌리면
    "기기 OEM 반도체"가 나왔습니다. 데이터가 아니라 **코드가 달랐던**
    것입니다 — 51차 수리가 그 판정 계산보다 뒤였습니다. 그런데 화면에는
    계산 시각만 있고 코드 판번호가 없어 사람이 알아챌 방법이 없었습니다.

    그래서 judge 가 판정 파일에 code_rev(git 짧은 해시)를 적고,
    화면은 그것이 지금 코드와 다르면 여기서 배너를 띄웁니다.

    아무 말도 하지 않는 경우(=None):
      - 두 판번호가 같다 (정상)
      - 판번호를 못 알아낸 채로 적혀 있다("알수없음") → 비교할 수가
        없으니 근거 없이 겁주지 않습니다

    판번호가 **아예 없는** 판정 파일은 다릅니다. 이 수리(52차) 이전에
    계산됐다는 뜻이므로 옛 코드인 것이 확실합니다 — 그때는 알립니다.
    """
    if not verdict:
        return None
    if current is None:
        current = judge.code_revision()
    tail = ("그 사이 고친 것이 판정 결과를 바꿨을 수 있습니다 — "
            "다음 로봇 수집 때 다시 계산됩니다.")

    if "code_rev" not in verdict:
        return (
            "⚠️ 이 판정에는 **코드 판번호가 없습니다** — 판번호를 적기 "
            "시작한 수리(52차)보다 먼저 계산됐다는 뜻입니다. " + tail
        )

    recorded = verdict.get("code_rev")
    unknown = (None, "", "알수없음")
    if recorded in unknown or current in unknown:
        return None
    if recorded == current:
        return None
    return (
        f"⚠️ 이 판정은 **옛 코드**로 계산됐습니다 "
        f"(판정 {recorded} · 지금 코드 {current}). " + tail
    )


# ---------------------------------------------------------------------------
# 가설 신뢰 등급 (171차, 주인 지시 "종류가 쓸데없이 너무 많다 — 가장 신뢰성
# 높은 것만") — **표시용 묶음**이지 판정이 아니다. 등록된 가설은 지우지
# 않는다(헌법 3조: 미채택 가설은 관찰 목록에 두어 자동 재판정). 화면이
# 등급 A·B 만 펼치고 C·D 는 접는다.
#   A 채택      — 판정이 채택/회피 채택 (지금 H9 하나)
#   B 유력 대기  — 판정 대기인데 **탐색표본에서 완전 분리**(신호 하한 > 기준선
#                 상한). 등록일 뒤 새 표본이 차면 채택될 가능성이 있는 것
#   C 우위 없음  — 미채택, 또는 판정 대기인데 탐색표본이 기준선과 겹침
#   D 자료 없음  — 수치가 아직 없음(섹터 주도 계열 등)
# 등급 안 순서 = 분리 폭(신호 하한 − 기준선 상한, %p) 큰 순.
def hypothesis_tier(entry: dict) -> dict:
    """한 가설의 등급·이유·분리 폭. 값을 만들지 않고 판정 파일의 수만 읽는다."""
    판정 = entry.get("판정")
    새 = entry.get("신규(판정)") or {}
    탐 = entry.get("탐색표본(참고)") or entry.get("전체(참고)") or {}
    회피 = entry.get("방향") == "회피"

    def 분리폭(j: dict) -> float | None:
        s, b = j.get("신호") or {}, j.get("기준선") or {}
        if not s.get("n") or not b.get("n") or not s.get("ci") or not b.get("ci"):
            return None
        if 회피:
            return round(b["ci"][0] - s["ci"][1], 1)     # 기준선 하한 − 신호 상한
        return round(s["ci"][0] - b["ci"][1], 1)         # 신호 하한 − 기준선 상한

    새폭, 탐폭 = 분리폭(새), 분리폭(탐)
    if 판정 in ("채택", "회피 채택"):
        return {"등급": "A", "이유": "새 표본으로 채택 기준 통과", "분리폭": 새폭}
    if 판정 == "판정 불가":
        if 탐폭 is not None and 탐폭 > 0:
            return {"등급": "B", "이유": f"탐색표본에서 완전 분리(+{탐폭}%p) — 새 표본 대기",
                    "분리폭": 탐폭}
        if 탐폭 is None and 새폭 is None:
            return {"등급": "D", "이유": "수치 없음", "분리폭": None}
        return {"등급": "C", "이유": "탐색표본에서 기준선과 겹침 — 새 표본 대기", "분리폭": 탐폭}
    if 판정 == "미채택":
        폭 = 새폭 if 새폭 is not None else 탐폭
        if 폭 is None:
            return {"등급": "D", "이유": "수치 없음", "분리폭": None}
        return {"등급": "C", "이유": f"새 표본에서 기준선과 갈라지지 않음({폭:+}%p)", "분리폭": 폭}
    return {"등급": "D", "이유": "판정 없음", "분리폭": None}


def hypothesis_tiers(verdict: dict | None) -> dict[str, dict]:
    """가설 이름 → 등급 정보. 판정 파일이 없으면 빈 표."""
    if not verdict or "가설" not in verdict:
        return {}
    return {name: hypothesis_tier(entry) for name, entry in verdict["가설"].items()}


def verdict_rows(verdict: dict | None) -> list[dict]:
    """판정 현황 표의 행 (신규 표본 기준 — 등록된 판정 표본)."""
    if not verdict or "가설" not in verdict:
        return []
    rows = []
    for name, entry in verdict["가설"].items():
        judged = entry.get("신규(판정)") or {}
        signal = judged.get("신호") or {}
        base = judged.get("기준선") or {}
        rows.append(
            {
                "가설": HYPOTHESIS_LABELS.get(name, name),
                "판정": entry.get("판정", "?"),
                "신호": (
                    f"{signal.get('rate')}% (n={signal.get('n')})"
                    if signal.get("n") else "—"
                ),
                "기준선": (
                    f"{base.get('rate')}%" if base.get("n") else "—"
                ),
            }
        )
    return rows



def signal_summary(entry: dict, signal: dict, base: dict) -> str:
    """미채택 칸에 적을 한 줄 — 그 가설이 실제로 몇 번 맞았는지.

    가설마다 결과의 모양이 다릅니다:
      · H2~H18  : 신규(판정) 안에 {신호, 기준선}
      · H19     : 국면 상태(현재 주도·국면 수) — 성공률이라는 게 없습니다
      · H20·H21 : 사건 목록의 성공/전체 (국면 단위)
    모양을 못 알아보면 **"표본이 아직 없습니다"로 뭉개지 않고** 그 사실을
    그대로 적습니다.
    """
    if signal.get("n"):
        return (f"실측 {signal.get('rate')}% (n={signal.get('n')}) "
                f"vs 기준선 {base.get('rate')}%")
    if entry.get("현재_주도") is not None or "국면수" in entry:
        leader = entry.get("현재_주도") or "없음"
        line = f"현재 지목: {leader} · 국면 {entry.get('국면수', 0)}개 (성공률 아님)"
        # 52차 감사의 요구 — 지목만 적고 흔들림을 안 적으면 사람이 그것을
        # 확정된 사실로 읽습니다. 반드시 함께 적습니다.
        shake = entry.get("안정성") or {}
        if shake.get("바뀐비율_중앙값") is not None:
            # 58차 — 중앙값만 적으면 정밀도를 과장합니다. 실측: 같은 6%를
            # 4번 지웠더니 49·60·82·113주로 나왔습니다. **흔들림을 재는 자
            # 자체가 흔들립니다.** 그래서 범위를 함께 적습니다.
            low, high = shake.get("바뀐주_최소"), shake.get("바뀐주_최대")
            span = (f" (뽑기마다 {low}~{high}주로 갈림)"
                    if low is not None and high is not None and low != high
                    else "")
            line += (
                f"  ⚠️ 흔들림: 잣대값을 {shake.get('지운비율', 0) * 100:.0f}% 만 "
                f"지워도 주도가 {shake['바뀐주_중앙값']}/{shake['판정주수']}주"
                f"({shake['바뀐비율_중앙값']}%) 바뀝니다{span} · 지금 지목도 "
                f"{shake.get('반복', 0)}번 중 {shake.get('마지막주_불일치', 0)}번 달라짐"
            )
        return line
    if entry.get("n"):
        line = f"실측 {entry.get('성공')}/{entry.get('n')}건 = {entry.get('rate')}%"
        ref = entry.get("기준선(참고)") or {}
        if ref.get("n"):
            line += f" vs 기준선 {ref.get('rate')}% (n={ref.get('n')})"
        return line
    explore = entry.get("탐색표본(참고)") or {}
    if explore.get("n"):
        return (f"판정 표본 0건 — 탐색 표본에서는 "
                f"{explore.get('rate')}% (n={explore.get('n')})")
    return "표본이 아직 없습니다"


def adopted_names(verdict: dict | None) -> list[str]:
    if not verdict or "가설" not in verdict:
        return []
    return [
        HYPOTHESIS_LABELS.get(name, name)
        for name, entry in verdict["가설"].items()
        if entry.get("판정") == "채택"
    ]


def adoption_caveats(verdict: dict | None) -> list[dict]:
    """채택된 가설마다 **얼마나 아슬아슬한지**를 함께 적습니다 (160차).

    왜 필요한가:
      2026-09-01 런에서 v3 들어 **처음으로** 가설 하나가 채택 기준을
      넘었습니다(H9). 그런데 넘긴 폭이 **0.1%p** 였고, 앞/뒤 시간 분할을
      보니 앞시기에는 우위가 없었습니다(8.3% — 기준선과 구간이 겹침).
      헌법 4원칙이 정확히 이것을 경고합니다: "어떤 측정이든 앞/뒤 시간
      분할을 함께 보고하고, **강세장 구간만의 우위는 믿지 않는다.**"

      판정 자체는 건드리지 않습니다 — 문턱은 데이터를 보기 전에 등록한
      것이고, 결과를 보고 규칙을 고치면 사전 등록이 무의미해집니다
      (헌법 8번). 대신 **화면이 함께 말하게** 합니다. 채택이라는 글자만
      띄우면 주인은 그것을 매수 근거로 읽습니다.

    지어내지 않습니다 — 판정 파일에 이미 있는 수를 옮겨 적고, 빼기만
    합니다. 없는 칸은 None 으로 둡니다.
    """
    out: list[dict] = []
    for name, entry in ((verdict or {}).get("가설") or {}).items():
        if entry.get("판정") != "채택":
            continue
        판정칸 = entry.get("신규(판정)") or {}
        신호 = 판정칸.get("신호") or {}
        기준 = 판정칸.get("기준선") or {}
        신호구간 = 신호.get("ci") or [None, None]
        기준구간 = 기준.get("ci") or [None, None]
        여유 = (round(신호구간[0] - 기준구간[1], 1)
              if 신호구간[0] is not None and 기준구간[1] is not None else None)
        앞 = entry.get("신규_앞시기") or {}
        뒤 = entry.get("신규_뒤시기") or {}
        전체 = (entry.get("전체(참고)") or {}).get("판정")

        주의: list[str] = []
        # ① 앞시기 구간이 기준선 구간과 겹치면 "뒤시기에만 우위"입니다.
        앞구간 = 앞.get("ci") or [None, None]
        if (앞구간[0] is not None and 기준구간[1] is not None
                and 앞구간[0] <= 기준구간[1]):
            주의.append("앞시기에는 우위 없음 — 뒤시기(강세장)에만 나타남")
        # ② 같은 자로 잰 전체 표본이 채택이 아니면 그것도 사실입니다.
        if 전체 and 전체 != "채택":
            주의.append(f"전체 표본으로는 {전체}")
        out.append({
            "이름": name,
            "라벨": HYPOTHESIS_LABELS.get(name, name),
            "여유": 여유,                      # 신호 하한 − 기준선 상한 (%p)
            "신호n": 신호.get("n"),
            "신호율": 신호.get("rate"),
            "기준선율": 기준.get("rate"),
            "앞시기율": 앞.get("rate"), "앞시기n": 앞.get("n"),
            "뒤시기율": 뒤.get("rate"), "뒤시기n": 뒤.get("n"),
            "전체판정": 전체,
            "주의": 주의,
        })
    return out


def adoption_caveat_line(caveats: list[dict]) -> str:
    """채택 신호 옆에 한 줄로 붙일 정직화 문구 (없으면 빈 글자)."""
    조각 = []
    for c in caveats:
        말 = []
        if c.get("여유") is not None:
            말.append(f"여유 {c['여유']:+.1f}%p")
        if c.get("앞시기율") is not None and c.get("뒤시기율") is not None:
            말.append(f"앞 {c['앞시기율']}% → 뒤 {c['뒤시기율']}%")
        말 += c.get("주의") or []
        if 말:
            조각.append(f"{c['라벨']}: " + " · ".join(말))
    return " / ".join(조각)


def recent_ticker_rows(ds: dict, today: str | None = None) -> list[dict]:
    """최근 RECENT_DAYS 일 안에 발표한 종목의 사실 상태 (판단 아님)."""
    if today is None:
        today = ds["prices"][ds["benchmark"]]["dates"][-1]
    cutoff = (date.fromisoformat(today) - timedelta(days=RECENT_DAYS)).isoformat()
    yard_names = {"adj_eps": "조정 EPS", "adjusted_ebitda": "조정 EBITDA",
                  "gaap_eps": "GAAP EPS"}
    rows = []
    for ticker in ds["tickers"]:
        quarters = ds["quarters"].get(ticker) or []
        # 측정 잣대(12차 사다리). 미달 종목도 화면에는 사실을 보여 주되
        # 측정 제외임이 드러나게 조정 EPS 기준으로 표시합니다.
        yardstick = me.yardstick_of(quarters) or "adj_eps"
        states = me.earnings_states(quarters, field=yardstick)
        if not states:
            continue
        last = states[-1]
        if last["announced"] < cutoff:
            continue
        if not last["decidable"]:
            status = "판단 불가 (이력 부족)"
        elif last["newhigh_streak"] == 1:
            status = "신고점 첫 돌파"
        elif last["new_high"]:
            status = f"신고점 (연속 {last['newhigh_streak']}번째)"
        else:
            status = "신고점 아님"
        rows.append(
            {
                "종목": ticker,
                "섹터": cfg.SECTORS.get(ticker, "미분류"),
                "잣대": yard_names[yardstick],
                "발표일": last["announced"],
                "TTM 조정EPS": (
                    round(last["ttm"], 2) if last["ttm"] is not None else None
                ),
                "상태": status,
            }
        )
    rows.sort(key=lambda r: r["발표일"], reverse=True)
    return rows


def gauge_now(ds: dict) -> dict:
    """현재 게이지 사실: 값 · H5b 상태 (판단 불가 포함)."""
    series = me.gauge_series(ds)
    today = ds["prices"][ds["benchmark"]]["dates"][-1]
    value = me.gauge_at(series, today)
    h5b = me.gauge_h5b_on(series, today)
    return {"value": value, "h5b": h5b, "asof": today}


def sector_gauge_rows(ds: dict) -> list[dict]:
    """섹터별 실적 폭 (관찰용 — 사전 등록된 가설이 아님, 판정에 안 씀).

    각 섹터에 대해 시장 게이지와 같은 계산을 그 섹터 종목만으로 하고,
    "평소(자기 이력 중앙값) 대비"도 같은 방법으로 봅니다.
    게이지가 높은 섹터부터 정렬합니다 — 신기록이 무리 지어 나오는
    무리(주도 후보)가 위로 옵니다.
    """
    today = ds["prices"][ds["benchmark"]]["dates"][-1]
    by_sector: dict[str, list[str]] = {}
    for ticker in ds["tickers"]:
        by_sector.setdefault(cfg.SECTORS.get(ticker, "미분류"), []).append(ticker)

    rows = []
    for sector, members in by_sector.items():
        # 섹터 게이지는 관찰용 — 종목수를 함께 보여 주므로 최소치를
        # 걸지 않습니다 (100차. 시장 전체 게이지에만 걸립니다)
        series = me.gauge_series(ds, tickers=members, min_tickers=1)
        value = me.gauge_at(series, today)
        above = me.gauge_h5b_on(series, today)
        rows.append(
            {
                "섹터": sector,
                "종목수": len(members),
                "게이지": value,
                "평소대비": {True: "평소보다 높음", False: "평소 이하",
                             None: "판단 불가"}[above],
            }
        )
    rows.sort(key=lambda r: (r["게이지"] is not None, r["게이지"] or 0), reverse=True)
    return rows


GUID_FRESH_DAYS = 140      # 가이던스 신선도 — 게이지(FRESH_DAYS)와 같은 기준
SPREAD_MIN_N = 3           # 이보다 표본이 적은 섹터는 "표본 부족"으로 표시


def forward_spread_rows(ds: dict, ledger: dict | None,
                        today: str | None = None,
                        metric: str = "eps") -> list[dict]:
    """종목별 전망 스프레드 = (가이던스 중간값 − 컨센서스 평균) / |컨센서스|.

    관찰 전용 (헌법 제1조 개정 조건 — 판정·점수에 안 씀).
    짝짓기 규칙: 마지막 실적 발표에서 나온 다음 분기 가이던스 ↔ 컨센서스
    "이번 분기(0q)" 추정 (둘 다 "다음에 발표될 분기"를 가리킴 — 발표
    주기 정렬 가정, 27차 한계에 기록). 조건:
      · 가이던스 발표가 GUID_FRESH_DAYS 안 (철 지난 가이던스 제외)
      · 컨센서스는 원장 마지막 스냅샷 (원장이 비면 그 종목은 제외)
    """
    if today is None:
        today = ds["prices"][ds["benchmark"]]["dates"][-1]
    tickers_ledger = (ledger or {}).get("tickers", {})
    rows = []
    guid_key = "guid_eps_mid" if metric == "eps" else "guid_rev_mid"
    cons_key = "avg" if metric == "eps" else "rev_avg"
    for ticker in ds["tickers"]:
        quarters = ds["quarters"].get(ticker) or []
        guided = [r for r in quarters
                  if r.get(guid_key) is not None and r.get("announced_date")]
        if not guided:
            continue
        last = max(guided, key=lambda r: r["announced_date"])
        age = (date.fromisoformat(today)
               - date.fromisoformat(last["announced_date"])).days
        if age > GUID_FRESH_DAYS or age < 0:
            continue
        entries = tickers_ledger.get(ticker) or []
        if not entries:
            continue
        cons = (entries[-1].get("rows") or {}).get("0q") or {}
        avg = cons.get(cons_key)
        if not avg:
            continue                      # 컨센서스 없음/0 — 제외
        spread = (last[guid_key] - avg) / abs(avg) * 100.0
        rows.append({
            "ticker": ticker,
            "섹터": cfg.SECTORS.get(ticker, "미분류"),
            "테마": cfg.theme_of(ticker),
            "가이던스": last[guid_key],
            "컨센서스": avg,
            "스프레드%": round(spread, 1),
            "발표일": last["announced_date"],
            "컨센서스일": entries[-1].get("as_of"),
        })
    return rows


def sector_spread_rows(ds: dict, ledger: dict | None,
                       today: str | None = None,
                       group_key: str = "섹터",
                       metric: str = "eps") -> list[dict]:
    """섹터(또는 테마)별 전망 스프레드 중앙값 — 주도 섹터 관찰용.

    스프레드가 큰 순서로 정렬하되, 표본이 SPREAD_MIN_N 미만인 그룹은
    "표본 부족"을 함께 표시합니다 (숨기지 않고 정직하게).
    """
    per_ticker = forward_spread_rows(ds, ledger, today, metric=metric)
    groups: dict[str, list[dict]] = {}
    for row in per_ticker:
        groups.setdefault(row[group_key], []).append(row)
    out = []
    for name, members in groups.items():
        spreads = sorted(r["스프레드%"] for r in members)
        mid = spreads[len(spreads) // 2] if len(spreads) % 2 else round(
            (spreads[len(spreads) // 2 - 1] + spreads[len(spreads) // 2]) / 2, 1)
        out.append({
            group_key: name,
            "종목수": len(members),
            "스프레드중앙%": mid,
            "표본": "표본 부족" if len(members) < SPREAD_MIN_N else "충분",
            "종목": ", ".join(r["ticker"] for r in members),
        })
    out.sort(key=lambda r: r["스프레드중앙%"], reverse=True)
    return out


def hypothesis_note(verdict: dict | None, name: str) -> str:
    """정직화 문구: 그 상태의 과거 실측 + 판정 상태 (verdict 에서 복사)."""
    if not verdict or name not in (verdict.get("가설") or {}):
        return "과거 실측 없음 (판정 대기)"
    entry = verdict["가설"][name]
    judged = entry.get("신규(판정)") or {}
    signal = judged.get("신호") or {}
    base = judged.get("기준선") or {}
    if not signal.get("n"):
        return f"판정: {entry.get('판정', '?')}"
    return (
        f"이 상태의 과거 폭등률 {signal.get('rate')}% (n={signal.get('n')}) · "
        f"기준선 {base.get('rate')}% · 판정: {entry.get('판정', '?')}"
    )


# ---------------------------------------------------------------------------
# 화면 (streamlit)
# ---------------------------------------------------------------------------
# --- 정배열 폭 구간별 과거 실측 (34차 탐색 — 등록 근거 아님, 표시용) ---
# 숫자는 34차 실행 출력에서 복사. 화면에 "탐색값"임을 반드시 함께 적습니다.
BREADTH_ZONES = [
    (60.0, 101.0, "정배열 완성 구간", 7.7,
     "무리 전체가 이미 정배열 — 과거 1년 폭등률이 가장 낮았습니다"),
    (40.0, 60.0, "쌓이는 중 (역사적 최적)", 33.3,
     "절반쯤 정배열 — 과거 1년 폭등률이 가장 높았던 구간"),
    (25.0, 40.0, "초기", 25.8, "이제 모이기 시작하는 단계"),
    (0.0, 25.0, "약함", 27.0, "정배열 종목이 드문 상태"),
]
# 34차에 실측한 기준선 (모든 섹터·주, n=1,872). **예비값입니다.**
# ⚠️ 이 숫자를 화면에 그대로 쓰면 안 됩니다 (101차). 10년 표본에서 다시
#    재니 31.6%(n=5,004) 였는데 화면은 계속 26.8% 이라고 말하고 있었습니다.
#    breadth_baseline() 으로 판정 파일에서 읽고, 없을 때만 이 값을 씁니다.
BREADTH_BASELINE = 26.8


def breadth_baseline(verdict: dict | None) -> float:
    """정배열 폭 모델의 기준선(%) — 판정 파일에서 그때그때 읽습니다 (101차).

    H11 의 기준선은 "모든 섹터·주"라 H11·H11b 가 같은 값을 씁니다.
    판정 파일에 없으면 문서화된 예비값(BREADTH_BASELINE)을 돌려줍니다 —
    지어내지 않고, 어디서 온 값인지 주석에 남깁니다.
    """
    entry = ((verdict or {}).get("가설") or {}).get("H11_섹터정배열폭_60") or {}
    rate = ((entry.get("신규(판정)") or {}).get("기준선") or {}).get("rate")
    return float(rate) if rate is not None else BREADTH_BASELINE


def surprise_sector_rows(surprise: dict | None, quarters: int = 2) -> list[dict]:
    """섹터별 실적 서프라이즈 중앙값 (야후 보관 기록 — 관찰용).

    최근 quarters 개 분기만 씁니다. 값이 없으면 빈 목록.
    """
    from statistics import median

    entries = (surprise or {}).get("tickers") or {}
    groups: dict[str, list[float]] = {}
    for ticker, rows in entries.items():
        sector = cfg.SECTORS.get(ticker, "미분류")
        for row in rows[-quarters:]:
            if row.get("surprise_pct") is not None:
                groups.setdefault(sector, []).append(row["surprise_pct"])
    out = [{"섹터": name, "건수": len(values),
            "중앙%": round(median(values), 1)}
           for name, values in groups.items()]
    out.sort(key=lambda r: r["중앙%"], reverse=True)
    return out


ZONE_COLORS = {
    "쌓이는 중 (역사적 최적)": "#2E9E5B",   # 초록 — 과거 가장 좋았던 구간
    "초기": "#3B82C4",                      # 파랑 — 모이기 시작
    "약함": "#8A8F98",                      # 회색 — 아직 아님
    "정배열 완성 구간": "#C4553B",          # 주황빨강 — 과거 가장 나빴던 구간
    "판단 불가": "#4A4F58",
}


def sorted_bar_chart(labels, values, value_title, colors=None,
                     positive_negative=False):
    """값 순으로 정렬된 가로 막대 그래프 (모바일 412px 기준).

    st.bar_chart 는 축을 가나다순으로 다시 정렬해 버려 '한눈에'가 깨집니다.
    그래서 알테어로 정렬 순서를 직접 고정합니다.
    colors: 라벨별 색 (없으면 값의 부호로 색을 나눔)
    """
    import altair as alt
    import pandas as pd

    frame = pd.DataFrame({"이름": list(labels), "값": list(values)})
    if colors:
        frame["색"] = [colors.get(label, "#3B82C4") for label in labels]
        color = alt.Color("색:N", scale=None, legend=None)
    elif positive_negative:
        frame["색"] = ["#2E9E5B" if v >= 0 else "#C4553B" for v in values]
        color = alt.Color("색:N", scale=None, legend=None)
    else:
        color = alt.value("#3B82C4")
    return (
        alt.Chart(frame)
        .mark_bar(cornerRadiusEnd=3)
        .encode(
            y=alt.Y("이름:N", sort=list(labels), title=None,
                    axis=alt.Axis(labelLimit=120)),
            x=alt.X("값:Q", title=value_title),
            color=color,
            tooltip=["이름", "값"],
        )
        .properties(height=max(200, 30 * len(frame)))
    )


def breadth_zone(breadth: float | None) -> dict:
    """폭 값 → 구간 이름·과거 실측 폭등률 (34차 탐색값)."""
    if breadth is None:
        return {"zone": "판단 불가", "rate": None, "note": "이력이 부족합니다"}
    for low, high, name, rate, note in BREADTH_ZONES:
        if low <= breadth < high:
            return {"zone": name, "rate": rate, "note": note}
    return {"zone": "판단 불가", "rate": None, "note": ""}


# ---------------------------------------------------------------------------
# 무거운 계산 캐시 (102차) — 화면이 57초 걸리던 문제
# ---------------------------------------------------------------------------
# 10년 확장 뒤 마지막 구역까지 그려지는 데 57~59초가 걸렸습니다. 짐작하지
# 않고 하나씩 재 봤습니다 (412px 실측):
#
#   sm.confirmation_rows   22.7초   ← 1위 (전체의 40%)
#   sm.current_breadth      5.1초
#   sm.cycle_series ×2      5.6초
#   dataset.build           1.4초
#                          ------
#                          약 35초 + 스트림릿 그리기
#
# 이 값들은 **로봇이 새 데이터를 커밋할 때만** 바뀝니다. 같은 스냅샷을
# 화면 새로 고칠 때마다 다시 계산할 이유가 없습니다.
#
# ⚠️ 캐시는 **속도만** 바꿉니다. 같은 입력이면 값이 똑같아야 하고, 그것을
#    시험으로 못박습니다 — 캐시가 값을 바꾸면 그건 고쳐야 할 결함입니다.
#    (측정 코드는 한 줄도 안 건드렸습니다. 계산 결과를 다시 쓰기만 합니다.)
def _cached(fn):
    """스트림릿이 있으면 결과를 담아 두고, 없으면 그냥 돌립니다.

    시험은 스트림릿 없이 app 을 불러오므로 그대로 통과해야 합니다.
    """
    try:
        import streamlit as st
    except Exception:
        return fn
    try:
        return st.cache_data(show_spinner=False)(fn)
    except Exception:
        return fn


def snapshot_key(snapshot: dict | None) -> str:
    """이 스냅샷을 가리키는 짧은 이름표 — 캐시를 언제 버릴지 정합니다.

    로봇이 새로 커밋하면 saved_at 이 바뀌므로 캐시가 저절로 갈립니다.
    saved_at 이 없으면 종목 수라도 씁니다 (없는 값을 지어내지 않습니다).
    """
    if not snapshot:
        return "없음"
    return str(snapshot.get("saved_at") or f"종목{len(snapshot.get('tickers') or [])}")


# 아래 함수들의 앞머리 `_ds` 는 **밑줄로 시작**합니다 — 스트림릿은 밑줄로
# 시작하는 인자를 캐시 열쇠에서 뺍니다. 큰 표를 통째로 해시하면 그 자체가
# 몇 초씩 걸리기 때문입니다. 대신 `키`(스냅샷 이름표)로 갈아 끼웁니다.
@_cached
def cached_dataset(_snapshot: dict, 키: str) -> dict:
    # 액면분할 환산 재료 (112차) — vendor.json 은 snapshot 과 같은 커밋으로
    # 오므로 캐시 열쇠(snapshot saved_at)가 그대로 유효합니다.
    return dataset.build(_snapshot, splits=dataset.load_splits())


@_cached
def cached_confirmation_rows(_ds: dict, 키: str) -> list[dict]:
    return sm.confirmation_rows(_ds)


@_cached
def cached_current_breadth(_ds: dict, 키: str) -> list[dict]:
    return sm.current_breadth(_ds)


@_cached
def cached_market_breadth(_ds: dict, 키: str) -> list[tuple[str, float]]:
    return sm.market_breadth_series(_ds)


@_cached
def cached_completion_events(_ds: dict, 키: str) -> list[dict]:
    return sm.completion_events(_ds)


@_cached
def cached_aligned_now(_ds: dict, 키: str) -> list[dict]:
    return aligned_now_rows(_ds)


@_cached
def cached_cycle_series(_ds: dict, members: tuple, base_day: str,
                        since: str, 키: str) -> list[dict]:
    return sm.cycle_series(_ds, list(members), base_day, since=since)

def breadth_verdict_lines(verdict: dict) -> list[str]:
    """정배열 폭 모델의 판정 상태 줄 — **판정 파일에서 그때그때 읽습니다** (101차).

    ⚠️ 왜 함수로 뺐나: 예전에는 이 문장이 화면 코드 안에 **글자로 박혀**
    있었습니다 — "H11 실측 9.4% vs 기준선 26.8%. 정배열이 다 찬 뒤 사는
    것은 **오히려 불리**했습니다." 10년 표본으로 다시 재니 **31.6% vs
    31.6% (n=187)** 이라 그 문장은 거짓말이 돼 있었습니다.

    화면이 옛 숫자를 사실처럼 말하는 것은 정직화 원칙 위반이고, 박힌
    글자는 데이터가 바뀌어도 아무도 모르게 남습니다. 그래서 판정 파일을
    읽게 하고, 시험으로 못박습니다.
    """
    lines = ["**이 모델의 판정 상태 (정직화)**  "]
    for name, label in (
        ("H11_섹터정배열폭_60", "H11(폭 60% 첫 돌파)"),
        ("H11b_섹터정배열폭_80", "H11b(폭 80% 첫 돌파)"),
    ):
        entry = (verdict.get("가설") or {}).get(name) or {}
        judged = entry.get("신규(판정)") or {}
        line = signal_summary(entry, judged.get("신호") or {},
                              judged.get("기준선") or {})
        lines.append(f"· **{label} — {entry.get('판정', '판정 없음')}**: {line}  ")
    lines.append(
        "· **H12(폭 40~59% 진입) — 판정 대기**: 탐색에서 가장 좋았으나 "
        "탐색값은 근거가 못 되어, 2026-08-14 이후 새 신호로만 판정합니다 (34차).  "
    )
    lines.append("· 따라서 위 순위는 **아직 매수 근거가 아닌 관찰**입니다.")
    return lines

def adoption_distance_lines(verdict: dict | None) -> list[str]:
    """**채택까지 얼마나 남았나** — 등록된 기준의 산수 (139차).

    주인이 물었던 "언제부터 포착할 수 있나"에 대한 정직한 답입니다.
    채택 기준은 "신호 윌슨 하한 > 기준선 상한"이고, 하한은 표본이 늘면
    올라갑니다. 그러니 **지금 폭등률이 유지된다면** 몇 개가 더 필요한지
    그냥 계산됩니다. 새 기준이 아니라 있는 기준의 산수입니다.

    이 계산이 드러낸 것: 표본이 있는 가설 대부분은 폭등률이 **기준선
    상한보다 낮아**, 표본을 아무리 모아도 못 넘습니다. 기다리면 되는
    문제가 아니라는 뜻이고, 그 사실은 화면에 있어야 합니다.
    """
    if not verdict:
        return ["아직 판정 파일이 없습니다 — 없는 것을 지어내지 않습니다."]
    행들 = judge.adoption_distance(verdict)
    있음 = [r for r in 행들 if r.get("n")]
    lines: list[str] = []
    if not 있음:
        # ⚠️ 예전에는 여기서 그냥 돌아갔습니다. 그러면 **모든 가설의
        #    표본이 0인 때** — 이 안내가 가장 필요한 때 — 아래의
        #    "언제부터 생기나"가 통째로 사라집니다 (140차에 새 시험이
        #    빨간 불로 알려 줬습니다).
        lines.append("아직 표본이 쌓인 가설이 없습니다 "
                     "(사전 등록일 뒤 새 신호만 셉니다).")
    else:
        넘음 = [r for r in 있음 if "이미" in r["상태"]]
        가능 = [r for r in 있음 if (r.get("필요표본") or 0) > 0]
        막힘 = [r for r in 있음
              if r.get("필요표본") is None and "이미" not in r["상태"]]
        lines += [
            f"**채택까지 얼마나 남았나** — 표본이 쌓인 가설 {len(있음)}개  ",
            f"· 이미 기준을 넘음: **{len(넘음)}개**  ",
            f"· 표본만 더 모으면 넘을 수 있음: **{len(가능)}개**  ",
            f"· 지금 폭등률로는 표본을 더 모아도 못 넘음: **{len(막힘)}개**  ",
        ]
        for r in sorted(가능, key=lambda r: r["필요표본"]):
            lines.append(
                f"  · {r['가설']}: 폭등률 {r['폭등률']}% vs 기준선 상한 "
                f"{r['기준선상한']}% → {r['상태']}  ")
        lines.append(
            "※ '못 넘음'은 **영원히**가 아니라 **지금 실측된 폭등률이 "
            "그대로라면** 이라는 뜻입니다. 표본이 쌓이면 폭등률 자체가 "
            "움직일 수 있습니다.")

    # 아직 표본이 0인 가설 (140차) — 언제부터 표본이 생길 수 있나.
    # 사전 등록 규율상 등록일 뒤의 새 신호만 세고, 표적을 재려면 창이
    # 끝나야 합니다. 그러니 첫 표본의 가장 이른 날은 산수로 정해집니다.
    바닥 = judge.first_verdict_floor(verdict)
    if 바닥:
        lines.append("")
        lines.append(f"**아직 표본이 0인 가설 {len(바닥)}개 — 언제부터 생기나**  ")
        for r in 바닥:
            lines.append(f"  · {r['가설']}: {r['설명']}  ")
        lines.append(
            "※ 이것은 예측이 아니라 **규칙에서 바로 나오는 하한선**입니다 — "
            "등록일 뒤의 새 신호만 세고(사전 등록), 표적을 재려면 창이 "
            "끝나야 하기 때문입니다. 이보다 이르게는 표본이 생길 수 없습니다.")
    return lines


def health_lines(검진: dict | None) -> list[str]:
    """수집물 건강검진(108차) 요약 줄 — 로봇 기록을 그대로 읽어 만듭니다.

    화면 없이도 시험할 수 있도록 함수로 뺐습니다 (새 코드 경로는 실행을
    증명한다 — 실행 불가능한 위치의 구제 코드가 루멘텀 전망을 무너뜨린
    사건). 값을 만들지 않습니다 — 검진이 없으면 없다고 말합니다.
    """
    if not 검진:
        return ["아직 없음 — 건강검진 장치(108차)가 반영된 다음 수집부터 "
                "실립니다. 없는 것을 지어내지 않습니다."]
    lines: list[str] = []
    채움 = 검진.get("채움률") or {}
    줄 = []
    행 = 채움.get("행")
    if 행 is not None:
        줄.append(f"행 {행:,}개")
    for 칸, 이름 in (("revenue", "매출"), ("adj_eps", "조정EPS"),
                   ("gaap_eps", "GAAP EPS"), ("revenue_xbrl", "매출XBRL")):
        r = 채움.get(칸) or {}
        if r.get("비율") is not None:
            줄.append(f"{이름} {r['비율']}%")
    lines.append("채움률: " + " · ".join(줄))
    이상 = 검진.get("이상값") or {}
    이상줄 = []
    for 칸, 이름 in (("revenue", "매출"), ("op_income", "영업이익")):
        r = 이상.get(칸) or {}
        조각 = f"{이름} {r.get('건수', '?')}건"
        # 모양별 쪼갬 (136차) — 총 건수만 적었더니 무엇이 망가졌는지
        # 알 수 없었습니다. 쪼개 보고서야 영업이익 쪽 정체가 **단위
        # 혼선**(한 종목 안에서 달러와 천/백만이 섞임)임이 드러났습니다.
        if r.get("종목수"):
            조각 += f"({r['종목수']}종목)"
        이상줄.append(조각)
    lines.append("자기 이력 1,000배 밖 이상값: " + " · ".join(이상줄)
                 + " (버리지 않고 세기만 합니다)")
    모양 = (이상.get("op_income") or {}).get("모양별") or {}
    if 모양:
        lines.append("  └ 영업이익 모양별: "
                     + " · ".join(f"{k} {v}칸" for k, v in
                                  sorted(모양.items(), key=lambda x: -x[1]))
                     + " — 대부분 한 종목 안에서 달러와 천·백만 단위가 "
                       "섞인 것입니다. 그 공시 원문을 부탁 목록에 담습니다.")
    어제 = 검진.get("어제 대비")
    if 어제 is None:
        lines.append("어제 대비: 없음 — 어제 수집물이 없어 맞대지 못했습니다.")
    else:
        lines.append(f"어제 대비: 맞대본 분기 {어제.get('맞대본 분기', 0):,}개 중 "
                     f"바뀐 칸 {어제.get('바뀐 칸', 0):,}개 · "
                     f"새 분기 {어제.get('새 분기', 0):,}개 · "
                     f"사라진 분기 {어제.get('사라진 분기', 0):,}개")
    return lines


MODERN_CSS = """
<style>
#MainMenu, footer {visibility: hidden;}
.block-container {padding-top: 1.1rem; padding-bottom: 4rem; max-width: 46rem;}
h1 {font-size: 1.4rem !important; letter-spacing: -0.4px; margin-bottom: .2rem;}
h2 {font-size: 1.12rem !important; border-left: 4px solid #22c55e;
    padding-left: 10px; margin-top: 1.4rem !important;}
h3 {font-size: 1.0rem !important;}
p, li {line-height: 1.55;}
div[data-testid="stExpander"] {border-radius: 12px;
    border: 1px solid rgba(128,128,128,.25); margin-bottom: .35rem;}
div[data-testid="stExpander"] summary {font-size: .92rem;}
[data-testid="stAlert"] {border-radius: 14px;}
[data-testid="stCaptionContainer"] {opacity: .8;}
.mv-grid {display: grid; grid-template-columns: 1fr 1fr; gap: 8px;
    margin: .3rem 0 .6rem 0;}
.mv-card {border-radius: 14px; padding: 10px 12px;
    background: rgba(128,128,128,.10);
    border: 1px solid rgba(128,128,128,.18);}
.mv-label {font-size: .72rem; opacity: .7; margin-bottom: 2px;}
.mv-value {font-size: 1.02rem; font-weight: 700; line-height: 1.3;}
.mv-sub {font-size: .74rem; opacity: .75; margin-top: 2px;}
.mv-green {color: #22c55e;} .mv-red {color: #ef4444;}
.mv-amber {color: #f59e0b;}
.mv-badge {display: inline-block; font-size: .68rem; font-weight: 700;
    border-radius: 999px; padding: 1px 8px; margin-left: 6px;
    background: rgba(0,200,5,.16); color: #00C805;}
h2 {border-left-color: #00C805 !important;}
.mv-green {color: #00C805;}
button[data-baseweb="tab"] {font-size: .95rem; font-weight: 700;}
div[data-baseweb="tab-highlight"] {background-color: #00C805;}
.rh-hero {margin: .2rem 0 0 0;}
.rh-hero .v {font-size: 2.1rem; font-weight: 800; letter-spacing: -1px;
    line-height: 1.1;}
.rh-hero .s {font-size: .8rem; opacity: .75; margin-top: 2px;}
.rh-row {display: flex; align-items: center; justify-content: space-between;
    padding: 9px 2px; border-bottom: 1px solid rgba(128,128,128,.15);}
.rh-row .l .t {font-weight: 800; font-size: .98rem;}
.rh-row .l .d {font-size: .72rem; opacity: .7; margin-top: 1px;}
.rh-pill {min-width: 74px; text-align: center; font-weight: 700;
    font-size: .86rem; border-radius: 10px; padding: 6px 10px;
    background: #00C805; color: #fff;}
.rh-pill.off {background: rgba(128,128,128,.25); color: inherit;}
.rh-dot {display:inline-block; width:7px; height:7px; border-radius:50%;
    background:#00C805; margin-left:6px; vertical-align:middle;}
</style>
"""


def signal_row_html(r: dict) -> str:
    """이격도 30%+ 완성 종목 한 줄 — 로빈후드풍 행 (131차, 순수 함수).

    왼쪽 = 종목·묶음·테마·완성일, 오른쪽 = 이격도 초록 알약.
    새 완성(7일 안)은 종목 이름 옆 초록 점. 델타상승은 설명 줄에.
    """
    테마 = f" · {r['테마']}" if r.get("테마") else ""
    델타 = " · 델타상승" if r.get("델타") is True else ""
    점 = '<span class="rh-dot"></span>' if r.get("새완성") else ""
    이격 = "—" if r.get("이격도") is None else f"+{r['이격도']}%"
    return (
        '<div class="rh-row">'
        f'<div class="l"><div class="t">{r["종목"]}{점}</div>'
        f'<div class="d">{r["묶음"]}{테마} · 완성 {r["완성일"]}{델타}</div></div>'
        f'<div class="rh-pill">{이격}</div>'
        '</div>'
    )


def summary_cards_html(폭: float | None, fired: list[dict],
                       watch: list[dict], 새완성: list[dict],
                       adopted: list[str],
                       caveats: list[dict] | None = None) -> str:
    """오늘 요약을 증권앱풍 2×2 카드로 (130차 — 순수 함수, 시험 가능).

    없는 값은 만들지 않습니다 — 폭이 없으면 "판단 불가"라고 적습니다.
    """
    if 폭 is None:
        장세값, 장세색, 장세설명 = "판단 불가", "", "이력 부족"
    elif 폭 < 20:
        장세값, 장세색, 장세설명 = f"{폭:.0f}%", "mv-red", "약한 장세 — 종목 신호 신뢰 낮음"
    elif 폭 < 60:
        장세값, 장세색, 장세설명 = f"{폭:.0f}%", "mv-green", "살아 있는 장세"
    else:
        장세값, 장세색, 장세설명 = f"{폭:.0f}%", "mv-amber", "과열권 — 표본 희소"

    확인이름 = " · ".join(sorted({r["묶음"] for r in fired})) if fired else "없음"
    확인색 = "mv-green" if fired else ""

    몰림 = [g for g in watch if g.get("완성", 0) >= 2][:2]
    몰림값 = (" · ".join(f"{g['묶음']} {g['완성']}" for g in 몰림)
            if 몰림 else "없음")

    새이름 = " · ".join(r["종목"] for r in 새완성[:4]) if 새완성 else "없음"
    새색 = "mv-green" if 새완성 else ""

    채택값 = " · ".join(adopted) if adopted else "없음"
    # 160차 — 채택이 생기면 **얼마나 아슬아슬한지**를 같은 칸에 적습니다.
    #   "채택"이라는 글자만 띄우면 주인은 그것을 매수 근거로 읽습니다.
    채택설명 = (adoption_caveat_line(caveats or []) if adopted
            else "모든 표시는 관찰 — 매수 근거 아님")

    return (
        '<div class="mv-grid">'
        f'<div class="mv-card"><div class="mv-label">장세 · 시장 정배열 폭</div>'
        f'<div class="mv-value {장세색}">{장세값}</div>'
        f'<div class="mv-sub">{장세설명}</div></div>'
        f'<div class="mv-card"><div class="mv-label">주도 교체 확인 신호</div>'
        f'<div class="mv-value {확인색}">{확인이름}</div>'
        f'<div class="mv-sub">늦지만 강한 확인</div></div>'
        f'<div class="mv-card"><div class="mv-label">이번 주 새 완성</div>'
        f'<div class="mv-value {새색}">{새이름}</div>'
        f'<div class="mv-sub">7일 안 정배열 완성</div></div>'
        # ⚠️ 160차 수리: 예전에는 `채택설명` 을 만들어 놓고 **그리지
        #    않았습니다.** "모든 표시는 관찰 — 매수 근거 아님"이라는
        #    정직화 문구가 계기판에 한 번도 뜬 적이 없습니다(웹앱에는
        #    있었습니다). 만들어 놓고 안 쓰는 칸은 없는 것과 같습니다
        #    (150차-C 와 같은 병).
        f'<div class="mv-card"><div class="mv-label">채택된 신호 / 몰리는 묶음</div>'
        f'<div class="mv-value">{채택값}</div>'
        f'<div class="mv-sub">{채택설명}</div>'
        f'<div class="mv-sub">{몰림값}</div></div>'
        '</div>'
    )


def dedupe_confirmations(rows: list[dict]) -> list[dict]:
    """같은 묶음이 섹터·테마 두 분류표에서 **같은 수치**로 겹치면 한 장만.

    (129차 — 주인 지적: 헬스케어가 (섹터)·(테마) 두 장으로 떠서 헷갈림.
    비기술 업종은 테마가 업종명을 그대로 쓰므로 구성원이 같아 수치도
    같습니다.) 수치가 다르면 정보가 다른 것이므로 둘 다 남깁니다.
    """
    out: list[dict] = []
    seen: dict[str, dict] = {}
    for row in rows:
        key = row["묶음"]
        prev = seen.get(key)
        if prev and all(prev.get(k) == row.get(k) for k in
                        ("정배열폭", "델타폭", "3개월상대", "확인")):
            prev["종류"] = "섹터·테마 동일"
            continue
        copied = dict(row)
        seen[key] = copied
        out.append(copied)
    return out


def group_member_rows(묶음: str, 완성사건: list[dict],
                      기준일: str) -> list[dict]:
    """묶음(섹터명 또는 사이클 묶음명)의 구성 종목 + 최근 완성 상태.

    (129차 — "헬스케어 어떤 종목인지 어떻게 보나"의 답.) 이름을 업종
    분류표(SECTORS)와 사이클 분류표(GROUPS) 양쪽에서 찾아 합칩니다.
    최근 91일 완성·이격도 30%+ 신호가 있으면 태그를 답니다.
    """
    members = sorted({t for t in cfg.TICKERS
                      if cfg.SECTORS.get(t) == 묶음 or cfg.GROUPS.get(t) == 묶음})
    최근 = {r["종목"]: r for r in recent_completion_rows(완성사건, 기준일)}
    rows = []
    for t in members:
        r = 최근.get(t)
        rows.append({
            "종목": t,
            "완성": r["완성일"] if r else None,
            "이격도": r["이격도"] if r else None,
            "신호": bool(r and r["신호"]),
            "델타": (r or {}).get("델타"),
        })
    rows.sort(key=lambda x: (not x["신호"], x["완성"] is None, x["종목"]))
    return rows


def gauge_gap_rows(ds: dict) -> dict:
    """두 '정배열' 잣대가 지금 갈리는 종목 (150차-D).

    같은 앱 안에 "정배열"이라는 말이 두 번 나오는데 **뜻이 다릅니다** —
    일부러 그렇게 두었습니다. 한쪽에 맞추면 사전 등록이 무너집니다.

      · 폭  (33차 등록, `aligned_flags`)       — 이평선 배열 **+ 주봉 종가 > 4주선**
      · 완성(39차 등록, `aligned_flags_chart`) — 이평선 **배열만**

    그래서 차트로는 정배열인데 주가가 살짝 눌린 종목은 완성에는 세어지고
    폭에는 안 세어집니다. 코드 주석에는 39차부터 적혀 있었지만 **화면에는
    한 글자도 없어서**, 주인이 보기에 두 탭이 서로 다른 말을 하는 것처럼
    보였습니다(실측: 반도체장비 폭 0% ↔ 홈 화면 TER·ONTO 완성).

    값을 만들지 않습니다 — 지금 갈리는 종목을 세어 적을 뿐입니다.
    """
    갈린 = []
    for ticker in ds.get("tickers") or []:
        prices = (ds.get("prices") or {}).get(ticker)
        if not prices or not prices.get("dates"):
            continue
        폭깃발 = sm.aligned_flags(prices)
        완성깃발 = sm.aligned_flags_chart(prices)
        if not 폭깃발 or not 완성깃발:
            continue
        if 완성깃발[max(완성깃발)] and not 폭깃발[max(폭깃발)]:
            갈린.append(ticker)
    갈린.sort()
    return {"종목수": len(갈린), "종목들": 갈린}


def aligned_now_rows(ds: dict) -> list[dict]:
    """지금(마지막 주) 주봉 정배열을 **유지 중**인 종목 — 이격도순 (130차).

    관찰판은 "최근 91일 새 완성"만 보므로, 오래전에 완성해 계속 달리는
    종목(예: 한창때의 MU)은 안 보입니다. 이 목록이 그 빈틈을 메웁니다.
    지금 정배열이 아니면(조정 중) 여기에도 없습니다 — 사실 그대로.
    """
    today = ds["prices"][ds["benchmark"]]["dates"][-1]
    rows = []
    for ticker in ds["tickers"]:
        prices = ds["prices"].get(ticker)
        if not prices or not prices.get("dates"):
            continue
        flags = sm.aligned_flags_chart(prices)
        if not flags:
            continue
        last_week = max(flags)
        if not flags[last_week]:
            continue
        gap = sm.gap_over_52w(prices, today)
        rows.append({"종목": ticker,
                     "묶음": cfg.GROUPS.get(ticker, "미분류"),
                     "이격도": None if gap is None else round(gap, 1)})
    rows.sort(key=lambda r: (r["이격도"] is None,
                             -(r["이격도"] if r["이격도"] is not None else 0)))
    return rows


def combo_now_rows(ds: dict) -> list[dict]:
    """지금 H29 조합 상태인 종목 — 이격도순 (154차, 주인 지시).

    조합 = 주봉 정배열 유지 ∧ 최근 발표 델타 상승 ∧ 52주선 이격도 30%+
    ∧ 이격도가 4주 전보다 상승. 판정이 아니라 **사실의 나열**이며,
    화면은 이 조합의 과거 실측(152차 백테스트)과 H29 판정 상태를 함께
    적습니다(정직화). 재료 하나라도 못 재면 목록에 안 넣습니다 —
    없는 값은 만들지 않습니다.
    """
    rows = []
    for ticker in ds["tickers"]:
        prices = ds["prices"].get(ticker)
        if not prices or not prices.get("dates"):
            continue
        flags = sm.aligned_flags_chart(prices)
        if not flags:
            continue
        days = sorted(flags)
        last_week = days[-1]
        if not flags[last_week]:
            continue
        gap = sm.gap_over_52w(prices, last_week)
        if gap is None or gap < sm.H18_GAP_MIN:
            continue
        i = days.index(last_week)
        if i < sm.H29_GAP_BACK_WEEKS:
            continue
        gap_before = sm.gap_over_52w(prices, days[i - sm.H29_GAP_BACK_WEEKS])
        if gap_before is None or gap <= gap_before:
            continue
        # 기준일(마지막 주)까지 발표된 것만 봅니다 — 미래 금지
        series = [s for s in sm._delta_series(ds, ticker)
                  if s[0] <= last_week]
        if not series or series[-1][1] is not True:
            continue
        # 신선도 — 마지막 발표가 너무 오래됐으면 델타 판단 불가로 뺍니다
        # (게이지·주도섹터 장치와 같은 140일 기준, 새 문턱을 만들지 않음)
        from datetime import date as _d
        if (_d.fromisoformat(last_week)
                - _d.fromisoformat(series[-1][0])).days > sm.DELTA_FRESH_DAYS:
            continue
        rows.append({"종목": ticker,
                     "묶음": cfg.GROUPS.get(ticker, "미분류"),
                     "이격도": round(gap, 1),
                     "이격도_4주전": round(gap_before, 1)})
    rows.sort(key=lambda r: -r["이격도"])
    return rows


# ---------------------------------------------------------------------------
# 성장 속도표 (162차, 주인 지시 "저런 표를 앞으로의 기준으로 삼아")
# ---------------------------------------------------------------------------
# CRDO 사건(161차)에서 배운 것: "TTM 신고점(델타 상승)"은 계속 깨지는데
# **속도가 꺾인** 회사를 이 모델의 자(첫 돌파)는 구별하지 못한다. 그래서
# 종목마다 TTM 증가율과 그 직전 증가율을 나란히 놓고 "가속/감속"을
# 사실로만 적는다. 판정도 점수도 아니다 — 가설로 쓰려면 H번호로 사전
# 등록하고 새 데이터로 재야 한다(헌법 2·5조).
GROWTH_MIN_QUARTERS = 6      # TTM 두 개의 증가율을 견주려면 연속 6분기가 필요
# 169차 — 저기저(低基底) 표시. 직전 TTM 이 그 종목 **역대 최고 TTM** 의 25%
# 미만이면 증가율(%)이 기저 효과로 과장된다(MXL 실물: TTM 0.30 → 0.57 →
# 0.90 이 "+90% → +58% 감속"으로 읽혔지만 달러 증가액은 +0.27 → +0.33 으로
# 커지는 중이었다). 가속/감속 정의(H31·H32 등록 장치)는 바꾸지 않고,
# **사실 표시**만 덧붙인다 — 이 표시가 있는 줄은 비율을 그대로 믿지 않는다.
LOW_BASE_FRACTION = 0.25


def _pct_change(now: float | None, before: float | None) -> float | None:
    """전 값 대비 % 변화. 전 값이 0 이하이면 비율이 뜻을 잃으므로 없음."""
    if now is None or before is None or before <= 0:
        return None
    return round((now / before - 1.0) * 100.0, 1)


def _historic_max_ttm(rows: list[dict], yardstick: str) -> float | None:
    """그 종목의 **모든 연속 구간**에서 나온 TTM 중 최고값 (없으면 None)."""
    best = None
    for run in me.eps_runs(rows, yardstick):
        values = [r[yardstick] for r in run]
        for i in range(3, len(values)):
            s = sum(values[i - 3:i + 1])
            if best is None or s > best:
                best = s
    return best


def growth_row(ds: dict, ticker: str) -> dict | None:
    """한 종목의 성장 속도 한 줄. 잣대가 없거나 연속 분기가 모자라면 None.

    담는 것 — 전부 **마지막 발표 시점에 알 수 있는 값**입니다:
      · 잣대        adj_eps / adj_ebitda / gaap_eps (사다리 규칙, 섞지 않음)
      · 최근발표    마지막 발표일
      · TTM · 신고점  마지막 TTM 과 그것이 수집 이력 내 신고점인가(델타)
      · TTM증가     TTM 이 한 분기 전 TTM 보다 몇 % 늘었나
      · 직전TTM증가  그 한 분기 전에는 몇 % 늘었었나
      · 가속        TTM증가 > 직전TTM증가 (둘 다 있을 때만, 아니면 None)
      · 분기QoQ · 직전분기QoQ  잣대 값 자체의 전분기비
      · 매출QoQ · 직전매출QoQ  매출의 전분기비 (없으면 None)
      · 상대60     마지막 발표일까지 60거래일 SPY 대비 초과수익(%p)
    마지막 **연속 구간**만 씁니다 — 끊긴 분기를 이어붙이면 한 푼도 안
    늘었는데 가속이 나옵니다(사고 7).
    """
    rows = ds["quarters"].get(ticker) or []
    yardstick = me.yardstick_of(rows)
    if not yardstick:
        return None
    runs = me.eps_runs(rows, yardstick)
    if not runs:
        return None
    run = runs[-1]
    if len(run) < GROWTH_MIN_QUARTERS:
        return None
    values = [r[yardstick] for r in run]
    ttm = me.ttm_series(values)
    states = me.earnings_states(rows, yardstick)
    last = run[-1]
    new_high = None
    for s in states:
        if s["announced"] == last.get("announced_date"):
            new_high = s["new_high"] if s.get("decidable") else None
    revenue = [r.get("revenue") for r in run]
    prices, spy = ds["prices"].get(ticker), ds["prices"].get(ds["benchmark"])
    상대60 = None
    if prices and spy and last.get("announced_date"):
        상대60 = me.backward_excess(prices, spy, last["announced_date"])
    증가, 직전증가 = _pct_change(ttm[-1], ttm[-2]), _pct_change(ttm[-2], ttm[-3])
    # 171차 — "가림 불가"의 **이유**를 함께 적는다 (주인 질문 "가림불가는 뭐지").
    가속불가사유 = None
    if 증가 is None or 직전증가 is None:
        가속불가사유 = ("직전 TTM이 0 이하 — 비율(%)을 낼 수 없음" if ttm[-2] <= 0
                  else "두 분기 전 TTM이 0 이하 — 직전 증가율을 낼 수 없음")
    역대최고 = _historic_max_ttm(rows, yardstick)
    저기저 = (역대최고 is not None and 역대최고 > 0
           and ttm[-2] < LOW_BASE_FRACTION * 역대최고)
    return {
        "종목": ticker,
        "묶음": cfg.GROUPS.get(ticker, "미분류"),
        # 세분 묶음 (45차 ④ 감도 분석 — 판정 근거 아님). 기기 OEM 반도체
        # 안의 데이터센터 실리콘 5(NVDA·AMD·AVGO·MRVL·CRDO), 구독 SW 안의
        # 사용량 과금 5 만 갈라집니다. 확정 묶음(GROUPS)은 H19~H21 이 쓰는
        # 등록 장치라 사후에 바꾸지 않습니다 — 그래서 한 칸을 더 둡니다.
        "세분묶음": cfg.group_of(ticker, fine=True),
        "잣대": yardstick,
        "최근발표": last.get("announced_date"),
        "TTM": round(ttm[-1], 2),
        "신고점": new_high,
        "TTM증가": 증가,
        "직전TTM증가": 직전증가,
        "가속": (증가 > 직전증가) if (증가 is not None and 직전증가 is not None) else None,
        "가속불가사유": 가속불가사유,
        # 169차 — 기저 효과를 드러내는 사실 칸. 비율이 아니라 **금액** 증가와
        # 역대 최고 대비 위치를 함께 적는다.
        "TTM증가액": round(ttm[-1] - ttm[-2], 2),
        "직전TTM증가액": round(ttm[-2] - ttm[-3], 2),
        "역대최고TTM": None if 역대최고 is None else round(역대최고, 2),
        "저기저": bool(저기저),
        "분기QoQ": _pct_change(values[-1], values[-2]),
        "직전분기QoQ": _pct_change(values[-2], values[-3]),
        "매출QoQ": _pct_change(revenue[-1], revenue[-2]),
        "직전매출QoQ": _pct_change(revenue[-2], revenue[-3]),
        "상대60": None if 상대60 is None else round(상대60, 1),
    }


def growth_table_rows(ds: dict, 묶음: str | None = None,
                      기준일: str | None = None) -> list[dict]:
    """성장 속도표 — 가속인 종목 먼저, 그 안에서 TTM증가 큰 순.

    묶음을 주면 그 묶음만. 잴 수 없는 종목은 빠집니다(없는 값을 만들지
    않음). 마지막 발표가 기준일(주가 마지막 날)보다 140일 넘게 오래된
    종목도 뺍니다 — 게이지·조합 장치와 같은 신선도 기준(새 문턱 아님).
    실측: 그대로 두면 UTHR 2022 · PFE 2023 · ZBH 2022 같은 낡은 줄이
    "지금의 감속"으로 섞여 들어갔습니다. 화면은 이 표가 **판정이 아님**을
    함께 적어야 합니다.
    """
    if 기준일 is None:
        기준일 = (ds["prices"].get(ds["benchmark"]) or {}).get("dates", [None])[-1]
    out = []
    for ticker in ds["tickers"]:
        if 묶음 and cfg.GROUPS.get(ticker, "미분류") != 묶음:
            continue
        row = growth_row(ds, ticker)
        if not row:
            continue
        if 기준일 and row["최근발표"] and (
                date.fromisoformat(기준일) - date.fromisoformat(row["최근발표"])
        ).days > sm.DELTA_FRESH_DAYS:
            continue
        out.append(row)
    순서 = {True: 0, None: 1, False: 2}
    out.sort(key=lambda r: (순서[r["가속"]],
                            -(r["TTM증가"] if r["TTM증가"] is not None else -1e9)))
    return out


def growth_sector_rows(ds: dict, fine: bool = False) -> list[dict]:
    """묶음별로 가속·감속·판단불가 종목 수와 가속 비율.

    "델타가 늘어나는 섹터가 있는가"에 답하는 표. 비율의 분모는 가속/감속을
    **가릴 수 있었던** 종목만(판단불가 제외). 종목 5개 미만 묶음은 비율을
    적지 않습니다 — 표본이 작아 한 종목이 20%p 를 움직입니다.
    fine=True 면 세분 묶음(45차 ④ 감도 분석)으로 셉니다 — 화면 참고용.
    """
    열 = "세분묶음" if fine else "묶음"
    묶음별: dict[str, dict] = {}
    for row in growth_table_rows(ds):
        g = 묶음별.setdefault(row[열], {"묶음": row[열], "가속": 0,
                                       "감속": 0, "판단불가": 0})
        g["가속" if row["가속"] is True else
          "감속" if row["가속"] is False else "판단불가"] += 1
    out = []
    for g in 묶음별.values():
        n = g["가속"] + g["감속"]
        g["가속비율"] = round(g["가속"] / n * 100.0, 1) if n >= 5 else None
        out.append(g)
    out.sort(key=lambda g: (-(g["가속비율"] if g["가속비율"] is not None else -1),
                            g["묶음"]))
    return out


# ---------------------------------------------------------------------------
# 내 포트폴리오 (167차, 주인 지시) — 보유 종목과 교체 후보를 **같은 자**로
# ---------------------------------------------------------------------------
# 세션이 손으로 보유 종목을 다시 재던 일(163차·165차 보고)을 로봇이 매일
# 같은 함수로 하게 합니다. 값은 전부 이미 있는 장치에서 옮겨 옵니다:
#   성장표(growth_row) · 신고점 연속(earnings_states) · 가격 위치(52주 고가,
#   52주선) · 발표 뒤 SPY 대비 · 두 관찰판 등재(완성 30%+ / H29 조합) ·
#   회사 가이던스(스냅샷 칸) · 야후 컨센서스(원장, 분기 정렬이 확인될 때만).
# 판정도 점수도 아닙니다. "언제 바꿔야 하나"는 가설로 사전 등록해 새 표본으로
# 판정한 뒤에만 말합니다(헌법 2·3조). 못 재는 칸은 None 으로 둡니다.
CONSENSUS_ALIGN_TOL = 0.02     # 야후 year_ago 와 우리 4분기 전 값이 2% 안이면 같은 분기
NEXT_ANNOUNCE_TOL_DAYS = 25    # 작년 같은 분기 발표일로 다음 발표를 짐작할 때 허용 오차


def _price_position(prices: dict | None, 기준일: str) -> dict:
    """기준일 종가의 **52주 고가 대비 %** 와 **52주선(주봉 52주 평균) 대비 %**.

    이력이 52주 미만이면 둘 다 None — 판단 불가를 지어내지 않습니다.
    """
    out = {"고가대비": None, "52주선대비": None}
    if not prices or not prices.get("dates"):
        return out
    import bisect
    dates, closes = prices["dates"], prices["close"]
    i = bisect.bisect_right(dates, 기준일) - 1
    if i < 0:
        return out
    시작 = (date.fromisoformat(dates[i]) - timedelta(days=365)).isoformat()
    j = bisect.bisect_left(dates, 시작)
    if dates[j] > 시작 and j > 0:
        j -= 1
    if date.fromisoformat(dates[j]) > date.fromisoformat(시작) + timedelta(days=14):
        return out                                   # 1년치가 없으면 재지 않음
    창 = closes[j:i + 1]
    if 창 and max(창) > 0:
        out["고가대비"] = round((closes[i] / max(창) - 1.0) * 100.0, 1)
    weeks: list[float] = []
    current = None
    for day, close in zip(dates[:i + 1], closes[:i + 1]):
        iso = date.fromisoformat(day).isocalendar()
        key = (iso[0], iso[1])
        if key != current:
            weeks.append(close)
            current = key
        else:
            weeks[-1] = close
    if len(weeks) >= 52:
        ma = sum(weeks[-52:]) / 52.0
        if ma > 0:
            out["52주선대비"] = round((weeks[-1] / ma - 1.0) * 100.0, 1)
    return out


def _excess_since(ds: dict, ticker: str, 시작일: str | None, 기준일: str) -> float | None:
    """시작일 종가(그날 이전 마지막 거래일)부터 기준일까지 SPY 대비 초과수익(%p)."""
    if not 시작일:
        return None
    import bisect
    p, spy = ds["prices"].get(ticker), ds["prices"].get(ds["benchmark"])
    if not p or not spy:
        return None
    a = bisect.bisect_right(p["dates"], 시작일) - 1
    b = bisect.bisect_right(p["dates"], 기준일) - 1
    sa = bisect.bisect_right(spy["dates"], 시작일) - 1
    sb = bisect.bisect_right(spy["dates"], 기준일) - 1
    if min(a, sa) < 0 or b <= a or sb <= sa:
        return None
    if p["close"][a] <= 0 or spy["close"][sa] <= 0:
        return None
    return round((p["close"][b] / p["close"][a] - spy["close"][sb] / spy["close"][sa]) * 100.0, 1)


def _next_announce_guess(announced: list[str], last: str | None) -> str | None:
    """작년 같은 분기의 발표일 + 364일로 다음 발표를 짐작합니다.

    마지막 발표 + 91일 근처에 오는 작년 발표(±25일)가 있을 때만 값을 내고,
    없으면 None 입니다. 회사가 공식으로 정한 날짜가 아니라 **짐작**이며
    화면도 그렇게 적습니다.
    """
    if not last:
        return None
    목표 = date.fromisoformat(last) + timedelta(days=91)
    best = None
    for d in announced:
        try:
            후보 = date.fromisoformat(d) + timedelta(days=364)
        except ValueError:
            continue
        차 = abs((후보 - 목표).days)
        if 차 <= NEXT_ANNOUNCE_TOL_DAYS and (best is None or 차 < best[0]):
            best = (차, 후보)
    return best[1].isoformat() if best else None


def _next_quarter_consensus(entries: list[dict], rows: list[dict]) -> dict | None:
    """야후 원장의 마지막 스냅샷에서 **다음 분기** 칸을 고릅니다.

    야후의 0q 가 이미 발표된 분기를 가리키는 일이 실제로 있었습니다(165차
    LITE: 0q 매출 9.88억 = 막 발표한 10.06억). 그래서 칸의 year_ago 가 우리
    표의 **4분기 전 조정 EPS** 와 2% 안에서 같을 때만 그 칸을 다음 분기로
    믿습니다. 어느 칸도 맞지 않으면 None — 정렬을 확인 못한 값은 안 씁니다.
    """
    if not entries or len(rows) < 4:
        return None
    ref = rows[-4].get("adj_eps")
    if ref is None:
        ref = rows[-4].get("gaap_eps")
    if ref is None or ref == 0:
        return None
    snap = entries[-1]
    칸들 = snap.get("rows") or {}
    for key in ("0q", "+1q"):
        r = 칸들.get(key) or {}
        ya, avg = r.get("year_ago"), r.get("avg")
        if ya is None or avg is None:
            continue
        if abs(ya - ref) / abs(ref) <= CONSENSUS_ALIGN_TOL:
            최근 = rows[-1].get("adj_eps")
            return {"as_of": snap.get("as_of"), "칸": key,
                    "EPS": round(avg, 3), "매출": r.get("rev_avg"),
                    "분석가": r.get("analysts"),
                    "vs최근": _pct_change(avg, 최근) if 최근 else None}
    return None


GUIDANCE_ECHO_TOL = 0.005      # 가이던스가 이번 분기 실제 매출과 0.5% 안이면 "되읊음"으로 봄


def _guidance_vs_revenue(row: dict) -> float | None:
    """다음 분기 매출 가이던스 중앙값의 이번 분기 매출 대비 %.

    스냅샷의 가이던스 칸은 165차 수리 전 파서가 **지난 분기 실적을 전망으로
    읊은** 값이 남아 있을 수 있습니다(CLS·ZETA·ICHR 실물: 가이던스 = 실제
    매출). 그래서 실제 매출과 0.5% 안이면 없음으로 둡니다 — 진짜 가이던스가
    우연히 그 안에 든 경우(NVDA 18Q4 등 드묾)를 잃지만, "+0%"로 보이는
    틀린 값보다 없음이 안전합니다(헌법 1조). 로봇이 원문을 다시 읽으면
    칸 자체가 바로잡힙니다.
    """
    mid, rev = row.get("guid_rev_mid"), row.get("revenue")
    if mid is None or rev is None or rev <= 0:
        return None
    if abs(mid - rev) / rev < GUIDANCE_ECHO_TOL:
        return None
    return _pct_change(mid, rev)


def ticker_fact_row(ds: dict, ticker: str, 기준일: str,
                    ledger: dict | None = None,
                    완성신호: frozenset = frozenset(),
                    조합: dict | None = None) -> dict:
    """한 종목의 사실 한 줄 — 보유 종목과 교체 후보가 **같은 열**을 갖습니다."""
    rows = ds["quarters"].get(ticker) or []
    g = growth_row(ds, ticker) or {}
    yardstick = me.yardstick_of(rows)
    states = me.earnings_states(rows, yardstick) if yardstick else []
    last_state = states[-1] if states else {}
    최근발표 = rows[-1].get("announced_date") if rows else None
    last = rows[-1] if rows else {}
    pos = _price_position(ds["prices"].get(ticker), 기준일)
    발표일들 = [r["announced_date"] for r in rows if r.get("announced_date")]
    entries = ((ledger or {}).get("tickers") or {}).get(ticker) or []
    컨센 = _next_quarter_consensus(entries, rows)
    조합행 = (조합 or {}).get(ticker)
    return {
        "종목": ticker,
        "묶음": cfg.GROUPS.get(ticker, "미분류"),
        "테마": cfg.theme_of(ticker),
        "잣대": yardstick,
        "최근발표": 최근발표,
        "TTM": g.get("TTM"),
        "신고점": last_state.get("new_high") if last_state.get("decidable") else None,
        "연속": last_state.get("newhigh_streak"),
        "TTM증가": g.get("TTM증가"),
        "직전TTM증가": g.get("직전TTM증가"),
        "가속": g.get("가속"),
        # 가속을 못 가린 이유 — 성장표가 줄을 못 만든 경우(연속 6분기 미달 =
        # 잣대 칸에 구멍이 있거나 상장이 짧음)와 비율을 못 낸 경우를 가른다.
        "가속불가사유": (g.get("가속불가사유") if g else
                    ("잣대 없음 — 조정 EPS·EBITDA·GAAP 중 읽힌 것이 없음" if not yardstick
                     else "연속 6분기 부족 — 잣대 칸에 구멍이 있거나 이력이 짧음")),
        "TTM증가액": g.get("TTM증가액"),
        "직전TTM증가액": g.get("직전TTM증가액"),
        "역대최고TTM": g.get("역대최고TTM"),
        "저기저": g.get("저기저"),
        "분기QoQ": g.get("분기QoQ"),
        "매출QoQ": g.get("매출QoQ"),
        "고가대비": pos["고가대비"],
        "52주선대비": pos["52주선대비"],
        "발표후SPY": _excess_since(ds, ticker, 최근발표, 기준일),
        "다음발표_짐작": _next_announce_guess(발표일들, 최근발표),
        "가이드매출QoQ": _guidance_vs_revenue(last),
        "가이드EPS": last.get("guid_eps_mid"),
        "컨센": 컨센,
        "완성30": ticker in 완성신호,
        "H29": 조합행 is not None,
        "이격도": 조합행["이격도"] if 조합행 else None,
        # 168차 — H32 회피 신호(감속 ∧ 발표 전 60거래일 런업 ≥ 20%p) 해당
        # 여부를 **사실**로 적습니다. 판정은 등록일 뒤 새 표본으로만 하며
        # 화면은 판정 상태를 함께 보입니다. 가속 판단 불가면 None.
        "런업60": g.get("상대60"),
        "H32신호": (g.get("가속") is False and g.get("상대60") is not None
                  and g["상대60"] >= judge.H32_RUNUP_MIN) if g.get("가속") is not None else None,
        "H32b신호": (g.get("가속") is True and g.get("상대60") is not None
                   and g["상대60"] >= judge.H32_RUNUP_MIN) if g.get("가속") is not None else None,
    }


def portfolio_rows(ds: dict, 기준일: str | None = None, ledger: dict | None = None,
                   완성판: list[dict] | None = None, 조합판: list[dict] | None = None,
                   tickers: tuple | list | None = None) -> list[dict]:
    """보유 종목(config.PORTFOLIO) 사실표 — 목록 순서 그대로, 판정 없음.

    유니버스에 없거나 자료가 없는 종목은 빼고, 빠진 종목은 payload 의
    '빠진종목' 칸이 따로 말합니다(없는 것을 없다고).
    """
    if 기준일 is None:
        기준일 = (ds["prices"].get(ds["benchmark"]) or {}).get("dates", [None])[-1]
    if tickers is None:
        tickers = cfg.PORTFOLIO
    완성신호 = frozenset(r["종목"] for r in (완성판 or []) if r.get("신호"))
    조합 = {r["종목"]: r for r in (조합판 or [])}
    out = []
    for t in tickers:
        if t not in ds["quarters"] and t not in ds["prices"]:
            continue
        out.append(ticker_fact_row(ds, t, 기준일, ledger, 완성신호, 조합))
    return out


def swap_candidate_rows(ds: dict, 기준일: str | None = None, ledger: dict | None = None,
                        완성판: list[dict] | None = None, 조합판: list[dict] | None = None,
                        exclude: tuple | list | None = None) -> list[dict]:
    """교체 후보 = 두 관찰판(완성 30%+ ∧ H29 조합)에 **동시에** 오른 종목.

    보유 종목은 뺍니다(exclude, 기본 config.PORTFOLIO). 이격도 큰 순.
    후보라는 말은 "두 판에 같이 있다"는 사실이지 추천이 아닙니다 — 두 판의
    가설(H18·H29)은 등록 뒤 새 표본이 아직 없어 판정 불가입니다.
    """
    if 기준일 is None:
        기준일 = (ds["prices"].get(ds["benchmark"]) or {}).get("dates", [None])[-1]
    if exclude is None:
        exclude = cfg.PORTFOLIO
    완성신호 = frozenset(r["종목"] for r in (완성판 or []) if r.get("신호"))
    조합 = {r["종목"]: r for r in (조합판 or [])}
    후보 = sorted(set(조합) & 완성신호 - set(exclude),
                key=lambda t: -조합[t]["이격도"])
    return [ticker_fact_row(ds, t, 기준일, ledger, 완성신호, 조합) for t in 후보]


def portfolio_payload(ds: dict, ledger: dict | None,
                      완성판: list[dict], 조합판: list[dict]) -> dict:
    """웹앱 '내 포트폴리오' 탭의 내용 — 계기판·웹앱·원장이 같은 함수를 씁니다."""
    기준일 = ds["prices"][ds["benchmark"]]["dates"][-1]
    보유 = portfolio_rows(ds, 기준일, ledger, 완성판, 조합판)
    있는 = {r["종목"] for r in 보유}
    return {
        "기준일": 기준일,
        "보유": 보유,
        "빠진종목": [t for t in cfg.PORTFOLIO if t not in 있는],
        "교체후보": swap_candidate_rows(ds, 기준일, ledger, 완성판, 조합판),
        "기준": ("성장표와 같은 자(마지막 연속 6분기 · 가속 = 이번 TTM 증가율 > 직전) · "
               "고가대비 = 기준일 종가 ÷ 1년 최고 종가 · 52주선 = 주봉 52주 평균 · "
               "발표후SPY = 마지막 발표일부터 SPY 대비 초과수익(%p) · 컨센서스는 야후 "
               "칸의 전년값이 우리 4분기 전 값과 2% 안에서 맞을 때만 · 다음 발표는 "
               "작년 같은 분기 발표일 + 364일의 짐작"),
        "정직화": ("사실의 나열입니다 — 판정·점수·추천이 아닙니다. 두 관찰판의 가설"
                 "(H18·H29)은 등록 뒤 새 표본이 없어 판정 불가이고, 가속(H31)도 새 "
                 "표본으로 재는 중입니다. 언제 바꿔야 하는지는 가설로 사전 등록해 "
                 "새 표본으로 판정한 뒤에만 말합니다."),
    }


def recent_completion_rows(완성사건: list[dict], 기준일: str,
                           days: int = 91) -> list[dict]:
    """최근 days 일 안의 정배열 완성 종목 (127차 — 주도 후보 관찰판).

    판정이 아니라 **사실의 나열**입니다. 이격도 30%+ 는 H18/H18b 신호
    표시를 답니다. 이격도를 못 잰 완성은 신호로 치지 않습니다 — 없는
    값은 만들지 않습니다.

    **한 종목은 한 줄만**(150차). 정배열이 깨졌다 다시 붙기를 반복하면
    같은 종목이 여러 번 완성합니다. 그것을 그대로 세면 관찰판이
    "여러 종목이 몰렸다"고 잘못 말합니다 — 실데이터에서 '광고 플랫폼'
    묶음이 **제타 한 종목뿐인데 완성 2개**로 올라 있었습니다. 종목당
    **가장 최근 완성**만 남깁니다.

    **차례**(150차) — ① 새 완성(7일 안)이 먼저 ② 그 안에서 이격도 높은
    순. 129차에 최신 완성이 이격도순에 밀려 접기 속에 숨은 사고를 고치며
    완성일순으로 바꿨는데, 이번엔 반대 사고가 났습니다 — 제타가 이격도
    50.7%(20개 중 4위)인데 완성일이 8일 전이라 6번째로 밀려 접기 속에
    들어갔습니다. 두 사고를 한꺼번에 막는 차례입니다.
    """
    from datetime import timedelta
    cut = (date.fromisoformat(기준일) - timedelta(days=days)).isoformat()
    최근: dict[str, dict] = {}          # 종목 → 가장 최근 완성 사건
    for e in 완성사건:
        if e["day"] < cut:
            continue
        앞 = 최근.get(e["ticker"])
        if 앞 is None or e["day"] > 앞["day"]:
            최근[e["ticker"]] = e
    rows = []
    for e in 최근.values():
        gap = e.get("이격도")
        rows.append({
            "종목": e["ticker"],
            "묶음": cfg.GROUPS.get(e["ticker"], "미분류"),
            "테마": e.get("테마"),
            "완성일": e["day"],
            "이격도": None if gap is None else round(gap, 1),
            "델타": e.get("델타"),
            "신호": gap is not None and gap >= sm.H18_GAP_MIN,
            # 새 완성 = 기준일에서 7일 안 (129차 주인 지적 — LITE 가
            # 이격도순 정렬에 밀려 접기 속에 숨었던 사고의 수리)
            "새완성": (date.fromisoformat(기준일)
                     - date.fromisoformat(e["day"])).days <= 7,
        })
    rows.sort(key=lambda r: (r["새완성"],
                             r["이격도"] if r["이격도"] is not None else -999,
                             r["완성일"]),
              reverse=True)
    return rows


def leader_watch_rows(완성사건: list[dict], 기준일: str,
                      days: int = 91) -> list[dict]:
    """묶음별 최근 완성 집계 (127차 — 주도 후보 관찰판, 판정 아님).

    같은 묶음에서 완성이 무리로 나오면 주도 **후보**로 볼 수 있다는
    관찰 요령을 세어 주는 판입니다. 125차 실측에서 무리 자체는 적중률을
    못 올렸으므로, 순서를 매길 뿐 점수·추천을 만들지 않습니다.
    """
    rows = recent_completion_rows(완성사건, 기준일, days)
    묶음별: dict[str, dict] = {}
    for r in rows:
        g = 묶음별.setdefault(r["묶음"], {"묶음": r["묶음"], "완성": 0,
                                       "신호": 0, "델타상승": 0,
                                       "마지막완성": r["완성일"]})
        g["완성"] += 1
        if r["신호"]:
            g["신호"] += 1
        if r["델타"] is True:
            g["델타상승"] += 1
        g["마지막완성"] = max(g["마지막완성"], r["완성일"])
    out = sorted(묶음별.values(),
                 key=lambda g: (-g["신호"], -g["완성"], g["묶음"]))
    return out


def market_regime_lines(폭: float | None, 직전폭: float | None,
                        verdict: dict | None) -> list[str]:
    """시장 전체 정배열 폭(장세 게이지, 121차)의 정직화 줄.

    지금 폭이 어느 구간인지와 함께 **그 구간의 과거 실측 적중률·기준선·
    판정 상태**를 같이 적습니다 (헌법 3조 정직화). 수치는 121차 탐색
    (2,326건)에서 복사했고 채택 근거가 아닙니다 — H24 판정은 등록일
    (2026-08-20) 뒤의 새 발표로만 이뤄집니다.
    """
    if 폭 is None:
        return ["시장 전체 정배열 폭: 판단 불가 (이력 부족) — 없는 값은 "
                "만들지 않습니다."]
    lines: list[str] = []
    delta = "" if 직전폭 is None else f" · 지난주 대비 {폭 - 직전폭:+.0f}%p"
    lines.append(f"시장 전체 정배열 폭: **{폭:.0f}%**{delta}")
    if 폭 < 20.0:
        lines.append(
            "🟥 **약한 장세 (20% 미만)** — 과거 실측(121차 탐색): 이 구간의 "
            "첫돌파 적중 **8.2%** (기준선 12.0%보다 낮음) · 큰 서프라이즈 "
            "추격도 3.7%로 최저. **종목 신호를 믿을 근거가 없던 구간**입니다."
        )
    elif 폭 < 60.0:
        lines.append(
            "🟩 **살아 있는 장세 (20~60%)** — 과거 실측(121차 탐색): 이 "
            "구간의 첫돌파 적중 **16.8%** (기준선 13.6%) · 다만 신뢰구간이 "
            "겹쳐 아직 우위 증명은 아닙니다."
        )
    else:
        lines.append(
            "⬜ **60% 이상** — 과거 사건이 4건뿐이라 실측이 없습니다. "
            "없는 것을 지어내지 않습니다."
        )
    h24 = ((verdict or {}).get("가설") or {}).get("H24_장세조건부_첫돌파")
    if h24:
        n = ((h24.get("신규(판정)") or {}).get("신호") or {}).get("n")
        lines.append(f"H24(장세 조건부 첫돌파) 판정: **{h24.get('판정')}** "
                     f"(등록 2026-08-20 뒤 새 표본 신호 n={n} — 쌓이는 중)")
    else:
        lines.append("H24(장세 조건부 첫돌파) 판정: 아직 없음 — 121차 등록이 "
                     "반영된 다음 수집부터 실립니다.")
    return lines


def live_signal_rows(ds: dict, days: int = 90) -> list[dict]:
    """지금 채택 신호(H9·H10)가 켜진 종목 — 최근 days 일 발표 중.

    H9 = 잣대 TTM 첫 신기록 ∧ 주봉 종가 < 52주선
    H10 = 논갭 영업이익 첫 신기록 ∧ 같은 조건
    """
    today = ds["prices"][ds["benchmark"]]["dates"][-1]
    cutoff = (date.fromisoformat(today) - timedelta(days=days)).isoformat()
    rows = []
    for ticker in ds["tickers"]:
        quarters = ds["quarters"].get(ticker) or []
        prices = ds["prices"].get(ticker)
        if not prices or not prices.get("dates"):
            continue
        hits = []
        yardstick = me.yardstick_of(quarters)
        if yardstick:
            states = me.earnings_states(quarters, field=yardstick)
            if states and states[-1]["announced"] >= cutoff \
                    and states[-1]["newhigh_streak"] == 1 \
                    and me.below_52wk_ma(prices, states[-1]["announced"]) is True:
                hits.append("H9")
        op_rows = [r for r in quarters if r.get("op_income") is not None]
        if len(op_rows) >= me.LADDER_MIN_QUARTERS:
            op_states = me.earnings_states(quarters, field="op_income")
            if op_states and op_states[-1]["announced"] >= cutoff \
                    and op_states[-1]["newhigh_streak"] == 1 \
                    and me.below_52wk_ma(prices, op_states[-1]["announced"]) is True:
                hits.append("H10")
        if hits:
            last = (me.earnings_states(quarters, field=yardstick or "op_income")
                    or [{}])[-1]
            rows.append({
                "종목": ticker,
                "섹터": cfg.SECTORS.get(ticker, "미분류"),
                "신호": " · ".join(hits),
                "발표일": last.get("announced", ""),
            })
    rows.sort(key=lambda r: r["발표일"], reverse=True)
    return rows


def main():
    import pandas as pd
    import streamlit as st
    import sector_model as sm

    st.set_page_config(page_title="상승 섹터 포착 계기판", layout="centered")
    st.title("상승 섹터 포착 계기판")

    verdict = load_json("verdict.json")
    log = load_json("robot_log.json")
    consensus = load_json("consensus.json")
    surprise = load_json("surprise.json")
    is_v3 = verdict_is_v3(verdict)
    snapshot = dataset.load()
    키 = snapshot_key(snapshot)
    ds = cached_dataset(snapshot, 키)

    if log:
        st.caption(f"로봇 마지막 수집: {str(log.get('ran_at', '?'))[:16]} UTC · "
                   f"{log.get('summary', '')}")
    # 판정이 옛 코드로 계산됐으면 맨 위에서 알립니다 (52차 감사 수리 ⑤)
    code_warning = verdict_code_warning(verdict)
    if code_warning:
        st.warning(code_warning)

    price_dates = ds["prices"][ds["benchmark"]]["dates"]
    # ⚠️ "약 5년"이 **글자로 박혀** 있었습니다 (101차). 수집을 10년으로
    #    늘린 뒤에도 그대로 5년이라 적혀, 날짜(2016~2026)와 대놓고
    #    어긋났습니다. 날짜에서 계산합니다.
    _years = (date.fromisoformat(price_dates[-1])
              - date.fromisoformat(price_dates[0])).days / 365.25
    st.caption(f"측정 기간: {price_dates[0]} ~ {price_dates[-1]} "
               f"(주가 {len(price_dates):,}거래일 · 약 {_years:.0f}년) · "
               "전망: 다음 1분기 (가이던스·컨센서스)")

    # 재료가 얼마나 더러운지 감추지 않습니다 (73차 — 65차 §④ 등록 방침).
    # 자동으로 지우지 않고 **세어서 보여 주기만** 합니다: 지울 규칙을
    # 만들면 진짜 실적까지 지운다는 것을 66차·73차에 실측했습니다.
    with st.expander("재료 상태 (수집 데이터 오염 조사)"):
        st.caption(audit_data.one_line(ds["quarters"]))
        튄칸 = audit_data.spike_cells(ds["quarters"])[:15]
        if 튄칸:
            st.caption("이웃 분기보다 크게 튄 칸 (큰 것부터 15개) — 참고용:")
            # 휴대폰 폭(412px)에서 열이 가려지지 않도록 4열로 줄이고,
            # 앞·뒤 이웃은 한 칸에 모아 적습니다.
            짧은칸 = {"adj_eps": "조정EPS", "adjusted_ebitda": "EBITDA",
                    "gaap_eps": "GAAP"}
            st.dataframe(pd.DataFrame([
                {"종목": r["종목"], "칸": 짧은칸.get(r["칸"], r["칸"]),
                 "값": f"{r['값']:,.2f}",
                 "앞→뒤": f"{r['앞']:,.2f} → {r['뒤']:,.2f}",
                 "배수": f"{r['배수']:.1f}배"} for r in 튄칸
            ]), width="stretch", hide_index=True)

        # 수집물 건강검진 (108차) — 로봇이 매일 세어 온 숫자를 그대로 보여
        # 줍니다. 값을 바꾸지 않고 세기만 한 결과이며, 경보 문턱은 며칠치를
        # 쌓아 정상 범위를 실측한 뒤 사전 등록합니다 (아직 등록 전).
        st.markdown("**수집물 건강검진** (로봇이 매일 자동으로 셉니다)")
        for 줄 in health_lines((log or {}).get("건강검진")):
            st.caption(줄)

        # 채택까지 얼마나 남았나 (139차) — 주인의 "언제부터 포착 가능한가"에
        # 대한 산수 답. 새 기준이 아니라 등록된 기준을 그대로 푼 것입니다.
        st.markdown("---")
        for 줄 in adoption_distance_lines(verdict):
            st.caption(줄)

    # =====================================================================
    # 0. 오늘 요약 (129차) — 화면 전체를 안 읽어도 되는 3~4줄
    # =====================================================================
    confirm_rows = cached_confirmation_rows(ds, 키)
    fired = dedupe_confirmations([r for r in confirm_rows if r["확인"]])
    _완성 = cached_completion_events(ds, 키)
    _오늘 = ds["prices"][ds["benchmark"]]["dates"][-1]
    _시장 = cached_market_breadth(ds, 키)
    _폭 = _시장[-1][1] if _시장 else None
    _직전폭 = _시장[-2][1] if len(_시장) >= 2 else None
    _묶음판 = leader_watch_rows(_완성, _오늘)
    st.markdown(MODERN_CSS, unsafe_allow_html=True)
    _종목판전체 = recent_completion_rows(_완성, _오늘)
    _새완성 = [r for r in _종목판전체 if r["새완성"] and r["신호"]]
    st.markdown(summary_cards_html(_폭, fired, _묶음판, _새완성,
                                   adopted_names(verdict) if verdict else [],
                                   adoption_caveats(verdict)),
                unsafe_allow_html=True)

    tab_home, tab_market, tab_fund, tab_check = st.tabs(
        ["🏠 주도", "📈 장세", "📊 실적", "🧾 검증"])

    # =====================================================================
    # 1. 주도 후보 — 확인 신호(늦지만 강함) + 관찰판(이르지만 약함)
    # =====================================================================
    with tab_home:
        # 히어로 — 로빈후드의 계좌 차트 자리에 "시장 정배열 폭" (사실 표시)
        if _폭 is not None:
            import altair as alt
            _추세색 = "mv-green" if (_직전폭 is not None and _폭 >= _직전폭) else "mv-red"
            _차이 = "" if _직전폭 is None else f" · 지난주 {(_폭-_직전폭):+.1f}%p"
            st.markdown(
                f'<div class="rh-hero"><div class="v {_추세색}">{_폭:.1f}%</div>'
                f'<div class="s">시장 정배열 폭 (전 종목){_차이} — 아래 모든 신호를 읽는 배경</div></div>',
                unsafe_allow_html=True,
            )
            _최근1년 = _시장[-52:]
            st.altair_chart(
                alt.Chart(pd.DataFrame(_최근1년, columns=["주", "폭"]))
                .mark_line(color="#00C805", strokeWidth=2)
                .encode(x=alt.X("주:T", axis=None),
                        y=alt.Y("폭:Q", axis=None,
                                scale=alt.Scale(zero=False)))
                .properties(height=90),
                use_container_width=True,
            )
        st.header("① 주도 후보")
        st.caption(
            "위쪽 **확인 신호** = 주가가 이미 돌아선(3개월 시장 대비 플러스) "
            "묶음에서 정배열 폭 40%p↑·이익 델타 폭 50%p↑ 급등이 **동시에** 나온 "
            "곳 — 늦지만 강한 확인. 아래쪽 **관찰판** = 최근 91일 정배열 완성이 "
            "몰리는 묶음 — 이르지만 약한 관찰."
        )
        if fired:
            for row in fired:
                st.success(
                    f"🔥 **{row['묶음']}** — 확인 신호 켜짐  \n"
                    f"3개월 상대수익 {row['3개월상대']:+.1f}%p · "
                    f"정배열 폭 {row['직전정배열폭']:.0f}% → **{row['정배열폭']:.0f}%** · "
                    f"이익 델타 폭 {row['직전델타폭']:.0f}% → **{row['델타폭']:.0f}%**"
                )
                with st.expander(f"{row['묶음']} 구성 종목 보기"):
                    for m in group_member_rows(row["묶음"], _완성, _오늘):
                        꼬리 = ""
                        if m["완성"]:
                            꼬리 = f" — 완성 {m['완성']}"
                            if m["이격도"] is not None:
                                꼬리 += f" · 이격도 {m['이격도']}%"
                            if m["신호"]:
                                꼬리 += " · 🔥신호"
                            if m["델타"] is True:
                                꼬리 += " · 델타상승"
                        st.markdown(f"· **{m['종목']}**{꼬리}")
        else:
            st.info("현재 확인 신호가 켜진 묶음이 없습니다.")

        near = dedupe_confirmations(
            [r for r in confirm_rows
             if not r["확인"] and r["전제"] and (r["정배열확인"] or r["델타확인"])])
        if near:
            with st.expander(f"한 조건만 채운 묶음 {len(near)}개 (아직 확인 아님)"):
                for row in near:
                    missing = "정배열 미달" if not row["정배열확인"] else "델타 미달"
                    st.markdown(
                        f"· {row['묶음']} — {missing} "
                        f"(정배열 {row['정배열폭']:.0f}% · 델타 {row['델타폭']:.0f}% · "
                        f"3개월 {row['3개월상대']:+.0f}%p)"
                    )

        _h14_경고 = (
            "**판정 상태 (정직화)** — H14: **미채택**  \n"
            "과거 실측(38차): 이 신호가 켜진 20건은 1년 뒤 **주도 유지 70.0%** · "
            "되돌림 25.0% · 상대수익 중앙 **+42.4%p** 였고, "
            "주가만 오른 대조군 339건은 53.7% · 35.7% · +13.4%p 였습니다. "
            "앞시기·뒤시기 모두 70%로 안정적이나, **표본 20건으로 구간이 넓어 "
            "채택 기준(완전 분리)을 넘지 못했습니다.** 따라서 아직 매수 근거가 "
            "아니라 관찰이며, 로봇이 표본을 쌓는 대로 자동 재판정합니다."
        )
        with st.expander("확인 신호의 과거 성적·판정 상태 (정직화)"):
            st.warning(_h14_경고)

        # 관찰판 (127차 — 이르지만 약한 관찰. ①로 이동, 129차)
        st.markdown("**관찰판** — 최근 91일 정배열 완성이 몰리는 묶음 (판정 아님)")
        _몰림 = [g for g in _묶음판 if g["완성"] >= 2]
        if not _몰림:
            st.markdown("최근 91일 안에 완성이 2개 이상 나온 묶음이 없습니다.")
        for g in _몰림[:3]:
            st.markdown(
                f"· **{g['묶음']}** — 완성 {g['완성']}개"
                f" (이격도30%+ {g['신호']}개 · 델타상승 {g['델타상승']}개)"
                f" · 마지막 {g['마지막완성']}"
            )
            with st.expander(f"{g['묶음']} 구성 종목 보기"):
                for m in group_member_rows(g["묶음"], _완성, _오늘):
                    꼬리 = ""
                    if m["완성"]:
                        꼬리 = f" — 완성 {m['완성']}"
                        if m["이격도"] is not None:
                            꼬리 += f" · 이격도 {m['이격도']}%"
                        if m["신호"]:
                            꼬리 += " · 🔥신호"
                        if m["델타"] is True:
                            꼬리 += " · 델타상승"
                    st.markdown(f"· **{m['종목']}**{꼬리}")
        if len(_몰림) > 3:
            with st.expander(f"나머지 묶음 {len(_몰림)-3}개 보기"):
                for g in _몰림[3:]:
                    st.markdown(
                        f"· **{g['묶음']}** — 완성 {g['완성']}개"
                        f" (이격도30%+ {g['신호']}개 · 델타상승 {g['델타상승']}개)"
                        f" · 마지막 {g['마지막완성']}"
                    )
        _종목판 = [r for r in _종목판전체 if r["신호"]]
        if _종목판:
            st.markdown("**이격도 30%+ 완성 종목** — 새 완성 먼저, "
                        "그다음 이격도순 (H18·H18b 신호, 탐색 참고):")
            # 5칸이던 것을 8칸으로 (150차). 제타가 6번째라 딱 한 칸
            # 차이로 접혀 있었습니다 — 주인이 "왜 화면에 없지"라고
            # 물은 것이 이것입니다. 모바일 412px 에서 8줄까지는 한
            # 화면에 무리 없이 들어갑니다.
            _펼침 = 8
            st.markdown("".join(signal_row_html(r) for r in _종목판[:_펼침]),
                        unsafe_allow_html=True)
            if len(_종목판) > _펼침:
                with st.expander(f"나머지 {len(_종목판)-_펼침}개 보기"):
                    st.markdown(
                        "".join(signal_row_html(r) for r in _종목판[_펼침:]),
                        unsafe_allow_html=True)
            st.caption(
                "과거 실측(126차): 이 신호의 1년 뒤 SPY+20%p **46.6%** (기준선 "
                "27.5%) · 시장을 이긴 비율 59% — **채택 전 참고**이며 10건 중 "
                "4건은 시장에 졌습니다."
            )
        else:
            st.markdown("지금 이격도 30%+ 완성 종목 없음 — 없는 것은 없다고 말합니다.")

        _유지 = cached_aligned_now(ds, 키)
        with st.expander(f"지금 주봉 정배열 유지 중인 종목 {len(_유지)}개 (이격도순)"):
            st.caption(
                "오래전 완성해 계속 달리는 종목은 위 관찰판(새 완성)에 안 "
                "보입니다 — 여기서 봅니다. 여기 없는 종목은 지금 정배열이 "
                "깨져 있다는 뜻입니다 (예: 조정 중인 MU·AMD)."
            )
            for r in _유지[:30]:
                이격 = "—" if r["이격도"] is None else f"{r['이격도']}%"
                st.markdown(f"· **{r['종목']}** ({r['묶음']}) · 이격도 {이격}")
            if len(_유지) > 30:
                st.caption(f"… 외 {len(_유지)-30}개")

        # =====================================================================
        # 2. 장세 (정배열 폭 모델, 33·34차 + 121차 시장 게이지)
        # =====================================================================
    with tab_market:
        st.header("② 장세 — 시장·섹터 정배열 폭")
        st.caption(
            "각 섹터에서 **주가가 완전 정배열**(주봉 종가 > 4주 > 13주 > 26주 > "
            "52주선)인 종목의 비율입니다. 옆 숫자는 **그 구간이 과거에 1년 뒤 "
            "시장을 20%p 이상 이긴 비율**(34차 탐색값)입니다."
        )

        # 시장 전체 장세 게이지 (121차) — 값은 맨 위 요약에서 이미 계산됨.
        for 줄 in market_regime_lines(_폭, _직전폭, verdict):
            st.markdown(줄)
        st.divider()

        breadth_rows = cached_current_breadth(ds, 키)
        measured = [r for r in breadth_rows if r["폭"] is not None]
        if measured:
            st.altair_chart(
                sorted_bar_chart(
                    [r["섹터"] for r in measured],
                    [r["폭"] for r in measured],
                    "정배열 폭 (%)",
                    colors={r["섹터"]: ZONE_COLORS[breadth_zone(r["폭"])["zone"]]
                            for r in measured},
                ),
                use_container_width=True,
            )
            st.caption(
                "🟩 쌓이는 중(40~59%) — 과거 1년 폭등률 33.3%로 최고 · "
                "🟥 정배열 완성(60%+) — 7.7%로 최저 · 🟦 초기 · ⬜ 약함"
            )

        with st.expander("섹터별 자세히 보기 (폭·상태·과거 실측)"):
            for row in breadth_rows:
                zone = breadth_zone(row["폭"])
                if row["폭"] is None:
                    st.markdown(f"**{row['섹터']}** ({row['종목수']}종목) — 판단 불가 (이력 부족)")
                    continue
                moved = ""
                if row["직전폭"] is not None:
                    delta = row["폭"] - row["직전폭"]
                    moved = f" · 지난주 대비 {delta:+.0f}%p"
                st.markdown(
                    f"**{row['섹터']}** {row['폭']:.0f}% ({row['종목수']}종목){moved}  \n"
                    f"{row['상태']} — {zone['zone']} · 이 구간의 과거 1년 폭등률 "
                    f"**{zone['rate']}%** (기준선 {breadth_baseline(verdict)}%)  \n"
                    f"<span style='color:gray;font-size:0.85em'>{zone['note']}</span>",
                    unsafe_allow_html=True,
                )
            st.warning("\n".join(breadth_verdict_lines(verdict)))

        # =====================================================================
        # 1-2. AI 사이클 추적 (37차) — 저장소 주인 관찰 국면의 실제 순서
        # =====================================================================
    with tab_fund:
        with st.expander("사이클 추적 — 정배열·이익 델타·주가 (펼쳐 보기)"):
            st.caption(
                "AI 사이클 종목 묶음의 **정배열 폭**(주가가 정배열인 비율)과 "
                "**이익 델타 폭**(직전 분기보다 이익이 는 비율), 그리고 "
                "**상대수익**(2024년 말 기준 SPY 대비 누적)을 함께 봅니다. "
                "세 선의 **순서**가 이 모델의 핵심 질문입니다."
            )
            ai_members, non_ai = sm.ai_members(ds)
            ai_series = cached_cycle_series(ds, tuple(ai_members), "2024-12-31",
                                            "2025-01-01", 키)
            non_series = cached_cycle_series(ds, tuple(non_ai), "2024-12-31",
                                             "2025-01-01", 키)
            if ai_series:
                import altair as alt
                frames = []
                for label, series in (("AI 정배열 폭", ai_series), ("AI 이익 델타 폭", ai_series)):
                    key = "정배열폭" if "정배열" in label else "델타폭"
                    frames += [{"월": r["월"][:7], "선": label, "값": r[key]} for r in series]
                width_df = pd.DataFrame(frames)
                st.altair_chart(
                    alt.Chart(width_df).mark_line(point=True).encode(
                        x=alt.X("월:N", title=None, axis=alt.Axis(labelAngle=-60)),
                        y=alt.Y("값:Q", title="폭 (%)"),
                        color=alt.Color("선:N", title=None,
                                        scale=alt.Scale(range=["#2E9E5B", "#E0A030"]),
                                        legend=alt.Legend(orient="top")),
                    ).properties(height=230),
                    use_container_width=True)
                rel_df = pd.DataFrame(
                    [{"월": r["월"][:7], "선": "AI 사이클", "값": r["상대수익"]}
                     for r in ai_series if r["상대수익"] is not None]
                    + [{"월": r["월"][:7], "선": "비AI", "값": r["상대수익"]}
                       for r in non_series if r["상대수익"] is not None])
                st.altair_chart(
                    alt.Chart(rel_df).mark_line(point=True).encode(
                        x=alt.X("월:N", title=None, axis=alt.Axis(labelAngle=-60)),
                        y=alt.Y("값:Q", title="SPY 대비 누적 (%p)"),
                        color=alt.Color("선:N", title=None,
                                        scale=alt.Scale(range=["#C4553B", "#8A8F98"]),
                                        legend=alt.Legend(orient="top")),
                    ).properties(height=230),
                    use_container_width=True)
                low = min(ai_series, key=lambda r: r["정배열폭"])
                st.info(
                    f"**실측 순서 (37차)**: AI 정배열 폭 최저는 **{low['월'][:7]} "
                    f"{low['정배열폭']}%** — 이익 델타 폭도 이 무렵 바닥이었고, "
                    "**주가가 먼저 돌아선 뒤** 정배열과 델타가 뒤따랐습니다. "
                    "'정배열·델타가 먼저, 주가가 나중'이 아니라 **반대 순서**입니다."
                )

            # =====================================================================
            # 2. 메인 — 채택된 신호
            # =====================================================================
    with tab_check:
        st.header("③ 채택된 신호")
        adopted = adopted_names(verdict) if is_v3 else []
        if not is_v3:
            st.warning("판정 파일이 v3 형식이 아닙니다 — 다음 로봇 수집 때 갱신됩니다.")
        elif adopted:
            st.success("채택: " + " · ".join(adopted))
            for name, entry in (verdict.get("가설") or {}).items():
                if entry.get("판정") != "채택":
                    continue
                judged = entry.get("신규(판정)") or {}
                s, b = judged.get("신호") or {}, judged.get("기준선") or {}
                st.markdown(
                    f"**{HYPOTHESIS_LABELS.get(name, name)}**  \n"
                    f"이 신호가 켜진 발표는 60거래일 뒤 시장을 20%p 이상 이긴 비율이 "
                    f"**{s.get('rate')}%** (n={s.get('n')}), 아무 발표나 샀을 때는 "
                    f"{b.get('rate')}% 였습니다.  \n"
                    f"앞시기 {entry.get('신규_앞시기', {}).get('rate')}% · "
                    f"뒤시기 {entry.get('신규_뒤시기', {}).get('rate')}%"
                )
            live = live_signal_rows(ds)
            st.markdown("**지금 이 신호가 켜진 종목** (최근 90일 발표)")
            if not live:
                st.caption("현재 없음 — 새 실적 발표를 기다립니다.")
            else:
                st.dataframe(pd.DataFrame(live), width="stretch", hide_index=True)
        else:
            st.info("채택된 신호 없음 — 어떤 상태도 매수 판단의 근거가 아닙니다.")
        st.caption(
            "⚠️ 채택 표본에는 그 가설을 찾아낸 탐색 종목이 섞여 있습니다. "
            "완전한 독립 확인은 등록 이후 새 발표가 쌓여야 완성됩니다."
        )

        # =====================================================================
        # 3. 섹터 한눈에 보기 (차트)
        # =====================================================================
    with tab_fund:
        with st.expander("섹터 실적 한눈에 — 신기록 폭·서프라이즈·전망 (펼쳐 보기)"):

            st.subheader("실적 신기록 폭")
            st.caption("최근 발표한 종목 중 이익 신기록이 나온 비율 (관찰).")
            gauge_rows = [r for r in sector_gauge_rows(ds) if r["게이지"] is not None]
            if gauge_rows:
                st.altair_chart(
                    sorted_bar_chart([r["섹터"] for r in gauge_rows],
                                     [r["게이지"] for r in gauge_rows], "신기록 폭 (%)"),
                    use_container_width=True)

            st.subheader("실적 서프라이즈 (컨센서스 대비)")
            st.caption("최근 2분기 발표가 애널리스트 추정을 몇 % 넘겼는지 (중앙값, 관찰).")
            sur_rows = surprise_sector_rows(surprise)
            if sur_rows:
                st.altair_chart(
                    sorted_bar_chart([r["섹터"] for r in sur_rows],
                                     [r["중앙%"] for r in sur_rows],
                                     "서프라이즈 중앙 (%)", positive_negative=True),
                    use_container_width=True)
                st.caption("⚠️ 적자 근처 종목은 % 가 크게 튑니다 (분모가 0에 가까움).")
            else:
                st.caption("아직 서프라이즈 원장이 비어 있습니다.")

            st.subheader("전망 스프레드 (가이던스 − 컨센서스)")
            st.caption("회사가 시장 기대보다 높게 부를수록 큰 값 (다음 1분기, 관찰).")
            spread_rows = sector_spread_rows(ds, consensus)
            if spread_rows:
                st.altair_chart(
                    sorted_bar_chart([r["섹터"] for r in spread_rows],
                                     [r["스프레드중앙%"] for r in spread_rows],
                                     "스프레드 중앙 (%)", positive_negative=True),
                    use_container_width=True)
                for row in spread_rows:
                    if row["표본"] == "표본 부족":
                        st.caption(f"⚠️ {row['섹터']}: {row['종목수']}종목뿐 — 표본 부족")
            else:
                st.caption("신선한 가이던스와 컨센서스가 함께 있는 종목이 아직 없습니다.")

            # =====================================================================
            # 4. 접기 — 미채택·판정 대기 신호 상세
            # =====================================================================
    with tab_check:
        with st.expander("⑥ 미채택·판정 대기 신호 — 무엇을 재봤고 왜 안 쓰는가"):
            st.caption(
                "아래는 사전 등록해 측정했으나 **채택 기준(신호 구간이 기준선 "
                "구간과 완전히 갈라짐, n≥10)을 넘지 못한** 신호들입니다. "
                "판단·점수·추천에 쓰지 않되, 버리지 않고 로봇이 계속 재판정합니다."
            )
            for name, entry in (verdict.get("가설") or {}).items():
                if entry.get("판정") == "채택":
                    continue
                judged = entry.get("신규(판정)") or {}
                s, b = judged.get("신호") or {}, judged.get("기준선") or {}
                label = HYPOTHESIS_LABELS.get(name, name)
                detail = HYPOTHESIS_DETAILS.get(name, "")
                rate_text = signal_summary(entry, s, b)
                st.markdown(
                    f"**{label}** — {entry.get('판정', '?')}  \n"
                    f"{detail}  \n"
                    f"<span style='color:gray;font-size:0.9em'>{rate_text}</span>",
                    unsafe_allow_html=True,
                )

        # =====================================================================
        # 5. 원자료·용어·한계
        # =====================================================================
        st.page_link("pages/1_원자료.py",
                     label="원자료 보기 — 종목별 최근 발표·전 종목 상태 →")

        with st.expander("용어 풀이"):
            st.markdown(
                "- **완전 정배열** — 주봉 종가가 4·13·26·52주 이동평균 위에 있고, "
                "그 이동평균들도 짧은 것부터 순서대로 위에 있는 상태\n"
                "- **정배열 폭** — 그 섹터 종목 중 완전 정배열인 비율\n"
                "- **TTM 조정 EPS** — 최근 4개 분기 조정 주당순이익 합 "
                "(회사가 보도자료에 직접 발표한 숫자만)\n"
                "- **첫 신기록** — TTM 이익이 과거 최고를 처음 넘은 발표\n"
                "- **52주선 아래** — 주가가 자기 1년 평균 아래 (아직 안 오른 상태)\n"
                "- **컨센서스** — 애널리스트들의 실적 추정 평균 (야후)\n"
                "- **가이던스** — 회사가 직접 발표한 다음 분기 전망\n"
                "- **서프라이즈** — 실제 실적이 컨센서스를 넘긴 정도\n"
                "- **폭등** — 60거래일(1년 모델은 250거래일) 수익이 SPY보다 +20%p 이상\n"
                "- **기준선** — 아무 때나 샀을 때의 같은 비율 (비교 대상)\n"
                "- **n** — 표본 수 · **윌슨 구간** — 적중률의 신뢰 범위 "
                "(표본이 적을수록 넓어져 과신을 막습니다)\n"
                "- **채택/미채택/판정 불가** — 신호 구간이 기준선 구간과 완전히 "
                "갈라지면 채택, 겹치면 미채택, 표본 10건 미만이면 판정 불가\n"
                "- **H번호** — 사전 등록된 가설의 일련번호 (측정결과.md 와 잇는 꼬리표)\n"
                "- **UTC** — 국제 표준시. 한국 시각보다 9시간 늦습니다"
            )

        with st.expander("한계 (감추지 않습니다)"):
            st.markdown(
                "- 채택되지 않은 신호는 판단·점수·추천에 쓰지 않습니다.\n"
                "- 정배열 폭 모델의 과거 실측(34차)은 **탐색값**이라 채택 근거가 "
                "아닙니다. H12 는 등록 이후 새 신호로만 판정합니다.\n"
                "- 컨센서스·서프라이즈는 야후 제공값입니다. 서프라이즈 소급분은 "
                "야후가 사후 보관한 기록이라 우리가 직접 박제한 원장과 구분해 둡니다.\n"
                "- 조정 EPS 를 발표하지 않는 종목은 EBITDA·GAAP EPS 잣대로 넘어가거나 "
                "'판단 불가'로 나옵니다 — 없는 값은 없음으로 둡니다.\n"
                "- 같은 시기의 사건들은 같은 장세를 공유하므로 통계 구간이 실제보다 "
                "좁게 나올 수 있습니다."
            )


if __name__ == "__main__":
    main()
