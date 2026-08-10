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
import os
import re
import threading
from datetime import datetime, timedelta, timezone

import config as cfg

# ---------------------------------------------------------------------------
# 숫자 뽑아내기 도우미
# ---------------------------------------------------------------------------

# 보도자료 표에서 "(단위: 천 달러)" 같은 표기를 찾기 위한 패턴
_UNIT_PATTERNS = [
    (re.compile(r"\(\s*in\s+thousands", re.I), 1_000),
    (re.compile(r"\(\s*in\s+millions", re.I), 1_000_000),
    (re.compile(r"\(\s*in\s+billions", re.I), 1_000_000_000),
    (re.compile(r"amounts?\s+in\s+thousands", re.I), 1_000),
    (re.compile(r"amounts?\s+in\s+millions", re.I), 1_000_000),
]

# 숫자 하나를 나타내는 패턴 — 예: $ 1,234.5  /  (1,234)  /  45.1
_NUMBER_RE = re.compile(
    r"""
    (?P<paren_open>\()?          # 괄호로 시작하면 음수
    \s*\$?\s*
    (?P<num>\d{1,3}(?:,\d{3})*(?:\.\d+)?|\d+(?:\.\d+)?)
    \s*
    (?P<paren_close>\))?
    \s*
    (?P<scale>million|billion|thousand|bn|mm|m\b|b\b)?
    """,
    re.I | re.X,
)

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
    """
    start = max(0, position - window)
    context = text[start:position]
    best_scale, best_pos = 1, -1
    for pattern, scale in _UNIT_PATTERNS:
        for match in pattern.finditer(context):
            if match.start() > best_pos:      # 가장 가까운(=뒤쪽) 표기를 채택
                best_pos, best_scale = match.start(), scale
    return best_scale


def _parse_number_at(
    text: str, search_from: int, search_len: int = 160
) -> tuple[float, int, int, bool, int] | None:
    """지정한 위치부터 오른쪽으로 훑으며 첫 번째 숫자를 찾아 값으로 바꿉니다.

    반환값: (숫자값, 시작위치, 끝위치, 단위단어가붙었는지, 숫자자체의끝) — 못 찾으면 None
    """
    segment = text[search_from : search_from + search_len]
    match = _NUMBER_RE.search(segment)
    if not match:
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
    for pattern_str in label_patterns:
        pattern = re.compile(pattern_str, re.I)
        for label_match in pattern.finditer(text):
            search_from = label_match.end()

            # 금액을 찾는 중이라면 퍼센트 숫자는 건너뛰고 계속 오른쪽을 봅니다.
            # 예) "Revenue grew 20% year-over-year to $135.0 million"
            #      → 20을 매출로 오인하지 않고 135.0 million을 찾아냅니다.
            for _ in range(4):   # 같은 문장 안에서 최대 4번까지 다시 시도
                parsed = _parse_number_at(text, search_from)
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
                    # 퍼센트를 찾는 중: %가 붙어 있고 0~100 범위여야 채택
                    if is_percent_value and 0 < value <= 100:
                        return value
                    search_from = number_end
                    continue

                # 금액을 찾는 중: 퍼센트 숫자는 건너뜁니다
                if is_percent_value:
                    search_from = number_end
                    continue

                # 숫자 뒤에 단위 단어가 없었다면 표 제목의 단위를 적용
                if apply_table_unit and not had_word_scale:
                    value *= _detect_table_unit(text, number_start)
                return value
    return None


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
LABELS_NONGAAP_GM_PCT = [
    r"non[-\s]?GAAP\s+gross\s+margin(?:\s+percentage)?",
    r"adjusted\s+gross\s+margin",
    r"gross\s+margin\s*\(non[-\s]?GAAP\)",
    r"non[-\s]?GAAP\s+gross\s+profit\s+margin",
]

# GAAP 매출총이익률(%) — 논갭이 없을 때 대체
LABELS_GAAP_GM_PCT = [
    r"GAAP\s+gross\s+margin",
    r"gross\s+margin(?:\s+percentage)?",
]

# 매출
LABELS_REVENUE = [
    r"total\s+revenues?",
    r"net\s+revenues?",
    r"^\s*revenues?\b",
    r"\brevenues?\b",
    r"net\s+sales",
]

# 논갭 영업비용 (역산에 사용)
LABELS_NONGAAP_OPEX = [
    r"non[-\s]?GAAP\s+(?:total\s+)?operating\s+expenses?",
    r"adjusted\s+(?:total\s+)?operating\s+expenses?",
    r"total\s+operating\s+expenses?\s*\(non[-\s]?GAAP\)",
]


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
    """
    result: dict = {
        "revenue": None,
        "op_income": None,
        "gross_margin_pct": None,
        "source": None,
        "gm_is_gaap": False,
        "derivation": "",   # 어떻게 구한 값인지 사람이 읽을 수 있는 설명
    }
    if not text:
        return result

    result["revenue"] = find_labeled_value(text, LABELS_REVENUE)

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
    if revenue is None or op_income is None:
        return
    if revenue <= 0 or abs(op_income) > revenue * 0.9:
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
        "xbrl_quarters": 0,        # XBRL로 만든 분기 수
        "merged_direct": 0,        # 8-K 값으로 덮어쓴 분기 수
        "text_source": "",         # 텍스트를 어디서 얻었나 (보도자료/첨부/본문)
        "first_error": "",         # 첫 예외 (원인 파악의 핵심)
        "unpaired_press": 0,       # 분기와 짝을 못 찾은 8-K 건수
        "pair_note": "",           # 짝짓기 실패 설명
        "forward_note": "",        # 전망(컨센서스) 수집 실패 사유
        "note": "",
    }


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

    _ensure_identity()
    from edgar import Company

    quarters: list[dict] = []
    company = Company(ticker)
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
        scanned += 1
        report["filings_found"] += 1

        text, text_source, had_exhibit = _earnings_text(filing, report)
        if not text:
            continue
        report["text_ok"] += 1
        if text_source and not report["text_source"]:
            report["text_source"] = text_source

        # EX-99 첨부(보도자료)를 확보했다면 표지 문구 판별은 건너뜁니다.
        # 표지에는 숫자가 없어 실적발표인데도 탈락하는 경우가 있기 때문입니다.
        if not had_exhibit and not _looks_like_earnings(text):
            continue
        report["gate_passed"] += 1

        parsed = parse_press_release(text)
        if parsed["revenue"] is None and parsed["op_income"] is None:
            continue  # 실적 숫자가 전혀 없으면 실적발표가 아닐 가능성

        # 보도자료 첨부가 아니라 공시 본문에서 읽은 경우에는 기준을 높입니다.
        # (실적과 무관한 8-K 본문에서 엉뚱한 금액을 주워 담아
        #  분기 짝짓기를 오염시키는 것을 실전에서 확인했습니다)
        if not had_exhibit and parsed["op_income"] is None:
            continue
        report["parsed_ok"] += 1

        filing_date = str(filing.filing_date)
        quarters.append(
            {
                "ticker": ticker,
                "filing_date": filing_date,
                "period_label": extract_period_label(text, filing_date),
                "revenue": parsed["revenue"],
                "op_income": parsed["op_income"],
                "gross_margin_pct": parsed["gross_margin_pct"],
                "source": parsed["source"] or cfg.SRC_DERIVED,
                "gm_is_gaap": parsed["gm_is_gaap"],
                # 화면의 "원문 보기" 링크에 사용 (사용자가 직접 공시를 확인할 수 있도록)
                "filing_url": _safe_filing_url(filing),
                "derivation": parsed.get("derivation", ""),  # 어떻게 계산했는지 설명
                "guidance_text": text[-6000:],  # 가이던스는 보도자료 뒷부분에 나옵니다
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


def _looks_like_earnings(text: str) -> bool:
    """이 공시가 실적발표인지 대략 판별합니다."""
    lowered = text[:20000].lower()
    return any(hint in lowered for hint in _EARNINGS_HINTS)


def _record_error(report: dict | None, where: str, exc: Exception) -> None:
    """첫 번째 예외만 진단 리포트에 남깁니다 (원인 파악용)."""
    if report is not None and not report.get("first_error"):
        report["first_error"] = f"[{where}] {type(exc).__name__}: {str(exc)[:180]}"


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
}


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
    from edgar import Company

    try:
        facts = Company(ticker).get_facts()
        if facts is None:
            return []
    except Exception as exc:
        _record_error(report, "get_facts", exc)
        return []

    # 개념별로 "분기(3개월)" 데이터를 뽑고, 빠진 4분기를 채워 넣습니다
    series: dict[str, dict[str, float]] = {}
    for key, concept_names in _XBRL_CONCEPTS.items():
        merged: dict[str, float] = {}
        for concept in concept_names:
            merged.update(_quarterly_series(facts, concept, report))

        # 연간(12개월) 값도 함께 가져와 빠진 4분기를 계산해 채웁니다
        annual: dict[str, float] = {}
        for concept in concept_names:
            annual.update(_annual_series(facts, concept, report))
        merged = _fill_missing_q4(merged, annual)

        series[key] = merged

    period_ends = sorted(
        d for d in series.get("op_income", {}) if d >= start_date
    )

    quarters: list[dict] = []
    for period_end in period_ends:
        gaap_op = series["op_income"].get(period_end)
        if gaap_op is None:
            continue

        sbc = series["sbc"].get(period_end) or 0.0
        amort = series["amortization"].get(period_end) or 0.0
        revenue = series["revenue"].get(period_end)
        gross_profit = series["gross_profit"].get(period_end)

        # 교차검증: 매출 ≥ 매출총이익 ≥ 0 이어야 하고, 영업이익은 매출을 넘을 수 없습니다.
        # 이 관계가 깨졌다면 매출 자리에 엉뚱한 항목이 들어온 것이므로 매출을 비웁니다.
        # (비워 두면 뒤의 검사 단계가 "매출 없음"으로 처리해 영업이익만 씁니다)
        approx_op = gaap_op + sbc + amort
        if revenue is not None:
            bad_gross = (
                gross_profit is not None and (gross_profit < 0 or gross_profit > revenue)
            )
            if revenue <= 0 or abs(approx_op) > revenue or bad_gross:
                if report is not None:
                    report.setdefault("xbrl_ambiguous", []).append(
                        f"{period_end}: 매출 ${revenue:,.0f} 이 다른 항목과 앞뒤가 맞지 않아 제외"
                    )
                revenue = None
                gross_profit = None

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
                "gross_margin_pct": gm_pct,
                "source": cfg.SRC_APPROX,
                "gm_is_gaap": True,
                "filing_url": f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&company={ticker}&type=8-K",
                "derivation": (
                    "보도자료를 읽지 못해 SEC XBRL 회계데이터로 근사했습니다.\n\n"
                    f"GAAP 영업이익 ${gaap_op/1e6:,.1f}M\n\n"
                    f"+ 주식보상비 ${sbc/1e6:,.1f}M\n\n"
                    f"+ 무형자산상각 ${amort/1e6:,.1f}M\n\n"
                    f"= **${approx_op/1e6:,.1f}M** (근사치)\n\n"
                    "※ GM%는 GAAP 값(매출총이익 ÷ 매출)을 사용했습니다."
                ),
                "guidance_text": "",
            }
        )

    quarters.sort(key=lambda q: q["filing_date"])
    return quarters


def _quarterly_series(facts, concept: str, report: dict | None = None) -> dict[str, float]:
    """XBRL에서 특정 항목의 분기(3개월) 값들을 {기간종료일: 값}으로 뽑습니다."""
    return _period_series(facts, concept, months=3, report=report)


def _annual_series(facts, concept: str, report: dict | None = None) -> dict[str, float]:
    """XBRL에서 특정 항목의 연간(12개월) 값들을 {기간종료일: 값}으로 뽑습니다.

    4분기(Q4)는 10-K에 연간으로만 신고되는 경우가 많아, 이 값이 필요합니다.
    """
    return _period_series(facts, concept, months=12, report=report)


def _fill_missing_q4(
    quarterly: dict[str, float],
    annual: dict[str, float],
) -> dict[str, float]:
    """빠져 있는 4분기를 `연간 − (1분기 + 2분기 + 3분기)` 로 계산해 채웁니다.

    왜 필요한가:
      회사는 1~3분기는 10-Q에 "3개월" 단위로 신고하지만,
      4분기는 10-K에 "연간(12개월)"으로만 신고하는 경우가 대부분입니다.
      그래서 3개월짜리만 모으면 매년 4분기가 통째로 빠지고,
      가속/감속을 판단할 표본이 25%나 줄어듭니다.
    """
    if not annual:
        return quarterly

    filled = dict(quarterly)

    for fy_end, annual_value in annual.items():
        if fy_end in filled:
            continue  # 이미 4분기 값이 있으면 건드리지 않습니다

        # 회계연도 종료일로부터 거슬러 올라가며 앞선 세 분기를 찾습니다
        prior = _find_prior_three_quarters(quarterly, fy_end)
        if prior is None:
            continue

        q4_value = annual_value - sum(prior)
        filled[fy_end] = q4_value

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
    facts, concept: str, months: int, report: dict | None = None
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
    try:
        df = (
            facts.query()
            .by_concept(concept)
            .by_period_length(months)
            .to_dataframe()
        )
    except Exception:
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

    for _, row in df.iterrows():
        # ① 개념 이름 완전일치 ("us-gaap:Revenues" → "Revenues")
        if concept_col is not None:
            name = str(row[concept_col]).split(":")[-1].strip().lower()
            if name != wanted:
                rejected += 1
                continue

        # ② 달러 단위만 (USD/shares, shares, pure 는 금액이 아닙니다)
        if unit_col is not None:
            unit = str(row[unit_col]).strip().upper()
            if unit and unit != "USD":
                rejected += 1
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
        low, high = unique[0], unique[-1]
        if low > 0 and high / low <= 2.0:
            out[key] = unique[len(unique) // 2]   # 비슷하면 가운데 값
        else:
            ambiguous.append(f"{concept} {key}: 후보 {len(unique)}개가 서로 달라 제외")

    if report is not None:
        if rejected:
            report["xbrl_rejected"] = report.get("xbrl_rejected", 0) + rejected
        if ambiguous:
            report.setdefault("xbrl_ambiguous", []).extend(ambiguous)

    return out


# ---------------------------------------------------------------------------
# 바깥에서 호출하는 함수
# ---------------------------------------------------------------------------
def _apply_press_to_row(row: dict, press: dict) -> None:
    """8-K에서 실제로 뽑힌 값만 XBRL 행에 덮어씁니다 (없는 값은 XBRL 값 유지)."""
    if press.get("op_income") is not None:
        row["op_income"] = press["op_income"]
        row["source"] = press.get("source") or cfg.SRC_DERIVED
        row["derivation"] = press.get("derivation", "")
        row["gm_is_gaap"] = press.get("gm_is_gaap", False)
    if press.get("revenue") is not None:
        row["revenue"] = press["revenue"]
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
        return sorted(press_quarters, key=lambda q: q["filing_date"])
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

        # 이 분기 종료 후 일정 기간 안에 나온 8-K 중 가장 가까운 것을 짝짓습니다
        # (연말 분기는 10-K와 함께 늦게 발표하는 회사가 있어 창을 넉넉히 둡니다)
        best_index, best_gap = None, None
        for index, press in enumerate(press_quarters):
            if index in used_press or index in promote_only:
                continue
            press_date = _to_date(press.get("filing_date", ""))
            if press_date is None:
                continue
            gap = (press_date - period_date).days
            if 0 <= gap <= cfg.PAIRING_WINDOW_DAYS and (best_gap is None or gap < best_gap):
                best_index, best_gap = index, gap

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

    # 짝을 못 찾은 8-K가 있으면 진단에 남깁니다 (짝짓기 실패 원인 추적용)
    if report is not None:
        unpaired = [
            press.get("filing_date", "?")
            for index, press in enumerate(press_quarters)
            if index not in used_press
        ]
        report["unpaired_press"] = len(unpaired)
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
