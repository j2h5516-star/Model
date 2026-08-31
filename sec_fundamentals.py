"""
sec_fundamentals.py — SEC 8-K 실적 보도자료에서 논갭(non-GAAP) 실적 수집
======================================================================

이 파일이 하는 일:
  1) edgartools로 각 종목의 8-K(Item 2.02 = 실적발표) 공시를 찾고
  2) 첨부된 실적 보도자료에서 다음 숫자를 뽑아냅니다:
       · 매출(revenue)
       · 논갭 영업이익(non-GAAP operating income)
       · 논갭 매출총이익률(non-GAAP gross margin %)
  3) 숫자를 못 찾으면 단계적으로 대체 방법을 시도합니다:
       ① 직접공시 : 보도자료에 논갭 영업이익이 그대로 적혀 있음
       ② 역산     : 매출 × 논갭 GM% − 논갭 영업비용
       ③ 근사치   : SEC XBRL의 GAAP 영업이익 + 주식보상비 + 무형자산상각

"논갭(non-GAAP)"이란?
  회계 규정(GAAP)대로 계산한 이익에서 일회성 비용이나 주식보상비처럼
  "실제 현금이 나가지 않는 항목"을 빼고 다시 계산한 이익입니다.
  회사의 진짜 영업 체력을 보려고 업계에서 널리 쓰는 숫자입니다.
"""

from __future__ import annotations

import json
import math
import os
import re
import threading
from datetime import datetime, timedelta, timezone

import config as cfg

# ---------------------------------------------------------------------------
# 숫자 뽑아내기 도우미
# ---------------------------------------------------------------------------

# 보도자료 표에서 "(단위: 천 달러)" 같은 표기를 찾기 위한 패턴
# 표 머리글의 단위 표기.
#
# ⚠️ 예전에는 "(in thousands" 처럼 **괄호 바로 뒤에 in 이 오는 형태**만 알아봤습니다.
#    그래서 아래 표기들이 전부 "단위 없음(×1)"으로 처리돼 값이 1,000배 작게
#    들어왔습니다 — 그런데 **매출과 영업이익이 같이 작아지므로 마진 비율이 보존되어
#    어떤 정합성 검사에도 걸리지 않습니다.** 옆 분기와 섞이면 "영업이익 99.9% 붕괴"
#    같은 가짜 델타가 생깁니다.
#
#      (Unaudited, in thousands)          (unaudited, in millions)
#      (Dollars in thousands)             ($ in millions)
#      (U.S. dollars in millions)         (dollars and shares in thousands, ...)
#
# 그래서 괄호 안 어디에 있든 "<무엇이> in thousands/millions/billions" 를 잡습니다.
_SCALE_WORDS = r"(thousand|million|billion)s?"
_UNIT_PATTERNS = [
    (re.compile(rf"\([^)]{{0,40}}?\bin\s+{_SCALE_WORDS}\b", re.I), None),
    (re.compile(rf"\(\s*\$\s*in\s+{_SCALE_WORDS}\b", re.I), None),
    (re.compile(rf"\bamounts?\s+in\s+{_SCALE_WORDS}\b", re.I), None),
    (re.compile(rf"\bdollars?\s+in\s+{_SCALE_WORDS}\b", re.I), None),
    # 87차 — 위 넷은 전부 "in" 을 요구합니다. 그런데 단위를 **이름에 붙여**
    # 적는 회사가 많습니다. 실물 AMD:
    #     "Revenue ($M)      $10,270      $7,658"
    # 여기에는 "(in millions)" 표기가 문서 어디에도 없어, 10,270 이 그대로
    # 들어갔습니다 (야후 10,270,000,000). 매출 단위가 종목마다 · 심지어
    # 한 종목 안에서도 뒤섞인 원인이 이것입니다 (86차 ⑫ — 44곳/20종목).
    #
    # 저장소 원문 732건 실측:
    #   "($M)" 꼴 (달러기호 있음)   300곳 / 19종목  ← 넣는다
    #   "(Millions…" 꼴 (in 없음)  335곳 / 25종목  ← 넣는다
    #   "(M)"  꼴 (달러기호 없음)   180곳 / 11종목  ← **넣지 않는다**
    #
    # 마지막 것을 뺀 이유: 실물이 전부 "Shares (M)" — **주식 수**의 단위지
    # 금액의 단위가 아닙니다. 이걸 매출에 곱하면 값이 100만 배 틀립니다.
    # 각주 기호 "(A) (B) (C)" 와도 구별이 안 됩니다. 그래서 달러 기호가
    # 붙은 것만 인정합니다.
    #
    # 두 번째 것에 닫는 괄호를 요구하지 않는 이유 — 실물 AMD 손익계산서
    # 머리글이 "**(Millions except per share amounts and percentages)**"
    # 입니다. 괄호가 **단위 낱말로 시작하면** 그것은 단위 선언입니다.
    (re.compile(r"\(\s*\$\s*([MBK])\s*\)"), None),
    (re.compile(rf"\(\s*\$?\s*{_SCALE_WORDS}\b", re.I), None),
    # 150차-AF — 위의 것들은 전부 **괄호**를 요구합니다. 그런데 단위를
    # 괄호 없이 **표 머리 한 줄로** 적는 회사가 많습니다. 실물 GS:
    #
    #     Segment Net Revenues (unaudited)
    #     $ in millions                     ← 괄호가 없다
    #     ...
    #     Net revenues            12,738
    #
    # 그래서 GS 매출이 127억이 아니라 **12,738 달러**로 들어왔습니다
    # (150차-AC 에서 화면 경보로만 표시하고 못 고쳤던 그 값입니다).
    #
    # 저장소 원문 1,443건 실측:
    #   "$ in millions" 꼴 (괄호 없음)   194곳 / 21종목 (GS·FITB·DIS·DE·KEY…)
    #   줄머리 맨 "In millions" 꼴       181곳 / 14종목 (HPE·EOG·LOW·HD·CVS…)
    #
    # 둘째 것을 **줄머리로 한정**한 이유: 문장 한가운데의 "in millions" 는
    # 서술문("costs in millions of dollars")일 수 있지만, 줄이 그 말로
    # 시작하면 그것은 표 머리입니다. 실측으로 걸린 서로 다른 줄 51가지가
    # 전부 단위 선언이었고 서술문은 한 건도 없었습니다.
    (re.compile(rf"\$\s*in\s+{_SCALE_WORDS}\b", re.I), None),
    (re.compile(rf"^[ \t]*in\s+{_SCALE_WORDS}\b", re.I | re.M), None),
]
# 잡아낸 낱말 → 배수 (약자 M·B·K 포함 — 87차)
_SCALE_MULTIPLIER = {
    "thousand": 1_000, "million": 1_000_000, "billion": 1_000_000_000,
    "k": 1_000, "m": 1_000_000, "b": 1_000_000_000,
}

# 숫자 하나를 나타내는 패턴 — 예: $ 1,234.5  /  (1,234)  /  45.1
_NUMBER_RE = re.compile(
    r"""
    (?P<paren_open>\()?          # 괄호로 시작하면 음수
    \s*\$?\s*
    # 150차-AS — 마지막 갈래 `\.\d+` 는 **소수점으로 시작하는 표기**입니다.
    # 실물 SCHW 는 주당 금액을 "$.75" 라고 적습니다(0 을 안 씁니다).
    # 이 갈래가 없어서 그 칸을 건너뛰고 **6개월 누적 칸($1.68)** 을
    # 분기값으로 읽었습니다.
    # "Dec. 31" 처럼 점 뒤에 공백이 오는 것은 걸리지 않습니다(점 바로
    # 뒤에 숫자를 요구하므로).
    (?P<num>\d{1,3}(?:,\d{3})*(?:\.\d+)?|\d+(?:\.\d+)?|\.\d+)
    \s*
    (?P<paren_close>\))?
    \s*
    (?P<scale>million|billion|thousand|bn|mm|m\b|b\b)?
    """,
    re.I | re.X,
)

# 창 끝에 걸친 숫자를 **끝까지 읽기 위해** 덧대는 여유 글자 수 (87차).
# 가장 긴 형태 "(1,234,567.89) billion" 이 30자쯤이라 넉넉히 40 을 둡니다.
# 이 여유는 숫자를 **끝맺는 데만** 쓰고, 여기서 새로 시작하는 숫자는
# 위 _parse_number_at 에서 물리치므로 탐색 범위가 넓어지지는 않습니다.
_NUMBER_TAIL = 40

# 숫자 바로 뒤에 붙어 "이건 퍼센트다"를 뜻하는 표기들.
# "%" 기호만 보면 "82 percent" 같은 낱말 표기를 금액으로 오인합니다.
_PERCENT_AFTER_RE = re.compile(
    r"\s*(%|％|percentage\b|percent\b|pct\b|basis\s+points\b|bps\b)", re.I
)

# 텍스트에 붙은 단위 단어 → 곱할 배수
_WORD_SCALE = {
    "thousand": 1_000,
    "million": 1_000_000,
    "mm": 1_000_000,
    "m": 1_000_000,
    "billion": 1_000_000_000,
    "bn": 1_000_000_000,
    "b": 1_000_000_000,
}


def _detect_table_unit(text: str, position: int, window: int = 3000) -> int:
    """숫자가 발견된 위치 바로 앞쪽에서 "(in thousands)" 같은 단위 표기를 찾습니다.

    표 형태의 보도자료는 숫자에 단위가 안 붙어 있고 표 제목에만 적혀 있기 때문입니다.
    못 찾으면 1(단위 없음)을 돌려줍니다.

    ⚠️ `position` 은 **숫자 앞 공백의 시작**입니다(_parse_number_at 이 돌려주는
       number_start). 아래 '표의 칸인가' 검사가 그 공백을 봅니다.
    """
    가까운 = _가장_가까운_단위(text, position, window)
    if 가까운 != 1:
        return 가까운
    return _멀리_있는_단위를_조심히_쓰기(text, position)


def _가장_가까운_단위(text: str, position: int, window: int) -> int:
    start = max(0, position - window)
    context = text[start:position]
    best_scale, best_pos = 1, -1
    for pattern, _ in _UNIT_PATTERNS:
        for match in pattern.finditer(context):
            if match.start() > best_pos:      # 가장 가까운(=뒤쪽) 표기를 채택
                word = (match.group(1) or "").lower()
                best_pos = match.start()
                best_scale = _SCALE_MULTIPLIER.get(word, 1)
    return best_scale


# 표의 칸인가 — 이름과 값 사이가 공백 두 칸 이상으로 벌어지면 표의 칸입니다.
# 서술문은 "revenues of $17.23" 처럼 한 칸으로 붙습니다.
_TABLE_CELL_RE = re.compile(r"[ \t]{2,}")
_FAR_UNIT_AHEAD = 40


def _멀리_있는_단위를_조심히_쓰기(text: str, position: int) -> int:
    """창(3,000자) 안에서 못 찾았을 때만, 더 멀리 있는 단위 선언을 씁니다.

    150차-AF — GS 는 합계 줄이 단위 선언에서 3,225자 떨어져 있어 창 밖입니다.
    그래서 매출이 172억이 아니라 **17,227 달러**로 남았습니다.

    다만 그냥 멀리까지 보게 하면 **더 나빠집니다.** 원문 1,443건 전수 실측 —
    문서 전체를 보게 하면 47건이 바뀌는데 그중 27건이 잘못됐고, 그 안에는
    Citi 10.35**조** · JPM 100**조** · TROW 5.6**조** 같은 값이 있었습니다.
    88차에 적어 둔 "그럴듯해져서 더 위험하다"가 그대로 재현된 것입니다.

    그래서 두 가지를 함께 요구합니다.

      (가) 배수가 **10억이면 쓰지 않습니다.** 재무제표 표는 천·백만 단위로
           적지 10억 단위로 적지 않습니다. 멀리 있는 "billion" 은 거의 언제나
           서술문("$1.2 billion 자사주 매입")이거나 다른 구역입니다.
           실측 — 먼 곳의 10억 선언 6건이 **6건 모두 틀렸습니다.**

      (나) 숫자가 **표의 칸**에 있어야 합니다. "(in millions)" 는 그 **표**의
           단위 선언이지 서술문의 단위가 아닙니다. 서술문 숫자에 표의 단위를
           곱하는 것은 종류가 다른 것을 곱하는 일입니다.
           실측 — 잃던 값 202·3·28 은 전부 슬라이드·서술문 숫자였습니다.

    둘을 얹은 뒤 전수 재측정: 바뀐 것 29건 중 **23건이 제자리로** 갔고
    (GS 5 · MCK 3 · LLY 3 · MET 2 · MCD 2 · CMCSA 2 · NFLX 2 · AFL · BAC ·
    CL · TTMI), 남은 6건(CGNX 3 · SO 2 · JPM 1)은 **고치기 전에도 틀린 값**
    이며 `dataset.py` 의 자릿수 안전망이 이웃 분기와 견주어 잡습니다.
    """
    if not _TABLE_CELL_RE.match(text, position, position + _FAR_UNIT_AHEAD):
        return 1
    먼 = _가장_가까운_단위(text, position, position)   # 문서 처음까지
    if 먼 < 1_000_000_000 and 먼 != 1:
        return 먼
    return _표_아래_각주에_적힌_단위(text, position)


# 각주 꼴 "(1) In millions, except per share amounts" — 번호가 괄호 안에 있어
# 종전 괄호 패턴(`\([^)]{0,40}?\bin …`)에 안 걸립니다. 닫는 괄호를 못 넘거든요.
_FOOTNOTE_UNIT_RE = re.compile(
    rf"\(\d+\)\s*in\s+{_SCALE_WORDS}\b", re.I
)
_FOOTNOTE_AHEAD = 2000     # 숫자 뒤 몇 자까지 각주를 찾을 것인가


def _표_아래_각주에_적힌_단위(text: str, position: int) -> int:
    """단위 선언이 표 **아래 각주**로 붙는 회사가 있습니다 (150차-AG, 실물 MCHP).

        Net sales                        $1,484.7
        ...
        (1) In millions, except per share amounts and percentages of net sales.

    앞쪽만 보는 규칙이라 이런 문서는 단위를 영영 못 찾았고, MCHP 매출이
    14.8억이 아니라 **1,484.7 달러**로 들어왔습니다.

    여는 조건은 앞의 것과 같습니다 — **표의 칸**이어야 하고(이 함수를 부르는
    자리에서 이미 확인됨), **10억 선언은 안 씁니다.** 여기에 하나를 더
    좁힙니다: 숫자 **바로 뒤 2,000자** 안의 각주만 봅니다. 더 멀리 보면
    다음 표의 각주를 이 표의 것으로 오해합니다.

    원문 1,443건 전수 실측 — 바뀐 것 **7건 / 3종목**뿐이고 사고는 없었습니다.
    MCHP 4건이 전부 정확히 제자리로 갔고(1,484.7 → 14.847억), BMY 2건도
    자릿수가 맞았습니다. 남은 1건(EOG 32)은 고치기 전에도 틀린 값입니다.
    """
    뒤 = text[position:position + _FOOTNOTE_AHEAD]
    최근, 배수 = None, 1
    for pattern, _ in _UNIT_PATTERNS:
        m = pattern.search(뒤)
        if m and (최근 is None or m.start() < 최근):   # 가장 가까운(=앞쪽)
            최근, 배수 = m.start(), _SCALE_MULTIPLIER.get(
                (m.group(1) or "").lower(), 1)
    m2 = _FOOTNOTE_UNIT_RE.search(뒤)
    if m2 and (최근 is None or m2.start() < 최근):
        배수 = _SCALE_MULTIPLIER.get(m2.group(1).lower(), 1)
    return 1 if 배수 >= 1_000_000_000 else 배수


def _parse_number_at(
    text: str, search_from: int, search_len: int = 160
) -> tuple[float, int, int, bool, int] | None:
    """지정한 위치부터 오른쪽으로 훑으며 첫 번째 숫자를 찾아 값으로 바꿉니다.

    반환값: (숫자값, 시작위치, 끝위치, 단위단어가붙었는지, 숫자자체의끝) — 못 찾으면 None

    ⚠️ 창(search_len)이 **숫자 한가운데를 자르던 결함** (87차, 실물 CRM):
       표의 이름과 값 사이가 공백 40칸쯤 벌어지면 160자 창의 끝이 값 위에
       떨어집니다. 실측 — 창 끝이 197, 값 "$1.59" 는 194~198:

           "GAAP diluted net income per share        $1.5|9"
                                            창 끝 ────┘

       그래서 1.59 가 아니라 **1** 로 읽혔고, 그 뒤 남은 ".59" 와 옆 칸의
       전년 값 1.56 까지 후보로 올라와 결국 **전년 값**이 채택됐습니다.

       고치는 방향: 창은 "숫자를 **어디까지 찾아 나설지**"를 정하는 것이지
       "숫자를 어디서 자를지"가 아닙니다. 그래서 찾는 범위는 그대로 두되
       (시작 위치가 창 안이어야 함), 숫자가 창 밖으로 이어지면 **끝까지
       읽습니다.** 창을 그냥 넓히는 것은 답이 아닙니다 — 넓힌 자리에서
       같은 사고가 다시 납니다.
    """
    segment = text[search_from : search_from + search_len + _NUMBER_TAIL]
    match = _NUMBER_RE.search(segment)
    if not match:
        return None
    # 창 밖에서 **시작**한 숫자는 이 자리의 값이 아닙니다.
    # ⚠️ match.start() 가 아니라 match.start("num") 을 봅니다 — 패턴이
    #    앞의 공백·괄호·$ 까지 포함해서 시작하므로, match.start() 는
    #    이름 바로 뒤(공백의 시작)를 가리켜 이 검사가 늘 통과해 버립니다.
    if match.start("num") >= search_len:
        return None

    raw = match.group("num").replace(",", "")
    try:
        value = float(raw)
    except ValueError:
        return None

    # 회계 표기에서 괄호는 음수를 뜻합니다: (1,234) = -1234
    if match.group("paren_open") and match.group("paren_close"):
        value = -value

    # 숫자 뒤에 million 같은 단어가 붙어 있으면 그 배수를 적용
    scale_word = (match.group("scale") or "").lower().strip()
    had_word_scale = scale_word in _WORD_SCALE
    if had_word_scale:
        value *= _WORD_SCALE[scale_word]

    return (
        value,
        search_from + match.start(),
        search_from + match.end(),
        had_word_scale,
        # 숫자 자체가 끝난 위치. 퍼센트 판정은 반드시 이 위치에서 봐야 합니다.
        # match.end()는 뒤따르는 공백·줄바꿈까지 삼켜서, 다음 줄의 "%"를
        # 이 숫자에 붙은 것으로 오해하게 만듭니다.
        search_from + match.end("num"),
    )


def find_labeled_value(
    text: str,
    label_patterns: list[str],
    *,
    is_percent: bool = False,
    apply_table_unit: bool = True,
) -> float | None:
    """"항목 이름"을 찾고 그 오른쪽에 있는 숫자를 값으로 돌려줍니다.

    예) "Non-GAAP operating income    $ 45,123"  →  45123 × 1000(단위 천) = 45,123,000

    label_patterns: 찾을 항목 이름들(정규식). 앞에 있는 것부터 우선 시도합니다.
    is_percent    : 퍼센트 값이면 True (단위 배수를 적용하지 않음)
    """
    # ⚠️ **같은 줄에 있는 숫자를 먼저 찾습니다.**
    #
    #   숫자 탐색 창(160자)이 줄을 넘어가기 때문에, 라벨과 같은 문구가 들어간
    #   **표 제목**에 먼저 걸리면 그 아래 첫 숫자를 값으로 채택했습니다.
    #
    #       RECONCILIATION OF GAAP TO NON-GAAP OPERATING INCOME   ← 여기 걸림
    #       GAAP operating income                     140,000     ← 이 값을 채택
    #       Non-GAAP operating income                 250,000     ← 진짜 값
    #
    #   44% 과소인데 화면에는 "조정표에 적힌 논갭 값을 그대로 사용"이라는
    #   **거짓 근거**가 붙었습니다. 이미 잡은 '가이던스에서 GAAP 을 먼저 집는
    #   버그'와 같은 부류가 본 지표에 그대로 남아 있었습니다.
    #
    #   표든 서술형이든 **진짜 값은 라벨과 같은 줄**에 있습니다. 제목 줄만
    #   숫자가 다음 줄에 있습니다. 그래서 같은 줄을 먼저 훑고, 거기서 아무것도
    #   못 찾았을 때만 예전처럼 줄을 넘어가 찾습니다.
    # 그리고 **분기 숫자를 연간 숫자보다 먼저** 찾습니다.
    #
    #   "4분기 및 연간 실적" 보도자료에는 두 숫자가 함께 들어 있는데, 예전에는
    #   구분하는 장치가 전혀 없어 연간 숫자를 분기 자리에 넣었습니다.
    #   매년 4분기마다 +228% 짜리 가짜 급등이 생겼습니다.
    for avoid_annual in (True, False):
        for same_line_only in (True, False):
            found = _scan_labeled_value(
                text, label_patterns, is_percent, apply_table_unit,
                same_line_only, avoid_annual,
            )
            if found is not None:
                return found
    return None


# 이 말이 라벨 앞에 있으면 그 숫자는 **연간** 수치입니다.
# ("fourth quarter and full year" 보도자료에서 분기 숫자와 연간 숫자를 가릅니다)
_ANNUAL_CONTEXT_RE = re.compile(
    r"(full[-\s]year|fiscal[-\s]year|twelve\s+months|year\s+ended|"
    r"annual\s+(?:revenue|results?)|for\s+the\s+year)",
    re.I,
)
_ANNUAL_LOOKBACK = 90   # 라벨 앞 몇 글자까지 되돌아볼 것인가


def _scan_labeled_value(
    text: str,
    label_patterns: list[str],
    is_percent: bool,
    apply_table_unit: bool,
    same_line_only: bool,
    avoid_annual: bool = False,
) -> float | None:
    """find_labeled_value 의 한 번 훑기 (같은 줄만 볼지 여부를 받습니다).

    ⚠️ **숫자가 가장 가까이 붙은 라벨**을 고릅니다.

    한 문장 안에 같은 라벨이 두 번 나오면 예전에는 앞의 것을 잡았습니다.

        "Management uses non-GAAP operating income to evaluate performance.
         GAAP operating income was $140.0 million, and
         non-GAAP operating income was $250.0 million."
                ↑ 앞의 라벨(설명 문구)이 걸려 뒤의 $140.0 을 채택

    값은 언제나 자기 라벨 바로 뒤에 옵니다. 설명 문구로 쓰인 라벨은 숫자가
    한참 뒤에 있으므로, 거리로 가리면 정확히 걸러집니다.
    """
    best_value, best_gap = None, None
    for pattern_str in label_patterns:
        pattern = re.compile(pattern_str, re.I)
        for label_match in pattern.finditer(text):
            # 맨 이름 "revenue" 앞에 **부문 이름**이 붙어 있으면 그것은
            # 전체 매출이 아니라 **한 조각**입니다 (89차 — 실물 SNOW).
            #
            # SNOW 보도자료의 머리기사는 "**Product revenue** of $1.16
            # billion" 이고, 전체 매출(Total revenue)은 뒤쪽 재무제표에만
            # 있습니다. 이름과 값의 거리로 고르는 규칙이라 가까운 쪽인
            # 조각이 이겼습니다.
            #
            # 88차 전에는 이 값이 단위 없이 1,090.5 로 들어와 뒷단 검사에
            # 걸려 XBRL 의 올바른 값(1,144,969,000)이 살아남았습니다.
            # 88차가 단위를 맞춰 주자 **그럴듯한 크기**가 되면서 올바른
            # 값을 밀어냈습니다 — 88차 ④에 "그럴듯해져서 더 위험하다"고
            # 적어 둔 일이 SNOW 네 분기에서 실제로 일어났습니다.
            #
            # 저장소 원문 실측 — "revenue" 앞 낱말: deferred 298 · product
            # 264 · service(s) 290 · organic 151 · other 143 · interest 104
            # · trading 103 · advertising 94 · subscription 85.
            # 전부 전체 매출이 아닙니다 (deferred 는 아예 부채 항목).
            #
            # 조각만 싣는 회사는 매출이 **없음**이 됩니다 — 조각을 전체라고
            # 하는 것보다 안전합니다 (헌법 1조).
            # ⚠️ 다만 **전체 매출이 그 문서에 있을 때만** 조각을 물립니다.
            #    조각만 싣는 회사가 실제로 있습니다 — 실물 VRTX: 문서 전체에
            #    "Total/Net revenue" 가 **한 번도 안 나오고** "Product revenue
            #    of $2.69 billion" 뿐인데, 그 값이 곧 전체 매출입니다.
            #    무조건 걸러 냈더니 **맞는 값(26.9억)을 잃었습니다.**
            #    "전체가 있으면 조각을 버리고, 조각뿐이면 조각을 쓴다."
            if _SEGMENT_REVENUE_RE.search(
                text[max(0, label_match.start() - _SEGMENT_LOOKBACK):
                     label_match.end()]
            ) and _WHOLE_REVENUE_RE.search(text):
                continue

            if avoid_annual:
                # 라벨 앞쪽(줄을 넘지 않고)에 '연간'을 뜻하는 말이 있으면 건너뜁니다.
                start = label_match.start()
                line_start = text.rfind("\n", 0, start) + 1
                window_start = max(line_start, start - _ANNUAL_LOOKBACK)
                if _ANNUAL_CONTEXT_RE.search(text[window_start:start]):
                    continue

            # 두 이익률의 **차이**를 말하는 자리면 이익률이 아닙니다
            # (77차 — 실물 AMBA). 라벨 앞쪽만 보고, 줄을 넘지 않습니다.
            if is_percent:
                _s = label_match.start()
                _ls = text.rfind("\n", 0, _s) + 1
                # 슬라이드가 눌린 줄은 문장이 아니라 **차트 숫자 나열**입니다
                # (13차에 EPS 경로에 넣은 규칙을 79차에 이익률에도 적용).
                # 실물 PG: "<img …/> • Core gross margin • Core operating
                # margin • Total productivity savings 5% 2% 1% 6% 3% 0% 3%"
                # — 여기서 읽은 값은 그때그때 달라집니다(51.2 · 30.0 · 100.0).
                # PG 실제 매출총이익률은 50% 안팎입니다. 읽을 수 없는
                # 문서에서는 **없음**이 정답입니다.
                # ⚠️ "줄 안에 <img 가 있는가"로 재면 너무 넓습니다 — 눌린
                #    문서는 한 줄이 수천 자라, 멀쩡한 문장까지 걸립니다
                #    (실물 MDB "representing a **74% non-GAAP gross margin**"
                #     을 잃었습니다). 라벨 **바로 앞**에 있을 때만 봅니다.
                #    실측 거리: PG 슬라이드 라벨 84~93자 · MDB 정상 문장 447자.
                if "<img" in text[max(_ls, _s - _SLIDE_LOOKBACK):_s]:
                    continue
                # 이미지 태그가 멀어도, **퍼센트가 줄줄이 이어지면** 차트
                # 계열입니다 (실물 MKSI: "… 46.9% Non-GAAP gross margin
                # **43.8% 45.0% 44.3% 43.3% 44.7% …**"). 문장이 아니라
                # 그래프의 값 나열이라 어느 하나를 이번 분기라 할 수 없습니다.
                # **사이에 낱말이 없는** 퍼센트 3개 이상만 봅니다 —
                # "71% for Q4-22 compared to 72% … and 73%"(CGNX) 처럼
                # 낱말이 끼어 있는 정상 문장은 걸리지 않습니다.
                if _PCT_SERIES_RE.search(text[label_match.end():
                                              label_match.end() + 80]):
                    continue
                if _GM_DIFFERENCE_NEAR_RE.search(
                    text[max(_ls, _s - _GM_DIFFERENCE_LOOKBACK):_s]
                ):
                    continue
                # 전망 문맥이면 실적이 아닙니다 (77차 — 실물 AMBA:
                # "Gross margin on a non-GAAP basis **is expected to be**
                # between 59.0% and 60.5%"). 실제 4분기 값은 59.8% 인데
                # 가까이 붙은 전망값 59.0 을 물고 있었습니다.
                # 전망 말은 라벨 **뒤**에 오므로 같은 줄의 앞뒤를 다 봅니다.
                _le = text.find("\n", label_match.end())
                _le = _le if _le != -1 else len(text)
                if _PCT_FORECAST_RE.search(text[_ls:_le]):
                    continue

            search_from = label_match.end()

            if same_line_only:
                line_end = text.find("\n", search_from)
                limit = (line_end if line_end != -1 else len(text)) - search_from
                if limit <= 0:
                    continue
            else:
                limit = 160

            # 금액을 찾는 중이라면 퍼센트 숫자는 건너뛰고 계속 오른쪽을 봅니다.
            # 예) "Revenue grew 20% year-over-year to $135.0 million"
            #      → 20을 매출로 오인하지 않고 135.0 million을 찾아냅니다.
            for _ in range(4):   # 같은 문장 안에서 최대 4번까지 다시 시도
                remaining = limit - (search_from - label_match.end())
                if remaining <= 0:
                    break
                parsed = _parse_number_at(text, search_from, remaining)
                if parsed is None:
                    break
                value, number_start, number_end, had_word_scale, num_end = parsed

                # 숫자 바로 뒤가 퍼센트 표기인지 확인.
                # ⚠️ "%" 기호뿐 아니라 "percent" 같은 낱말도 봐야 합니다.
                #    ("gross margin of 82 percent" 의 82를 금액으로 채택해
                #     마진이 1억%가 되는 사고가 실제로 재현됐습니다)
                # ⚠️ 줄이 바뀌면 그 뒤는 다른 항목이므로 퍼센트 판정을 멈춥니다.
                #    (다음 줄이 "% of revenue" 로 시작하면 진짜 영업이익을
                #     퍼센트로 오인해 버리는 문제가 있었습니다)
                tail = text[num_end : num_end + 24]
                tail_line = tail.split("\n", 1)[0]
                is_percent_value = bool(_PERCENT_AFTER_RE.match(tail_line))

                if is_percent:
                    # 이름과 숫자 사이에 **금액**이 끼어 있으면 그 줄은
                    # 금액 행이고, 끝의 퍼센트는 **증감률 열**입니다
                    # (82차 — 실물 DELL):
                    #     Gross margin      $5,057   $4,992   **1** %
                    #     % of net revenue   21.1 %   22.0 %
                    # 파서는 저 1 을 매출총이익률 1% 로 저장하고 있었습니다.
                    # 진짜 비율(21.1%)은 **다음 줄**에 있습니다.
                    # 비율만 적힌 줄에는 금액이 없으므로 이 규칙에 안 걸립니다.
                    if _MONEY_BEFORE_RE.search(
                            text[label_match.end():number_start]):
                        search_from = number_end
                        continue
                    # **베이시스 포인트는 언제나 '변화'** 이지 수준이 아닙니다
                    # (81차 — 실물 WMT "Gross margin rate **up 2 bps**",
                    #  "Gross profit rate increased **19 bps**").
                    # 파서는 이 2 를 매출총이익률 2% 로 저장하고 있었습니다.
                    # 월마트 실제 매출총이익률은 24~25% 입니다. 이 회사는
                    # 보도자료에 **수준을 아예 안 적고 변화만** 적으므로
                    # 정답은 **없음**입니다.
                    # 이익률 수준을 bps 로 적는 회사는 없습니다("2,450 bps"
                    # 라고 쓰지 않습니다) — 그래서 bps 는 통째로 거릅니다.
                    if _BPS_UNIT_RE.match(text[num_end:num_end + 20]):
                        search_from = number_end
                        continue
                    # "from A% to B%" 의 A 는 **지난 기간** 값입니다 (78차 —
                    # 실물 BMY "gross margin decreased **from 77.3% to
                    # 76.1%**"). 이번 분기 값은 B 입니다. A 를 건너뛰면
                    # 다음 바퀴에서 B 를 찾습니다.
                    if (is_percent_value
                            and _FROM_BEFORE_RE.search(
                                text[label_match.end():number_start])):
                        search_from = number_end
                        continue
                    # 퍼센트를 찾는 중: %가 붙어 있고 0~100 범위여야 채택
                    if is_percent_value and 0 < value <= 100:
                        gap = number_start - label_match.end()
                        if best_gap is None or gap < best_gap:
                            best_value, best_gap = value, gap
                        break
                    search_from = number_end
                    continue

                # 금액을 찾는 중: 퍼센트 숫자는 건너뜁니다
                if is_percent_value:
                    search_from = number_end
                    continue

                # 숫자 뒤에 단위 단어가 없었다면 표 제목의 단위를 적용
                if apply_table_unit and not had_word_scale:
                    value *= _detect_table_unit(text, number_start)
                gap = number_start - label_match.end()
                if best_gap is None or gap < best_gap:
                    best_value, best_gap = value, gap
                break
    return best_value


# ---------------------------------------------------------------------------
# 보도자료에서 찾을 항목 이름들 (회사마다 표기가 달라 여러 변형을 준비)
# ---------------------------------------------------------------------------

# 논갭 영업이익
LABELS_NONGAAP_OP_INCOME = [
    r"non[-\s]?GAAP\s+(?:income|profit)\s+from\s+operations",
    r"non[-\s]?GAAP\s+operating\s+(?:income|profit)",
    r"adjusted\s+operating\s+(?:income|profit)",
    r"adjusted\s+(?:income|profit)\s+from\s+operations",
    r"operating\s+income\s*\(non[-\s]?GAAP\)",
    r"non[-\s]?GAAP\s+operating\s+results?",
]

# 논갭 매출총이익률(%)
# 77차 실물(AMBA)에서 배운 것: 회사는 **어순을 바꿔** 적습니다 —
#   "Gross margin **on a non-GAAP basis** for the fourth quarter … was 60.7%"
# 아래 이름들은 전부 "non-GAAP" 이 앞에 오는 형태만 잡아서 이 문장을
# 놓쳤고, 그 바람에 훨씬 뒤쪽 각주의 "The difference between GAAP and
# non-GAAP gross margin was **1.4%**" 를 물어 **1.4% 를 매출총이익률로**
# 저장했습니다. AMBA 실제 매출총이익률은 60% 안팎입니다.
LABELS_NONGAAP_GM_PCT = [
    r"non[-\s]?GAAP\s+gross\s+margin(?:\s+percentage)?",
    r"adjusted\s+gross\s+margin",
    r"gross\s+margin\s*\(non[-\s]?GAAP\)",
    r"non[-\s]?GAAP\s+gross\s+profit\s+margin",
    # 어순이 뒤집힌 형태 (실물 AMBA) — 가장 마지막에 둡니다
    r"gross\s+margin\s+on\s+an?\s+non[-\s]?GAAP\s+basis",
    r"gross\s+margin\s+on\s+an?\s+adjusted\s+basis",
]

# 매출총이익률 이름 앞에 이 말이 있으면 **이익률이 아니라 두 이익률의 차이**
# 입니다 (77차 — 실물 AMBA "The difference between GAAP and non-GAAP gross
# margin was 1.4%"). 차이는 1~3% 라 범위 검사(-100~100%)를 그대로 통과해
# 그동안 아무도 못 잡았습니다.
_GM_DIFFERENCE_NEAR_RE = re.compile(
    r"\bdifference\b|\bgap\s+between\b|\bbridge\b|\breconcil", re.I)
_GM_DIFFERENCE_LOOKBACK = 70

# 슬라이드 이미지 태그가 라벨 앞 이만큼 안에 있으면 그 줄은 차트 숫자 나열
_SLIDE_LOOKBACK = 150

# 이름과 숫자 사이의 금액 표기 ($5,057 류) — 그 줄은 금액 행입니다
_MONEY_BEFORE_RE = re.compile(r"[$€£]\s*[\d,]+")

# 베이시스 포인트 단위 — 이익률의 **변화**를 적는 단위입니다 (수준이 아님)
_BPS_UNIT_RE = re.compile(r"\s*(?:basis\s+points?\b|bps\b|bp\b)", re.I)

# 사이에 낱말 없이 이어지는 퍼센트 3개 이상 = 그래프의 값 나열
_PCT_SERIES_RE = re.compile(r"(?:\d+(?:\.\d+)?%\s+){2,}\d+(?:\.\d+)?%")

# "from A% to B%" — A 는 지난 기간, B 가 이번 기간입니다 (78차, 실물 BMY:
# "On a GAAP basis, gross margin decreased from 77.3% to 76.1%").
# 이름과 숫자 사이에 from 이 있고, 숫자 뒤가 "to 숫자" 면 그 숫자는 A 입니다.
# 창을 20자로 좁게 잡습니다 — "benefited from higher pricing and was 76.1%"
# 처럼 from 이 우연히 멀리 있는 문장까지 건너뛰면 안 됩니다.
#
# ⚠️ 처음엔 "숫자 뒤에 to 숫자가 따라오는가"까지 확인했는데, 저장소
#    원문 652건을 훑어 보니 **그 확인이 필요한 사례가 0건**이었습니다
#    (반대로 from~to 형태는 12건). 근거 없는 검사는 넣지 않습니다.
_FROM_BEFORE_RE = re.compile(r"\bfrom\b[^.\n]{0,20}$", re.I)

# 이익률 자리의 **전망** 문맥 (77차 — 실물 AMBA "Gross margin on a non-GAAP
# basis **is expected to be** between 59.0% and 60.5%"). 실제 4분기 값은
# 59.8% 인데 가까이 붙은 전망값 59.0 을 물고 있었습니다.
# ⚠️ 기존 _FORECAST_NEAR_RE 는 "expects/expecting" 만 잡고 **"expected"**
#    를 못 잡습니다. 그 규칙은 EPS 경로도 함께 쓰므로 건드리지 않고,
#    이익률 전용 규칙을 따로 둡니다 (고치는 범위를 좁게).
_PCT_FORECAST_RE = re.compile(
    r"\b(?:expect(?:s|ed|ing)?|anticipat(?:e|es|ed|ing)|guidance|outlook"
    r"|estimat(?:e|es|ed)|project(?:s|ed|ing)?|target(?:s|ed|ing)?"
    r"|reaffirm(?:s|ing|ed)?)\b", re.I)

# GAAP 매출총이익률(%) — 논갭이 없을 때 대체
LABELS_GAAP_GM_PCT = [
    r"GAAP\s+gross\s+margin",
    r"gross\s+margin(?:\s+percentage)?",
]

# 매출
# "revenue" 앞에 붙어 **전체가 아니라 한 조각**임을 뜻하는 말들 (89차).
# 끝($)에 못박아, 지금 걸린 이름이 바로 그 "조각 매출"일 때만 걸립니다 —
# "total revenue" · "net revenue" 는 여기 없으므로 그대로 통과합니다.
_SEGMENT_REVENUE_RE = re.compile(
    r"\b(?:product|services?|subscription|deferred|organic|other|interest"
    r"|trading|advertising|licens\w*|maintenance|hardware|software)\s+"
    r"revenues?\s*$",
    re.I,
)
# 값 없는 **제목 줄** 건너뛰기 (103차 — 87차 ⑤ 로 밀려 있던 마지막 결함).
#
# 실물 CRM 2025-05-28 (조정 EPS 를 1.59 로 읽었는데 참값은 2.58):
#
#     Non-GAAP diluted net income per share            ← 값이 없다 (제목 줄)
#     GAAP diluted net income per share      $1.59     ← 다음 줄의 GAAP 값
#     Plus: ...
#     Non-GAAP diluted net income per share  $2.58     ← 진짜 값 (같은 줄)
#
# 논갭 조정표는 맨 위에 **결과 항목 이름만** 적고 그 아래로 조정 내역을
# 늘어놓은 뒤 마지막에 합계를 적습니다. 그래서 첫 번째 "Non-GAAP …
# per share" 는 값이 없는 **제목**인데, 이름과 숫자의 거리로 고르는
# 규칙이 바로 아래 GAAP 줄의 숫자를 물어 버립니다.
#
# 판별법: 이름과 숫자 **사이에 또 다른 주당 항목 이름이 끼어 있으면**
# 그 이름은 자기 값을 가진 것이 아닙니다. 값은 언제나 자기 이름 바로
# 뒤에 옵니다 (_scan_labeled_value 의 대전제).
#
# ⚠️ **주당 항목 이름일 때만** 봅니다. 매출·영업이익 이름에까지 적용하면
#    "Net income per share of $1.59" 같은 정상 문장을 잃습니다.
_PER_SHARE_LABEL_RE = re.compile(r"per\s+(?:diluted\s+)?share", re.I)
_TITLE_LINE_AHEAD = 200      # 이름 뒤 몇 글자까지 훑어볼 것인가
_FIRST_DIGIT_RE = re.compile(r"\d")

_SEGMENT_LOOKBACK = 30

# "전체 매출"을 뜻하는 이름이 이 문서에 하나라도 있는가 (89차).
# 있으면 조각을 버리고, 없으면 조각이라도 씁니다.
_WHOLE_REVENUE_RE = re.compile(
    r"\b(?:total\s+revenues?|net\s+revenues?|net\s+sales)\b", re.I
)

LABELS_REVENUE = [
    # 150차-AF — **"Total net revenues" 를 맨 앞에 둡니다.**
    #
    # 은행·증권사는 부문 표를 먼저 싣고 합계를 뒤에 놓습니다. 실물 GS:
    #     Net revenues        12,738   ← Global Banking & Markets 부문
    #     Net revenues         4,078   ← Asset & Wealth Management 부문
    #     Net revenues           411   ← Platform Solutions 부문
    #     Total net revenues  $17,227  ← 진짜 전체 매출
    # 이름과 값 사이가 전부 공백뿐이라 거리가 똑같이 0 이고, 그러면
    # **먼저 나온 것**이 이깁니다 — 즉 맨 위 부문이 전체 매출 자리에
    # 들어갔습니다(GS 매출이 172억이 아니라 127억).
    #
    # 종전 맨 앞 이름 "total revenues" 는 사이에 "net" 이 끼어 있어
    # "Total net revenues" 에 안 걸립니다. 그 한 낱말 때문에 놓쳤습니다.
    # 원문 1,443건 실측 — "Total net revenues" 215곳/17종목(SCHW·GS·EXTR·
    # ROKU·PYPL·JPM…), "Total net sales" 53곳/11종목(GNRC·HD·PG·CL…).
    #
    # 이름을 더 좁게 적은 쪽이 언제나 더 확실합니다. 거리가 같을 때
    # 문서 순서로 이기게 두지 말고, **더 좁은 이름을 먼저** 묻습니다.
    r"total\s+net\s+revenues?",
    r"total\s+net\s+sales",
    r"total\s+revenues?",
    r"net\s+revenues?",
    r"^\s*revenues?\b",
    r"\brevenues?\b",
    r"net\s+sales",
]

# 조정 EBITDA — ZETA·TSLA·APP 처럼 논갭 영업이익을 발표하지 않는 회사가 많습니다.
# 이 값에서 감가상각비를 빼면 논갭 영업이익을 역산할 수 있습니다.
LABELS_ADJUSTED_EBITDA = [
    r"adjusted\s+EBITDA",
    r"non[-\s]?GAAP\s+EBITDA",
]

# 논갭 영업비용 (역산에 사용)
LABELS_NONGAAP_OPEX = [
    r"non[-\s]?GAAP\s+(?:total\s+)?operating\s+expenses?",
    r"adjusted\s+(?:total\s+)?operating\s+expenses?",
    r"total\s+operating\s+expenses?\s*\(non[-\s]?GAAP\)",
]

# 손익 항목 이름의 공통 부분.
# 표에서는 "net income (loss) per diluted share" 처럼 괄호로 흑자·적자를 함께
# 표기하는 것이 표준입니다. 그 괄호를 허용해야 표 형식 보도자료를 읽을 수 있습니다.
# "net income / earnings per share" 처럼 순이익과 EPS 를 한 항목으로 묶은
# 대조표 제목(실물: AMD)도 잡도록 빗금 꼬리를 허용합니다.
_PROFIT_WORD = (
    r"(?:income|earnings|loss)(?:\s*\((?:loss|income)\))?"
    r"(?:\s*/\s*(?:income|earnings|loss))?"
)

# 조정(논갭) 주당순이익 — 앞에 있는 것부터 우선 시도합니다.
# ⚠️ "non-GAAP **diluted** net income per share" 처럼 diluted 가 앞에 오는
#    어순(실물: CRDO·샌디스크)이 있어 (?:diluted\s+)? 를 앞자리에도 둡니다.
LABELS_ADJUSTED_EPS = [
    # 수식어(net·diluted)는 회사마다 순서가 다릅니다 — "diluted net income"
    # (CRDO·샌디스크), "net diluted loss"(SEDG). 순서 무관 최대 2개 허용.
    # 적자를 "(loss)" 로 묶는 표기(실물: STX)와 케이맨 법인의 "ordinary share"
    # (실물: AMBA)도 허용합니다. (9차 감사에서 실물 4형식 보강)
    #
    # 150차-AZ — 세 곳을 넓힙니다. 전부 GAAP 쪽 이름에는 이미 있던 것들인데
    # 조정 쪽에만 빠져 있었습니다(반쪽만 넣은 자리):
    #   ① **common share** — 실물 EL "Adjusted diluted net earnings
    #      **per common share** increased to $.32" (EL 5건).
    #      GAAP 쪽 `_SHARE` 는 common|ordinary 를 둘 다 받고 있었습니다.
    #   ② **operating** 수식어 — 실물 AMP "Adjusted **operating** earnings
    #      per diluted share increased 7 percent to $9.11". 보험·자산운용사가
    #      쓰는 표준 표현입니다 (AMP·MET).
    #   ③ **쉼표** — 실물 UTHR 표 "Non-GAAP earnings**,** per diluted
    #      share(1)   $3.34" (UTHR 4건).
    rf"non[-\s]?GAAP\s+(?:(?:net|diluted|operating)\s+){{0,3}}\(?{_PROFIT_WORD}\)?"
    r"[\s,]+per\s+(?:diluted\s+)?(?:common\s+|ordinary\s+)?share",
    rf"adjusted\s+(?:(?:net|diluted|operating)\s+){{0,3}}\(?{_PROFIT_WORD}\)?"
    r"[\s,]+per\s+(?:diluted\s+)?(?:common\s+|ordinary\s+)?share",
    # MPWR 형 대조표: "Non-GAAP net income per share:" 제목 아래 Basic/Diluted
    # 줄이 따로 옵니다. Diluted 바로 뒤의 값을 집습니다.
    # (표 여백이 넓어 창을 600자로 둡니다 — Basic 줄 하나를 건너뛰는 거리)
    r"non[-\s]?GAAP\s+net\s+income\s+per\s+share:[\s\S]{0,600}?\bDiluted\b",
    # AMBA 형 문장: "non-GAAP net profit of $5.5 million, or earnings per
    # diluted ordinary share of $0.13" — 논갭 문맥이 같은 문장 앞쪽에 있음.
    # 금액($5.5)의 소수점과 줄바꿈이 사이에 끼므로 문장 종료(.)로 끊지 않고
    # 거리로만 제한합니다.
    # 여백 패딩(공백 60~70자 + 줄바꿈)이 문장 중간에 끼는 실물이 있어 220자.
    # 150차-AT — 적자 분기의 같은 문장 꼴도 함께 받습니다. 실물 AMBA:
    #   "non-GAAP net **loss** of $5.9 million, or **loss** per diluted
    #    ordinary share of $0.13"
    # 앞의 이름은 "net profit … or **earnings** per …" 만 받아 적자 분기
    # 6건을 놓쳤습니다. 이름에 loss 가 들어가면 find_eps_value 의
    # says_loss 규칙이 부호를 알아서 뒤집습니다.
    r"non[-\s]?GAAP\s+net\s+(?:profit|loss|income)[\s\S]{0,220}?"
    r"\bor\s+(?:earnings|loss|income)\s+per\s+diluted"
    r"\s+(?:ordinary\s+|common\s+)?share",
    # 150차-AP ① — 수식어가 **두 개 겹치는** 줄임말: "non-GAAP **diluted net**
    # EPS"(실물 HPE). 아래 두 줄은 diluted 하나만 허용해서 이 꼴을 놓쳤습니다.
    r"non[-\s]?GAAP\s+(?:(?:diluted|net)\s+){0,2}EPS",
    r"adjusted\s+(?:(?:diluted|net)\s+){0,2}EPS",
    # 150차-AT — **제목 줄 아래 GAAP/논갭 하위 항목** (실물 HPE):
    #     •Diluted net earnings per share (“EPS”):
    #     ◦GAAP of $0.44, up $1.26 from the prior-year period …
    #     ◦Non-GAAP(1) of $0.79, up $0.41 …          ← 우리가 쓰는 값
    # GAAP 쪽은 76차에 넣은 "net EPS" 이름이 잡고 있었는데 **논갭 쪽 짝이
    # 없어서** 조정 EPS 가 비어 있었습니다(HPE 5건). 제목 줄에서 논갭
    # 항목까지를 이름으로 삼습니다(MPWR 형과 같은 방식). GAAP 항목 한 줄을
    # 건너뛸 거리만 줍니다(200자).
    r"(?:diluted\s+)?net\s+(?:earnings|income|loss)\s+per\s+share[^\n]{0,20}:"
    r"[\s\S]{0,200}?non[-\s]?GAAP\s*(?:\(\d+\))?\s+of\b",
    # 150차-AT — 이름 **가운데에 수식어가 길게** 끼는 표기.
    # 실물 CGNX: "Net income per **diluted weighted-average common and
    # common-equivalent** share (Non-GAAP)   $0.21"
    # 위 줄임 표기는 "per (diluted) share" 가 붙어 있기를 요구해 놓쳤습니다
    # (이 한 꼴로 CGNX 9건). 낱말·붙임표·공백만 허용하고 60자로 묶습니다 —
    # "Per share impact of Non-GAAP adjustments identified above" 같은
    # **영향 줄**은 이름이 share 로 끝나지 않아 걸리지 않습니다.
    r"(?:earnings|income|loss)\s+per\s+[a-z\-\s]{0,60}?share\s*\(non[-\s]?GAAP\)",
    # ------------------------------------------------------------------
    # 150차-AP ② — **회사가 자기 논갭 지표를 "core" 라고 부르는 경우.**
    # 실물 BA(보잉): "GAAP loss per share of ($0.11) and **core loss per
    # share (non-GAAP)*** of ($0.20)". PG 도 "Core EPS" 라고 씁니다.
    # `_NONGAAP_NEAR_RE` 는 이미 core 를 논갭으로 알고 있었는데(그래서 GAAP
    # 자리에 들어가는 것은 막고 있었는데), **조정 EPS 이름 목록에는 core 가
    # 없어서** 그 값이 어디에도 안 들어가고 사라지고 있었습니다.
    # 저장원문 실측 — 이 꼴로 조정 EPS 를 놓친 발표가 BA 만 9건입니다.
    rf"(?<!non-)(?<!non )\bcore\s+(?:(?:net|diluted)\s+){{0,2}}\(?{_PROFIT_WORD}\)?"
    r"\s+per\s+(?:diluted\s+)?(?:common\s+|ordinary\s+)?share",
    # ⚠️ **맨 "core EPS" 는 넣지 않습니다.** 넣어 봤더니 원문 전수에서 값이
    #    네 곳 새로 생겼는데 **넷 다 틀렸습니다** — PG·PEP 의 발표자료는
    #    "Core EPS **Growth** +19%" 처럼 EPS 가 아니라 **증감률**의 이름으로
    #    이 말을 쓰기 때문입니다(PG 0.40·5.35·0.10, PEP 8.16 — 진짜 값은
    #    1.5 안팎). 반면 "core earnings per share were $1.83" 꼴(위 이름)은
    #    같은 전수에서 PG 세 분기가 전부 맞았습니다(1.83·1.84·1.52).
    #    "없음"이 "틀림"보다 안전하므로 맞는 쪽만 남깁니다.
    # ------------------------------------------------------------------
    # 150차-AP ③ — **"…였고, 주당 얼마" 꼴** (값이 이름보다 **앞**에 옵니다).
    # 실물:
    #   UCTT "On a non-GAAP basis, … net income was $14.5 million
    #         **or $0.31 per diluted share**"
    #   MCHP "Non-GAAP net income … was $252.8 million, **or $0.44 per
    #         diluted share**"
    #   ISRG "non-GAAP* net income … was $901 million, **or $2.50 per
    #         diluted share**"
    # 파서는 이름 **뒤**에서 숫자를 찾으므로, 이름을 "…or" 까지로 끊고
    # 그 뒤의 숫자를 읽게 합니다. 뒤에 "per (diluted) share" 가 실제로
    # 오는지는 **미리보기(lookahead)** 로 확인합니다 — 확인하지 않으면
    # "or" 뒤의 아무 숫자나 물게 됩니다.
    #
    # ⚠️ 이 이름은 **목록의 맨 끝**에 둡니다. 앞의 구체적인 이름들이 먼저
    #    이기고, 아무것도 못 읽었을 때만 이 마지막 수단이 나섭니다.
    # 이름과 이익 낱말 사이에 다른 말이 끼는 실물이 많아 거리로만 묶습니다:
    #   "non-GAAP basis, gross margin was 16.5% … and **net income** was"(UCTT)
    #   "non-GAAP***** net income attributable to Intuitive Surgical, Inc."(ISRG)
    # 문장 끝(". ")으로 끊으려 했으나 회사 이름의 "Inc. was" 에 걸려 못 씁니다.
    # ⚠️ "non-**core**" 를 core 로 읽으면 안 됩니다 — 붙임표도 낱말 경계라
    #    `\bcore\b` 만으로는 걸립니다. 실물 PCG: "GAAP results include
    #    **non-core** items … totaled $3.3 billion after-tax, **or $2.66 per
    #    share, for the year**" → 연간 제외액 2.66 을 조정 EPS 로 읽었습니다.
    r"(?:non[-\s]?GAAP|adjusted|(?<!non-)(?<!non )\bcore\b)[^\n]{0,120}?"
    r"\b(?:income|profit|earnings|loss)\b[^\n]{0,140}?\bor\b"
    r"(?=\s*\$?\s*\(?-?\d[\d.,]*\)?\s*per\s+(?:diluted\s+)?"
    r"(?:common\s+|ordinary\s+)?share)",
]

# GAAP 주당순이익 — 조정 EPS 와 짝지어 '이익의 질'을 보는 데 씁니다.
# ⚠️ "non-GAAP" 안에도 "GAAP" 글자가 들어 있습니다. 뒤쪽 패턴들은 아무 수식어 없이
#    매칭되므로, 실제 채택할 때 앞을 되돌아보며 논갭 표기를 걸러 냅니다
#    (find_eps_value 의 exclude_nongaap).
# 50차 감사 — 은행·금융은 "per **common** share" 로 씁니다.
# 실물: GS "Diluted earnings per common share (EPS)1 was $20.98" ·
#       WFC "Diluted earnings per common share 2.00" ·
#       COF "$4.73 per diluted common share".
# 이 한 낱말이 없어서 대형 은행 5곳이 통째로 측정에서 빠져 있었습니다.
_SHARE = r"(?:common\s+|ordinary\s+)?share"

LABELS_GAAP_EPS = [
    rf"(?<!non-)(?<!non )GAAP\s+(?:(?:net|diluted)\s+){{0,2}}\(?{_PROFIT_WORD}\)?"
    rf"\s+per\s+(?:diluted\s+)?{_SHARE}",
    r"(?<!non-)(?<!non )GAAP\s+(?:diluted\s+)?EPS",
    rf"net\s+{_PROFIT_WORD}\s+per\s+(?:diluted\s+)?{_SHARE}",
    rf"diluted\s+{_PROFIT_WORD}\s+per\s+{_SHARE}",
    rf"{_PROFIT_WORD}\s+per\s+diluted\s+{_SHARE}",
    # 47차 감사 — "earnings per share of $2.14" 처럼 **아무 수식어 없이**
    # 쓰는 회사가 있습니다(TXN·은행권 등). 위 다섯 개는 전부 GAAP·net·
    # diluted 중 하나를 요구해서 이 문장을 놓쳤고, 그 결과 잣대 사다리를
    # 못 넘어 **측정에서 통째로 빠진 종목이 24개**였습니다.
    # 가장 마지막에 둡니다 — 앞의 구체적인 이름이 먼저 이깁니다.
    # 'adjusted/non-GAAP earnings per share' 는 exclude_nongaap 의 같은 줄
    # 되돌아보기가 막아 줍니다 (이 목록은 항상 그 옵션과 함께 쓰입니다).
    rf"{_PROFIT_WORD}\s+per\s+{_SHARE}",
    # 76차 — "•Diluted net EPS: ⏎◦GAAP of $0.49" (실물 HPE). 구역 제목으로
    # 연간/분기를 나누는 회사는 분기 쪽 이름을 **줄여서** 적습니다.
    # 위 이름들은 전부 "per share" 를 요구해 이 줄을 놓쳤고, 그 바람에
    # 연간값(1.54)이나 배당금(0.13)이 EPS 자리에 들어왔습니다.
    # "net EPS" 로 좁혀 잡습니다 — 맨 EPS 는 각주 표시까지 뭅니다.
    r"(?:diluted\s+)?net\s+EPS\b",
    # 50차 — "EPS of $12.19, or $13.91 as adjusted" (실물 BLK).
    # 이름 없이 EPS 만 쓰는 회사가 있습니다. **"EPS of" 형태로만** 좁혀
    # 잡습니다 — 맨 EPS 는 각주 표시(GS "EPS1")나 "EPS Impact"(COF) 같은
    # 문구까지 물어 엉뚱한 숫자를 집습니다.
    r"(?<!non-)(?<!non )\bEPS\s+of\b",
]

# 매칭된 이름 앞쪽에 이 말이 있으면 GAAP 이 아니라 논갭 수치입니다.
# ⚠️ 바로 앞 한 낱말만 봐서는 안 됩니다. "Non-GAAP net income per diluted share" 에서
#    'income per diluted share' 부분만 매칭되면 그 앞은 "Non-GAAP net " 이라
#    'non-GAAP' 이 낱말 하나 건너에 있습니다. 그래서 범위를 두고 훑습니다.
# 83차 — "Core EPS" 도 논갭입니다 (실물 PG). P&G 는 자기네 논갭 지표를
# "Core" 라고 부릅니다: "Diluted EPS $1.63 … **Core EPS $1.59**".
# 이 말을 모르면 논갭 값이 GAAP 칸에 들어갑니다.
_NONGAAP_NEAR_RE = re.compile(r"non[-\s]?GAAP|adjusted|\bcore\b", re.I)
# 150차-AS — 40 → 120. 대신 창 안에 **문장 끝**이 있으면 거기서부터만
# 봅니다(위 find_eps_value 안 주석). 글자 수만으로 재면 두 실물이 서로
# 반대 방향으로 깨지기 때문입니다:
#   SCHW "…costs, **adjusted** (1) net income and diluted common
#         **earnings per share** … $.74"   ← 51자 앞, 같은 문장 → 막아야 함
#   CRDO "…**non-GAAP** net income of $208.8 million. **GAAP diluted
#         net income per share** of $0.82" ← 55자 앞, 앞 문장 → 살려야 함
# 차이는 글자 수가 아니라 **문장**입니다.
# GAAP 을 찾을 때 이름 **앞**에서 보는 말 — 위 목록에 "excluding" 을 더합니다.
# "**Excluding** tax reform benefits, diluted earnings per share were $4.31"
# (실물 KSS)은 정의상 **GAAP 이 아닌** 수치입니다. 이 낱말은 이름 뒤(값과
# 이름 사이)에서는 반대로 **막으면 안 되므로**(VZ "adjusted EPS, excluding
# special items, of $1.19" 가 진짜 값) 여기서만 씁니다.
_NONGAAP_BEFORE_RE = re.compile(
    r"non[-\s]?GAAP|adjusted|\bcore\b", re.I)

# 줄(글머리표 항목)이 "**Excluding** …" 으로 **시작하면** 그 줄의 주당 값은
# 정의상 GAAP 이 아닙니다 (실물 KSS: "• Excluding tax reform benefits,
# diluted earnings per share were $4.31" — 게다가 연간값입니다).
# ⚠️ **줄머리에서만** 봅니다. 문장 중간의 excluding 까지 막았더니 실물
#    PYPL 의 진짜 GAAP 값 세 분기가 죽었습니다("…; **excluding** eBay,
#    revenue grew 15% … • GAAP EPS of $0.43").
_EXCLUDING_HEAD_RE = re.compile(r"^[\s•·◦\-–*]*excluding\b", re.I)

_NONGAAP_LOOKBACK = 120  # 이름 앞 몇 글자까지 (문장 끝이 있으면 거기까지)

# 문장 끝 — 마침표 뒤에 공백과 대문자가 오는 자리. 숫자의 소수점("1.00")이나
# 약자("U.S. ")를 문장 끝으로 오인하지 않도록 **앞뒤를 함께** 봅니다.
_SENTENCE_END_RE = re.compile(r"(?<=[a-z0-9%)\]])\.\s+(?=[A-Z])")

# 배당금 문맥 — "$0.13 per share" 앞에 배당 이야기가 있으면 EPS 가 아닙니다
# (76차 — 실물 HPE "regular cash dividend of $0.13 per share").
_DIVIDEND_NEAR_RE = re.compile(r"\bdividends?\b|\bdistribution\b", re.I)

# 전망·목표 문맥 — 이 낱말들이 EPS 이름 주변에 있으면 그 자리는
# 분기 **실적**이 아니라 전망·연간 목표입니다 (사고 16 — 실물:
# QRVO "expect … approaching $7.00" · TER "2024 earnings model to …
# $8.00" · TTMI "to approach $5.00"). 그 이름 자리는 건너뛰고 다음
# 자리(대개 표의 진짜 분기 값)를 계속 찾습니다.
_FORECAST_NEAR_RE = re.compile(
    # 150차-AP — "expect**ed**" 가 빠져 있었습니다. 실물 PYPL 2024-02-07:
    #   "• Non-GAAP earnings per diluted share **expected** to be in line
    #    with $5.10 in the prior year."
    # 이 한 낱말이 없어 연간 전망치 5.10 이 4분기 조정 EPS 로 들어왔습니다
    # (진짜 값은 같은 글의 1.48).
    # 150차-AS — "Approx." (전망표의 표준 약자). 실물 CCL 2024-12-20 의
    # 전망표: "Adjusted earnings per share - diluted (a)  Approx. $0.00
    #          Approx. $1.70" — 둘 다 **전망**이다(분기·연간).
    # 낱말 전체("approximately")는 실적 문장에도 흔히 쓰여 넣지 않고,
    # **약자만** 넣습니다.
    r"\bapprox\.?(?=\s|$)"
    r"|\b(?:expects?|expected|expecting|anticipates?|approach(?:es|ing)?"
    r"|targets?|targeting|forecasts?|estimates?|guidance|outlook"
    r"|earnings\s+model)\b",
    re.I,
)
_FORECAST_BACK = 80      # 이름 앞
_FORECAST_AHEAD = 80     # 이름 뒤 (값과 이름 사이의 to approach 류)

# 주식 수 행 (74차 — 실물 원문으로 확증)
# ---------------------------------------------------------------------------
# 손익계산서 맨 아래에는 EPS 바로 다음에 **주식 수** 표가 붙습니다:
#
#   Net loss per share attributable to UCT common stockholders:
#   Basic                                   $(0.40)      $(0.11)
#   Diluted                                 $(0.40)      $(0.11)
#   Shares used in computing net loss per share:      ← 이 줄도 "per share" 다!
#   Basic                                     45.3         45.1
#   Diluted                                   45.3         45.1
#
# 파서가 아래쪽 이름을 물어 **45.3 을 EPS 로** 읽었고, 이름에 loss 가 있어
# 부호까지 뒤집어 **−45.30** 이 됐습니다. UCTT 는 세 해 연속 같은 사고를
# 냈고(−45.30 · −45.10 · −44.60), 주식 수는 해마다 비슷하니 오염도 비슷한
# 크기로 되풀이됐습니다.
#
# 이름 **앞쪽**에 "shares used in computing" · "weighted average shares" 가
# 있으면 그 자리는 EPS 가 아니라 주식 수입니다. 건너뜁니다.
_SHARE_COUNT_NEAR_RE = re.compile(
    r"(?:shares?\s+(?:used|outstanding)|weighted[-\s]average"
    r"|number\s+of\s+shares)", re.I,
)
_SHARE_COUNT_LOOKBACK = 60

# 연간(누적) 값이라고 **원문이 직접 말하는** 자리 (74차 — 실물로 확증)
# ---------------------------------------------------------------------------
# 결산 분기 보도자료에는 분기와 연간이 나란히 실리고, 연간 쪽이 헤드라인인
# 회사가 많습니다. 원문이 그것을 글자로 적어 두므로 추측할 필요가 없습니다:
#
#   GS  "Diluted earnings per common share (EPS) was **$59.45 for the year
#        ended December 31, 2021** … and was **$10.81 for the fourth quarter**"
#        → 진짜 분기값은 10.81 인데 59.45 를 읽었습니다.
#   VZ  "**Full-year** 2022 … adjusted EPS … of **$5.18**"
#        → 분기 실제는 1.2 안팎입니다.
#
# 이름 앞(full year/fiscal year) 또는 값 바로 뒤(for the year ended /
# for the twelve months ended)에 이 말이 있으면 그 자리는 분기가 아닙니다.
# **추측이 아니라 원문이 스스로 밝힌 사실**이라 안전합니다.
# ⚠️ 이름 **앞쪽 되돌아보기**에 쓰는 것은 좁게 잡아야 합니다.
#    "fiscal year 2022" 는 연간값 표시가 아니라 **비교 대상**으로 늘 나옵니다:
#      "GAAP net income per share of $1.13 compared to $1.14 in the fourth
#       quarter of **fiscal year 2022**; non-GAAP net income per share of $1.54"
#    이것까지 연간이라 보면 진짜 분기값 1.54 를 버립니다 (실물 NTAP —
#    74차 전수 비교에서 실제로 그렇게 됐고, 0.08(환율 영향)을 물었습니다).
_ANNUAL_BEFORE_RE = re.compile(
    r"\bfull[-\s]?year\b|\bfor\s+the\s+(?:full\s+)?year\b"
    r"|\btwelve\s+months\b|\bfiscal\s+year\s+ended\b"
    # "annual" (119차 — 실물 ADBE): "reported **annual** GAAP diluted
    # earnings per share of $3.38 and non-GAAP diluted earnings per share
    # of $4.31" — 조정 EPS 이름 앞 52자에 annual 이 있는데 위 낱말들에는
    # 없어 연간값 4.31 을 분기로 물었습니다. \bannual\b 이므로
    # annualized(연율화)는 걸리지 않습니다.
    r"|\bannual\b"
    # "FY 2025"/"FY2025" (119차 — 실물 AXP 머리글: "FY 2025 EARNINGS PER
    # SHARE ROSE TO $15.38"). FY 는 fiscal year 의 표준 약자입니다.
    r"|\bFY\s*20\d{2}\b",
    re.I,
)
# 줄머리에서는 넓게 봐도 됩니다 — 줄이 "Fiscal year 2023 …" 으로 **시작**
# 하면 그 줄은 연간 실적 줄입니다 ("Fourth quarter of fiscal year 2023 …"
# 처럼 분기로 시작하는 줄은 맨 앞이 안 맞아 걸리지 않습니다).
_ANNUAL_LINE_HEAD_RE = re.compile(
    r"\bfull[-\s]?year\b|\bfor\s+the\s+(?:full\s+)?year\b"
    r"|\btwelve\s+months\b|\bfiscal\s+year\s+(?:20\d{2}|ended)\b",
    re.I,
)
_ANNUAL_AFTER_RE = re.compile(
    r"^\s*(?:in\s+|of\s+)?for\s+(?:the\s+)?(?:full\s+)?"
    r"(?:year|twelve\s+months|fiscal\s+year)\b"
    # "…of $59.45 **for 2021**" — 헤드라인이 연도만 적는 형식 (실물 GS)
    r"|^\s*for\s+(?:fiscal\s+)?20\d{2}\b",
    re.I,
)
_ANNUAL_BACK = 60        # 이름 앞 몇 글자까지 (줄을 넘지 않습니다)
_ANNUAL_AHEAD = 40       # 값 뒤 몇 글자까지

# 이름과 값 **사이**의 연간 표시 (119차 — 실물 SWKS·MCHP):
#   "Non-GAAP diluted earnings per share **for fiscal year 2018** was $7.22"
#   "Non-GAAP earnings per diluted share **for the fiscal year ended
#    March 31, 2025** were $1.31"
# 이름 앞도 값 뒤도 아니라 **사이**에 있어 기존 두 가드를 다 빠져나갔습니다.
# ⚠️ 사이 구간에 quarter 가 함께 있으면(비교 문구) 연간으로 보지 않습니다 —
#    74차 NTAP 반례("in the fourth quarter of fiscal year 2022")와 같은 꼴.
_ANNUAL_GAP_RE = re.compile(
    r"\bfor\s+(?:the\s+)?fiscal\s+(?:year\b|20\d{2}\b)"
    r"|\bfiscal\s+year\s+ended\b"
    r"|\bfor\s+the\s+(?:full\s+)?year\b|\bfull[-\s]?year\b"
    r"|\btwelve\s+months\b",
    re.I,
)

# 이름과 값 **사이**에 "제외한다"는 말 — 그 숫자는 잣대가 아니라 빼낸 항목입니다.
# (150차-AP — 실물 HPE "non-GAAP diluted net EPS estimate **excludes** net
#  after-tax adjustments of approximately $1.28 per diluted share")
#
# ⚠️ **"excluding" 은 넣으면 안 됩니다.** 처음에 exclude/excludes/excluded/
#    excluding 을 다 넣었더니 원문 전수에서 멀쩡한 값 8건이 죽었습니다:
#      VZ  "adjusted EPS1, **excluding** special items, of $1.19"   ← 진짜 값
#      IFF "Adjusted EPS **excluding** amortization1 was $0.72"     ← 진짜 값
#    "excluding …" 은 잣대 이름을 꾸미는 **단서**이고, "excludes …" 는
#    "이 잣대는 무엇무엇을 뺀다"는 **다른 문장**입니다. 뒤엣것만 막습니다.
# 150차-AS — "**영향**" 도 같은 갈래입니다. 실물 PYPL 2023-11-01:
#   "GAAP EPS **included a negative impact of** approximately $0.11 on
#    PayPal's strategic investments"
#   → 0.11 은 잣대가 아니라 그 안에 든 영향의 크기입니다.
_EXCLUDE_GAP_RE = re.compile(r"\bexcludes?\b|\bimpacts?\b|\bimpacted\b", re.I)

# 멀리 있는 연간 표시 (150차-AP — 위 find_eps_value 안 주석 참조).
# 좁은 낱말만 씁니다: "그 해 동안"을 뜻하는 말.
_ANNUAL_FAR_RE = re.compile(
    r"\bfor\s+the\s+(?:fiscal\s+|full\s+)?year\b"
    r"|\bfull[-\s]?year\b|\bfiscal\s+year\s+ended\b|\btwelve\s+months\b",
    re.I,
)
_ANNUAL_FAR_BACK = 200   # 이름 앞 몇 글자까지 (줄을 넘지 않습니다)

# 값 **뒤**에 붙는 연간 표시. 뒤쪽에서는 "for 2019" 같은 맨 연도도 연간을
# 뜻합니다(실물 PCG "or $3.93 per share, **for 2019**"). 앞쪽 되돌아보기에
# 쓰면 비교 문구까지 물어 위험하므로 **뒤쪽 전용**으로 따로 둡니다.
# 값 **바로 앞**에 붙은 비교 문구 — "compared to $1.15" 의 1.15 는 견주는
# 대상(전년)이지 이번 값이 아닙니다 (150차-AP).
#
# ⚠️ **아주 짧게** 겁니다(24자). 처음에는 "이름과 값 **사이** 어디든 비교
#    문구가 있으면 포기"로 넓게 걸었더니, 기존 시험 두 개가 빨간 불로
#    잡았습니다:
#      GS   "…was $59.45 for the year ended … **compared with** $24.74 …,
#            and was $10.81 for the fourth quarter" → 진짜 값 10.81 을 잃음
#      NTAP "Non-GAAP net income per share **compared to** the fourth quarter
#            of fiscal year 2022 was $1.54"        → 진짜 값 1.54 를 잃음
#    두 경우 모두 비교 문구와 **진짜 값 사이에 다른 말**이 끼어 있습니다.
#    막아야 할 것은 "compared to $숫자" 처럼 **바로 붙은** 꼴뿐입니다.
_COMPARED_BEFORE_RE = re.compile(
    r"\b(?:compared\s+(?:with|to)|versus|vs\.?)\s*\$?\s*$", re.I)
_COMPARED_BACK = 24

# 값 **바로 뒤**의 "같은 기간·전년" — 그 값도 견주는 대상입니다.
# 실물 PCG "…, or $3.93 per share, **during the same period in 2019**"
_PRIOR_PERIOD_AFTER_RE = re.compile(
    r"\b(?:during|in)\s+the\s+same\s+period\b|\ba\s+year\s+ago\b"
    r"|\byear[-\s]ago\b|\bprior[-\s]year\s+period\b|\bin\s+the\s+prior\s+year\b",
    re.I,
)

# 값 바로 뒤의 "at the midpoint of guidance" 꼴 (150차-AT, 위 주석 참조)
_GUIDANCE_TAIL_RE = re.compile(
    r"\s*at\s+the\s+(?:mid|high|low)[-\s]?point\b", re.I)

_ANNUAL_AFTER_FAR_RE = re.compile(
    r"\bfor\s+the\s+(?:fiscal\s+|full\s+)?year\b"
    r"|\bfull[-\s]?year\b|\bfiscal\s+year\s+ended\b|\btwelve\s+months\b"
    r"|\bfor\s+(?:fiscal\s+)?20\d{2}\b",
    re.I,
)

# 값 뒤가 "to $숫자" 면 **전망 범위**입니다 (119차 — 실물 LOW·LLY·NEE):
#   "Adjusted diluted EPS of approximately **$11.80 to $11.90**" (연간 전망)
#   "Earnings per share (non-GAAP)  **$5.60 to $5.70**" (전망 조정표)
# 실적을 "X to Y" 범위로 적는 회사는 없습니다 — 84차의 "X - Y" 범위
# 가드와 같은 근거이며 to 표기만 빠져 있었습니다.
_RANGE_TO_AFTER_RE = re.compile(r"^\s*to\s*\$?\d")
# 범위의 **뒤끝**: 숫자 앞이 "$소수 to $" 면 그 숫자는 범위의 위끝입니다
# ("$5.60 to $5.70" 에서 앞끝만 막으면 뒤끝 5.70 을 뭅니다 — 84차의
#  "X - Y" 범위 가드가 앞·뒤 양끝을 막는 것과 같은 구조).
# ⚠️ 앞이 **달러 소수**일 때만 겁니다. 처음에 `숫자 to $` 로 넓게 걸었더니
#    성장 문구 "increased 16% for Q3 **to $1.61**"(실물 DIS)의 Q3 의 3 이
#    걸려 **진짜 분기값을 버리고 전년값 1.39 를 물었습니다** (전수 채점에서
#    발각). "% to $"·"Q3 to $" 는 범위가 아닙니다.
_RANGE_TO_BEFORE_RE = re.compile(r"\$\d+\.\d+\s*to\s*\$?\s*$", re.I)
# "from $X to $Y" 는 범위가 아니라 **변화 문구**입니다 (실물 AMGN:
# "Non-GAAP EPS remained relatively unchanged **from $5.31 to $5.29** for
# the fourth quarter" — 5.31 이 전년, **5.29 가 진짜 이번 분기값**).
# 뒤끝 가드가 이 Y 까지 죽이면 옳은 값을 잃으므로, from 이 이끄는 짝의
# 뒤끝은 살립니다. 앞끝(X)은 아래 _FROM_BEFORE_RE 가 전년값으로 거릅니다.
_FROM_RANGE_RE = re.compile(r"\bfrom\s*\$\d+\.\d+\s*to\s*\$?\s*$", re.I)
# 값 바로 앞이 "from $" 면 그 숫자는 **비교 원점(전년·직전)**입니다
# ("improved to $1.61 from $1.39" 의 1.39, "from $5.31 to $5.29" 의 5.31).
_FROM_BEFORE_RE = re.compile(r"\bfrom\s*\$?\s*$", re.I)

# 줄이 "Fiscal 2018 …" 로 시작하면 그 줄은 연간 줄입니다 (119차 — 실물
# SYNA: "• Fiscal 2018 revenue of $1.63 billion, … non-GAAP net income per
# diluted share of $4.05"). 기존 줄머리 가드는 "fiscal year 20xx" 만 알고
# "Fiscal 20xx"(year 생략)를 몰랐습니다. quarter 가 같은 줄머리에 있으면
# ("Fiscal 2018 fourth quarter …") 분기 줄이므로 건너뛰지 않습니다.
_FISCAL_HEAD_RE = re.compile(r"^fiscal\s+20\d{2}\b", re.I)
_FISCAL_HEAD_SPAN = 60   # 줄머리에서 이만큼 안에서 quarter 여부를 함께 봅니다
_ANNUAL_LINE_HEAD = 24   # 줄머리에서 이만큼 안에 "Full-year" 가 있으면 그 줄은 연간

# 구역 제목으로 연간/분기를 가르기 (76차 — 실물 HPE 로 확증)
# ---------------------------------------------------------------------------
# 보도자료는 흔히 **구역 제목**으로 연간과 분기를 나눠 적습니다:
#
#   Fiscal 2023 Full-Year Financial Results          ← 구역 제목 ①
#   •Diluted net earnings per share ("EPS"):
#   ◦GAAP of $1.54, up 133% …                        ← 연간값
#   …
#   Fourth Quarter Fiscal 2023 Financial Results     ← 구역 제목 ②
#   •Diluted net EPS:
#   ◦GAAP of $0.49, up 313% …                        ← 진짜 분기값
#
# 값이 있는 줄만 봐서는 둘을 가를 수 없습니다 — 글자가 똑같습니다.
# 가르는 정보는 **몇 줄 위의 구역 제목**에 있습니다.
#
# ⚠️ 문서 제목과 구역 제목을 반드시 구분해야 합니다. 문서 제목은
#    "Western Digital **Reports** Fiscal Fourth Quarter and Fiscal Year
#    2022 Financial Results" 처럼 **길고** 회사 이름·Reports 가 들어갑니다.
#    이것을 구역 제목으로 오인해 연간이라 판정하면 **문서 전체를**
#    버립니다. 그래서 구역 제목은 ⑴ 짧고 ⑵ Reports/Announces 가 없는
#    줄만 인정합니다.
# ⚠️ 제목에 "quarter" 가 함께 있으면(예: "Fourth Quarter and Full Year
#    2025 Financial Results") 분기값도 그 아래 있으므로 건너뛰지 않습니다.
# 150차-AP — "Financial **Highlights**" 도 구역 제목입니다. 실물 CSX
# 2026-01-22 는 "Fourth Quarter Financial Highlights" 와 "**Full Year 2025**
# Financial Highlights" 로 나눠 적는데, 이 말을 몰라서 연간 구역의
# "•EPS was $1.54, and adjusted EPS was $1.61" 을 4분기 값으로 읽었습니다.
_SECTION_TITLE_RE = re.compile(r"financial\s+(?:results|highlights)", re.I)
_SECTION_DOC_TITLE_RE = re.compile(r"\b(?:reports?|announces?|announced)\b", re.I)
_SECTION_QUARTER_RE = re.compile(r"\bquarter(?:ly)?\b|\bQ[1-4]\b", re.I)
_SECTION_MAX_CHARS = 60       # 이보다 길면 문서 제목으로 봅니다
_SECTION_LOOKBACK_LINES = 12  # 이름에서 몇 줄 위까지 구역 제목을 찾을까


def _in_annual_section(text: str, line_start: int) -> bool:
    """이 줄이 **연간 구역** 안에 있는가 (가장 가까운 구역 제목으로 판단).

    구역 제목을 못 찾으면 False — 모르면 건드리지 않습니다.
    """
    줄들 = text[:line_start].split("\n")[-(_SECTION_LOOKBACK_LINES + 1):]
    for line in reversed(줄들):
        조각 = line.strip()
        if not _SECTION_TITLE_RE.search(조각):
            continue
        if len(조각) > _SECTION_MAX_CHARS or _SECTION_DOC_TITLE_RE.search(조각):
            return False              # 문서 제목 — 구역 제목이 아닙니다
        if _SECTION_QUARTER_RE.search(조각):
            return False              # 분기 구역
        return bool(_ANNUAL_LINE_HEAD_RE.search(조각))
    return False

# 이름 뒤 숫자를 몇 번까지 다시 찾을 것인가 (74차에 4 → 10)
# ---------------------------------------------------------------------------
# 왜 늘리는가 (실물 GS): 한 문장에 연간·전년·분기가 줄줄이 있습니다.
#   "was $59.45 for the year ended December 31, 2021 compared with $24.74
#    for the year ended December 31, 2020, and was $10.81 for the fourth
#    quarter of 2021"
# 앞의 두 연간값과 그 사이의 날짜 정수(31 · 2021 · 31 · 2020)를 건너뛰다
# 보면 4번으로는 **진짜 분기값 10.81 에 닿기도 전에 포기**합니다.
#
# 늘려도 되는 이유: 아래에서 탐색을 **같은 줄 안으로** 못박았기 때문에,
# 횟수를 늘려도 문단을 건너뛰어 엉뚱한 숫자로 갈 수가 없습니다.
_EPS_RETRY = 10

# 이름 뒤 숫자를 어디까지 찾을 것인가 (74차)
#   · 빈 줄(문단 끝)을 넘지 않는다
#   · 그 안에서도 이 글자 수까지만 (한 문장에 연간·전년·분기가 줄줄이
#     이어지는 GS 형식이 약 120자라 넉넉히 잡되, 문단을 건너뛸 만큼
#     길지는 않게)
_EPS_SPAN = 300

# 숫자 앞의 "이상/약" 표기 — 목표·전망에만 붙습니다 (실적에는 안 붙습니다)
_TARGET_SIGN_RE = re.compile(r"[>≥~]\s*\$?\s*$")

# 주당 이름 뒤의 "by 숫자" — 영향(변화)을 말하는 문장의 표시 (87차)
# 마침표·줄바꿈을 넘지 않으므로 **같은 문장 안**에서만 봅니다.
_PER_SHARE_BY_RE = re.compile(r"[^.\n]{0,25}\bby\s+\$?\s*\(?-?\d", re.I)

# 범위 표기 "$18.45 - $18.95" 의 앞끝/뒤끝
_RANGE_AFTER_RE = re.compile(r"^\s*[-–]\s*\$\s*\d")
_RANGE_BEFORE_RE = re.compile(r"\d[\d.,]*\s*[-–]\s*\$?\s*$")
_BLANK_LINE_RE = re.compile(r"\n[ \t]*\n")


def find_eps_value(
    text: str, label_patterns: list[str], *, exclude_nongaap: bool = False
) -> float | None:
    """보도자료에서 **주당** 금액(EPS)을 찾습니다.

    금액을 찾는 find_labeled_value 와 따로 두는 이유가 세 가지 있습니다.

    ① **단위 배수를 절대 적용하면 안 됩니다.**
       표 제목에 "(in thousands)" 가 있어도 주당 금액에는 곱하지 않습니다.
       곱해 버리면 $1.00 이 $1,000 이 됩니다.
    ② **적자는 이름에 적혀 있습니다.**
       "net loss per diluted share of $0.83" 은 숫자가 양수 0.83 이지만
       실제로는 −0.83 입니다. 이름에 loss 가 있으면 부호를 뒤집습니다.
       (표 형식의 "$(0.83)" 은 괄호 규칙으로 이미 음수가 됩니다)
    ③ **크기 검사가 필요합니다.**
       주당 금액이 수백 달러를 넘는 일은 거의 없습니다. 그보다 크면 표의
       다른 숫자(매출 등)를 잘못 집은 것이므로 버립니다.

    exclude_nongaap: True 면 이름 바로 앞에 "non-GAAP"·"adjusted" 가 붙은
                     경우를 건너뜁니다 (GAAP 값을 찾을 때 사용).
    """
    if not text:
        return None

    for pattern_str in label_patterns:
        pattern = re.compile(pattern_str, re.I)
        for label_match in pattern.finditer(text):
            # 이미지 슬라이드가 눌린 줄은 문장이 아니라 차트 숫자 나열입니다
            # (실물: MKSI 8.00 — 연간 차트 값). 슬라이드는 날짜 열 파서만 다룹니다.
            #
            # ⚠️ 83차 — "줄 안에 `<img` 가 **있기만** 하면"으로 재던 것을
            #    **라벨 앞 거리**로 바꿉니다. 눌린 문서는 한 줄이 수천 자라,
            #    이미지 뒤에 이어지는 **멀쩡한 문장까지 통째로** 버렸습니다.
            #
            #    실물 BAC 2025-10-15: "Diluted earnings per share of $1.06
            #    compared to $0.81" 이 진짜 값인데, 그 줄 맨 앞에 이미지가
            #    있다는 이유로 건너뛰어 **BAC 전 분기가 "없음"** 이었습니다.
            #    라벨과 이미지의 실측 거리: BAC 1,094~4,751자 · PG 슬라이드
            #    잡음 104자. 80차에 매출총이익률 쪽에서 같은 판단을 이미
            #    했고(150자), 같은 자를 EPS 쪽에도 댑니다.
            line_start = text.rfind("\n", 0, label_match.start()) + 1
            if "<img" in text[max(line_start,
                                 label_match.start() - _SLIDE_LOOKBACK):
                             label_match.start()]:
                continue

            # 전망·연간 목표 문맥이면 이 자리는 분기 실적이 아닙니다 (사고 16).
            # ⚠️ 창은 같은 문장·같은 줄 안으로 제한합니다 — 표의 진짜 값 행이
            #    바로 앞 문장의 outlook 같은 낱말 때문에 버려지면 안 됩니다.
            back = max(
                label_match.start() - _FORECAST_BACK,
                text.rfind("\n", 0, label_match.start()) + 1,
                text.rfind(". ", 0, label_match.start()) + 2,
            )
            ahead_limit = label_match.end() + _FORECAST_AHEAD
            for stop in ("\n", ". "):
                cut = text.find(stop, label_match.end(), ahead_limit)
                if cut != -1:
                    ahead_limit = cut
            # 뒤쪽 창은 **값 앞에서 멈춥니다** (150차-AT).
            #
            # 이 창의 뜻은 처음부터 "이름과 값 **사이**의 to approach 류"였는데,
            # 실제로는 값을 지나 그 뒤 설명까지 읽고 있었습니다. 실물 HPE:
            #   "◦Non-GAAP(1) of **$0.79**, up $0.41 from the prior-year
            #    period and above our **outlook** range of $0.51 - $0.55"
            # 값 뒤의 "outlook" 은 이번 분기 실적을 전망과 견주는 말인데,
            # 그것 때문에 **진짜 실적 0.79 를 통째로 버리고** 있었습니다.
            # 값 **앞**의 전망 문구("expected to be in the range of $…",
            # "Approx. $…")는 그대로 걸립니다.
            _첫숫자 = _FIRST_DIGIT_RE.search(text, label_match.end(), ahead_limit)
            if _첫숫자:
                ahead_limit = _첫숫자.start()
            # ⚠️ **이름 몸통은 통째로 보지 않습니다** (150차-AT).
            #    이름이 여러 줄에 걸치는 꼴(MPWR·HPE 형)에서는 이름 안에
            #    다른 항목의 설명이 통째로 들어옵니다. 실물 HPE 의 이름은
            #      "Diluted net earnings per share (“EPS”): ⏎◦GAAP of $0.44,
            #       … above our **outlook** range of $0.09 - $0.13 ⏎
            #       ◦Non-GAAP(1) of"
            #    이라 **GAAP 줄의 outlook** 때문에 논갭 값 0.79 를 버렸습니다.
            #    값에 닿아 있는 세 곳만 봅니다 — 이름 앞 · 이름의 **마지막
            #    줄** · 값 앞.
            _끝줄시작 = max(text.rfind("\n", label_match.start(),
                                    label_match.end()) + 1,
                         label_match.start())
            if (_FORECAST_NEAR_RE.search(text[back:label_match.start()])
                    or _FORECAST_NEAR_RE.search(text[_끝줄시작:label_match.end()])
                    or _FORECAST_NEAR_RE.search(
                        text[label_match.end():ahead_limit])):
                continue

            if exclude_nongaap:
                # 이름 앞쪽을 되돌아보되 **줄을 넘어가지는 않습니다.**
                # 표에서는 한 줄이 한 항목이므로, 윗줄의 'Non-GAAP' 때문에
                # 아랫줄의 진짜 GAAP 값까지 버리는 일을 막아야 합니다.
                #
                # ⚠️ 150차-AS 에 **문장 단위**로 바꿔 봤다가 되돌렸습니다.
                #    실물 SCHW 2024-04-15 은 "…costs, **adjusted** (1) net
                #    income and diluted common **earnings per share** …
                #    $.74" 처럼 'adjusted' 가 51자 앞이라 이 40자 창에
                #    닿지 않아 조정값이 GAAP 칸에 들어갑니다. 그런데
                #    창을 넓히면 CRDO "…**non-GAAP** net income of $208.8
                #    million. **GAAP diluted net income per share** of
                #    $0.82" 에서 진짜 GAAP 값을 잃습니다(55자 앞).
                #    문장(". ")으로 자르는 안은 표 안의 소수점·약어를 문장
                #    끝으로 오인해 **기존 시험 5개**가 빨간 불이 됐습니다.
                #    셋 다 실측으로 확인했고, 지금은 **40자·줄 안**이 가장
                #    덜 나쁩니다. SCHW 의 GAAP 칸이 틀리는 것은 남은 흠으로
                #    적어 둡니다 — 주 잣대(조정 EPS)는 맞고, GAAP EPS 는
                #    XBRL 이 94.7% 를 채우므로 영향이 작습니다.
                start = label_match.start()
                line_start = text.rfind("\n", 0, start) + 1
                window_start = max(line_start, start - _NONGAAP_LOOKBACK)
                # **앞 문장의 논갭 이야기는 이 이름과 상관없습니다.**
                # 창 안에 문장 끝이 있으면 거기서부터만 봅니다 (150차-AS).
                문장 = None
                for m in _SENTENCE_END_RE.finditer(text, window_start, start):
                    문장 = m.end()
                if 문장 is not None:
                    window_start = 문장
                if _EXCLUDING_HEAD_RE.match(text[line_start:start]):
                    continue
                if _NONGAAP_BEFORE_RE.search(text[window_start:start]):
                    continue

            # 주식 수 행이면 EPS 가 아닙니다 (74차 — 실물 UCTT).
            # "Shares used in computing net loss per share:" 도 'per share' 라
            # 이름에 걸립니다. 앞쪽만 보고, 줄을 넘지 않습니다.
            _start = label_match.start()
            _line_start = text.rfind("\n", 0, _start) + 1
            if _SHARE_COUNT_NEAR_RE.search(
                text[max(_line_start, _start - _SHARE_COUNT_LOOKBACK):_start]
            ):
                continue

            # 원문이 "연간"이라고 **직접 말하는** 자리면 분기값이 아닙니다
            # (74차 — 실물 GS 59.45 · VZ 5.18). 이름 앞쪽만 여기서 보고,
            # 값 뒤쪽은 숫자를 읽은 자리에서 다시 봅니다.
            if _ANNUAL_BEFORE_RE.search(
                text[max(_line_start, _start - _ANNUAL_BACK):_start]
            ):
                continue
            # **멀리 있는 연간 표시** (150차-AP). 위 되돌아보기는 60자라
            # 발표자료(슬라이드)처럼 한 줄이 수천 자인 글에서는 닿지 않습니다.
            # 실물 PG 2026-07-29(4분기 발표자료):
            #   "… held or grew share **for the fiscal year**. Global aggregate
            #    value share was slightly down vs the prior year. **Core
            #    earnings per share were $6.89**, +1% vs the prior year."
            #   → 6.89 는 그 해 **연간** 값입니다. 이 글에는 4분기 값이
            #     아예 없으므로 올바른 답은 "없음"입니다.
            # 넓게 보는 만큼 **좁은 낱말**만 씁니다 — annual·FY2025 처럼
            # 비교 대상으로 늘 나오는 말은 빼고, "그 해 동안"을 뜻하는
            # 말만 봅니다. 그리고 같은 구간에 quarter 가 하나라도 있으면
            # 분기 이야기가 섞인 것이므로 건드리지 않습니다.
            _먼앞 = text[max(_line_start, _start - _ANNUAL_FAR_BACK):_start]
            if (_ANNUAL_FAR_RE.search(_먼앞)
                    and not _SECTION_QUARTER_RE.search(_먼앞)):
                continue
            # 줄(=글머리표 항목)이 "Full-year …" 로 **시작하면** 그 줄의 값은
            # 전부 연간입니다 (실물 VZ: "•Full-year 2022 earnings per share
            # (EPS) of $5.06 … adjusted EPS1 … of $5.18"). 이름이 줄머리에서
            # 멀어 위의 60자 되돌아보기로는 닿지 않습니다. 줄머리 쪽만
            # 짧게 확인합니다 — 문장 중간의 'full year' 은 보지 않습니다.
            if _ANNUAL_LINE_HEAD_RE.match(
                text[_line_start:_line_start + _ANNUAL_LINE_HEAD].lstrip("•·-– \t")
            ):
                continue
            # 줄이 "Fiscal 20xx"로 **시작**하고 그 줄머리에 quarter 가 없으면
            # 연간 줄입니다 (119차 — 실물 SYNA, 위 _FISCAL_HEAD_RE 주석)
            _머리 = text[_line_start:_line_start + _FISCAL_HEAD_SPAN].lstrip("•·-– \t")
            if _FISCAL_HEAD_RE.match(_머리) and not _SECTION_QUARTER_RE.search(_머리):
                continue
            # 겹친 머리글: **바로 윗줄**이 "Fiscal 20xx …"/"Full-year …" 로
            # 시작하는 짧은 제목 줄이고 이 줄에도 quarter 가 없으면, 이 줄은
            # 그 연간 머리글의 연속입니다 (119차 — 실물 QCOM:
            #   "Fiscal 2017 Revenues $22.3 billion
            #    GAAP EPS $1.65, Non-GAAP EPS $4.28").
            # 조건을 셋 다 요구해 좁게 겁니다 — 윗줄이 길거나(제목이 아님)
            # quarter 가 보이면 건드리지 않습니다.
            _윗줄 = text[:_line_start].rstrip("\n")
            _윗시작 = _윗줄.rfind("\n") + 1
            _윗글 = _윗줄[_윗시작:].strip().lstrip("•·-– \t")
            _이줄 = text[_line_start:text.find("\n", _line_start)
                        if text.find("\n", _line_start) != -1 else len(text)]
            if (_윗글 and len(_윗글) <= 60
                    and (_FISCAL_HEAD_RE.match(_윗글)
                         or _ANNUAL_LINE_HEAD_RE.match(_윗글))
                    and not _SECTION_QUARTER_RE.search(_윗글)
                    and not _SECTION_QUARTER_RE.search(_이줄[:120])):
                continue
            # 몇 줄 위의 **구역 제목**이 연간 구역이라고 말하면 건너뜁니다
            # (실물 HPE — 위 _in_annual_section 주석 참조)
            if _in_annual_section(text, _line_start):
                continue

            label_text = label_match.group(0)

            # 이름 바로 뒤가 "**by** $숫자" 면 그것은 **영향(변화)을 말하는
            # 문장**이지 실적이 아닙니다 (87차 — 실물 CRM):
            #   "gains (losses) on strategic investments **impacted GAAP
            #    diluted net income per share by $0.00** and $(0.03)"
            # 이 한 문장 때문에 CRM 의 조정 EPS·GAAP EPS 가 **둘 다 0.00** 이
            # 됐습니다 (진짜 값은 같은 문서의 표에 2.91·1.96 으로 있었는데도).
            # 이름 하나에 두 잣대가 같은 값으로 무너지는 것이 이 결함의 표시입니다.
            #
            # 저장소 원문 732건 실측 — **반례가 0건**입니다. 이 꼴로 나온 20곳은
            # 전부 영향·변화 문장이었습니다:
            #   CRM "impacted … per share by $0.00" · GOOGL "increased …
            #   per share by $2.35" · IPGP "decreased … earnings per share
            #   by $0.03" · EL "impact … per share growth by 6%" ·
            #   GS "Book value per common share increased by 20.4%"
            # 진짜 실적 표는 "Diluted net income per share (3)   $1.96" 처럼
            # 이름과 값 사이에 "by" 가 없습니다.
            #
            # 그 숫자 하나만 건너뛰지 않고 **이름 자리를 통째로 포기**합니다 —
            # "by $0.00 **and $(0.03)**" 처럼 같은 문장에 숫자가 이어지면
            # 뒤엣것도 영향값이라 건너뛰기만으로는 또 뭅니다.
            if _PER_SHARE_BY_RE.match(text[label_match.end():
                                           label_match.end() + 48]):
                continue

            # 값 없는 **제목 줄**이면 이름 자리를 포기합니다 (103차).
            # 이름과 숫자 사이에 **또 다른 주당 항목 이름**이 끼어 있으면
            # 그 이름은 자기 값을 가진 것이 아닙니다 — 자세한 내력은
            # _PER_SHARE_LABEL_RE 주석에 적어 두었습니다 (실물 CRM).
            #
            # ⚠️ **논갭 이름일 때만** 봅니다. 처음에는 모든 주당 이름에
            #    걸었더니 원문 982건 중 4건이 바뀌었는데 그중 **3건이
            #    회귀**였습니다 (MDB GAAP −0.02 → 1.44 · SNDK 43.97 → 39.25,
            #    둘 다 GAAP 이 조정값으로 무너짐). 원인은 이렇습니다 —
            #    조정표 머리글 "Reconciliation of GAAP net loss per share …
            #    to non-GAAP net income per share:" 안의 GAAP 이름이
            #    **우연히 바로 뒤의 올바른 GAAP 값과 가장 가까워서** 정답을
            #    내고 있었습니다. 그 머리글을 막자 더 나쁜 후보가 이겼습니다.
            #    실측된 결함(87차 ⑤)은 **조정 EPS** 제목 줄이므로 거기에만
            #    겁니다 — 넓게 걸면 하나 고치고 셋을 잃습니다.
            _논갭 = _NONGAAP_NEAR_RE.search(label_match.group(0)) or (
                _NONGAAP_NEAR_RE.search(
                    text[max(0, label_match.start() - _NONGAAP_LOOKBACK)
                         : label_match.start()]
                )
            )
            if _논갭:
                _앞 = text[label_match.end() : label_match.end() + _TITLE_LINE_AHEAD]
                _숫자 = _FIRST_DIGIT_RE.search(_앞)
                if _숫자 and _PER_SHARE_LABEL_RE.search(_앞[: _숫자.start()]):
                    continue

            # 이름이 "적자"라고 말하면 양수를 음수로 뒤집습니다 (아래 두 곳 공용).
            # "net income (loss) per share" 같은 겸용 표기는 선언이 아니므로 제외.
            says_loss = re.search(r"\bloss\b", label_text, re.I) and not re.search(
                r"\b(?:income|earnings)\b", label_text, re.I
            )

            # "($43.97 diluted net income per share)" 처럼 **숫자가 이름 앞 괄호
            # 안에** 있는 형식(실물: 샌디스크). 이름 바로 뒤가 닫는 괄호라면 값은
            # 괄호 안쪽에 있으므로, 뒤쪽을 훑으면 엉뚱한 숫자를 뭅니다
            # (실제로 뒤 문장의 연도 표기를 물어 GAAP EPS 가 202.0 이 됐습니다).
            if text[label_match.end() : label_match.end() + 2].lstrip().startswith(")"):
                line_start = text.rfind("\n", 0, label_match.start()) + 1
                open_paren = text.rfind("(", line_start, label_match.start())
                if open_paren >= 0:
                    parsed = _parse_number_at(text, open_paren + 1)
                    if (
                        parsed is not None
                        and parsed[1] < label_match.start()      # 숫자가 괄호 안에 있음
                        and abs(parsed[0]) <= cfg.EPS_MAX_ABS
                        # 소수점 없는 숫자는 EPS 가 아닙니다 (아래 본검사와 동일 규칙)
                        and "." in text[parsed[1]:parsed[4]]
                    ):
                        value = parsed[0]
                        if value > 0 and says_loss:
                            value = -value
                        return value
                continue   # 괄호형인데 값을 못 읽으면 이 자리는 건너뜁니다

            search_from = label_match.end()
            # 탐색은 **같은 문단 안에서만** 합니다 (74차 — 실물 IPGP).
            #   각주 "… excluded from the calculation of **adjusted EPS**,
            #   stock based compensation of $11.0 million …" 뒤로 문단을 넘어
            #   300자 뒤의 "**Exhibit 99.1**"(공시 번호)을 물어 조정 EPS 가
            #   99.10 이 됐습니다.
            #
            # ⚠️ "같은 **줄**"로 못박으면 너무 좁습니다. 이름이 **제목 줄**이고
            #    값이 다음 줄에 오는 형식이 실제로 흔합니다 (실물 HPE:
            #    "net earnings per share (“EPS”):⏎◦GAAP of $0.31"). 74차 전수
            #    비교에서 이 형식 2건을 잃는 것을 보고 문단 단위로 넓혔습니다.
            #
            # 문단의 끝 = **빈 줄**. 거기에 글자 수 상한을 함께 겁니다.
            line_end = min(label_match.end() + _EPS_SPAN, len(text))
            blank = _BLANK_LINE_RE.search(text, label_match.end(), line_end)
            if blank:
                line_end = blank.start()
            for _ in range(_EPS_RETRY):
                parsed = _parse_number_at(text, search_from)
                if parsed is None:
                    break
                value, num_start, number_end, _had_scale, num_end = parsed
                if num_start >= line_end:
                    break            # 줄을 넘었습니다 — 이 이름 자리는 포기

                # 퍼센트 숫자는 EPS 가 아닙니다 (예: "grew 20% to $1.00")
                tail_line = text[num_end : num_end + 24].split("\n", 1)[0]
                if _PERCENT_AFTER_RE.match(tail_line):
                    search_from = number_end
                    continue

                if abs(value) > cfg.EPS_MAX_ABS:
                    search_from = number_end
                    continue

                # 소수점 없는 숫자는 EPS 가 아닙니다. 실제 EPS 는 보도자료에서
                # 항상 소수점 표기($1.66, $39.25)로 나오기 때문입니다.
                # 처음엔 묶음 제목(실물: AMD — [순이익 $2,760][EPS $1.66] 짝)
                # 에만 적용했지만, 이름 뒤의 날짜·연도류 정수를 무는 사고가
                # 실제로 났습니다: 분기 종료일 "March 31" 의 31을 물어 MCHP
                # 조정 EPS 가 세 해 연속 31.0, 정수 202 를 물어 202.0 이 30건
                # (2026-08-12 snapshot 실측 — FN 15개 분기 전부·TER·CLS·FORM 등).
                # 그래서 모든 EPS 후보에 적용합니다. 소수점 값을 끝내 못 찾으면
                # 답은 "없음"입니다 — 없음이 틀림보다 안전합니다.
                if "." not in text[num_start:num_end]:
                    search_from = number_end
                    continue

                # **0.00 은 EPS 로 읽지 않습니다** (150차-AS).
                # 실물 BE 2026-02-05: "GAAP EPS, Diluted   **$.00**   $(0.10)
                # $0.46 …" — 첫 칸이 빈칸 대신 놓인 0 이고, 그 분기의 진짜
                # 값은 −0.10 입니다. 주당 정확히 0.00 인 분기는 사실상 없고,
                # 있더라도 신기록 판단에 쓸 정보가 없습니다.
                # 이미 아랫줄 표 파서에서 쓰던 규칙을 본줄기에도 댑니다.
                if value == 0:
                    search_from = number_end
                    continue

                # 소수점 아래가 **세 자리 이상**이면 마지막 자리는 숫자가
                # 아니라 **각주 번호**입니다 (150차-AP). 주당 금액을 세
                # 자리로 적는 회사는 없습니다. 실물:
                #   DG "Adjusted Diluted EPS Increased 14.5% to $1.74**1**"
                #   MS "…per diluted share of $1.30**1**"
                # 저장원문 2,064건 전수에서 이 꼴은 이 둘뿐이었고 둘 다
                # 각주였습니다. 잘라 쓰지 않고 **이 숫자를 건너뜁니다** —
                # 자릿수를 우리가 고치면 그것은 창작입니다(헌법 1조).
                # ⚠️ 이 자리는 **건너뛰지 말고 통째로 포기**합니다. 각주가
                #    붙은 그 숫자가 답이었으므로, 뒤로 더 훑으면 상관없는
                #    숫자를 뭅니다 — 실제로 그렇게 해 봤더니 DG 의 조정
                #    EPS 가 같은 문단 뒤쪽의 **배당금 0.32** 가 됐습니다.
                _소수부 = text[num_start:num_end].split(".")[-1]
                if len(_소수부) >= 3 and _소수부.isdigit():
                    break

                # 숫자 바로 앞이 ">" · "≥" · "~" 면 그것은 **목표·전망**이지
                # 실적이 아닙니다 (84차 — 실물 UNH 전망표):
                #   "Diluted Net Earnings per Share to UNH Shareholders
                #    **> $17.10**   $18.45 - $18.95"
                # 83차에 슬라이드 규칙을 좁히자 이 전망표가 새로 읽히면서
                # UNH 분기 EPS 가 6.04 → 17.10 으로 **틀려졌습니다.**
                # "이상/약" 표기를 붙인 실적 숫자는 없습니다.
                if _TARGET_SIGN_RE.search(text[max(num_start - 4, 0):
                                               num_start]):
                    search_from = number_end
                    continue

                # **범위 표기의 양끝**도 전망입니다 (84차 — 실물 UNH 전망표
                # "Earnings per Share **$18.45 - $18.95**  $19.50 - $20.00").
                # 실적을 범위로 적는 회사는 없습니다.
                #   앞끝: 숫자 뒤에 " - $숫자" 가 이어진다
                #   뒤끝: 숫자 앞이 "숫자 - $" 다 (적자 표기 "-$0.40" 은
                #         대시 앞에 숫자가 없어 걸리지 않습니다)
                if (_RANGE_AFTER_RE.match(text[num_end:num_end + 14])
                        or _RANGE_BEFORE_RE.search(
                            text[max(num_start - 16, 0):num_start])):
                    search_from = number_end
                    continue

                # 값 **바로 뒤**가 "for the year ended …" 면 그 숫자는 연간값
                # 입니다 (74차 — 실물 GS: "was $59.45 for the year ended
                # December 31, 2021 … and was $10.81 for the fourth quarter").
                # 이 자리는 건너뛰고 다음 숫자를 계속 찾습니다 — 바로 뒤에
                # 진짜 분기값이 이어지는 경우가 많습니다.
                if _ANNUAL_AFTER_RE.match(text[num_end:num_end + _ANNUAL_AHEAD]):
                    search_from = number_end
                    continue
                # 값 뒤에 **"주당 …, 그 해 동안"** 이 붙은 경우 (150차-AP).
                # 위 검사는 값 **바로 뒤**만 보므로 "per share" 가 사이에
                # 끼면 닿지 않습니다. 실물 PCG 2021-02-25:
                #   "non-GAAP core earnings … were $2,020 million, **or $1.61
                #    per share, for the full year 2020**"
                #   → 1.61 은 연간, 진짜 4분기값은 같은 문단의 0.21 입니다.
                # 이 자리만 건너뛰면 바로 뒤의 분기값을 이어서 찾습니다.
                # 값 바로 뒤가 "**at the midpoint of guidance**" 면 그
                # 숫자는 실적이 아니라 **전망 그 자체**입니다 (150차-AT).
                # 실물 QRVO 2020-09-08(가이던스 갱신 공시):
                #   "Diluted earnings per share  **$2.14 at the midpoint of
                #    guidance**"
                # ⚠️ "**above** the midpoint of guidance"(실물 MKSI)는 실적을
                #    전망과 견주는 말이라 **막으면 안 됩니다** — 그래서
                #    "at" 으로 시작하는 꼴만 좁게 봅니다.
                if _GUIDANCE_TAIL_RE.match(text[num_end:num_end + 40]):
                    search_from = number_end
                    continue

                if _ANNUAL_AFTER_FAR_RE.search(
                        text[num_end:num_end + _ANNUAL_AHEAD]):
                    search_from = number_end
                    continue
                # 값 바로 **앞**이 "compared to $" 면 견주는 대상입니다.
                # (150차-AP — 실물 PYPL "non-GAAP earnings per diluted share
                #  of ~$0.86, **compared to $1.15** in the prior year period")
                # ⚠️ 건너뛰지 않고 **자리를 포기**합니다. 이 자리의 값은
                #    그 숫자였고 다만 이번 기간 것이 아니었을 뿐이라,
                #    뒤로 더 훑으면 상관없는 숫자를 뭅니다 — 실제로
                #    PYPL 이 전망 문장 뒤의 "0.20(러시아 영향)"을 물었습니다.
                if _COMPARED_BEFORE_RE.search(
                        text[max(num_start - _COMPARED_BACK, 0):num_start]):
                    break
                # 값 바로 **뒤**가 "같은 기간에" 면 그 값도 견주는 대상입니다.
                # (실물 PCG "or $3.93 per share, during the same period in 2019")
                #
                # ⚠️ **다음 숫자가 나오기 전까지만** 봅니다. 넓게 봤더니
                #    "Adjusted EPS of $2.45 compared to $1.90 **in the prior
                #    year period**" 에서 앞의 진짜 값 2.45 까지 버렸습니다
                #    (기존 시험 4개가 빨간 불로 잡음). 그 꼬리말은 뒤엣
                #    숫자(1.90)의 것이지 2.45 의 것이 아닙니다.
                _뒤 = text[num_end:num_end + _ANNUAL_AHEAD]
                _다음숫자 = re.search(r"[\$\d]", _뒤)
                if _다음숫자:
                    _뒤 = _뒤[:_다음숫자.start()]
                if _PRIOR_PERIOD_AFTER_RE.search(_뒤):
                    break            # 위와 같은 이유로 자리를 포기합니다

                # "X to Y" 전망 범위의 양끝 (119차 — 위 _RANGE_TO_AFTER_RE
                # 주석). 앞끝은 값 뒤의 "to $숫자"로, 뒤끝은 값 앞의
                # "$소수 to $"로 알아봅니다. 단 "from $X to $Y" 는 범위가
                # 아니라 변화 문구라 뒤끝(Y = 진짜 값)을 살립니다 (실물 AMGN).
                _앞쪽 = text[max(num_start - 28, 0):num_start]
                if (_RANGE_TO_AFTER_RE.match(text[num_end:num_end + 16])
                        or (_RANGE_TO_BEFORE_RE.search(_앞쪽)
                            and not _FROM_RANGE_RE.search(_앞쪽))):
                    search_from = number_end
                    continue

                # 값 바로 앞이 "from $" 면 비교 원점(전년값)입니다 — 건너뛰고
                # 다음 숫자를 봅니다 (실물 AMGN "from $5.31 to $5.29").
                if _FROM_BEFORE_RE.search(text[max(num_start - 10, 0):num_start]):
                    search_from = number_end
                    continue

                # 이름과 값 **사이**가 짧고 거기에 연간 표시가 있으면 그
                # 표시는 이름을 직접 꾸미는 말입니다 — 이 자리는 연간입니다
                # (119차 — 실물 SWKS "… per share **for fiscal year 2018**
                # was $7.22" · MCHP). 같은 구간에 quarter 가 있으면 비교
                # 문구이므로 제외합니다 (NTAP 반례).
                # ⚠️ 60자 제한이 핵심입니다 — GS 처럼 연간·전년·분기가 한
                #    문장에 줄줄이 나열되면 뒤쪽 분기값의 사이 구간에도
                #    앞 연간 문구가 들어오는데, 그건 이름을 꾸미는 말이
                #    아닙니다 (제한 없이 걸면 GS 분기값 10.81 을 잃습니다 —
                #    기존 시험이 빨간 불로 잡아 줬습니다).
                _사이 = text[label_match.end():num_start]
                if (len(_사이) <= 60 and _ANNUAL_GAP_RE.search(_사이)
                        and not _SECTION_QUARTER_RE.search(_사이)):
                    break            # 자리 포기 — 다음 이름 자리로

                # GAAP 을 찾는 중인데 **이름과 값 사이에서 논갭 이야기가
                # 시작되면** 그 뒤의 값은 논갭 값입니다 (150차-AS).
                # 실물 SCHW 2024-04-15:
                #   "…, or $.68 diluted earnings per common share.
                #    **Excluding** $140 million of … costs, **adjusted** (1)
                #    net income and diluted common earnings per share equaled
                #    $1.5 billion and **$.74**, respectively."
                #   → 이름의 제 값(0.68)은 이름 **앞**에 있고, 앞으로 훑다
                #     보면 다음 문장의 조정값 0.74 에 닿습니다. GAAP·조정이
                #     **같은 값으로 무너지는 것**이 이 결함의 표시입니다.
                # exclude_nongaap 은 이름 **앞**만 보므로 여기서 뒤도 봅니다.
                if exclude_nongaap and _NONGAAP_NEAR_RE.search(_사이):
                    break            # 자리 포기 — 여기서부터는 논갭 구간

                # 이름과 값 사이에 "**제외한다**"는 말이 있으면 그 숫자는
                # 잣대가 아니라 **빼낸 항목의 크기**입니다 (150차-AP).
                # 실물 HPE 2026-03-09:
                #   "Fiscal 2026 full year non-GAAP diluted net EPS **estimate
                #    excludes** net after-tax adjustments of **approximately
                #    $1.28 per diluted share**"
                # 문단 단위(300자)로 앞을 훑는 규칙 때문에, 앞 문장의 전망
                # 범위($2.30~$2.50)를 전부 걸러낸 뒤 **다음 문장의 제외액**을
                # 물었습니다. 1분기 진짜 논갭 EPS 는 0.65 인데 1.28 이 됐고,
                # 이것은 "없음"보다 나쁩니다.
                # "A 는 B 를 제외한다" 문장에서 B 가 A 인 경우는 없습니다.
                # ⚠️ **이름 자체**도 함께 봅니다. "…or" 로 끊어 읽는 이름
                #    (150차-AP ③)은 문장을 통째로 물기 때문에 "제외한다"가
                #    이름 안쪽에 들어갑니다. 실물 DG 2019-12-05:
                #      "Adjusted net income and adjusted diluted EPS … which
                #       **exclude** the after-tax impact of approximately
                #       $24.1 million **or $0.09 per diluted share**"
                #      → 0.09 는 빼낸 항목의 크기입니다(진짜 값 1.42).
                if _EXCLUDE_GAP_RE.search(label_text + _사이):
                    break            # 자리 포기 — 다음 이름 자리로


                # 이름이 "적자"라고 말하고 있는데 숫자가 양수면 부호를 뒤집습니다.
                #   "net loss per diluted share of $0.83"  →  −0.83
                # ("net income (loss) per share" 겸용 표기는 위 says_loss 에서
                #  이미 제외했습니다 — 금액이 "(0.83)" 형태로 이미 음수입니다)
                if value > 0 and says_loss:
                    value = -value
                return value
    값 = _주당값이_아랫줄에_있는_표(text, label_patterns)
    if 값 is not None:
        return 값
    return _이름이_두_줄로_쪼개진_표(text, label_patterns)


# ---------------------------------------------------------------------------
# 이름이 **두 줄로 쪼개진** 표 (150차-AS) — 실물 CGNX·SCHW
# ---------------------------------------------------------------------------
# 표의 항목 이름이 길어 줄바꿈이 되면서, **어느 잣대인지 말해 주는 꼬리표**
# 가 아랫줄로 밀려난 형식입니다. 값은 **윗줄**에 있습니다.
#
#   CGNX
#     Net income per diluted weighted-average common and common-equivalent   $0.23
#     share (Non-GAAP)                                     ← 여기서야 논갭이라고 말한다
#
#   SCHW
#     Adjusted net income available to common stockholders   $1,358   $.74
#     (non-GAAP), Adjusted diluted EPS (non-GAAP)          ← 두 번째 칸이 EPS 라고 말한다
#
# 기존 이름들은 전부 "non-GAAP … per share" 가 **붙어 있기**를 요구해서
# 이 꼴을 하나도 못 읽었습니다 — 두 종목만 20건입니다.
#
# 값 고르기: 윗줄에서 **소수점이 있는 첫 숫자**를 집습니다. SCHW 의
# "$1,358"(백만 달러)은 소수점이 없어 저절로 걸러지고 "$.74" 가 남습니다.
# 이것은 이미 쓰고 있는 규칙(소수점 없는 숫자는 EPS 가 아니다)과 같습니다.

# 이어짐줄의 표시 — **자기 숫자가 없고**, 잣대 이름을 괄호로 달고 있고,
# 주당 값을 말하는 줄
_CONT_MARK_RE = re.compile(r"\((?:non[-\s]?GAAP|GAAP)\)", re.I)
_CONT_SHARE_RE = re.compile(r"\bshare\b|\bEPS\b", re.I)
_CONT_MAX = 90            # 이어짐줄은 짧습니다 (설명 문장이 아닙니다)
_CONT_PROFIT_RE = re.compile(r"\b(?:income|earnings|loss|profit)\b", re.I)


def _이름이_두_줄로_쪼개진_표(text: str, label_patterns: list[str]) -> float | None:
    조정찾는중 = any(_NONGAAP_NEAR_RE.search(p) for p in label_patterns)
    줄들 = text.split("\n")
    for i, 줄 in enumerate(줄들):
        if i == 0:
            continue
        이어짐 = 줄.strip()
        # ① 자기 숫자가 있으면 이어짐줄이 아니라 제 값을 가진 줄입니다
        if not 이어짐 or len(이어짐) > _CONT_MAX or _FIRST_DIGIT_RE.search(이어짐):
            continue
        # ② 잣대 꼬리표와 주당 표시가 둘 다 있어야 합니다
        표 = _CONT_MARK_RE.search(이어짐)
        if not 표 or not _CONT_SHARE_RE.search(이어짐):
            continue
        이줄이조정 = bool(re.match(r"\(non", 표.group(0), re.I))
        if 이줄이조정 != 조정찾는중:
            continue
        윗 = 줄들[i - 1]
        if not _CONT_PROFIT_RE.search(윗):
            continue          # 이익 항목이 아니면 모르는 줄 — 건드리지 않음
        # ③ 윗줄에서 **소수점이 있는 첫 숫자**를 집습니다
        자리 = 0
        while 자리 < len(윗):
            parsed = _parse_number_at(윗, 자리, len(윗))
            if parsed is None:
                break
            값, 시작, 끝, _배수, 끝2 = parsed
            if "." in 윗[시작:끝2] and 값 != 0 and abs(값) <= _EPS_LINE_MAX:
                if 값 > 0 and re.search(r"\bnet\s+loss\b", 윗, re.I):
                    값 = -값
                return 값
            자리 = 끝
        continue
    return None


# 줄 **맨 앞**이 "per (diluted) (common) share" 인 줄 — 실물 ALL(올스테이트).
_PER_SHARE_LINE_RE = re.compile(
    r"^\s*per\s+(?:diluted\s+)?(?:common\s+)?share", re.I)
# 윗줄이 어느 잣대인지 가르는 말
_ADJ_ITEM_RE = re.compile(r"adjusted|non[-\s]?GAAP|\bcore\b", re.I)
_NET_ITEM_RE = re.compile(r"net\s+(?:income|earnings|loss)", re.I)
_EPS_LINE_MAX = 60          # 주당 값의 크기 상한 (find_eps_value 와 같은 뜻)


def _주당값이_아랫줄에_있는_표(
    text: str, label_patterns: list[str]
) -> float | None:
    """주당 값이 **윗줄 항목에 딸린 아랫줄**로 적히는 표를 읽습니다 (150차-AJ).

    보험사가 이렇게 씁니다. 실물 ALL(올스테이트) 2022년 2분기:

        Net income (loss) applicable to common shareholders   (1,042)   1,595
        per diluted common share (1)                           (3.81)    5.26
        Adjusted net income (loss)*                             (420)     316
        per diluted common share* (1)                          (1.56)    1.04

    지금 파서는 **이름과 "per share" 가 같은 줄에 붙어 있다**고 보고 찾으므로
    이 꼴을 통째로 못 읽습니다. ALL 은 GAAP EPS 도 조정 EPS 도 전부 없음이었습니다.

    ⚠️ **아무것도 못 찾았을 때만** 도는 마지막 수단입니다. 이미 찾은 값은
       절대 바꾸지 않습니다 — 없음을 값으로 바꿀 뿐이라 되돌리기 쉽습니다.

    가르는 법은 **윗줄의 이름**입니다. 윗줄에 adjusted·non-GAAP·core 가 있으면
    조정 잣대, net income/earnings/loss 면 GAAP 잣대입니다. 둘 다 아니면
    건드리지 않습니다 — 모르면 없음이 정답입니다(헌법 1조).

    원문 실측 — 이 꼴이 있는 파일 76개 / 36종목, 그중 윗줄을 알아본 줄이
    47개(GAAP 34 · 조정 13)입니다.
    """
    조정찾는중 = any(_NONGAAP_NEAR_RE.search(p) for p in label_patterns)
    줄들 = text.split("\n")
    for i, 줄 in enumerate(줄들):
        if i == 0 or not _PER_SHARE_LINE_RE.match(줄):
            continue
        윗 = 줄들[i - 1]
        윗이조정 = bool(_ADJ_ITEM_RE.search(윗))
        if 윗이조정 != 조정찾는중:
            continue
        if not 윗이조정 and not _NET_ITEM_RE.search(윗):
            continue          # GAAP 쪽인데 순이익 항목이 아니면 모르는 줄
        이름끝 = _PER_SHARE_LINE_RE.match(줄).end()
        # ⚠️ **이름 뒤에는 각주 번호·별표·공백만 와야 합니다.**
        #
        #    이 문지기가 없으면 "per share" 로 시작하기만 하면 무엇이든
        #    주당 값으로 읽습니다. 실측으로 잡힌 헛값 둘:
        #      PG   "per share from early debt retirement … Core Effective
        #            Tax Rate range of 18% to 19%"  → 세율 18 을 EPS 로 (참값 1.6대)
        #      CGNX "Per share impact of discrete tax adjustments identified
        #            above   0.0x"                  → 영향값을 EPS 로
        #    둘 다 이름 뒤에 **설명 글**이 이어집니다. 진짜 표의 줄은
        #    "per diluted common share (1)        (3.81)" 처럼 이름이 끝나면
        #    바로 숫자입니다.
        나머지 = 줄[이름끝:]
        머리 = re.match(r"^[\s*]*(?:\(\d+\))?[\s*]*", 나머지).group(0)
        if not _FIRST_DIGIT_RE.match(나머지[len(머리):].lstrip("$([ ")[:1] or "x"):
            continue
        나머지 = 나머지[len(머리):]
        parsed = _parse_number_at(나머지, 0, len(나머지))
        if parsed is None:
            continue
        값 = parsed[0]
        if 값 == 0 or abs(값) > _EPS_LINE_MAX:
            continue
        # 윗줄 이름이 손실을 말하는데 값이 양수면 부호를 뒤집습니다
        # (표 형식의 "(3.81)" 은 괄호 규칙으로 이미 음수입니다)
        if 값 > 0 and re.search(r"\bnet\s+loss\b", 윗, re.I):
            값 = -값
        return 값
    return None


# ---------------------------------------------------------------------------
# 열 방향 표 파서 — 열 제목이 여러 줄에 쌓인 위치 정렬 표 (실물: CGNX)
# ---------------------------------------------------------------------------
# 문장형 파서(find_eps_value)는 글을 왼쪽→오른쪽으로 읽으므로,
#   [Revenue] [Net Income] [Net Income per Diluted Share] [Non-GAAP ... Share*]
# 처럼 열 제목이 3~4줄에 걸쳐 세로로 쌓이고 값이 "Current quarter" 행의
# 몇 번째 칸에 있는 표를 읽지 못합니다 (10차 감사 B유형 — CGNX·MU 등 15건).
# 이 파서는 그런 표에서 **확신이 설 때만** 값을 읽고, 조금이라도 구조가
# 어긋나면 "없음"을 돌려줍니다 — 틀림이 없음보다 위험하기 때문입니다.

_SEP_LINE_RE = re.compile(r"^\s*-{20,}\s*$")        # 표의 구분선
_COL_SEG_RE = re.compile(r"\S(?:\S| (?=\S))*")       # 2칸 이상 공백으로 칸 나누기
_ADJ_COL_RE = re.compile(r"non[-\s]?gaap|adjusted", re.I)
_PER_SHARE_COL_RE = re.compile(r"per\s+(?:diluted\s+)?share|per\s+diluted|\bEPS\b", re.I)
_CURRENT_ROW_RE = re.compile(r"current\s+quarter|three\s+months\s+ended", re.I)


def _line_segments(line: str) -> list[tuple[int, int, str]]:
    return [(m.start(), m.end(), m.group()) for m in _COL_SEG_RE.finditer(line)]


def _cell_per_share(cell: str) -> float | None:
    """표 칸 하나를 주당 금액으로 읽습니다.

    소수점 표기($0.27)만 인정합니다 — 사고 15(소수점 없는 정수를 뭄)와
    같은 규칙입니다. 퍼센트·대시(—)·정수는 없음.
    """
    cell = cell.strip()
    if "%" in cell:
        return None
    negative = cell.startswith("(") and cell.rstrip().endswith(")")
    cleaned = cell.strip("() ").replace("$", "").replace(",", "").strip()
    if not re.fullmatch(r"\d+\.\d+", cleaned):
        return None
    value = float(cleaned)
    if value > cfg.EPS_MAX_ABS:
        return None
    return -value if negative else value



# 50차 — "…net income of $3.0 billion, **or $4.73 per diluted common share**"
# 처럼 **숫자가 이름 앞**에 오는 문장 (실물: COF·CAT·HD·MCD 등 다수).
# 기존 find_eps_value 는 이름 뒤에서 숫자를 찾으므로 이 형태를 못 읽습니다.
# 괄호형("($43.97 diluted net income per share)")은 이미 따로 처리하고 있고,
# 이것은 그 사촌뻘입니다.
_EPS_BEFORE_RE = re.compile(
    r"\$?\s*(\(?-?[\d,]+\.\d{2}\)?)\s+per\s+"
    r"(?P<kind>diluted\s+|basic\s+)?(?:common\s+|ordinary\s+)?share",
    re.I,
)


def find_eps_before_per_share(text: str) -> float | None:
    """"$4.73 per diluted common share" 형태에서 주당 금액을 읽습니다.

    · **희석(diluted)** 표기를 먼저 찾고, 없으면 수식어 없는 것을 씁니다.
      'basic' 은 쓰지 않습니다 — 희석이 회사가 대표로 내세우는 값입니다.
    · 같은 줄 앞쪽에 'non-GAAP'·'adjusted' 가 있으면 건너뜁니다.
    · 괄호 표기 "$(8.58)" 는 음수입니다 (회계 표기).
    · 전망 문맥(사고 16)이면 건너뜁니다.
    """
    if not text:
        return None
    for want_diluted in (True, False):
        for match in _EPS_BEFORE_RE.finditer(text):
            kind = (match.group("kind") or "").strip().lower()
            if kind == "basic":
                continue
            if want_diluted and kind != "diluted":
                continue
            start = match.start()
            line_start = text.rfind("\n", 0, start) + 1
            window = text[max(line_start, start - _NONGAAP_LOOKBACK):start]
            if _NONGAAP_NEAR_RE.search(window):
                continue
            # 배당금은 EPS 가 아닙니다 (76차 — 실물 HPE). 67차에도 JPM
            # 배당금이 EPS 자리에 들어와 이익 시계열을 톱니로 만들었습니다.
            if _DIVIDEND_NEAR_RE.search(text[line_start:start]):
                continue
            back = max(start - _FORECAST_BACK, line_start,
                       text.rfind(". ", 0, start) + 2)
            if _FORECAST_NEAR_RE.search(text[back:match.end()]):
                continue
            raw = match.group(1)
            negative = raw.startswith("(") and raw.endswith(")")
            value = float(raw.strip("()").replace(",", ""))
            if negative:
                value = -value
            if abs(value) > cfg.EPS_MAX_ABS:
                continue
            return value
    return None


def find_eps_in_column_table(text: str) -> dict:
    """위치 정렬 표에서 (조정 EPS, GAAP EPS)를 읽습니다. 못 읽으면 없음.

    확신 조건 — 전부 만족해야만 값을 돌려줍니다:
      ① 구분선(-----) 위에 열 제목 줄들이 있고
      ② 제목을 세로로 쌓아 보면 "Non-GAAP/Adjusted ... per share" 열이 있고
      ③ 구분선 아래에 "Current quarter"/"three months ended" 행이 있고
      ④ 그 행의 값 칸 수가 열 수와 정확히 일치한다 (순서로 짝지음)
    """
    result = {"adj_eps": None, "gaap_eps": None}
    if not text:
        return result
    lines = text.split("\n")
    for sep_idx, line in enumerate(lines):
        if not _SEP_LINE_RE.match(line):
            continue

        # ① 구분선 위 최대 5줄을 열 제목 후보로 모읍니다 (빈 줄은 건너뜀)
        header_rows: list[list[tuple[int, int, str]]] = []
        for j in range(sep_idx - 1, max(sep_idx - 6, -1), -1):
            segments = _line_segments(lines[j])
            if not segments:
                if header_rows:
                    break
                continue
            if len(segments) == 1 and len(segments[0][2]) > 60:
                break                     # 산문 줄 — 제목이 아닙니다
            header_rows.append(segments)
        if not header_rows:
            continue
        header_rows.reverse()             # 위→아래 순서로

        # ② 가로 위치가 겹치는 조각끼리 한 열로 묶어 제목을 완성합니다
        columns: list[list] = []          # [시작x, 끝x, [단어들]]
        for segments in header_rows:
            for start, end, word in segments:
                for col in columns:
                    if start <= col[1] + 2 and end >= col[0] - 2:
                        col[0] = min(col[0], start)
                        col[1] = max(col[1], end)
                        col[2].append(word)
                        break
                else:
                    columns.append([start, end, [word]])
        columns.sort(key=lambda c: c[0])
        titles = [" ".join(c[2]) for c in columns]

        adj_idx = gaap_idx = None
        for i, title in enumerate(titles):
            if not _PER_SHARE_COL_RE.search(title):
                continue
            if _ADJ_COL_RE.search(title):
                adj_idx = i if adj_idx is None else adj_idx
            elif gaap_idx is None:
                gaap_idx = i
        if adj_idx is None:
            continue                      # 논갭 열이 없는 표 — 지어내지 않음

        # ③ 구분선 아래에서 "이번 분기" 행을 찾습니다 (각주 * 에서 중단)
        blanks = 0
        for k in range(sep_idx + 1, min(sep_idx + 30, len(lines))):
            row = lines[k]
            if not row.strip():
                blanks += 1
                if blanks >= 2:
                    break
                continue
            blanks = 0
            if row.lstrip().startswith("*"):
                break
            if not _CURRENT_ROW_RE.search(row):
                continue

            # ④ 첫 칸(행 이름)을 뺀 값 칸 수가 열 수와 같을 때만 짝지음
            cells = _line_segments(row)[1:]
            if len(cells) != len(columns):
                break                     # 구조가 어긋남 — 없음이 안전
            result["adj_eps"] = _cell_per_share(cells[adj_idx][2])
            if gaap_idx is not None:
                result["gaap_eps"] = _cell_per_share(cells[gaap_idx][2])
            return result
    return result


# ---------------------------------------------------------------------------
# 날짜 열 표 파서 — 열 제목이 분기(Q2-2025 …)인 한 줄 표 (실물: TSLA)
# ---------------------------------------------------------------------------
# TSLA 슬라이드 표는 텍스트로 눌리면 한 줄이 됩니다:
#   "... Q2-2025 Q3-2025 Q4-2025 Q1-2026 Q2-2026 YoY
#    EPS ... diluted (non-GAAP) 0.40 0.50 0.50 0.41 0.33 -18% ..."
# 열 순서가 과거→현재라 "첫 숫자" 규칙은 1년 전 값을 뭅니다 (사고 9 —
# 이 때문에 TSLA 자동 추출을 차단했었습니다). 여기서는 날짜를 읽어
# **가장 최신 분기 열**을 고릅니다. 열 순서가 반대여도 동작합니다.

_PERIOD_TOKEN_RE = re.compile(r"\b(?:Q([1-4])[- ]20(\d{2})|([1-4])Q[- ]20(\d{2}))\b")
_DATE_HEADER_RE = re.compile(
    r"(?:(?:Q[1-4][- ]20\d{2}|[1-4]Q[- ]20\d{2})\s+){2,}"
    r"(?:Q[1-4][- ]20\d{2}|[1-4]Q[- ]20\d{2})"
)
# 표 칸 하나: (1,092) / 0.33 / 17.2% / — / $3,401
_DATE_CELL_RE = re.compile(r"\(?\$?-?[\d,]+(?:\.\d+)?\)?%?|—")
_DATE_EPS_LABELS = {
    # 값이 이름 **뒤 괄호**에 (non-GAAP)/(GAAP) 로 붙는 형식 (실물: TSLA)
    "adj_eps": re.compile(r"EPS[^()\n]{0,80}\(non[-\s]?GAAP\)", re.I),
    "gaap_eps": re.compile(r"EPS[^()\n]{0,80}\((?<!non-)(?<!non )GAAP\)", re.I),
}


def _period_key(match: re.Match) -> tuple[int, int]:
    quarter = int(match.group(1) or match.group(3))
    year = 2000 + int(match.group(2) or match.group(4))
    return (year, quarter)


def find_eps_in_date_column_table(text: str) -> dict:
    """날짜 열 표에서 (조정 EPS, GAAP EPS)를 읽습니다. 확신이 없으면 없음.

    확신 조건: ① 분기 표기 3개 이상이 연달아 나오는 열 제목이 있고
    ② 그 뒤의 EPS 행에서 열 수만큼의 칸을 정확히 읽을 수 있어야 하며
    ③ 고른 칸(최신 분기 열)이 소수점 표기여야 한다 (사고 15 규칙).
    """
    result = {"adj_eps": None, "gaap_eps": None}
    if not text:
        return result
    for line in text.split("\n"):
        header = _DATE_HEADER_RE.search(line)
        if not header:
            continue
        periods = list(_PERIOD_TOKEN_RE.finditer(header.group(0)))
        if len(periods) < 3:
            continue
        # 열 순서와 무관하게 "가장 최신 분기"의 열 번호를 고릅니다
        latest_idx = max(range(len(periods)), key=lambda i: _period_key(periods[i]))
        body = line[header.end():]

        for field, label_re in _DATE_EPS_LABELS.items():
            if result[field] is not None:
                continue
            label = label_re.search(body)
            if not label:
                continue
            # 이름 바로 뒤에서 열 수만큼의 칸을 연달아 읽습니다.
            # 칸 사이에 글자가 끼면(=다음 행 이름) 구조가 어긋난 것 — 없음.
            cells = []
            pos = label.end()
            for _ in range(len(periods)):
                while pos < len(body) and body[pos] == " ":
                    pos += 1
                cell = _DATE_CELL_RE.match(body, pos)
                if cell is None:
                    break
                cells.append(cell.group(0))
                pos = cell.end()
            if len(cells) != len(periods):
                continue
            result[field] = _cell_per_share(cells[latest_idx])
    return result


# ---------------------------------------------------------------------------
# 보도자료 텍스트 → 실적 숫자
# ---------------------------------------------------------------------------
def parse_press_release(text: str) -> dict:
    """실적 보도자료 텍스트에서 필요한 숫자를 뽑아냅니다.

    반환 딕셔너리:
      revenue           매출 (달러)
      op_income         논갭 영업이익 (달러)
      gross_margin_pct  논갭 매출총이익률 (%)
      source            "직접공시" / "역산" / None (못 찾음)
      gm_is_gaap        GM%가 GAAP 값이면 True
      adj_eps           조정(논갭) 주당순이익 — 규정상 존재가 보장된 값
      gaap_eps          GAAP 주당순이익 — 조정 EPS 와 짝지어 '이익의 질'을 봅니다
    """
    result: dict = {
        "revenue": None,
        "op_income": None,
        "gross_margin_pct": None,
        "adjusted_ebitda": None,   # 논갭 영업이익이 없을 때 역산에 씁니다
        "adj_eps": None,
        "gaap_eps": None,
        "source": None,
        "gm_is_gaap": False,
        "derivation": "",   # 어떻게 구한 값인지 사람이 읽을 수 있는 설명
    }
    if not text:
        return result

    # 주당순이익은 다른 항목의 성공 여부와 무관하게 항상 읽어 둡니다.
    # 미국 증권규정이 조정 EPS 옆에 GAAP EPS 를 나란히 싣도록 요구하기 때문에,
    # 논갭 영업이익을 못 찾은 보도자료에서도 이 둘은 잡히는 경우가 많습니다.
    result["adj_eps"] = find_eps_value(text, LABELS_ADJUSTED_EPS)
    result["gaap_eps"] = find_eps_value(
        text, LABELS_GAAP_EPS, exclude_nongaap=True
    )
    if result["gaap_eps"] is None:
        # 숫자가 이름 앞에 오는 문장 (50차) — 이름 뒤 탐색이 실패한 뒤에만
        result["gaap_eps"] = find_eps_before_per_share(text)

    # 문장형으로 못 찾았을 때만 열 방향 표를 시도합니다 (실물: CGNX —
    # 열 제목이 여러 줄에 쌓인 표. 확신이 안 서면 그대로 없음).
    if result["adj_eps"] is None:
        table = find_eps_in_column_table(text)
        if table["adj_eps"] is not None:
            result["adj_eps"] = table["adj_eps"]
            if result["gaap_eps"] is None:
                result["gaap_eps"] = table["gaap_eps"]

    result["revenue"] = find_labeled_value(text, LABELS_REVENUE)
    # ZETA·TSLA·APP 처럼 논갭 영업이익을 발표하지 않는 회사가 많습니다.
    # 조정 EBITDA 를 챙겨 두었다가, 감가상각비를 빼서 논갭 영업이익을 역산합니다.
    result["adjusted_ebitda"] = find_labeled_value(text, LABELS_ADJUSTED_EBITDA)

    # ① 논갭 GM% — 없으면 GAAP GM%로 대체
    gm = find_labeled_value(text, LABELS_NONGAAP_GM_PCT, is_percent=True)
    if gm is None:
        gm = find_labeled_value(text, LABELS_GAAP_GM_PCT, is_percent=True)
        if gm is not None:
            result["gm_is_gaap"] = True
    result["gross_margin_pct"] = gm

    # ② 논갭 영업이익이 직접 적혀 있는가?
    op = find_labeled_value(text, LABELS_NONGAAP_OP_INCOME)
    if op is not None:
        result["op_income"] = op
        result["source"] = cfg.SRC_DIRECT
        result["derivation"] = (
            "보도자료의 GAAP→non-GAAP 조정표에 적힌 "
            f"non-GAAP 영업이익 값을 그대로 사용했습니다 (${op/1e6:,.1f}M)."
        )
        _sanity_check_press(result)   # ← 직접공시 경로에서도 반드시 검사
        return result

    # ③ 없으면 역산: 매출 × GM% − 논갭 영업비용
    opex = find_labeled_value(text, LABELS_NONGAAP_OPEX)
    if result["revenue"] is not None and gm is not None and opex is not None:
        gross_profit = result["revenue"] * (gm / 100.0)
        result["op_income"] = gross_profit - abs(opex)
        result["source"] = cfg.SRC_DERIVED
        result["derivation"] = (
            "보도자료에 non-GAAP 영업이익이 없어 아래 식으로 역산했습니다.\n\n"
            f"매출 ${result['revenue']/1e6:,.1f}M × GM {gm:.1f}% "
            f"= 매출총이익 ${gross_profit/1e6:,.1f}M\n\n"
            f"매출총이익 ${gross_profit/1e6:,.1f}M − 영업비용 ${abs(opex)/1e6:,.1f}M "
            f"= **${result['op_income']/1e6:,.1f}M**"
        )

    # ④ 그래도 없으면 조정 EBITDA 에서 감가상각비를 빼 역산합니다.
    #    감가상각비는 XBRL 에서 오므로 여기서는 값만 챙겨 두고,
    #    실제 계산은 merge_quarters 에서 XBRL 감가상각비와 만났을 때 합니다.
    if result["op_income"] is None and result["adjusted_ebitda"] is not None:
        result["source"] = None      # 아직 확정 아님 — merge 에서 정해집니다
        result["derivation"] = (
            "보도자료에 non-GAAP 영업이익이 없어 조정 EBITDA "
            f"${result['adjusted_ebitda']/1e6:,.1f}M 에서 감가상각비를 빼 역산할 예정입니다."
        )

    _sanity_check_press(result)
    return result


def _sanity_check_press(result: dict) -> None:
    """뽑아낸 숫자끼리 앞뒤가 맞는지 마지막으로 확인합니다.

    매출은 영업이익보다 커야 합니다(영업이익은 매출에서 비용을 뺀 값이므로).
    이 관계가 깨졌다면 매출 자리에 엉뚱한 숫자(퍼센트·주당 금액 등)가
    들어온 것이므로, 그 매출은 채택하지 않고 비워 둡니다.
    비워 두면 XBRL에서 온 매출이 그대로 남아 계산이 이어집니다.
    """
    revenue, op_income = result.get("revenue"), result.get("op_income")
    ebitda = result.get("adjusted_ebitda")

    # 조정 EBITDA 검사 (8차 감사 후속 — 실물 오류 2건에서 규칙을 만듦):
    # ① $10만 미만은 주당 금액·퍼센트가 잘못 잡힌 것 (실물: FSLR 에서 1.0)
    if ebitda is not None and abs(ebitda) < 100_000:
        result["rejected_ebitda"] = ebitda
        result["adjusted_ebitda"] = ebitda = None
    # ② EBITDA 가 매출보다 크면 산수 모순 — **매출 쪽을** 버립니다.
    #    (실물: ZETA 에서 가이던스 상향 폭 $33M 이 매출로 잘못 잡힘. 매출은
    #    버려도 XBRL 원문이 대신 남지만 EBITDA 는 대체재가 없고, EBITDA 는
    #    라벨에 붙어 뽑혀 오답 확률이 낮습니다)
    if ebitda is not None and revenue is not None and ebitda > revenue:
        result["rejected_revenue"] = revenue
        result["revenue"] = revenue = None

    if revenue is None or op_income is None:
        return
    # 흑자면 영업이익이 매출의 90%를 넘을 수 없습니다.
    # 적자는 비용이 매출을 넘을 수 있으므로 마진 하한(-500%)으로만 봅니다.
    too_big = op_income > 0 and op_income > revenue * 0.9
    too_deep = op_income < 0 and op_income / revenue * 100.0 < cfg.MARGIN_MIN_PCT
    if revenue <= 0 or too_big or too_deep:
        result["rejected_revenue"] = revenue
        result["revenue"] = None


# 회계 분기 이름 뽑기 — 예: "first quarter of fiscal 2025" → "FY2025 Q1"
_QUARTER_WORDS = {"first": 1, "second": 2, "third": 3, "fourth": 4}
_QUARTER_RE = re.compile(
    r"(first|second|third|fourth)\s+quarter[^.]{0,40}?(?:fiscal\s+(?:year\s+)?)?(20\d{2})",
    re.I,
)
_QUARTER_RE_SHORT = re.compile(r"\bQ([1-4])\s*(?:of\s*)?(?:FY\s*)?(20\d{2}|\d{2})\b", re.I)


def extract_period_label(text: str, filing_date: str) -> str:
    """보도자료에서 "몇 년 몇 분기"인지 알아냅니다. 못 찾으면 제출일로 대신합니다.

    차트 가로축에 들어가므로 최대한 짧게 만듭니다 (예: "25 Q3").
    """
    if text:
        match = _QUARTER_RE.search(text)
        if match:
            return f"{match.group(2)[2:]} Q{_QUARTER_WORDS[match.group(1).lower()]}"
        match = _QUARTER_RE_SHORT.search(text)
        if match:
            year = match.group(2)
            year = year[2:] if len(year) == 4 else year
            return f"{year} Q{match.group(1)}"
    # 분기 표현을 못 찾으면 제출 연월만 짧게 표시 (예: "25/05")
    return f"{filing_date[2:4]}/{filing_date[5:7]}"


def orphan_counts(series: dict, period_ends, start_date: str) -> dict:
    """분기 목록에 **짝이 없어 버려지는** XBRL 사실을 셉니다 (106차 계기).

    분기 목록(`period_ends`)은 **논갭 영업이익에서만** 만들어집니다.
    다른 항목은 그 날짜를 열쇠로 찾으므로, 영업이익이 없는 분기의 값은
    찾아 놓고도 버려집니다.

    실물로 그 꼴이 보였습니다 — BAC 는 `Revenues` 가 55건 살아남는데
    스냅샷의 `revenue_xbrl` 은 44행 전부 비어 있고, 대신 보도자료의
    쓰레기 값(8달러)이 들어갔습니다. 은행·보험은 `OperatingIncomeLoss`
    를 신고하지 않습니다(이자수익·대손충당금 구조).

    ⚠️ **세기만 합니다.** 분기 목록의 뼈대를 바꾸면 모든 종목의 행이
    달라져 판정까지 흔들리므로, 먼저 숫자를 보고 고칠지 정합니다.
    """
    보유 = set(period_ends)
    out = {"분기목록(영업이익 기준)": len(보유)}
    for key in ("revenue", "gaap_eps", "gross_profit"):
        있는날 = {d for d in (series.get(key) or {}) if d >= start_date}
        out[key] = len(있는날 - 보유)
    return out


def period_end_label(period_end: str) -> str:
    """기간종료일(2026-01-31)을 짧은 표시로 바꿉니다 → "26/01" """
    return f"{period_end[2:4]}/{period_end[5:7]}"


# ---------------------------------------------------------------------------
# 캐시 (한 번 받은 데이터는 저장해 두고 재사용)
# ---------------------------------------------------------------------------
def _cache_path(ticker: str) -> str:
    os.makedirs(cfg.CACHE_DIR, exist_ok=True)
    return os.path.join(cfg.CACHE_DIR, f"{ticker}_fundamentals.json")


def load_cache(ticker: str, ttl_days: int | None = None) -> list[dict] | None:
    """저장해 둔 실적 데이터를 불러옵니다. 너무 오래됐으면 None을 돌려줍니다."""
    if ttl_days is None:
        ttl_days = cfg.CACHE_TTL_DAYS
    path = _cache_path(ticker)
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            payload = json.load(f)
        saved_at = datetime.fromisoformat(payload["saved_at"])
        if datetime.now(timezone.utc) - saved_at > timedelta(days=ttl_days):
            return None
        return payload["quarters"]
    except Exception:
        return None  # 캐시 파일이 깨졌으면 무시하고 새로 받습니다


def save_cache(ticker: str, quarters: list[dict]) -> None:
    """받아온 실적 데이터를 파일로 저장합니다 (다음 실행 때 재사용)."""
    try:
        with open(_cache_path(ticker), "w", encoding="utf-8") as f:
            json.dump(
                {"saved_at": datetime.now(timezone.utc).isoformat(), "quarters": quarters},
                f,
                ensure_ascii=False,
                indent=1,
            )
    except OSError:
        pass  # 저장에 실패해도 앱은 계속 동작해야 합니다


# ---------------------------------------------------------------------------
# SEC에서 실제로 받아오기
# ---------------------------------------------------------------------------
# 마지막으로 SEC에 알린 신원 (bool이 아니라 문자열로 둡니다 — 아래 설명 참고)
_configured_identity: str | None = None

# ⚠️ 이 자물쇠가 없으면 전 종목 실적이 통째로 비는 사고가 납니다.
#    edgartools의 set_identity()는 신원만 바꾸는 게 아니라 **여러 종목이 함께 쓰는
#    접속 창구(HTTP 클라이언트)를 닫고 새로 엽니다.** 여러 종목을 동시에 받는 도중
#    다른 종목이 set_identity를 또 부르면, 이미 요청을 보내던 쪽의 창구가 닫혀
#    "Cannot send a request, as the client has been closed" 오류로 실패합니다.
_identity_lock = threading.Lock()


def _ensure_identity() -> None:
    """SEC는 요청자 신원을 요구합니다. 신원이 바뀔 때만 다시 설정합니다.

    신원은 config.get_sec_identity()가 결정합니다
    (환경변수 SEC_IDENTITY에 이메일이 있으면 그것, 없으면 기본값).

    '한 번만' 이 아니라 '바뀌면 다시' 인 이유:
    Streamlit Secrets에서 신원을 고쳤을 때 앱을 껐다 켜지 않아도 반영되어야 합니다.
    (예전에는 최초 1회만 설정해, 설정을 고쳐도 옛 신원으로 계속 요청했습니다)
    """
    global _configured_identity

    wanted = cfg.get_sec_identity()
    if _configured_identity == wanted:
        return

    with _identity_lock:
        # 자물쇠를 얻는 사이 다른 쪽이 먼저 설정했을 수 있으니 한 번 더 확인합니다
        if _configured_identity == wanted:
            return

        if _configured_identity is None:
            # 최초 1회 — 아직 접속 창구가 없으므로 정식 함수를 써도 안전합니다
            from edgar import set_identity

            set_identity(wanted)
        else:
            # 신원이 바뀐 경우: set_identity()를 다시 부르면 이미 열려 있는 접속 창구를
            # 닫아버려, 다른 종목이 요청 중이면 그 요청이 끊깁니다.
            # edgartools는 요청할 때마다 이 환경변수를 읽으므로, 값만 바꿔 두면
            # 다음에 새로 열리는 접속부터 새 신원이 적용됩니다.
            os.environ["EDGAR_IDENTITY"] = wanted

        _configured_identity = wanted


def _회사(ticker: str, report: dict | None = None):
    """티커로 SEC 회사를 엽니다. 티커표에서 사라진 종목은 **번호(CIK)** 로 엽니다.

    왜 필요한가 (159차 실측):
      유니버스를 401종목으로 넓힌 뒤, 6개 종목(ZI·CFLT·DFS·HOLX·X·HES)이
      8일 내내 똑같은 오류로 **한 건도** 수집되지 않았습니다
      ("Company not found"). SEC가 주는 티커→회사번호 표는 **지금 상장된
      티커만** 담고 있어서, 티커를 바꾼 회사(ZI→GTM)나 인수되어 상장이
      끝난 회사는 이름표에서 빠집니다. 회사가 낸 공시는 번호로는 그대로
      남아 있으므로, 번호를 알면 되살아납니다.

    번호를 모르는 종목은 예전처럼 티커로 열어 보고, 실패하면 그대로
    실패로 둡니다 — 짐작한 번호를 넣지 않습니다(헌법 1조).
    """
    from edgar import Company

    cik = cfg.TICKER_CIK.get(ticker)
    if cik is not None:
        if report is not None:
            report["회사번호로_열었음"] = cik
        return Company(cik)
    return Company(ticker)


def 사라진회사_찾아보기(ticker: str, report: dict | None = None) -> dict:
    """티커로 회사를 못 찾았을 때, 이름으로 SEC 를 뒤져 **기록만** 합니다 (159차).

    ⚠️ 찾은 번호를 **쓰지 않습니다.** 이 개발 환경에서는 SEC 접속이 막혀
    있어 번호가 맞는지 확인할 방법이 없습니다. 그래서 로봇이 실제로
    무엇을 찾았는지 로그에 실어 오게 하고, 사람이 그 숫자를 보고
    `config.TICKER_CIK` 에 넣습니다 (짐작 전에 계기 — 106·157차 규칙).

    돌려주는 것: {"이름": 찾을 때 쓴 이름,
                 "찾음": [[회사이름, 번호, 지금티커], …]}
    이름표에 없는 티커면 빈 dict — 아무 일도 하지 않습니다.
    """
    name = cfg.TICKER_NAME_HINT.get(ticker)
    if not name:
        return {}
    찾음: list[list] = []
    try:
        from edgar import find_company

        results = find_company(name, top_n=3)
        # edgartools 의 검색 결과는 results.results 라는 표(cik·ticker·company)
        for row in results.results.itertuples():
            찾음.append([str(row.company), str(row.cik), str(row.ticker)])
    except Exception as exc:      # 검색이 깨져도 수집 전체를 멈추지 않습니다
        찾음 = [["검색실패", f"{type(exc).__name__}: {str(exc)[:80]}", ""]]
    out = {"이름": name, "찾음": 찾음}
    if report is not None:
        report["사라진회사_검색"] = out
    return out


def new_report(ticker: str) -> dict:
    """수집 과정을 기록할 '진단 리포트'를 만듭니다.

    8-K 수집이 실패해도 예외가 조용히 삼켜져 원인을 알 수 없던 문제를 해결하기 위해,
    각 단계에서 몇 건이 통과했는지를 남깁니다. 화면의 '데이터 수집 진단'에 표시됩니다.
    """
    return {
        "ticker": ticker,
        "filings_found": 0,        # 8-K 공시를 몇 건 찾았나
        "text_ok": 0,              # 보도자료 텍스트를 확보한 건수
        "gate_passed": 0,          # 실적발표로 판별된 건수
        "parsed_ok": 0,            # 숫자 추출에 성공한 건수
        # ↓ 1단계에서 새로 재는 것 — "조정 EPS 를 논갭 영업이익보다 잘 읽어 오는가"
        #   이 두 숫자를 배포 환경에서 비교해 보고 모델의 기준자를 바꿀지 결정합니다.
        "op_income_ok": 0,         # 논갭 영업이익을 뽑아낸 건수
        "adj_eps_ok": 0,           # 조정 EPS 를 뽑아낸 건수
        "gaap_eps_ok": 0,          # GAAP EPS 를 뽑아낸 건수 (이익의 질 검사용)
        "xbrl_quarters": 0,        # XBRL로 만든 분기 수
        "merged_direct": 0,        # 8-K 값으로 덮어쓴 분기 수
        "cache_hits": 0,           # 원문 캐시에서 읽은 공시 수 (26차 증분 수집)
        "cache_downloads": 0,      # SEC에서 새로 내려받아 **텍스트를 얻은** 수
        # 아래 셋은 145차 — 보이지 않던 접속을 드러내기 위해 추가
        "fetch_attempts": 0,       # SEC 접속을 **시도한** 총 횟수(빈 결과 포함)
        "negative_cached": 0,      # "실적 문서 아님"으로 새로 기억한 공시 수
        "negative_hits": 0,        # 그 기억 덕에 다시 안 받은 공시 수
        "text_source": "",         # 텍스트를 어디서 얻었나 (보도자료/첨부/본문)
        "first_error": "",         # 첫 예외 (화면 요약용)
        "all_errors": [],          # 그 뒤의 예외들 — 첫 것만 보면 진짜 실패를 놓칩니다
        "unpaired_press": 0,       # 분기와 짝을 못 찾은 8-K 건수
        # 그 8-K 들의 **날짜** (150차-AA). 개수만으로는 "어느 분기가
        # 빠졌나"를 알 수 없습니다 — GS 의 1월 발표가 그래서 안 보였습니다.
        "unpaired_dates": [],
        "hole_filled": 0,          # 구멍 메우기로 새로 끼운 분기 수 (150차-Q)
        "pair_note": "",           # 짝짓기 실패 설명
        "forward_note": "",        # 전망(컨센서스) 수집 실패 사유
        "note": "",
        # 조정 EPS 를 읽지 못한 보도자료 원문 (측정용 저장 대상 — 전략.md 8장 1단계)
        # 개발 환경은 SEC 가 차단되어 실물을 볼 수 없으므로, 실패한 원문을
        # 배포된 앱이 저장소로 커밋해 주면 다음 세션이 그 실물로 파서를 고칩니다.
        "raw_texts": [],
        # 시간 예산에 걸려 옛 분기를 못 훑고 멈췄나 (94차 — 10년 확장 안전장치)
        "시간초과": False,
    }


# ---------------------------------------------------------------------------
# 수집 시간 예산 (94차 — 10년 확장 안전장치)
# ---------------------------------------------------------------------------
# 깃허브 액션은 180분에 작업을 강제로 끊습니다. 끊기면 그날 수집물도,
# 애써 내려받은 공시 원문 캐시도 **둘 다** 사라집니다. 10년치를 한 번에
# 받으려다 매번 이 덫에 걸리면 영영 못 받습니다.
#
# 그래서 로봇이 시작할 때 "이 시각까지만 8-K 를 훑는다"는 마감 시각을
# 정해 두고, 넘으면 지금까지 받은 만큼으로 멈춥니다. 8-K 는 최신 것부터
# 훑으므로 잘리는 쪽은 항상 **가장 오래된 분기**이고 최근 분기는 온전합니다.
# 받은 원문은 캐시에 남으니 다음 런이 그만큼 공짜로 앞서 나갑니다.
#
# 예산을 안 걸면(기본) 마감이 없어 예전과 똑같이 동작합니다 — 테스트와
# 개발 환경은 이 길로 갑니다.
_DEADLINE_LOCK = threading.Lock()
_DEADLINE: float | None = None


def set_collect_budget(minutes: float | None, clock=None) -> None:
    """지금부터 minutes 분 뒤를 8-K 훑기의 마감으로 정합니다.

    minutes 가 None 이거나 0 이하면 마감을 없앱니다(무제한).
    clock 은 시험에서 가짜 시계를 끼우기 위한 자리입니다.
    """
    global _DEADLINE
    if clock is None:
        import time as _time
        clock = _time.monotonic
    with _DEADLINE_LOCK:
        _DEADLINE = None if not minutes or minutes <= 0 else clock() + minutes * 60.0


def _budget_over(clock=None) -> bool:
    """마감을 넘겼는가. 마감이 없으면 항상 False."""
    with _DEADLINE_LOCK:
        deadline = _DEADLINE
    if deadline is None:
        return False
    if clock is None:
        import time as _time
        clock = _time.monotonic
    return clock() >= deadline


# 원문 부탁 목록 (73차) — 조사(audit_data.py)가 "이 공시는 값을 읽었더라도
# 원문이 필요하다"고 적어 둔 목록입니다.
# ---------------------------------------------------------------------------
# 왜 필요한가: 지금 보관되는 원문은 "잣대값을 하나도 못 읽은" **실패** 공시
# 뿐입니다. 그래서 VZ 5.18 · GS 59.45 처럼 **잘못 읽은** 값의 원인을 볼
# 방법이 없었습니다 (개발 환경은 SEC 차단). 목록에 적힌 공시는 값을
# 읽었어도 원문을 함께 담아 옵니다. **값은 지우지 않습니다.**
_WANTED_CACHE: dict | None = None


def _wanted_raw_set() -> set[tuple[str, str]]:
    """{(종목, 발표일)} 집합. 파일이 없거나 깨졌으면 빈 집합 (조용히 넘어감)."""
    global _WANTED_CACHE
    if _WANTED_CACHE is None:
        wanted: set[tuple[str, str]] = set()
        try:
            with open(os.path.join(cfg.MEASURE_DIR, "wanted_raw.json"),
                      encoding="utf-8") as f:
                for row in (json.load(f).get("목록") or []):
                    종목, 날짜 = row.get("종목"), str(row.get("발표일") or "")[:10]
                    if 종목 and 날짜:
                        wanted.add((종목, 날짜))
        except (OSError, ValueError, AttributeError):
            pass          # 목록이 없어도 수집은 그대로 돌아야 합니다
        _WANTED_CACHE = wanted
    return _WANTED_CACHE


def _is_wanted_raw(ticker: str, filing_date) -> bool:
    """이 공시가 '원문 부탁 목록'에 있는가."""
    return (ticker, str(filing_date)[:10]) in _wanted_raw_set()


def _should_keep_raw(had_exhibit: bool, parsed: dict, text: str) -> bool:
    """이 보도자료 원문을 고칠 재료로 보관할 것인가.

    보관 자리는 종목당 몇 칸뿐이라 **무엇을 넣느냐가 중요합니다.**

    60차 — 조건을 "조정 EPS 를 못 읽음"에서 "**잣대값을 하나도** 못 읽음"
    으로 좁혔습니다. 잣대 사다리는 셋(조정EPS·EBITDA·GAAP EPS) 중 아무거나
    8분기면 되는데, 옛 조건은 GAAP EPS 나 EBITDA 를 멀쩡히 읽은 원문까지
    보관했습니다. 실측: 보관된 153건을 지금 파서로 다시 읽어 보니
    **47건(31%)이 이미 잣대값을 읽을 수 있는 것**이었고, 그것들이 정작
    막힌 종목이 써야 할 자리를 차지하고 있었습니다.

    보도자료 첨부(EX-99)가 아니거나 실적발표로 보이지 않으면 보관하지
    않습니다 — 파트너십 발표 같은 비실적 8-K 가 자리를 차지한 실물 사고가
    있었습니다(LITE·COHR 2026-03-02).
    """
    if not had_exhibit:
        return False
    if any(parsed.get(f) is not None
           for f in ("adj_eps", "adjusted_ebitda", "gaap_eps")):
        return False
    return _looks_like_earnings(text)


def _keep_raw_text(report: dict, filing_date: str, url: str, text: str) -> None:
    """조정 EPS 를 읽지 못한 보도자료 원문을 진단 리포트에 남깁니다.

    파싱 실패 사례가 가장 값진 디버깅 자료입니다 — 지금까지 파서가 계속
    어긋난 이유가 "가짜(지어낸) 예제로만 테스트해서"였기 때문입니다.
    저장소가 무한히 커지지 않게 종목당 **건수·글자수·총량** 셋 다 막습니다.

    총량 상한이 왜 따로 필요한가 (60차): 건수만 막으면 최악의 경우
    12건 × 120,000자 = 1.4MB 가 한 종목에 쌓입니다. 실패 종목이 66개면
    저장소가 93MB 까지 부풀 수 있습니다. 실측 평균은 23KB 라 보통은
    문제가 안 되지만, **보통을 믿고 상한을 안 두면 언젠가 터집니다.**
    """
    kept = report.setdefault("raw_texts", [])
    if len(kept) >= cfg.MEASURE_RAW_MAX:
        return
    piece = text[: cfg.MEASURE_RAW_TEXT_CAP]
    if sum(len(k["text"]) for k in kept) + len(piece) > cfg.MEASURE_RAW_TOTAL_CAP:
        return
    kept.append(
        {
            "filing_date": filing_date,
            "url": url,
            "text": piece,
        }
    )


def fetch_earnings_8k(
    ticker: str,
    start_date: str | None = None,
    report: dict | None = None,
) -> list[dict]:
    """한 종목의 8-K 실적발표를 모두 찾아 숫자를 뽑아냅니다.

    반환: 분기별 실적 목록 (오래된 것부터 순서대로)
    report: 진단 리포트 (전달하면 단계별 건수를 기록합니다)
    """
    if start_date is None:
        start_date = cfg.HISTORY_START_DATE
    if report is None:
        report = new_report(ticker)

    # 표가 과거→현재 열 순서인 회사(TSLA)는 일반 파서가 첫 열(1년 전 값)을
    # 뭅니다 (사고 9). 그래서 이런 회사는 **날짜 열 EPS 전용 부분 파싱**만
    # 합니다 — 날짜 열 파서는 최신 분기 열을 고르므로 안전하고,
    # 매출 등 다른 항목은 계속 차단합니다 ("없음"이 "틀림"보다 안전).
    eps_only = ticker in cfg.PRESS_PARSE_SKIP
    if eps_only:
        report["note"] = (
            "보도자료 형식 특수(config.PRESS_PARSE_SKIP) — "
            "날짜 열 EPS 만 부분 파싱, 나머지는 XBRL 근사"
        )

    _ensure_identity()

    quarters: list[dict] = []
    company = _회사(ticker, report)
    filings = company.get_filings(form="8-K", filing_date=f"{start_date}:")

    scanned = 0
    for filing in filings:
        # 필요한 만큼 숫자를 확보했으면 더 내려받지 않습니다 (속도)
        if report["parsed_ok"] >= cfg.EARLY_STOP_PARSED:
            report["note"] = f"실적 {report['parsed_ok']}건 확보 후 조기 종료"
            break
        # 8-K는 실적 외 사유로도 자주 올라오므로 살펴볼 건수를 제한합니다
        if scanned >= cfg.MAX_8K_SCAN:
            report["note"] = f"8-K {cfg.MAX_8K_SCAN}건까지만 확인했습니다"
            break
        # 시간 예산 초과 — **새 내려받기만** 다음 런으로 미룹니다 (94차→153차)
        #
        # ⚠️ 단, 바닥(COLLECT_BUDGET_FLOOR)을 채우기 전에는 멈추지 않습니다.
        #    예산은 전체 시계로 재는데 종목은 순서대로 처리되므로, 바닥이
        #    없으면 뒷쪽 종목이 8-K 를 **한 건도** 못 훑어 최신 분기까지
        #    잃습니다 — 표본을 늘리려다 있던 표본을 깎는 최악입니다.
        #
        # 153차 수리: 예전에는 여기서 통째로 멈췄습니다(break). 그러자 예산에
        # 걸리는 종목이 날마다 달라서, **캐시에 멀쩡히 있는 옛 분기 값이
        # 사라졌다 돌아왔다** 했습니다 (08-25/26/27 실측: GWW 조정 EPS
        # 10→27→10칸, 하루 ±수백 칸 출렁임 — 측정 이력이 매일 바뀌는 병).
        # 캐시된 원문의 파싱은 네트워크가 아니라 몇 초면 되므로, 예산이
        # 미루는 것은 **내려받기뿐**이어야 합니다. 캐시에 없는 공시만
        # 건너뛰고(시간초과 표시), 캐시에 있는 공시는 계속 읽습니다.
        if report["parsed_ok"] >= cfg.COLLECT_BUDGET_FLOOR and _budget_over() \
                and not _text_in_cache(ticker, filing):
            report["시간초과"] = True
            report["note"] = (
                f"수집 시간 예산 초과 — 새 내려받기는 다음 런으로 미루고 "
                f"캐시된 원문만 읽었습니다 (실적 {report['parsed_ok']}건 시점)"
            )
            continue
        scanned += 1
        report["filings_found"] += 1

        text, text_source, had_exhibit = _earnings_text_cached(ticker, filing, report)
        if not text:
            continue
        report["text_ok"] += 1
        if text_source and not report["text_source"]:
            report["text_source"] = text_source

        # 투자자 설명회 자료는 첨부(EX-99)로 와도 실적발표가 아닙니다 (85차).
        # 이 검사를 아래 표지 판별보다 **먼저** 두는 이유: 설명회 자료는
        # 대개 EX-99 첨부라, 표지 판별을 건너뛰는 길로 그대로 들어옵니다.
        if _looks_like_slide_deck(text):
            continue

        # EX-99 첨부(보도자료)를 확보했다면 표지 문구 판별은 건너뜁니다.
        # 표지에는 숫자가 없어 실적발표인데도 탈락하는 경우가 있기 때문입니다.
        if not had_exhibit and not _looks_like_earnings(text):
            continue
        report["gate_passed"] += 1

        if eps_only:
            table = find_eps_in_date_column_table(text)
            parsed = {
                "revenue": None, "op_income": None, "gross_margin_pct": None,
                "adjusted_ebitda": None, "adj_eps": table["adj_eps"],
                "gaap_eps": table["gaap_eps"], "source": None,
                "gm_is_gaap": False, "derivation": (
                    "날짜 열 표에서 최신 분기 열의 EPS 만 부분 파싱했습니다 "
                    "(다른 항목은 열 순서 문제로 차단 — 사고 9)."
                ),
            }
        else:
            parsed = parse_press_release(text)

        # 보도자료 첨부(EX-99)인데 조정 EPS 를 못 읽었다면 — 파서가 진 것입니다.
        # 그 원문을 진단에 남겨, 배포된 앱의 '측정용 실데이터 저장' 버튼이
        # 저장소로 커밋할 수 있게 합니다. (전략.md 8장 1단계 연료 파이프라인)
        # ⚠️ 실적발표로 보이는 것만 남깁니다. 실제 저장해 보니 파트너십 발표 같은
        #    비실적 8-K(EPS 가 원래 없는 문서)가 보관 상한(종목당 2건)을 차지해
        #    정작 파서가 진 실적 원문이 못 담겼습니다 (실물: LITE·COHR 2026-03-02).
        if _should_keep_raw(had_exhibit, parsed, text):
            _keep_raw_text(
                report, str(filing.filing_date), _safe_filing_url(filing), text
            )

        # 진단 장치 (2026-08-13, 13차 의심 목록): 소수점 규칙을 통과한
        # 정수 EPS(5.0·7.0·8.0 류)가 남아 있는데 출처 원문이 없어 감사가
        # 막혔습니다. 그런 값이 또 나오면 **값은 그대로 두되 원문을 보관**해
        # 다음 세션이 실물로 감사할 수 있게 합니다.
        suspect = parsed["adj_eps"]
        if (suspect is not None and suspect == int(suspect) and abs(suspect) >= 5
                and had_exhibit):
            _keep_raw_text(
                report, f"의심정수_{filing.filing_date}",
                _safe_filing_url(filing), text,
            )

        # 조사(73차)가 콕 집어 부탁한 공시 — 값을 읽었더라도 원문을 담아 옵니다.
        # 다음 세션이 "왜 이 값이 들어왔는지"를 실물로 볼 수 있게 하려는 것이고,
        # **값은 그대로 둡니다** (65차 §④ "표시부터, 지우지 말고").
        if had_exhibit and _is_wanted_raw(ticker, filing.filing_date):
            _keep_raw_text(
                report, f"부탁_{filing.filing_date}",
                _safe_filing_url(filing), text,
            )

        # 실적 숫자가 하나도 없으면 실적발표가 아닐 가능성이 큽니다.
        # 조정 EPS 도 실적 숫자로 인정합니다 — 규정상 존재가 보장된 값이라
        # 매출·영업이익을 못 읽은 보도자료에서도 이것만 잡히는 경우가 있습니다.
        if (
            parsed["revenue"] is None
            and parsed["op_income"] is None
            and parsed["adj_eps"] is None
            and parsed["adjusted_ebitda"] is None
        ):
            continue

        # 보도자료 첨부가 아니라 공시 본문에서 읽은 경우에는 기준을 높입니다.
        # (실적과 무관한 8-K 본문에서 엉뚱한 금액을 주워 담아
        #  분기 짝짓기를 오염시키는 것을 실전에서 확인했습니다)
        if not had_exhibit and parsed["op_income"] is None and parsed["adj_eps"] is None:
            continue
        report["parsed_ok"] += 1
        # 어느 항목이 얼마나 잡히는지 따로 셉니다 — 기준자 교체 판단의 근거가 됩니다.
        if parsed["op_income"] is not None:
            report["op_income_ok"] += 1
        if parsed["adj_eps"] is not None:
            report["adj_eps_ok"] += 1
        if parsed["gaap_eps"] is not None:
            report["gaap_eps_ok"] += 1

        filing_date = str(filing.filing_date)
        quarters.append(
            {
                "ticker": ticker,
                "filing_date": filing_date,
                "period_label": extract_period_label(text, filing_date),
                "revenue": parsed["revenue"],
                "op_income": parsed["op_income"],
                "gross_margin_pct": parsed["gross_margin_pct"],
                "adj_eps": parsed["adj_eps"],
                # 조정 EPS 미발표 회사(ZETA·APP 등)의 대체 잣대. 예전에는 이 복사가
                # 빠져 있어서 _apply_press_to_row 의 EBITDA 역산이 실행 불가능한
                # 위치에 있었습니다 (루멘텀 사건과 같은 유형 — 9차 감사에서 발견).
                "adjusted_ebitda": parsed["adjusted_ebitda"],
                "gaap_eps": parsed["gaap_eps"],
                "source": parsed["source"] or cfg.SRC_DERIVED,
                "gm_is_gaap": parsed["gm_is_gaap"],
                # 화면의 "원문 보기" 링크에 사용 (사용자가 직접 공시를 확인할 수 있도록)
                "filing_url": _safe_filing_url(filing),
                "derivation": parsed.get("derivation", ""),  # 어떻게 계산했는지 설명
                # 전망 문단 위치는 회사마다 제각각 — 앞쪽(결과 요약 직후,
                # AMBA는 23,700자 중 2,700자 지점)부터 맨 끝까지 다양합니다.
                # 끝 N자 창은 21종목의 가이던스를 잘랐음(32차 실측: 코퍼스
                # 56건 → 창 적용 시 18건). 전체 원문을 넘깁니다 — 파서가
                # 앞보는 말·분기 선언 가드로 과거 서술을 걸러냅니다.
                "guidance_text": text,
            }
        )

    quarters.sort(key=lambda q: q["filing_date"])  # 발표일(8-K 제출일) 순 정렬
    return quarters


def _safe_filing_url(filing) -> str:
    """공시 원문 주소를 가져옵니다 (실패해도 앱이 멈추지 않도록 감쌉니다)."""
    for attr in ("filing_url", "url", "homepage_url"):
        try:
            value = getattr(filing, attr, None)
            if value:
                return str(value)
        except Exception:
            continue
    return ""


# 실적발표 공시인지 알아보는 신호들 (회사마다 표현이 달라 여러 개를 봅니다)
_EARNINGS_HINTS = [
    "item 2.02",
    "results of operations and financial condition",
    "financial results",
    "reports first quarter",
    "reports second quarter",
    "reports third quarter",
    "reports fourth quarter",
    "announces first quarter",
    "announces second quarter",
    "announces third quarter",
    "announces fourth quarter",
    "quarterly results",
    "non-gaap",
]


# 합병·인수 발표의 신호 — 실적 신호 없이 이것만 있으면 실적발표가 아닙니다.
# 실물: SWKS·QRVO 2025-10-28 합병 발표가 실적으로 오인되어 ① 원문 보관함
# (종목당 2건)을 차지하고 ② "accretive to EPS" 문구 근처의 금액이 가짜 행을
# 만들 뻔했습니다 (ACLS 2025-10-01 인수 발표의 EBITDA $387M 도 같은 부류).
_MERGER_HINTS_RE = re.compile(
    r"definitive\s+(?:merger\s+)?agreement|to\s+combine|combined\s+company"
    r"|to\s+acquire|agreement\s+to\s+be\s+acquired|all[-\s]cash\s+transaction",
    re.I,
)
_RESULTS_HINTS_RE = re.compile(
    r"reports?\s+(?:first|second|third|fourth)\s+quarter"
    r"|financial\s+results\s+for|quarterly\s+(?:results|revenue)"
    r"|results\s+of\s+operations|item\s+2\.02",
    re.I,
)


# 투자자 설명회 자료(슬라이드 묶음)를 실적발표와 가르는 표시 (85차)
# ---------------------------------------------------------------------------
# 80·84차에 KLAC·QRVO·MKSI 세 건이 **투자자 설명회 자료인데 실적발표로
# 통과**해 엉뚱한 값을 냈습니다(KLAC 4.93·35% 는 모델 가정과 KPI 표,
# MKSI 5.59 는 분기인지 연간인지도 확정 못 한 값). 그때는 근거가 모자라
# 미뤘는데, 세 번째 사례가 나와 실물로 재 봤습니다.
#
# 갈라주는 표시는 **이미지 파일 이름**입니다. 설명회 자료는 장마다 그림이라
# `src="…slide7.jpg"` · `…p14g1.jpg` · `…ex99_1p81g1.jpg` 처럼 **쪽 번호가
# 박힌 이름**을 씁니다. 실적발표문에도 그림(로고·차트)은 있지만 그런
# 이름은 아닙니다.
#
# 저장소 원문 694건 실측 — 경계가 아주 깨끗합니다:
#     쪽 그림 0개   672건   (BAC 19·JPM 14·UNH 17 은 전부 0)
#     쪽 그림 1~9개   2건
#     쪽 그림 10개+  20건   (KLAC 142 · GS 118 · QRVO 95 · MKSI 51 · PG 35)
# 2개와 10개 사이가 비어 있어 문턱을 10으로 둡니다.
_SLIDE_SRC_RE = re.compile(r'src="[^"]*(?:slide|p\d+g\d+|ex99_1p\d)', re.I)
_SLIDE_DECK_MIN = 10


def _looks_like_slide_deck(text: str) -> bool:
    """쪽 번호가 박힌 그림이 여럿이면 투자자 설명회 자료입니다."""
    return len(_SLIDE_SRC_RE.findall(text)) >= _SLIDE_DECK_MIN


def _looks_like_earnings(text: str) -> bool:
    """이 공시가 실적발표인지 대략 판별합니다."""
    lowered = text[:20000].lower()
    if not any(hint in lowered for hint in _EARNINGS_HINTS):
        return False
    # 합병·인수 발표인데 실적 신호가 없으면 실적발표가 아닙니다
    if _MERGER_HINTS_RE.search(lowered) and not _RESULTS_HINTS_RE.search(lowered):
        return False
    # 투자자 설명회 자료는 실적발표가 아닙니다 (위 상자 참조)
    if _looks_like_slide_deck(text):
        return False
    return True


def _record_error(report: dict | None, where: str, exc: Exception) -> None:
    """예외를 진단 리포트에 남깁니다 (원인 파악용).

    ⚠️ 예전에는 **첫 예외만** 남겼습니다. 그런데 8-K 60건을 훑는 동안 앞쪽에서
       사소한 예외 하나가 나면 뒤의 **진짜 실패는 전부 보이지 않았습니다.**
       그래서 첫 예외는 그대로 두되(화면 요약용), 나머지도 목록에 모읍니다.
    """
    if report is None:
        return
    message = f"[{where}] {type(exc).__name__}: {str(exc)[:180]}"
    if not report.get("first_error"):
        report["first_error"] = message
    errors = report.setdefault("all_errors", [])
    if message not in errors and len(errors) < 20:
        errors.append(message)


def _raw8k_cache_path(ticker: str, accession: str) -> str:
    """공시 원문 캐시 파일 경로 (data/raw8k/종목/공시번호.json)."""
    safe = str(accession).replace("/", "-")
    folder = os.path.join(cfg.RAW8K_CACHE_DIR, ticker)
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, f"{safe}.json")


def _text_in_cache(ticker: str, filing) -> bool:
    """이 공시의 원문이 캐시에 있는가 — **네트워크 없이** 확인만 합니다.

    (153차) 시간 예산이 초과됐을 때 "이 공시는 공짜로 읽을 수 있나"를
    가르는 용도입니다. 판 번호가 다른 옛 캐시는 없는 것으로 칩니다
    (읽어 봐야 다시 내려받게 되므로).
    """
    accession = (getattr(filing, "accession_no", None)
                 or getattr(filing, "accession_number", None))
    if not accession:
        return False
    path = _raw8k_cache_path(ticker, accession)
    if not os.path.exists(path):
        return False
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f).get("v") == cfg.RAW8K_CACHE_VERSION
    except Exception:
        return False


def _earnings_text_cached(
    ticker: str, filing, report: dict | None = None
) -> tuple[str, str, bool]:
    """공시 문서는 불변 — 한 번 추출한 텍스트는 캐시에서 읽습니다 (26차 개선).

    · 캐시 열쇠 = 공시 고유번호(accession). 없으면 캐시 없이 그냥 받습니다.
    · 빈 결과는 캐시하지 않습니다 — 일시적 네트워크 실패가 "이 공시엔
      텍스트 없음"으로 영구 박제되는 것을 막기 위해서입니다.
    · 캐시에는 **추출 텍스트**만 저장하고 해석(파싱)은 매번 새로 하므로,
      파서를 고치면 과거 데이터에도 즉시 반영됩니다.
    · 추출 방식 자체가 바뀌면 config.RAW8K_CACHE_VERSION 을 올려 무효화.
    """
    accession = (getattr(filing, "accession_no", None)
                 or getattr(filing, "accession_number", None))
    if accession:
        path = _raw8k_cache_path(ticker, accession)
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    payload = json.load(f)
                if payload.get("v") == cfg.RAW8K_CACHE_VERSION:
                    if report is not None:
                        report["cache_hits"] = report.get("cache_hits", 0) + 1
                        if not payload.get("text"):
                            # 음성 캐시 적중 — 이 공시는 실적 문서가 아니라고
                            # 이미 확인했습니다. 다시 받지 않습니다 (145차).
                            report["negative_hits"] = \
                                report.get("negative_hits", 0) + 1
                    return payload["text"], payload["source"], payload["had_exhibit"]
            except Exception:
                pass    # 깨진 캐시는 무시하고 새로 받습니다

    # 실제 SEC 접속 횟수 (145차) — **성공만 세면 안 됩니다.**
    # 예전에는 텍스트를 얻었을 때만 cache_downloads 를 올렸습니다. 그래서
    # "받아 봤는데 실적 문서가 아니어서 빈 결과"인 공시는 **한 건도 안
    # 세어졌습니다.** 실측: V(비자)는 내려받기 0건으로 기록됐는데 905초가
    # 걸렸습니다 — 보이지 않는 접속이 그만큼 있었다는 뜻입니다.
    # 이 칸이 있어야 SEC 요청 속도를 제대로 잽니다(일꾼 수 판단의 근거).
    if report is not None:
        report["fetch_attempts"] = report.get("fetch_attempts", 0) + 1
    에러전 = len((report or {}).get("all_errors") or [])
    text, source, had_exhibit = _earnings_text(filing, report)
    에러후 = len((report or {}).get("all_errors") or [])

    # 음성 캐시 (145차) — **에러 없이** 비어 있던 공시는 "실적 문서가 아님"
    # 으로 기억해 다음 런에서 다시 받지 않습니다.
    #
    # ⚠️ 원래 빈 결과를 캐시하지 않은 이유는 "일시적 망 실패가 '텍스트
    #    없음'으로 영구 박제되는 것"을 막기 위해서였습니다. 그 걱정은
    #    그대로 옳으므로, **예외가 한 건이라도 났으면 캐시하지 않습니다.**
    #    받아 보긴 했는데 실적 보도자료가 안 붙은 8-K(인사·배당·계약 공시)
    #    만 기억합니다. 판단이 바뀌면 RAW8K_CACHE_VERSION 을 올려 무효화.
    if accession and not text and 에러후 == 에러전:
        try:
            with open(_raw8k_cache_path(ticker, accession), "w",
                      encoding="utf-8") as f:
                json.dump({"v": cfg.RAW8K_CACHE_VERSION, "text": "",
                           "source": "실적문서아님", "had_exhibit": False},
                          f, ensure_ascii=False)
        except OSError:
            pass
        if report is not None:
            report["negative_cached"] = report.get("negative_cached", 0) + 1

    if accession and text:
        try:
            with open(_raw8k_cache_path(ticker, accession), "w", encoding="utf-8") as f:
                json.dump({"v": cfg.RAW8K_CACHE_VERSION, "text": text,
                           "source": source, "had_exhibit": had_exhibit},
                          f, ensure_ascii=False)
        except OSError:
            pass        # 캐시 저장 실패는 수집을 막지 않습니다
    if report is not None and text:
        report["cache_downloads"] = report.get("cache_downloads", 0) + 1
    return text, source, had_exhibit


def _earnings_text(filing, report: dict | None = None) -> tuple[str, str, bool]:
    """8-K에서 실적 보도자료 텍스트를 최대한 확보합니다.

    회사마다 첨부 방식이 달라 아래 순서로 시도합니다:
      ① edgartools가 인식한 보도자료(press release) 첨부
      ② EX-99 계열 첨부파일을 직접 뒤져서 읽기
      ③ 공시 본문 전체

    반환: (텍스트, 어디서 얻었는지, 보도자료 첨부였는지)
    """
    # ① edgartools의 보도자료 인식 기능
    try:
        eightk = filing.obj()
    except Exception as exc:
        _record_error(report, "filing.obj", exc)
        eightk = None

    if eightk is not None:
        try:
            releases = eightk.press_releases
            if releases:
                parts = []
                for release in releases:
                    try:
                        parts.append(release.text())
                    except Exception as exc:
                        _record_error(report, "press_release.text", exc)
                if parts:
                    return "\n".join(parts), "보도자료", True
        except Exception as exc:
            _record_error(report, "press_releases", exc)

    # ② EX-99 계열 첨부파일을 직접 찾아 읽기
    # 회사마다 표기가 달라 종류·설명·파일명·용도를 모두 확인합니다.
    try:
        for attachment in filing.attachments.exhibits:
            haystack = " ".join(
                str(getattr(attachment, field, "") or "")
                for field in ("document_type", "description", "display_description",
                              "document", "purpose")
            ).lower()
            if "99" not in haystack and "press" not in haystack:
                continue
            try:
                content = attachment.text()
            except Exception as exc:
                _record_error(report, "attachment.text", exc)
                continue
            if content and len(content) > 500:
                return content, "EX-99 첨부", True
    except Exception as exc:
        _record_error(report, "attachments.exhibits", exc)

    # ③ 마지막 수단: 공시 본문 전체 (표지만 있을 수 있어 첨부 여부는 False)
    for source, label in ((eightk, "공시 본문"), (filing, "공시 원문")):
        if source is None:
            continue
        try:
            content = source.text()
            if content:
                return content, label, False
        except Exception as exc:
            _record_error(report, "filing.text", exc)
    return "", "", False


# ---------------------------------------------------------------------------
# 마지막 안전망: SEC XBRL로 근사치 계산
# ---------------------------------------------------------------------------
# XBRL은 회사가 SEC에 제출하는 "기계가 읽는 재무데이터"입니다.
# 여기엔 GAAP 숫자만 있으므로, 논갭에 가깝게 되돌리려고
#   GAAP 영업이익 + 주식보상비 + 무형자산상각
# 을 더해 근사치를 만듭니다.

_XBRL_CONCEPTS = {
    "op_income": ["OperatingIncomeLoss"],
    "sbc": [
        "ShareBasedCompensation",
        "AllocatedShareBasedCompensationExpense",
    ],
    # 감가상각비 — 조정 EBITDA 로만 발표하는 회사의 논갭 영업이익을 역산할 때 씁니다.
    #   논갭 영업이익 ≈ 조정 EBITDA − 감가상각비(D&A)
    "depreciation_amortization": [
        "DepreciationDepletionAndAmortization",
        "DepreciationAmortizationAndAccretionNet",
        "DepreciationAndAmortization",
    ],
    "amortization": [
        "AmortizationOfIntangibleAssets",
        "AmortizationOfAcquiredIntangibleAssets",
        "FiniteLivedIntangibleAssetsAmortizationExpense",
    ],
    "revenue": [
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
    ],
    "gross_profit": ["GrossProfit"],
    # GAAP 희석 주당순이익 — 조정 EPS 를 발표하지 않는 회사(TXN·TSLA·FSLR)의
    # 사다리 잣대 재료 (10차 후속 대책 4). 구조화 데이터라 파싱 위험이 없습니다.
    "gaap_eps": ["EarningsPerShareDiluted"],
}

# 항목별 단위 — 적지 않으면 달러(USD). 주당 금액은 단위가 다릅니다.
_XBRL_UNITS = {"gaap_eps": "USD/SHARES"}

# 주당 금액이 "같다"고 볼 차이 (92차)
# ---------------------------------------------------------------------------
# EPS 는 센트 단위로 발표되므로, 후보 둘이 1센트 안이면 같은 값입니다.
_PER_SHARE_CLOSE = 0.01


def _is_per_share(unit: str) -> bool:
    """주당 단위인가 — 글자 그대로가 아니라 낱말로 가릅니다."""
    return "SHARE" in (unit or "").upper()


def _unit_matches(row_unit: str, wanted: str) -> bool:
    """자료의 단위 글자가 우리가 원하는 단위인가.

    주당 단위는 제공처마다 "USD/shares" · "USD-per-shares" · "usd/share"
    처럼 적는 법이 달라 글자 비교로는 새 나갑니다. 주당 단위에는 반드시
    "share" 가 들어가고 금액 단위에는 안 들어가므로 그것으로 가릅니다.
    """
    if _is_per_share(wanted):
        return _is_per_share(row_unit)
    return not _is_per_share(row_unit) and row_unit.upper() == wanted.upper()


def _pick_close_value(unique: list[float], per_share: bool) -> float | None:
    """같은 분기에 후보가 여럿일 때 하나를 고릅니다 (못 고르면 None).

    ⚠️ 92차 — 예전 규칙은 `low > 0 and high / low <= 2.0` 하나뿐이었고,
       그것이 **주당 금액을 거의 다 버리고 있었습니다.** 규칙을 직접
       돌려 본 결과(SEC 접속 없이 재현):

           적자 분기   [-0.31, -0.30]  → 제외   ← 사실상 같은 값인데
           0 근처      [ 0.00,  0.02]  → 제외
           기본/희석   [ 0.10,  0.30]  → 제외
           매출        [154억, 154억]  → 통과

       원인 둘. ⑴ `low > 0` 이라 **적자면 무조건 탈락**한다.
       ⑵ 비율 판정은 값이 작을수록 가혹하다 — 0.10 과 0.30 은 3배지만
       실제 차이는 20센트뿐이다.

       그래서 **주당 금액은 비율이 아니라 절대차**로 본다. 금액은 자릿수가
       커서 비율이 맞으므로 그대로 둔다 (거기까지 손대면 검증 범위가
       넓어져 89차 전망 가드의 실수를 되풀이한다).
    """
    if len(unique) == 1:
        return unique[0]
    low, high = unique[0], unique[-1]
    if per_share:
        # 1e-9 은 부동소수점 여유입니다 — -0.30 − (-0.31) 이 컴퓨터에서는
        # 0.010000000000000009 로 나와 "1센트 이내"를 아슬아슬하게 벗어납니다.
        가까움 = (high - low) <= _PER_SHARE_CLOSE + 1e-9
        return unique[len(unique) // 2] if 가까움 else None
    if low > 0 and high / low <= 2.0:
        return unique[len(unique) // 2]
    return None


def _facts_with_retry(fetch, report: dict | None,
                      tries: int = 3, wait: float = 2.0, sleeper=None):
    """XBRL 조회를 재시도합니다 (155차).

    왜 필요한가: RH 실측 — SEC 가 이따금 SSL 오류를 내면(일시적)
    뼈대가 통째로 비고, merge_quarters 의 대체 경로가 보도자료 행을
    발표일 그대로 승격해 **분기 신원이 틀린 행**(한 날짜에 분기 4개,
    이름 중복)이 생겼습니다. 나흘간 16→24→16→24칸으로 번갈아 흔들린
    원인이 이것입니다.

    돌려주는 값: (facts, 오류였나). 오류로 끝나면 report 에
    xbrl_error 를 찍어 merge_quarters 가 대체 경로를 밟지 않게 합니다
    — "없음"은 안전하고 "틀림"은 위험합니다(헌법 1조).
    """
    if sleeper is None:
        import time as _time
        sleeper = _time.sleep
    for attempt in range(tries):
        try:
            return fetch(), False
        except Exception as exc:
            if attempt == tries - 1:
                _record_error(report, "get_facts", exc)
                if report is not None:
                    report["xbrl_error"] = True
                return None, True
            sleeper(wait * (attempt + 1))
    return None, True


def fetch_xbrl_approximation(
    ticker: str,
    start_date: str | None = None,
    report: dict | None = None,
) -> list[dict]:
    """XBRL에서 분기 실적 근사치를 만듭니다.

    이 결과가 전체 분기의 "뼈대"가 되고, 8-K 파싱에 성공한 분기만
    나중에 공식 논갭 수치로 덮어씁니다.

    화면에는 "근사치" 배지가 붙습니다.
    """
    if start_date is None:
        start_date = cfg.HISTORY_START_DATE

    _ensure_identity()

    # 155차 — 일시적 오류(SSL 등)는 재시도로 흡수하고, 끝내 실패하면
    # xbrl_error 를 찍어 둡니다. "조회 실패"와 "원래 XBRL 없음"은
    # 다른 사건이고 대응이 정반대입니다 (아래 merge_quarters 참고).
    facts, errored = _facts_with_retry(
        lambda: _회사(ticker, report).get_facts(), report)
    if errored or facts is None:
        # 159차 — 티커표에서 사라진 회사면 이름으로 뒤져 **기록만** 합니다.
        #   여기서 찾은 번호는 쓰지 않습니다. 다음 런에서 사람이 보고
        #   config.TICKER_CIK 에 넣습니다.
        사라진회사_찾아보기(ticker, report)
        return []

    # 개념별로 "분기(3개월)" 데이터를 뽑고, 빠진 4분기를 채워 넣습니다
    series: dict[str, dict[str, float]] = {}
    for key in _XBRL_CONCEPTS:
        series[key] = _series_for_key(key, facts, report)

    # 회계연도 **연간** GAAP EPS — 오염을 가릴 **독립된 자** (150차-AL).
    # 이 계열은 12개월짜리라 열쇠(기간종료일)가 곧 **결산일**입니다.
    # 그래서 회계 달력을 짐작할 필요가 없습니다 — 12개월 값이 있는 그 날이
    # 그 회사의 회계연도 끝입니다.
    annual_eps = _연간_gaap_eps(facts, report)

    # 157차 계기 — **은행 매출 개념 후보를 세기만 합니다** (값은 안 씁니다).
    #
    # 156차 계기가 실측으로 확정한 것: 은행은 XBRL 매출 계열이 아예 없거나
    # (SYF·TFC 연간 0·분기 0) 분기 계열만 비어 있습니다(RF 연간 6·분기 0).
    # 그래서 분기 뼈대가 안 만들어지고 "빠진 분기" 81건의 상위가 전부
    # 은행(JPM 6·MTB 6·RF 5·SYF 4·WFC 3·TFC 3)입니다.
    #
    # 은행은 손익 구조가 달라 `Revenues`·`OperatingIncomeLoss` 대신 아래
    # 개념을 씁니다. ⚠️ 이 개념들이 **실제로 있는지** 개발 환경에서는
    # 확인할 수 없으므로(SEC 차단), 먼저 **세기만** 하고 뼈대는 그대로
    # 둡니다. 숫자를 보고 나서 넣을지 정합니다 (106차 규칙 그대로 —
    # 짐작으로 뼈대를 건드리지 않는다).
    if report is not None:
        report["은행개념_후보"] = _은행개념_세기(facts, report)

    return _quarters_from_series(ticker, series, start_date, report, annual_eps)


# 은행·보험이 매출 자리에 쓰는 개념들 (157차 — 세기 전용)
_BANK_REVENUE_CANDIDATES = [
    "RevenuesNetOfInterestExpense",
    "InterestAndDividendIncomeOperating",
    "InterestIncomeExpenseNet",
    "InterestIncomeExpenseAfterProvisionForLoanLoss",
    "NoninterestIncome",
]


def _은행개념_세기(facts, report: dict | None = None) -> dict[str, dict[str, int]]:
    """은행 매출 후보 개념이 분기·연간으로 몇 개나 있는지 셉니다 (157차).

    **값을 쓰지 않습니다** — 뼈대에 넣을지 정하기 전에 재기만 하는
    자리입니다. 조회가 실패하면 그 개념은 0으로 둡니다(창작 금지).
    """
    out: dict[str, dict[str, int]] = {}
    for concept in _BANK_REVENUE_CANDIDATES:
        try:
            분기 = len(_quarterly_series(facts, concept, None, unit="USD"))
            연간 = len(_annual_series(facts, concept, None, unit="USD"))
        except Exception:
            분기 = 연간 = 0
        if 분기 or 연간:
            out[concept] = {"분기": 분기, "연간": 연간}
    return out


def _연간_gaap_eps(facts, report: dict | None = None) -> dict[str, float]:
    """회계연도 **연간** GAAP 희석 EPS 를 {결산일: 값} 으로 뽑습니다.

    무엇에 쓰나 (150차-AL): 보도자료가 **4분기 자리에 연간값을 넣는** 사고를
    가리는 데 씁니다. 150차-AK 에서 항등식(Q1+Q2+Q3+Q4=연간)만으로는
    연간값과 진짜 성수기 4분기를 **원리적으로 못 가른다**는 것을 실측으로
    확인했습니다 — 식 안에서는 둘이 똑같이 생겼기 때문입니다.

    가르려면 **식 밖에서 온 값**이 있어야 하고, 그것이 이 계열입니다.
    저장된 4분기 값이 이 연간값과 같으면 그것은 연간값이고, 다르면
    진짜 4분기값입니다. 150차-V 가 단위를 고칠 때 XBRL 을 자로 삼은 것과
    같은 수입니다 — 숫자를 만드는 것이 아니라 **다른 출처에 묻는 것**입니다.

    ⚠️ 이 값으로 4분기 EPS 를 **계산해 채우지 않습니다.** EPS 는 비율이라
       연간에서 분기 합을 빼도 4분기 EPS 가 아닙니다(주식 수가 분기마다
       다릅니다). 오직 **가리는 데만** 씁니다.
    """
    annual: dict[str, float] = {}
    for concept in _XBRL_CONCEPTS["gaap_eps"]:
        annual.update(_annual_series(
            facts, concept, report, unit=_XBRL_UNITS["gaap_eps"]))
    return annual




def _quarters_from_series(
    ticker: str,
    series: dict,
    start_date: str,
    report: dict | None = None,
    annual_eps: dict[str, float] | None = None,
) -> list[dict]:
    """XBRL 시계열(series)에서 분기 뼈대 행을 조립합니다 (113차에 분리).

    fetch_xbrl_approximation 에서 떼어 낸 순수 함수입니다 — SEC 접속 없이
    가짜 시계열로 시험하기 위해서입니다 (개발 환경은 SEC 차단).
    """
    # 분기 목록 — **세 항목의 합집합** (113차. 예전에는 영업이익 하나뿐).
    #
    # 왜 바꾸나 (106·107차 실측): 은행·보험은 `OperatingIncomeLoss` 를
    # 신고하지 않아(이자수익·대손충당금 구조) 분기 목록이 **아예 안
    # 만들어졌고**, 그래서 멀쩡히 찾아 둔 매출 851건 · GAAP EPS 760건 ·
    # 매출총이익 221건 = **1,832건이 붙을 자리가 없어 버려졌습니다**
    # (22종목 — 금융 9 · 제약 5 · 에너지 4 등). 그 빈자리에 보도자료의
    # 쓰레기 값(BAC 매출 8달러)이 대신 들어갔습니다.
    #
    # 합집합으로 만들면 영업이익이 없는 분기도 매출·GAAP EPS 가 있으면
    # 행이 생기고, 8-K 짝짓기가 발표일 도장을 찍어 측정에 들어갑니다.
    period_ends = sorted({
        d
        for key in ("op_income", "revenue", "gaap_eps")
        for d in series.get(key, {})
        if d >= start_date
    })

    # 106차 계기 — **분기 목록을 논갭 영업이익에서만 만든다**는 사실을
    # 숫자로 남깁니다.
    #
    # 무엇이 의심스러운가: 아래에서 매출·이익률 등은 이 `period_ends` 를
    # 열쇠로 찾습니다. 그러니 영업이익이 없는 분기의 매출은 **찾아 놓고도
    # 버려집니다.** 실측으로 그 꼴이 이미 보였습니다 —
    #
    #   BAC  Revenues(3개월): 받음 104 · 이름버림 0 · 단위버림 0 · 남음 55
    #   그런데 스냅샷의 revenue_xbrl 은 44행 전부 None
    #
    #   은행·보험은 `OperatingIncomeLoss` 를 신고하지 않습니다 (이자수익·
    #   대손충당금 구조라 "영업이익" 줄이 없습니다). 제약사도 분기에 따라
    #   빠집니다. 그러면 이 종목들은 XBRL 매출이 **한 칸도** 안 붙고,
    #   92차 설계상 언제나 보도자료로 떨어져 쓰레기 값이 들어갑니다
    #   (실물 BAC 8달러 · C −500만 — 106차에 전수로 182칸 확인).
    #
    # ⚠️ 아직 **고치지 않습니다.** 분기 목록의 뼈대를 바꾸면 모든 종목의
    #    행이 달라져 판정까지 흔들립니다. 먼저 **얼마나 버려지는지** 세고,
    #    그 숫자를 보고 고칠지 정합니다 (짐작으로 뼈대를 건드리지 않는다).
    if report is not None:
        report["xbrl_orphan"] = orphan_counts(series, period_ends, start_date)

    # ⚠️ **되돌릴 항목은 모든 분기에 있을 때만 씁니다.**
    #
    #   예전에는 `sbc = ... or 0.0` 처럼 없으면 그냥 0 으로 처리했습니다.
    #   그런데 주식보상비는 10-Q 현금흐름표에 **누적기간(3·6·9개월)** 으로
    #   신고돼 3개월 값이 Q1 에만 있는 경우가 흔합니다. 그러면 한 열 안에
    #   "GAAP+주식보상비" 와 "GAAP" 이 섞여 **분기마다 정의가 달라집니다.**
    #
    #     Q1  GAAP 100 + 주식보상비 40 = 140
    #     Q2  GAAP 110              = 110   ← 주식보상비 없음
    #     → 델타 -21% (실제로는 +10%)
    #
    #   그리고 진단에는 한 줄도 남지 않았습니다.
    #
    #   델타 모델은 값의 크기가 아니라 **값의 변화**를 봅니다. 그래서 절대
    #   정확도보다 **한 종목 안에서 정의가 일관된 것**이 훨씬 중요합니다.
    #   그래서 모든 분기에 다 있는 항목만 되돌리고, 빠진 항목은 **아예 쓰지
    #   않으며**, 무엇을 뺐는지 진단에 남깁니다.
    usable_ends = [e for e in period_ends if series["op_income"].get(e) is not None]
    consistent = {}
    for key in ("sbc", "amortization"):
        have = sum(1 for e in usable_ends if series[key].get(e) is not None)
        consistent[key] = bool(usable_ends) and have == len(usable_ends)
        if usable_ends and 0 < have < len(usable_ends) and report is not None:
            name = "주식보상비" if key == "sbc" else "무형자산상각"
            report.setdefault("xbrl_ambiguous", []).append(
                f"{name}가 {have}/{len(usable_ends)} 분기에만 있어 "
                "되돌리지 않았습니다 (분기마다 정의가 달라지는 것을 막기 위함)"
            )

    quarters: list[dict] = []
    for period_end in period_ends:
        gaap_op = series["op_income"].get(period_end)

        # GAAP 희석 EPS — 구조화 값 그대로. 비상식 크기만 없음 처리 (2차 방어)
        gaap_eps = series.get("gaap_eps", {}).get(period_end)
        if gaap_eps is not None and abs(gaap_eps) > cfg.EPS_MAX_ABS:
            gaap_eps = None

        # 영업이익이 없는 분기(은행 등)는 근사 논갭도 없습니다 — 지어내지
        # 않고 없음으로 둡니다 (113차). 되돌림(sbc·amort)은 영업이익이
        # 있을 때만 뜻이 있습니다.
        if gaap_op is None:
            sbc = amort = 0.0
            approx_op = None
        else:
            sbc = (series["sbc"].get(period_end) or 0.0) if consistent["sbc"] else 0.0
            amort = (
                (series["amortization"].get(period_end) or 0.0)
                if consistent["amortization"] else 0.0
            )
            approx_op = gaap_op + sbc + amort
        da = series.get("depreciation_amortization", {}).get(period_end)
        revenue = series["revenue"].get(period_end)
        gross_profit = series["gross_profit"].get(period_end)


        # 교차검증: 매출 ≥ 매출총이익 ≥ 0 이어야 하고, 영업이익은 매출을 넘을 수 없습니다.
        # 이 관계가 깨졌다면 매출 자리에 엉뚱한 항목이 들어온 것이므로 매출을 비웁니다.
        # (비워 두면 뒤의 검사 단계가 "매출 없음"으로 처리해 영업이익만 씁니다)
        if revenue is not None:
            bad_gross = (
                gross_profit is not None and (gross_profit < 0 or gross_profit > revenue)
            )
            # 적자 분기는 영업이익이 매출보다 클 수 있습니다(비용이 매출을 넘으므로).
            # 흑자일 때만 "영업이익 ≤ 매출"을 강제하고, 적자는 마진 하한으로만 봅니다.
            # 영업이익이 없으면(113차) 이 검사는 건너뜁니다 — 검사할 값이 없습니다.
            bad_op = approx_op is not None and (
                approx_op > revenue or (
                    approx_op < 0 and approx_op / revenue * 100.0 < cfg.MARGIN_MIN_PCT
                )
            )
            if revenue <= 0 or bad_op or bad_gross:
                if report is not None:
                    report.setdefault("xbrl_ambiguous", []).append(
                        f"{period_end}: 매출 ${revenue:,.0f} 이 다른 항목과 앞뒤가 맞지 않아 제외"
                    )
                revenue = None
                gross_profit = None

        # 셋 다 없는 분기는 행을 만들 재료가 없습니다 (113차).
        # ⚠️ 교차검증 **뒤**에 둡니다 — 검증이 매출을 비워 셋 다 없어진
        #    행도 걸러야 하기 때문입니다 (앞에 두면 그 경우를 놓칩니다).
        if approx_op is None and revenue is None and gaap_eps is None:
            continue

        gm_pct = None
        if revenue and gross_profit is not None and revenue > 0:
            gm_pct = gross_profit / revenue * 100.0
        quarters.append(
            {
                "ticker": ticker,
                "filing_date": period_end,   # 실제 제출일 대신 기간종료일 사용
                "period_label": period_end_label(period_end),
                "revenue": revenue,
                "op_income": approx_op,   # 논갭 근사
                "gaap_eps": gaap_eps,     # GAAP 희석 EPS (XBRL 구조화 — 근사 아님)
                # 이 기간끝이 **결산일**이면 그 해의 연간 GAAP EPS.
                # 4분기 자리에 연간값이 들어왔는지 가리는 자입니다 (150차-AL).
                # 결산일이 아니면 없음 — 12개월 값이 그 날에 없기 때문입니다.
                "gaap_eps_annual_xbrl": (annual_eps or {}).get(period_end),
                "da": da,                 # 감가상각비 — EBITDA 역산에 씁니다
                "gross_margin_pct": gm_pct,
                "source": cfg.SRC_APPROX,
                # 아직 보도자료가 안 붙은 상태 (98차 계기). 붙으면
                # _apply_press_to_row 가 True 로 바꿉니다.
                "press_matched": False,
                "gm_is_gaap": True,
                "filing_url": f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&company={ticker}&type=8-K",
                "derivation": (
                    (
                        "보도자료를 읽지 못해 SEC XBRL 회계데이터로 근사했습니다.\n\n"
                        f"GAAP 영업이익 ${gaap_op/1e6:,.1f}M\n\n"
                        f"+ 주식보상비 ${sbc/1e6:,.1f}M\n\n"
                        f"+ 무형자산상각 ${amort/1e6:,.1f}M\n\n"
                        f"= **${approx_op/1e6:,.1f}M** (근사치)\n\n"
                        "※ GM%는 GAAP 값(매출총이익 ÷ 매출)을 사용했습니다."
                    ) if gaap_op is not None else (
                        "XBRL 에 영업이익 항목이 없는 회사(은행 등)라 매출·"
                        "GAAP EPS 만 담은 뼈대 행입니다 (113차). 논갭 근사는 "
                        "지어내지 않고 없음으로 둡니다."
                    )
                ),
                "guidance_text": "",
            }
        )

    quarters.sort(key=lambda q: q["filing_date"])
    return quarters

def _series_for_key(key: str, facts, report: dict | None = None) -> dict[str, float]:
    """개념 묶음(key) 하나의 분기 시계열을 만듭니다.

    달러 항목은 빠진 4분기를 `연간 − (1+2+3분기)` 로 채우지만,
    ⚠️ **gaap_eps 는 채우지 않습니다.** EPS 는 비율(주당 금액)이라
    연간 EPS 에서 분기 EPS 합을 빼도 4분기 EPS 가 아닙니다
    (주식 수가 분기마다 다릅니다). 없으면 없는 채로 둡니다 — 창작 금지.
    """
    unit = _XBRL_UNITS.get(key, "USD")
    merged: dict[str, float] = {}
    for concept in _XBRL_CONCEPTS[key]:
        merged.update(_quarterly_series(facts, concept, report, unit=unit))
    if key == "gaap_eps":
        return merged
    annual: dict[str, float] = {}
    for concept in _XBRL_CONCEPTS[key]:
        annual.update(_annual_series(facts, concept, report, unit=unit))
    # 156차 — 4분기 채움의 성패를 항목별로 남깁니다 (빠진 분기 79건 추적).
    계기 = None
    if report is not None:
        계기 = report.setdefault("q4_채움", {}).setdefault(key, {})
    return _fill_missing_q4(merged, annual, 계기)


def _quarterly_series(
    facts, concept: str, report: dict | None = None, unit: str = "USD"
) -> dict[str, float]:
    """XBRL에서 특정 항목의 분기(3개월) 값들을 {기간종료일: 값}으로 뽑습니다."""
    return _period_series(facts, concept, months=3, report=report, unit=unit)


def _annual_series(facts, concept: str, report: dict | None = None, unit: str = "USD") -> dict[str, float]:
    """XBRL에서 특정 항목의 연간(12개월) 값들을 {기간종료일: 값}으로 뽑습니다.

    4분기(Q4)는 10-K에 연간으로만 신고되는 경우가 많아, 이 값이 필요합니다.
    """
    return _period_series(facts, concept, months=12, report=report, unit=unit)


def _fill_missing_q4(
    quarterly: dict[str, float],
    annual: dict[str, float],
    계기: dict | None = None,
) -> dict[str, float]:
    """빠져 있는 4분기를 `연간 − (1분기 + 2분기 + 3분기)` 로 계산해 채웁니다.

    왜 필요한가:
      회사는 1~3분기는 10-Q에 "3개월" 단위로 신고하지만,
      4분기는 10-K에 "연간(12개월)"으로만 신고하는 경우가 대부분입니다.
      그래서 3개월짜리만 모으면 매년 4분기가 통째로 빠지고,
      가속/감속을 판단할 표본이 25%나 줄어듭니다.

    `계기` 를 주면 **왜 못 채웠는지**를 갈래별로 세어 넣습니다 (156차).
    빠진 분기 91건 중 79건이 "행 자체가 없음"인데, 그 원인이 이 함수의
    실패인지 애초에 연간값도 없는 것인지 개발 환경에서는 SEC 접속이
    막혀 확인할 수 없습니다. 로봇이 답을 실어 오게 하는 자리입니다
    (짐작 전에 계기 — 91·98·106차 규칙).
    """
    if 계기 is not None:
        계기["연간수"] = 계기.get("연간수", 0) + len(annual)
        계기["분기수"] = 계기.get("분기수", 0) + len(quarterly)
        if not annual:
            계기["연간없음"] = 계기.get("연간없음", 0) + 1
    if not annual:
        return quarterly

    filled = dict(quarterly)

    for fy_end, annual_value in annual.items():
        if fy_end in filled:
            if 계기 is not None:
                계기["이미있음"] = 계기.get("이미있음", 0) + 1
            continue  # 이미 4분기 값이 있으면 건드리지 않습니다

        # 회계연도 종료일로부터 거슬러 올라가며 앞선 세 분기를 찾습니다
        prior = _find_prior_three_quarters(quarterly, fy_end)
        if prior is None:
            if 계기 is not None:
                계기["앞선세분기부족"] = 계기.get("앞선세분기부족", 0) + 1
                예 = 계기.setdefault("못채운예", [])
                if len(예) < 5:
                    예.append(fy_end)
            continue

        q4_value = annual_value - sum(prior)
        filled[fy_end] = q4_value
        if 계기 is not None:
            계기["채움"] = 계기.get("채움", 0) + 1

    return filled


def _find_prior_three_quarters(
    quarterly: dict[str, float],
    fy_end: str,
) -> list[float] | None:
    """회계연도 종료일 직전의 3개 분기 값을 찾습니다 (없으면 None).

    분기 종료일은 회사마다 다르므로, 연도 종료일보다 앞서면서
    1년 이내에 있는 분기 3개를 시간 역순으로 고릅니다.
    """
    from datetime import date

    def _to_date(text: str):
        try:
            return date(int(text[0:4]), int(text[5:7]), int(text[8:10]))
        except (ValueError, IndexError):
            return None

    fy_date = _to_date(fy_end)
    if fy_date is None:
        return None

    candidates = []
    for period_end, value in quarterly.items():
        period_date = _to_date(period_end)
        if period_date is None or period_date >= fy_date:
            continue
        days_before = (fy_date - period_date).days
        if 0 < days_before <= 330:   # 1년 이내 (약간의 여유를 둡니다)
            candidates.append((period_date, value))

    if len(candidates) < 3:
        return None

    candidates.sort(reverse=True)          # 최근 분기부터
    return [value for _, value in candidates[:3]]


def _period_series(
    facts, concept: str, months: int, report: dict | None = None,
    unit: str = "USD",
) -> dict[str, float]:
    """XBRL에서 특정 항목을 지정한 기간 길이로 뽑아 {기간종료일: 값}으로 만듭니다.

    ⚠️ 여기가 배포 사고("평균 영업마진 82,804,463%")의 진짜 원인이었습니다.

    edgartools의 `by_concept("Revenues")` 는 **부분일치**가 기본값이라, 개념 이름뿐
    아니라 사람이 읽는 이름표(label)까지 훑습니다. 그래서 매출을 찾는 질의가
      · "Cost of revenues"(매출원가)
      · "Concentration risk percentage of revenues"(비율, 단위 pure)
      · "...WeightedAverageGrantDateFairValue"(주당 금액, 단위 USD/shares)
    같은 **전혀 다른 항목**을 함께 물어왔고, 같은 분기의 값을 나중 행이 덮어써서
    매출 자리에 $102(비율 값)가 들어앉았습니다.

    그래서 세 겹으로 막습니다.
      ① 개념 이름 **완전일치**만 채택 (부분일치로 딸려온 남의 항목 제거)
      ② 단위가 **달러(USD)** 인 값만 채택 (주당·주식수·비율은 이 계산에 쓸 일이 없음)
      ③ 같은 분기에 값이 여러 개면 덮어쓰지 않고, 서로 2배 넘게 다르면
         **아예 쓰지 않고** 진단에 남김 (덮어쓰면 SEC가 준 순서에 따라 결과가 달라짐)

    (`exact=True` 로 조회하는 방법은 쓸 수 없습니다. edgartools의 색인 열쇠는
     "us-gaap:Revenues" 처럼 앞머리가 붙어 있어, 앞머리 없는 이름으로는 전부 0건이 됩니다.)
    """
    # ⚠️ 예전에는 여기서 예외를 통째로 삼키고 빈 결과만 돌려줬습니다(진단 기록 없음).
    #    edgartools API 가 깨져도, 열 이름이 바뀌어도 화면에는 "데이터 없음"만
    #    뜨고 **원인을 알 방법이 전혀 없었습니다.**
    try:
        df = (
            facts.query()
            .by_concept(concept)
            .by_period_length(months)
            .to_dataframe()
        )
    except Exception as exc:
        _record_error(report, f"XBRL 조회({concept}, {months}개월)", exc)
        return {}

    if df is None or len(df) == 0:
        return {}

    # 열 이름은 버전에 따라 다를 수 있어 후보를 순서대로 확인합니다
    date_col = next((c for c in ("period_end", "end", "period_ending") if c in df.columns), None)
    value_col = next((c for c in ("numeric_value", "value", "val") if c in df.columns), None)
    if date_col is None or value_col is None:
        return {}

    concept_col = "concept" if "concept" in df.columns else None
    unit_col = "unit" if "unit" in df.columns else None
    wanted = concept.lower()

    candidates: dict[str, list[float]] = {}
    rejected = 0

    # 93차 — 어느 검사가 버리는지 따로 셉니다.
    #   92차 계기가 "받음 N · 버림 N · 남음 0" 을 보여 줬지만, ①이름과
    #   ②단위 중 어느 쪽인지는 알려 주지 못했습니다. 본 값의 예시도
    #   함께 남겨야 다음 실행에서 짐작 없이 확정할 수 있습니다.
    이름버림 = 단위버림 = 0
    본이름: list[str] = []
    본단위: list[str] = []

    for _, row in df.iterrows():
        # ① 개념 이름 완전일치 ("us-gaap:Revenues" → "Revenues")
        if concept_col is not None:
            name = str(row[concept_col]).split(":")[-1].strip().lower()
            if len(본이름) < 3 and name not in 본이름:
                본이름.append(name)
            if name != wanted:
                rejected += 1
                이름버림 += 1
                continue

        # ② 항목에 맞는 단위만 — 달러 항목은 USD, 주당 항목은 주당 단위.
        #    (다른 단위의 값이 섞이면 자릿수가 무너집니다 — 배포 사고의 원인)
        #
        # 92차 — 주당 항목의 단위 글자를 **글자 그대로 맞추지 않습니다.**
        #   자료 제공처마다 "USD/shares" · "USD-per-shares" · "usd/share"
        #   처럼 적는 법이 달라, 한 글자만 어긋나도 전부 버려집니다.
        #   주당 단위에는 반드시 "share" 가 들어가고 금액 단위에는 들어가지
        #   않으므로, **낱말로** 가릅니다.
        if unit_col is not None:
            row_unit = str(row[unit_col]).strip().upper()
            if len(본단위) < 3 and row_unit not in 본단위:
                본단위.append(row_unit)
            if row_unit and not _unit_matches(row_unit, unit):
                rejected += 1
                단위버림 += 1
                continue

        try:
            key = str(row[date_col])[:10]
            candidates.setdefault(key, []).append(float(row[value_col]))
        except (TypeError, ValueError):
            continue

    out: dict[str, float] = {}
    ambiguous: list[str] = []
    for key, values in candidates.items():
        unique = sorted(set(values))
        if len(unique) == 1:
            out[key] = unique[0]
            continue
        # ③ 값이 서로 크게 다르면 어느 것이 맞는지 알 수 없으므로 쓰지 않습니다
        picked = _pick_close_value(unique, per_share=_is_per_share(unit))
        if picked is not None:
            out[key] = picked
        else:
            ambiguous.append(f"{concept} {key}: 후보 {len(unique)}개가 서로 달라 제외")

    if report is not None:
        if rejected:
            report["xbrl_rejected"] = report.get("xbrl_rejected", 0) + rejected
        if ambiguous:
            report.setdefault("xbrl_ambiguous", []).extend(ambiguous)
        # 91차 — **조용한 0 을 없앤다.**
        #   XBRL GAAP EPS 가 스냅샷 3,066행에 **한 건도** 안 들어와 있는데
        #   로봇 기록에 오류가 하나도 없었습니다. 어디서 사라졌는지 알 수가
        #   없어 추측만 가능했습니다(단위 글자? 개념 이름? 모호 판정?).
        #   그래서 개념별로 **받은 줄 · 버린 줄 · 남은 분기**를 적어 둡니다.
        #   다음 실행이 숫자로 말해 주면 그때 고칩니다.
        report.setdefault("xbrl_calls", []).append(
            f"{concept}({months}개월): 받음 {len(df)} · "
            f"이름버림 {이름버림} · 단위버림 {단위버림} · "
            f"모호 {len(ambiguous)} · 남음 {len(out)} · "
            f"본이름 {본이름} · 본단위 {본단위}"
        )

    return out


# ---------------------------------------------------------------------------
# 바깥에서 호출하는 함수
# ---------------------------------------------------------------------------
# 보도자료가 덮어쓰기 전의 XBRL 값을 따로 남길 칸 (90차).
# XBRL 에 **규정으로 존재가 보장된** 항목만 넣습니다.
# 조정 EPS·조정 EBITDA 는 회사가 스스로 정의한 값이라 XBRL 에 없고,
# 그래서 보도자료 파서가 계속 맡아야 합니다.
#
# `op_income` 은 150차-V 에 넣었습니다 — 아래 단위 자(尺)로 쓰고, 나중에
# 사람이 되짚어 볼 수 있게 원기록으로도 남깁니다.
_XBRL_KEPT_FIELDS = ("gaap_eps", "revenue", "gross_margin_pct", "op_income")

# 보도자료 숫자가 몇 배 작게 들어왔는지 정할 때 쓰는 배수 후보.
# 표가 쓰는 단위는 이 셋뿐입니다 (천·백만·십억 달러).
_UNIT_SCALES = (1, 1_000, 1_000_000, 1_000_000_000)


def _단위배수_정하기(보도값, xbrl값, band: tuple[float, float]) -> int | None:
    """보도자료 값이 몇 배 작게 들어왔는지 **XBRL 을 자로 삼아** 정합니다.

    ─────────────────────────────────────────────────────────────────
    왜 필요한가 (150차-U·V, 주인 질문 "값이 왜 비지? 어떻게 가져오지?")
    ─────────────────────────────────────────────────────────────────
    보도자료 표는 대개 **천 달러 단위**로 찍힙니다. 표 머리의
    "(in thousands)" 를 못 찾으면 파서는 단위 없음(×1)으로 봅니다.
    그러면 매출은 XBRL(절대 달러)에서 오고 영업이익만 1,000배 작게
    들어와, 마진이 0.05% 같은 값이 되고 검사에서 **버려집니다.**

        실측: 영업이익 1,543칸 · 조정EBITDA 257칸이 이렇게 버려짐 (138종목)
        CRDO 26 Q4: 216,722 ↔ 매출 437,003,000 = 마진 0.05%
        CRDO 를 깃허브에서 다시 받아 XBRL 과 맞대니
        **19분기 중 11분기가 정확히 1,000배** 차이 (나머지 8분기는 1배)

    ─────────────────────────────────────────────────────────────────
    왜 이것이 '창작'이 아닌가 (헌법 1조)
    ─────────────────────────────────────────────────────────────────
    숫자를 만들어 내는 것이 아닙니다. **같은 자릿수의 같은 숫자**를 어느
    단위로 읽을지 셋 중에서 고르는 것이고, 그 선택을 **독립된 자(XBRL)**
    가 정합니다. XBRL 은 규정상 절대 달러이고, 같은 분기의 GAAP 영업이익과
    논갭 영업이익이 1,000배 차이 나는 일은 없습니다.

    **딱 하나로 정해질 때만 고칩니다.** 자가 없거나(XBRL 값 없음), 둘
    이상이 맞거나, 하나도 안 맞으면 **None** 을 돌려주고 아무것도 하지
    않습니다 — 그 값은 지금처럼 검사에서 버려집니다. "없음"은 안전하고
    "틀림"은 위험합니다.

    band: (아래, 위) — 보도값×배수 ÷ XBRL 값이 이 안에 들어와야 합니다.
          띠의 폭이 1,000배보다 좁아야 두 배수가 동시에 맞는 일이
          생기지 않습니다 (그래서 위/아래 비가 1,000 미만이어야 합니다).

    ⚠️ **정직하게 적어 둡니다**: 아래 `len(맞는) == 1` 은 지금 **한 번도
    걸리지 않습니다.** 띠 폭이 1,000배 미만으로 강제되므로 배수가 둘
    이상 맞는 일이 구조적으로 없기 때문입니다. 돌연변이로 확인했습니다 —
    이 줄을 `맞는[0] if 맞는` 으로 바꿔도 시험이 전부 통과합니다.
    실제로 막고 있는 것은 **띠 폭 검사**입니다(그쪽은 돌연변이가
    빨간 불을 봤습니다). 이 줄은 두 번째 방어막으로 남겨 둡니다 —
    누가 띠 검사를 지워도 조용히 틀린 배수를 고르지 않도록.
    """
    if 보도값 in (None, 0) or xbrl값 in (None, 0):
        return None
    if not (_finite(보도값) and _finite(xbrl값)):
        return None
    아래, 위 = band
    if 위 / 아래 >= 1_000:                     # 띠가 넓으면 배수가 겹칩니다
        raise ValueError("띠가 1,000배 이상이면 배수를 하나로 정할 수 없습니다")
    맞는 = [s for s in _UNIT_SCALES
           if 아래 <= abs(보도값 * s / xbrl값) <= 위]
    return 맞는[0] if len(맞는) == 1 else None


def _finite(value) -> bool:
    """숫자이고 무한대·NaN 이 아닌가."""
    return isinstance(value, (int, float)) and math.isfinite(value)


def _apply_press_to_row(row: dict, press: dict) -> None:
    """8-K에서 실제로 뽑힌 값만 XBRL 행에 덮어씁니다 (없는 값은 XBRL 값 유지).

    ⚠️ 90차 — **덮어쓰기 전에 XBRL 값을 따로 남깁니다.**

    왜 (89차까지의 실측): 야후와 어긋난 칸을 배율로 나눠 보니, 파서가
    숫자를 잘못 읽는 게 아니라 **엉뚱한 자리의 숫자를 집는** 것이
    대부분이었습니다 — GAAP EPS 어긋난 128칸 중 전년 열 25.8% ·
    9개월 누적 18.8% · 연간값 9.4% · 부호 반대 7.0%.

    보도자료는 표를 글자로 납작하게 편 것이라 숫자에 "이건 3개월치다 /
    올해 열이다 / 전체가 아니라 사업부다"라는 표시가 남아 있지 않습니다.
    반면 XBRL 은 값마다 기간·단위·부문이 태그로 붙어 있어 그 네 종류가
    **구조적으로 불가능**합니다. 그런데 지금은 태그 붙은 값을 짐작으로
    읽은 값이 무조건 덮어쓰고 있었습니다.

    그래서 **먼저 재 봅니다.** 두 값을 나란히 남기고, 야후를 심판으로
    칸별 승률을 실측한 뒤에 어느 쪽을 본선으로 삼을지 정합니다.
    (근거 없이 뒤집었다가 되돌린 89차 전망 가드의 실수를 되풀이하지
    않으려는 것입니다.)

    값은 **하나도 바뀌지 않습니다** — `_xbrl` 칸이 늘어날 뿐입니다.
    """
    # 덮어쓰기 전의 XBRL 값 (짝지을 XBRL 행이 없으면 None)
    for _field in _XBRL_KEPT_FIELDS:
        row[f"{_field}_xbrl"] = row.get(_field)

    # 이 분기에 **보도자료가 실제로 붙었는가** (98차 계기).
    #
    # 왜 필요한가: 조정 EPS 가 빈 칸일 때 원인이 두 가지인데 지금은 구분이
    # 안 됩니다.
    #   ㉠ 보도자료를 아예 못 붙였다      → 수집 문제 (고칠 수 있음)
    #   ㉡ 붙었는데 그 안에 값이 없었다   → 회사가 안 준 것 (없음이 정답)
    # 둘은 대응이 정반대인데, 표시가 없어서 **짐작밖에 할 수 없었습니다.**
    #
    # ⚠️ 기존 `source` 로는 알 수 없습니다. `source` 는 **논갭 영업이익의
    #    출처**만 적는 칸이라, 보도자료가 붙었어도 그 안에 논갭 영업이익이
    #    없으면 XBRL 값인 '근사치'로 남습니다 (실물: FN·QCOM·TER 는 조정
    #    EPS 를 보도자료에서 읽었는데도 전 행이 '근사치'입니다).
    #    97차에서 제가 `source` 로 이걸 재려다 **틀린 진단을 냈습니다.**
    row["press_matched"] = True

    # ── 보도자료 표의 **단위**를 XBRL 을 자로 삼아 맞춥니다 (150차-V) ──
    #
    # 표 머리의 "(in thousands)" 를 못 찾으면 파서는 단위 없음(×1)으로
    # 봅니다. 매출은 XBRL(절대 달러)에서 오므로 멀쩡한데 영업이익만
    # 1,000배 작게 들어와 마진이 0.05% 가 되고 **버려집니다.**
    # 실측 1,543칸 · 138종목. CRDO 는 19분기 중 11분기가 그랬습니다.
    #
    # 자(尺): 이 행의 XBRL 영업이익. 규정상 절대 달러이고, 같은 분기의
    # GAAP 과 논갭이 1,000배 차이 날 수는 없습니다. **딱 하나로 정해질
    # 때만** 고치고, 아니면 그대로 둡니다(= 지금처럼 버려집니다).
    xbrl_op = row.get("op_income")
    배수 = _단위배수_정하기(press.get("op_income"), xbrl_op, (0.05, 20.0))
    if 배수 is None and press.get("op_income") is None:
        # 논갭 영업이익이 없는 회사(ZETA·APP 등)는 조정 EBITDA 로 잽니다.
        # EBITDA 는 감가상각비를 더한 값이라 영업이익보다 큽니다 — 띠를
        # 위로 넓히되, 폭은 1,000배 아래로 둡니다.
        배수 = _단위배수_정하기(press.get("adjusted_ebitda"), xbrl_op, (0.2, 100.0))
    if 배수 and 배수 > 1:
        # **같은 문서에서 온 돈 값**에 같은 배수를 적용합니다.
        # 한 표의 단위는 하나이므로, 하나가 천 단위면 나머지도 천 단위입니다.
        #
        # ⚠️ 넘겨받은 `press` 를 **제자리에서 고치지 않습니다.** 사본을
        #    만들어 씁니다. 지금은 `used_press` 가 같은 보도자료를 두 번
        #    쓰지 못하게 막고 있지만, 그 방어막이 언젠가 바뀌면 배수가
        #    두 번 곱해져 1,000,000배가 됩니다. 조용히 틀리는 쪽이라
        #    구조로 막아 둡니다.
        press = dict(press)
        for _돈칸 in ("op_income", "adjusted_ebitda"):
            if press.get(_돈칸) is not None:
                press[_돈칸] = press[_돈칸] * 배수
        row["unit_scale_fixed"] = 배수      # 되짚어 볼 수 있게 남깁니다

    if press.get("op_income") is not None:
        row["op_income"] = press["op_income"]
        row["source"] = press.get("source") or cfg.SRC_DERIVED
        row["derivation"] = press.get("derivation", "")
        row["gm_is_gaap"] = press.get("gm_is_gaap", False)
    elif press.get("adjusted_ebitda") is not None and row.get("da") is not None:
        # 논갭 영업이익을 발표하지 않는 회사 (ZETA·TSLA·APP 등)
        #   논갭 영업이익 ≈ 조정 EBITDA − 감가상각비(D&A)
        # XBRL 근사치보다 회사 공식 숫자에 가까우므로 이쪽을 씁니다.
        ebitda, da = press["adjusted_ebitda"], row["da"]
        derived = ebitda - da
        row["op_income"] = derived
        row["source"] = cfg.SRC_DERIVED
        row["derivation"] = (
            "회사가 non-GAAP 영업이익을 발표하지 않아 조정 EBITDA에서 "
            "감가상각비를 빼 역산했습니다.\n\n"
            f"조정 EBITDA ${ebitda/1e6:,.1f}M\n\n"
            f"− 감가상각비 ${da/1e6:,.1f}M (SEC XBRL)\n\n"
            f"= **${derived/1e6:,.1f}M** (역산)"
        )
    if press.get("adjusted_ebitda") is not None:
        row["adjusted_ebitda"] = press["adjusted_ebitda"]
    # 주당순이익은 점수에 쓰지 않고 '이익의 질' 검사에만 씁니다(1단계).
    # 논갭 영업이익을 못 찾은 분기에서도 이 둘은 잡히는 경우가 많으므로
    # op_income 성공 여부와 무관하게 따로 옮겨 둡니다.
    if press.get("adj_eps") is not None:
        row["adj_eps"] = press["adj_eps"]
    # GAAP EPS — **XBRL 우선, 없으면 보도자료** (99차, 92차 매출과 같은 설계)
    #
    # 야후를 심판으로 갈린 칸을 셌더니 **XBRL 61 : 보도자료 2** 였다
    # (매출 때의 98:0 과 같은 방향). 전체 정확도로도 확인했다:
    #   지금(보도자료 우선)        341/439 = 77.7%
    #   XBRL 우선, 없으면 보도자료  400/439 = **91.1%**
    #
    # "XBRL 만 쓴다"로 두지 않은 이유: 그러면 93칸(보도자료만 있는 칸)을
    # 통째로 잃는다. XBRL 이 있는 칸은 5,851행 중 3,241행뿐이고,
    # **XBRL 만 있고 보도자료가 없는 칸은 0개**라 이 설계는 커버리지를
    # 한 칸도 안 줄인다.
    #
    # ⚠️ 잃는 것을 적어 둔다 — 보도자료가 맞고 XBRL 이 틀린 칸 **2개**를
    #    잃는다 (ETSY 2026-06-30 −0.49 ↔ −0.36 · BE 2025-10-28 −0.10 ↔
    #    **−100.0**). BE 의 −100.0 은 우리 XBRL 읽기의 명백한 쓰레기값이다.
    #    다만 3,241행 중 |EPS|≥50 인 행이 **이 1건뿐**이라, n=1 로 새
    #    문턱 규칙을 만들지 않는다 — 근거 없는 가드를 넣었다가 되돌린
    #    89차의 실수를 되풀이하지 않는다. 남은 결함으로 기록만 한다.
    if row.get("gaap_eps") is None and press.get("gaap_eps") is not None:
        row["gaap_eps"] = press["gaap_eps"]
    # 매출 — **XBRL 우선, 없으면 보도자료** (92차, 91차 승부 결과 반영)
    #
    # 91차에 야후를 심판으로 셌더니 갈린 98칸에서 **XBRL 98 : 보도자료 0**
    # 이었다. 이긴 자리의 종류가 그동안 이름 붙인 결함과 겹친다 —
    # 부문 매출 27건(89차 "조각 매출") · 연간/전망 10건(89차 ⑤).
    # 실물 ABBV 117.6억(부문) ↔ 154.2억 · ADBE 259억(연간) ↔ 61.9억.
    #
    # "XBRL 이 언제나 이긴다"로 두지 않은 이유: 그러면 XBRL 에 없는 11칸을
    # 잃는데 **그중 7칸이 맞던 값**이다 (ABNB 4 · NTAP 2 · APP 1, 전부
    # 야후와 일치). 헌법 1조는 "없음이 틀림보다 안전"이지 "없음이 맞음보다
    # 안전"이 아니다. 그래서 XBRL 이 없을 때만 보도자료를 쓴다 — 잃는 것 0.
    if row.get("revenue") is None and press.get("revenue") is not None:
        row["revenue"] = press["revenue"]
    # 매출총이익률은 **뒤집지 않는다** (92차).
    #   숫자만 보면 169:0 으로 XBRL 압승이지만, 이 칸은 뜻이 다르다 —
    #   보도자료는 회사가 발표한 **논갭** 이익률이고 XBRL·야후는 **갭**이다
    #   (실물 ADI 69.4% ↔ 61.0%). 심판이 갭 기준이라 XBRL 이 자동으로
    #   이길 뿐, 더 정확해서가 아니다 (86차에 이미 적어 둔 정의 차이).
    if press.get("gross_margin_pct") is not None:
        row["gross_margin_pct"] = press["gross_margin_pct"]
    if press.get("period_label"):
        row["period_label"] = press["period_label"]
    if press.get("filing_url"):
        row["filing_url"] = press["filing_url"]
    if press.get("guidance_text"):
        row["guidance_text"] = press["guidance_text"]
    # 발표일 기준 정렬을 위해 8-K 제출일을 따로 남깁니다
    row["announced_date"] = press.get("filing_date", "")


# 150차-Q — 분기 발표는 보통 분기끝 뒤 10~70일입니다(138차 등록).
# 100일이 넘으면 "이 분기 발표"로 보기 어렵습니다. 뼈대에 구멍이 있을 때
# 앞 분기가 뒷 분기 8-K 를 가져가 자기 숫자를 덮어쓰는 것을 막는 문턱입니다.
_NEXT_QUARTER_TAKEN_DAYS = 100
_QUARTER_DAYS = 91


def merge_quarters(
    xbrl_quarters: list[dict],
    press_quarters: list[dict],
    report: dict | None = None,
) -> list[dict]:
    """XBRL로 만든 뼈대에 8-K에서 뽑은 공식 논갭 수치를 덮어씁니다.

    예전에는 "8-K가 되면 8-K만, 안 되면 XBRL만" 이라서 하나라도 실패하면
    전 분기가 근사치가 됐습니다. 이제는 **분기 단위로** 성공한 것만 올려서,
    일부만 성공해도 그 분기는 '직접공시'가 됩니다.

    같은 분기인지는 기간종료일과 8-K 제출일의 간격으로 판단합니다.
    (실적발표는 보통 분기 종료 후 2~8주 안에 이뤄집니다)
    """
    from datetime import date

    def _to_date(text: str):
        try:
            return date(int(text[0:4]), int(text[5:7]), int(text[8:10]))
        except (ValueError, IndexError, TypeError):
            return None

    if not xbrl_quarters:
        # 155차 — 뼈대가 **오류로** 빈 날은 아래 대체 경로를 밟지 않습니다.
        # 대체 경로는 보도자료 행을 발표일 그대로 분기 행으로 승격하는데,
        # 그 행은 분기 신원(기간종료일)이 틀려 규격 위반입니다(실측: RH —
        # 한 날짜에 분기 4개, 이름 중복). 원래 XBRL 이 없는 회사(오류
        # 아님 — 41차의 은행·제약 구멍)는 지금까지처럼 승격합니다.
        if report is not None and report.get("xbrl_error"):
            return []
        # XBRL 뼈대가 통째로 비면 보도자료 행을 그대로 돌려주는데, 예전에는
        # 여기서 **발표일 도장을 찍지 않고** 나갔습니다. 측정은 발표일로만
        # 전 종목을 줄 세우므로(전략.md 7장), 도장이 없는 행은 측정에서
        # 통째로 빠집니다. 실제로 22종목(은행·제약·에너지·산업재 전부와
        # KLAC)이 이 구멍으로 빠져 나가, 41차 검증에서 "판단 불가"군이
        # 무작위가 아니라 **업종 편향**이 되는 원인이 됐습니다.
        # → 아래 승격 경로와 똑같이 8-K 제출일을 발표일로 찍어 줍니다.
        stamped = []
        for press in sorted(press_quarters, key=lambda q: q["filing_date"]):
            row = dict(press)
            row["announced_date"] = press.get("filing_date", "")
            stamped.append(row)
        return stamped
    if not press_quarters:
        return xbrl_quarters

    merged = [dict(q) for q in xbrl_quarters]
    used_press: set[int] = set()

    # --- 짝짓기 대상과 승격 대상 구분 ---
    # 마지막 XBRL 분기보다 "한 분기 이상 뒤"(약 91일 + 최소 발표 간격 14일)에
    # 제출된 8-K는 아직 XBRL에 없는 새 분기의 발표입니다. 이것을 과거 분기에
    # 역방향으로 짝지으면 다른 분기 숫자로 덮어쓰는 오염이 생기므로,
    # 짝짓기에서 빼고 아래 승격 단계에서 새 분기 행으로 추가합니다.
    PROMOTE_AFTER_DAYS = 105
    last_period = max(
        (d for d in (_to_date(r.get("filing_date", "")) for r in merged) if d is not None),
        default=None,
    )
    promote_only: set[int] = set()
    if last_period is not None:
        for index, press in enumerate(press_quarters):
            press_date = _to_date(press.get("filing_date", ""))
            if press_date is not None and (press_date - last_period).days > PROMOTE_AFTER_DAYS:
                promote_only.add(index)

    for row in merged:
        period_date = _to_date(row.get("filing_date", ""))
        if period_date is None:
            continue

        # 이 분기 종료 후 일정 기간 안에 나온 8-K 중에서 고릅니다
        # (연말 분기는 10-K와 함께 늦게 발표하는 회사가 있어 창을 넉넉히 둡니다).
        #
        # ⚠️ 거리보다 **알맹이 우선**: 예비 매출 공지(EPS·이익 없음)가 분기
        #    종료일에 더 가깝다는 이유로 진짜 실적 발표를 밀어내고 분기를
        #    차지하는 사고가 있었습니다 (실물: CRDO 26Q3 — 02-09 예비 공지가
        #    이기고 03-02 실적 발표의 EPS $1.07 이 통째로 버려짐, 9차 감사).
        #    이익 숫자(조정EPS·영업이익·조정EBITDA)를 실은 발표가 항상 이깁니다.
        best_index, best_gap, best_rank = None, None, None
        for index, press in enumerate(press_quarters):
            if index in used_press or index in promote_only:
                continue
            press_date = _to_date(press.get("filing_date", ""))
            if press_date is None:
                continue
            gap = (press_date - period_date).days
            if not (0 <= gap <= cfg.PAIRING_WINDOW_DAYS):
                continue
            rank = 0 if (
                press.get("adj_eps") is not None
                or press.get("op_income") is not None
                or press.get("adjusted_ebitda") is not None
            ) else 1

            # ⚠️ **이 8-K 를 더 가깝게 받을 분기가 따로 있으면 가져가지 않습니다.**
            #
            #   짝짓기 창(120일)이 분기 간격(약 91일)보다 넓습니다. 그래서 예전에는
            #   앞 분기의 8-K 가 파싱에 실패하면 **앞 분기가 뒷 분기의 8-K 를
            #   가로챘습니다.** 12월 결산 회사의 2분기 발표(7/25)는 1분기 종료일
            #   (3/31)에서 116일이라 창 안에 들어옵니다.
            #
            #   그 결과 실제 100→130(+30%)이 모델에는 155→130(-16%)으로 보였고,
            #   분기 이름표까지 "24 Q2"로 덮여 3월 분기 자리에 표시됐습니다.
            #   진단에는 실패 기록이 한 줄도 남지 않았습니다.
            #
            #   창을 좁히면 10-K 와 함께 늦게 나오는 연말 발표를 놓치므로,
            #   창은 그대로 두고 **가장 가까운 분기가 임자**라는 규칙을 넣습니다.
            stolen = False
            for other in merged:
                if other is row:
                    continue
                other_date = _to_date(other.get("filing_date", ""))
                if other_date is None:
                    continue
                other_gap = (press_date - other_date).days
                if 0 <= other_gap < gap:
                    stolen = True
                    break
            if stolen:
                continue

            # ⚠️ **뼈대에 구멍이 있으면 "가장 가까운 분기가 임자" 규칙이
            #    듣지 않습니다** (150차-Q). 임자가 될 행 자체가 없기 때문에
            #    앞 분기가 그 8-K 를 가져가 **자기 숫자를 덮어씁니다.**
            #
            #    실측한 모양 — 앞끝 04-30 · 구멍 07-31 · 뒤 10-31 일 때:
            #      발표 08-14(앞끝+106일) → 앞 분기(04-30)를 덮어씀 ⛔
            #      발표 08-28(앞끝+120일) → 앞 분기를 덮어씀 ⛔
            #      발표 09-03(앞끝+126일) → 창 밖이라 버려짐(→ 아래서 구멍 메움)
            #
            #    값이 **사라지는 것보다 나쁩니다** — 엉뚱한 분기에 들어가
            #    앞 분기 숫자가 통째로 틀려집니다.
            #
            #    분기 발표는 보통 분기끝 뒤 10~70일입니다(138차 등록).
            #    그보다 훨씬 늦은(=한 분기 넘게 지난) 8-K 가 이 행을
            #    차지하려 하면, **다음 분기 자리가 비어 있는지** 봅니다.
            #    비어 있으면 이건 그 빈 분기의 발표이므로 가져가지 않습니다.
            if gap > _NEXT_QUARTER_TAKEN_DAYS:
                다음끝 = period_date + timedelta(days=_QUARTER_DAYS)
                다음행있음 = any(
                    d is not None and abs((d - 다음끝).days) <= 45
                    for d in (_to_date(o.get("filing_date", "")) for o in merged)
                    if d is not None
                )
                if not 다음행있음:
                    continue      # 다음 분기가 비었다 — 그 분기의 발표다

            if best_rank is None or (rank, gap) < (best_rank, best_gap):
                best_index, best_gap, best_rank = index, gap, rank

        if best_index is None:
            continue

        press = press_quarters[best_index]
        used_press.add(best_index)
        _apply_press_to_row(row, press)

    # --- 늦은 발표 흡수 (이중 계상 방지 ①) ---
    # 마지막 XBRL 분기의 발표가 106~120일 뒤에 나온 경우(늦은 연간 보고),
    # 승격 규칙에 걸려 새 분기로 추가되면 같은 분기가 근사치 행과 직접공시 행
    # 두 개로 이중 계상됩니다(QoQ가 0% 부근으로 왜곡됨).
    # → 마지막 XBRL 행이 아직 짝이 없고, 승격 후보의 매출이 그 행과 ±10% 이내로
    #   거의 같으면(같은 분기의 GAAP vs 논갭 매출은 사실상 동일) 승격 대신 흡수합니다.
    #   매출이 10% 넘게 다르면 다음 분기의 발표로 보고 승격을 유지합니다.
    if merged and last_period is not None:
        last_row = max(
            (r for r in merged if _to_date(r.get("filing_date", "")) is not None),
            key=lambda r: _to_date(r["filing_date"]),
        )
        if not last_row.get("announced_date"):
            for index in sorted(
                promote_only, key=lambda i: press_quarters[i].get("filing_date", "")
            ):
                if index in used_press:
                    continue
                press = press_quarters[index]
                press_date = _to_date(press.get("filing_date", ""))
                if press_date is None:
                    continue
                gap = (press_date - last_period).days
                row_revenue = last_row.get("revenue")
                press_revenue = press.get("revenue")
                revenues_match = (
                    row_revenue and press_revenue
                    and abs(press_revenue / row_revenue - 1.0) <= 0.10
                )
                if 0 <= gap <= cfg.PAIRING_WINDOW_DAYS and revenues_match:
                    _apply_press_to_row(last_row, press)
                    used_press.add(index)
                    break

    # --- 최신 8-K 승격 (이중 계상 방지 ② 포함) ---
    # 실적 8-K는 10-Q(XBRL)보다 몇 주 먼저 나옵니다. 그 사이에는 최신 분기의
    # XBRL 행이 아직 없어 8-K가 짝을 못 찾는데, 이걸 버리면
    #   · 가장 신선한 분기 실적이 사라지고
    #   · 그 안의 최신 가이던스(다음 분기 전망)도 함께 사라집니다.
    # → 모든 XBRL 분기보다 뒤에 제출됐고 영업이익이 있는 8-K는
    #   새 분기 행으로 승격시킵니다.
    # 같은 분기에 8-K가 2건(원본 + 정정 발표)인 경우를 대비해:
    #   · 제출일 오름차순으로 돌아 최초 발표가 우선 채택되고
    #   · 이미 승격한 발표와 60일 미만 간격(분기 간격은 ~91일)이면 같은 분기로
    #     보고 건너뜁니다.
    promoted_dates: list = []
    for index in sorted(
        range(len(press_quarters)), key=lambda i: press_quarters[i].get("filing_date", "")
    ):
        if index in used_press:
            continue
        press = press_quarters[index]
        press_date = _to_date(press.get("filing_date", ""))
        if press_date is None or press.get("op_income") is None:
            continue
        if last_period is not None and press_date <= last_period:
            continue
        if any(abs((press_date - d).days) < 60 for d in promoted_dates):
            continue   # 같은 분기의 두 번째 발표 → 이중 계상 방지
        promoted = dict(press)
        promoted["announced_date"] = press.get("filing_date", "")
        merged.append(promoted)
        promoted_dates.append(press_date)
        used_press.add(index)

    merged.sort(key=lambda r: r.get("filing_date", ""))

    # --- 뼈대 **가운데 구멍** 메우기 (150차-Q) -------------------------------
    #
    # 여기까지 오면 보도자료가 할 수 있는 일은 둘뿐이었습니다 — 기존 XBRL
    # 행을 덮어쓰거나, **마지막 분기 뒤로** 승격하거나. 그래서 XBRL 뼈대
    # **가운데**에 분기가 빠져 있으면 그 분기의 8-K 는 덮어쓸 행도 없고
    # 승격 대상도 아니라 **통째로 버려졌습니다.**
    #
    # 실행으로 재현한 모양(150차-Q):
    #   XBRL  04-30 · (07-31 없음) · 10-31 · 01-31
    #   보도  09-03 발표 조정EPS 0.5   →  합친 결과 3행, 0.5 는 사라짐
    #
    # 실데이터 규모: 발표일이 130일 넘게 벌어진 170건 중 **156건(61종목)**이
    # "한 분기 빠짐" 모양입니다. 주인이 CRDO 에서 "실적이 왜 빈칸이지?"라고
    # 물어 드러났습니다.
    #
    # ⚠️ **분기끝을 지어내지 않습니다.** 앞뒤 XBRL 행이 **정확히 두 분기**
    #    (약 182일) 떨어져 있을 때만, 그 중간을 분기끝으로 봅니다 — 이건
    #    추정이 아니라 분기 달력이 정하는 자리입니다. 간격이 애매하면
    #    (150일 미만·215일 초과) **아무것도 하지 않습니다**(헌법 1조).
    #
    # ⚠️ 이익 숫자를 실은 발표만 끼웁니다. 예비 매출 공지가 분기를 차지해
    #    진짜 발표를 밀어낸 사고(9차 감사 CRDO 26Q3)의 재발 방지입니다.
    끼운날: list = []
    for index in sorted(
        range(len(press_quarters)),
        key=lambda i: press_quarters[i].get("filing_date", ""),
    ):
        if index in used_press:
            continue
        press = press_quarters[index]
        press_date = _to_date(press.get("filing_date", ""))
        if press_date is None:
            continue
        # 이익 숫자가 없으면 실적 발표가 아닐 수 있습니다 — 끼우지 않습니다.
        #
        # ⚠️ **GAAP EPS 도 이익 숫자입니다** (150차-AB). 처음에는 조정EPS·
        #    논갭 영업이익·조정EBITDA 셋만 봤는데, **은행은 그 셋을 아예
        #    발표하지 않습니다.** 그래서 골드만삭스의 1월 발표(4분기)가
        #    아홉 해 내리 "실적 발표가 아님"으로 걸러졌습니다.
        #
        #    실측(깃허브에서 GS 를 받아 계기를 읽음): 짝 못 찾은 8-K 12건 중
        #    **9건이 1월**(2018-01-17 … 2026-01-15) — 빠진 아홉 개의 Q4 와
        #    정확히 같은 자리입니다. 구멍 메움은 **0건**이었습니다.
        #
        #    이 문지기의 목적은 "예비 **매출** 공지"를 막는 것입니다
        #    (9차 감사 CRDO 26Q3). GAAP EPS 가 실린 문서는 예비 매출 공지가
        #    아니라 실적 발표입니다. 잣대 사다리도 gaap_eps 를 셋째 칸으로
        #    이미 인정합니다 — 여기서만 빼 두면 앞뒤가 안 맞습니다.
        if (press.get("adj_eps") is None and press.get("op_income") is None
                and press.get("adjusted_ebitda") is None
                and press.get("gaap_eps") is None):
            continue
        # 이 발표를 감싸는 **이웃한 두 XBRL 행**을 찾습니다
        앞행 = 뒤행 = None
        for row in merged:
            d = _to_date(row.get("filing_date", ""))
            if d is None:
                continue
            if d < press_date and (앞행 is None
                                   or d > _to_date(앞행["filing_date"])):
                앞행 = row
            if d > press_date and (뒤행 is None
                                   or d < _to_date(뒤행["filing_date"])):
                뒤행 = row
        if 앞행 is None or 뒤행 is None:
            continue                      # 가운데가 아니면 여기 일이 아님
        앞끝 = _to_date(앞행["filing_date"])
        뒤끝 = _to_date(뒤행["filing_date"])
        사이 = (뒤끝 - 앞끝).days
        if not (150 <= 사이 <= 215):
            continue                      # 두 분기 간격이 아니면 모르는 것
        빠진끝 = 앞끝 + timedelta(days=사이 // 2)
        # 발표는 분기끝 뒤 상식 범위(10~70일) 안이어야 합니다
        지연 = (press_date - 빠진끝).days
        if not (10 <= 지연 <= 70):
            continue
        # 이미 끼운 것·기존 행과 너무 붙으면 같은 분기의 두 번째 발표입니다
        if any(abs((press_date - d).days) < 60 for d in 끼운날):
            continue
        if any(abs((빠진끝 - _to_date(r.get("filing_date", "")) ).days) < 45
               for r in merged if _to_date(r.get("filing_date", "")) is not None):
            continue
        새행 = dict(press)
        새행["filing_date"] = 빠진끝.isoformat()
        새행["announced_date"] = press.get("filing_date", "")
        새행["구멍메움"] = True            # 어디서 왔는지 남깁니다
        merged.append(새행)
        끼운날.append(press_date)
        used_press.add(index)
    if 끼운날:
        merged.sort(key=lambda r: r.get("filing_date", ""))
        if report is not None:
            report["hole_filled"] = len(끼운날)

    # 짝을 못 찾은 8-K가 있으면 진단에 남깁니다 (짝짓기 실패 원인 추적용)
    if report is not None:
        unpaired = [
            press.get("filing_date", "?")
            for index, press in enumerate(press_quarters)
            if index not in used_press
        ]
        report["unpaired_press"] = len(unpaired)
        # **날짜를 남깁니다** (150차-AA). 개수만 보면 "어느 분기가 빠졌나"를
        # 알 수 없습니다. 실물: GS 는 1월 발표(4분기)가 아홉 해 내리 없는데,
        # 그 1월 8-K 가 짝을 못 찾아 남은 것인지(= 구멍 메우기가 기회를
        # 얻고도 실패) 아니면 다른 분기가 가져간 것인지 구분이 안 됐습니다.
        report["unpaired_dates"] = sorted(unpaired)[-24:]
        if unpaired:
            report["pair_note"] = (
                f"짝 못 찾은 8-K {len(unpaired)}건 (예: {unpaired[0]}) — "
                f"분기 종료일과 {cfg.PAIRING_WINDOW_DAYS}일 넘게 떨어져 있거나 "
                "실적발표가 아닐 수 있습니다"
            )

    return merged


def get_fundamentals(
    ticker: str,
    start_date: str | None = None,
    use_cache: bool = True,
) -> tuple[list[dict], dict]:
    """한 종목의 분기별 논갭 실적을 가져옵니다.

    수집 순서:
      ① XBRL로 전체 분기 뼈대를 만든다 (빠진 4분기도 계산해 채움)
      ② 8-K 보도자료에서 공식 논갭 수치를 뽑는다
      ③ 성공한 분기만 ①에 덮어쓴다

    반환: (분기 목록, 진단 리포트)
    """
    report = new_report(ticker)

    if use_cache:
        cached = load_cache(ticker)
        if cached is not None:
            report["note"] = "저장된 데이터를 재사용했습니다"
            report["xbrl_quarters"] = len(cached)
            report["merged_direct"] = sum(
                1 for q in cached if q.get("source") in (cfg.SRC_DIRECT, cfg.SRC_DERIVED)
            )
            return cached, report

    # ① XBRL 뼈대
    xbrl_quarters: list[dict] = []
    try:
        xbrl_quarters = fetch_xbrl_approximation(ticker, start_date, report)
    except Exception as exc:
        _record_error(report, "xbrl", exc)
    report["xbrl_quarters"] = len(xbrl_quarters)

    # ② 8-K 보도자료
    press_quarters: list[dict] = []
    try:
        press_quarters = fetch_earnings_8k(ticker, start_date, report)
    except Exception as exc:
        _record_error(report, "8-K", exc)

    # ③ 합치기
    quarters = merge_quarters(xbrl_quarters, press_quarters, report)
    report["merged_direct"] = sum(
        1 for q in quarters if q.get("source") in (cfg.SRC_DIRECT, cfg.SRC_DERIVED)
    )

    if quarters:
        save_cache(ticker, quarters)
    return quarters, report
