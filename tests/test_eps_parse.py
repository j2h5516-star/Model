"""
test_eps_parse.py — 보도자료에서 주당순이익(EPS) 읽어 오기 · 1단계 검증
========================================================================

왜 EPS 를 따로 읽는가
---------------------
미국 증권규정(Reg S-K 10(e))은 회사가 '조정 EPS' 를 발표하면 **반드시** GAAP EPS 와
나란히 대조표를 싣도록 정해 두었습니다. 그래서 조정 EPS 는 보도자료 안에서
**존재가 규정으로 보장된 유일한 논갭 숫자**입니다. 논갭 영업이익은 그런 의무가
없어 아예 안 싣는 회사가 많습니다(ZETA 는 조정 EBITDA 만 발표).

여기서 확인하는 것
------------------
  ① 회사마다 다른 표기 형식을 모두 읽는가
  ② **단위 배수를 적용하지 않는가** — 이게 가장 중요합니다.
     "(in thousands)" 표에서 $1.00 에 1,000 을 곱하면 $1,000 이 되어 버립니다.
  ③ 적자를 음수로 만드는가 (이름의 loss / 표의 괄호 두 가지 경로)
  ④ 논갭 값을 GAAP 자리에 잘못 넣지 않는가
  ⑤ 근사치만으로 '적자 지속'을 선언하지 않는가 (코히런트 사태)
  ⑥ 이익의 질(격차 추세) 판정이 맞는가

⚠️ 이 단계에서 EPS 는 **점수에 쓰지 않습니다.** 실제 배포 환경에서 얼마나 잘
   읽히는지 재는 것이 목적입니다. 기준자를 EPS 로 바꿀지는 그 성공률을 보고
   결정합니다.

실행: python3 tests/test_eps_parse.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import config as cfg  # noqa: E402
import pipeline  # noqa: E402
import scoring  # noqa: E402
import sec_fundamentals as sf  # noqa: E402

M = 1_000_000.0


# ---------------------------------------------------------------------------
# ① 회사마다 다른 표기 읽기
# ---------------------------------------------------------------------------
def test_narrative_form_reads_both_eps():
    """서술형 — 코히런트 실제 형식 (GAAP 적자 · 논갭 흑자)"""
    text = (
        "Coherent Corp. Reports Fourth Quarter Fiscal 2025 Results\n"
        "Revenue was a record $1,527.5 million. GAAP gross margin was 35.7%.\n"
        "GAAP net loss per diluted share was $0.83, while on a non-GAAP basis,\n"
        "gross margin was 38.1% and non-GAAP earnings per diluted share was $1.00."
    )
    result = sf.parse_press_release(text)
    assert result["adj_eps"] == 1.00, result["adj_eps"]
    assert result["gaap_eps"] == -0.83, result["gaap_eps"]


def test_nvidia_style_reads_both_eps():
    """서술형 — 둘 다 흑자이고 GAAP 이 먼저 나오는 형식"""
    text = (
        "For the quarter, GAAP earnings per diluted share was $0.78, up 12%.\n"
        "Non-GAAP earnings per diluted share was $0.89, up 19% from a year ago."
    )
    result = sf.parse_press_release(text)
    assert result["adj_eps"] == 0.89
    assert result["gaap_eps"] == 0.78


def test_adjusted_eps_wording_is_recognised():
    """'Adjusted EPS' 라는 짧은 표기와 'Diluted earnings per share' 어순"""
    text = (
        "Adjusted EPS of $2.45 compared to $1.90 in the prior year period.\n"
        "Diluted earnings per share was $1.12."
    )
    result = sf.parse_press_release(text)
    assert result["adj_eps"] == 2.45
    assert result["gaap_eps"] == 1.12


def test_reversed_word_order_is_recognised():
    """'Non-GAAP diluted earnings per share' 처럼 diluted 가 앞에 오는 형식"""
    text = (
        "Non-GAAP diluted earnings per share    $ 3.53\n"
        "Diluted loss per share                 $ (0.52)"
    )
    result = sf.parse_press_release(text)
    assert result["adj_eps"] == 3.53
    assert result["gaap_eps"] == -0.52


def test_missing_eps_returns_none():
    """EPS 가 없는 보도자료는 조용히 None — 억지로 다른 숫자를 집지 않습니다"""
    text = "Revenue was $500 million. Non-GAAP operating income was $100 million."
    result = sf.parse_press_release(text)
    assert result["adj_eps"] is None
    assert result["gaap_eps"] is None


# ---------------------------------------------------------------------------
# ② 단위 배수를 적용하지 않는가 — 가장 중요한 검사
# ---------------------------------------------------------------------------
def test_table_unit_is_never_applied_to_eps():
    """'(in thousands)' 표 안에서도 $1.00 은 $1.00 이어야 합니다.

    금액용 함수(find_labeled_value)는 표 제목의 단위를 곱합니다. 주당 금액에
    그 규칙을 그대로 쓰면 $1.00 이 $1,000 이 되어 모든 계산이 무너집니다.
    """
    text = (
        "CONDENSED RESULTS (in thousands, except per share data)\n"
        "Total revenue                                  $ 1,527,500\n"
        "GAAP net income (loss) per diluted share       $   (0.83)\n"
        "Non-GAAP net income per diluted share          $    1.00"
    )
    result = sf.parse_press_release(text)
    assert result["adj_eps"] == 1.00, f"단위 배수가 잘못 적용됨: {result['adj_eps']}"
    assert result["gaap_eps"] == -0.83, result["gaap_eps"]
    # 매출은 반대로 단위 배수가 적용되어야 합니다 (천 단위 → 15억 달러)
    assert result["revenue"] == 1_527_500 * 1_000


def test_absurd_per_share_value_is_rejected():
    """주당 금액이 비현실적으로 크면 표의 다른 숫자를 잘못 집은 것입니다"""
    text = f"Non-GAAP EPS {cfg.EPS_MAX_ABS * 10:,.0f}"
    assert sf.find_eps_value(text, sf.LABELS_ADJUSTED_EPS) is None


# ---------------------------------------------------------------------------
# ③ 적자를 음수로 — 두 가지 경로
# ---------------------------------------------------------------------------
def test_loss_in_label_flips_sign():
    """이름이 'loss' 라고 말하면 양수로 적혀 있어도 적자입니다"""
    text = "The company reported a net loss per diluted share of $1.25 for the quarter."
    result = sf.parse_press_release(text)
    assert result["gaap_eps"] == -1.25


def test_income_loss_header_does_not_flip_sign():
    """'net income (loss) per share' 는 표의 항목 이름일 뿐 적자 선언이 아닙니다.

    이 표기를 적자로 오해해 부호를 뒤집으면, 괄호로 이미 음수가 된 값이
    다시 양수가 되어 흑자로 둔갑합니다.
    """
    text = "GAAP net income (loss) per diluted share       $ 2.10"
    result = sf.parse_press_release(text)
    assert result["gaap_eps"] == 2.10, result["gaap_eps"]


# ---------------------------------------------------------------------------
# ④ 논갭 값을 GAAP 자리에 넣지 않는가
# ---------------------------------------------------------------------------
def test_nongaap_line_is_not_read_as_gaap():
    """'Non-GAAP' 안에도 'GAAP' 글자가 있습니다. 여기에 속으면 안 됩니다."""
    text = "Non-GAAP net income per diluted share    $ 1.00"
    result = sf.parse_press_release(text)
    assert result["adj_eps"] == 1.00
    assert result["gaap_eps"] is None, f"논갭 값을 GAAP 으로 읽음: {result['gaap_eps']}"


def test_adjusted_prefix_is_not_read_as_gaap():
    """'Adjusted diluted earnings per share' 도 GAAP 이 아닙니다"""
    text = "Adjusted diluted earnings per share    $ 4.20"
    result = sf.parse_press_release(text)
    assert result["adj_eps"] == 4.20
    assert result["gaap_eps"] is None


# ---------------------------------------------------------------------------
# ⑤ 근사치만으로 '적자 지속'을 선언하지 않는가 — 코히런트 사태
# ---------------------------------------------------------------------------
def _quarters(values, source):
    """분기 목록을 만듭니다. values 는 백만 달러 단위입니다."""
    return [
        {
            "period_label": f"Q{i + 1}",
            "fiscal_quarter": i % 4 + 1,
            "op_income": v * M,
            "revenue": abs(v) * M * 5 + 1e8,
            "source": source,
        }
        for i, v in enumerate(values)
    ]


def test_approximated_negative_is_unknown_not_loss():
    """근사치가 마이너스면 '적자 지속'이 아니라 '판단불가'.

    코히런트 FY2025 는 GAAP 주당순이익 −$0.52 인데 조정 주당순이익은 +$3.53
    이었습니다. 인수로 생긴 상각비 때문에 GAAP 만 적자입니다. XBRL 근사치가
    상각을 되돌리지 못하면 이런 회사가 '적자 지속'(국면 최하 등급)이 되어
    순위 바닥으로 밀립니다.
    """
    quarters = _quarters([-30, -28, -32, -29, -31, -27], cfg.SRC_APPROX)
    result = scoring.score_phase(quarters)
    assert result["phase"] == cfg.PH_UNKNOWN, result["phase"]
    assert "근사치" in result["detail"]


def test_disclosed_negative_is_still_loss():
    """회사가 직접 밝힌 값이 마이너스면 그건 진짜 적자입니다 — 그대로 표시합니다"""
    quarters = _quarters([-30, -28, -32, -29, -31, -27], cfg.SRC_DIRECT)
    result = scoring.score_phase(quarters)
    assert result["phase"] == cfg.PH_LOSS, result["phase"]


def test_one_disclosed_quarter_is_enough_to_trust_the_loss():
    """최근 1년치에 공식 발표가 한 분기라도 섞여 있으면 근거가 있다고 봅니다"""
    quarters = _quarters([-30, -28, -32, -29, -31, -27], cfg.SRC_APPROX)
    quarters[-1]["source"] = cfg.SRC_DIRECT
    assert scoring.score_phase(quarters)["phase"] == cfg.PH_LOSS


def test_approximated_profit_is_unaffected():
    """흑자인 근사치는 지금까지와 똑같이 판정합니다 (이번 변경의 부작용 없음)"""
    quarters = _quarters([10, 12, 14, 16, 18, 20], cfg.SRC_APPROX)
    assert scoring.score_phase(quarters)["phase"] == cfg.PH_NEW_HIGH


# ---------------------------------------------------------------------------
# ⑥ 이익의 질 — GAAP 과 조정의 격차 추세
# ---------------------------------------------------------------------------
def _eps_quarters(pairs):
    """(조정 EPS, GAAP EPS) 짝 목록을 분기 형태로 만듭니다"""
    return [
        {"period_label": f"Q{i + 1}", "adj_eps": adj, "gaap_eps": gaap}
        for i, (adj, gaap) in enumerate(pairs)
    ]


def test_widening_gap_is_flagged():
    """격차가 계속 벌어지면 경고 — '조정'이 가리는 몫이 커지는 중"""
    quarters = _eps_quarters([
        (1.00, 0.90), (1.10, 0.98),      # 격차 0.10, 0.12
        (1.40, 0.90), (1.60, 1.00),      # 격차 0.50, 0.60
    ])
    result = scoring.check_earnings_quality(quarters)
    assert result["verdict"] == cfg.QUALITY_GAP_WIDENING, result["verdict"]
    assert "믿으면 안 됩니다" in result["detail"]


def test_narrowing_gap_is_healthy():
    """격차가 줄면 일회성 비용이 실제로 끝나가는 중입니다"""
    quarters = _eps_quarters([
        (1.50, 0.50), (1.60, 0.60),      # 격차 1.00
        (1.70, 1.50), (1.80, 1.70),      # 격차 0.20, 0.10
    ])
    result = scoring.check_earnings_quality(quarters)
    assert result["verdict"] == cfg.QUALITY_GAP_NARROWING, result["verdict"]


def test_stable_gap_is_normal():
    """인수 상각처럼 예정된 항목은 격차가 일정합니다"""
    quarters = _eps_quarters([
        (1.00, 0.60), (1.10, 0.70), (1.20, 0.80), (1.30, 0.90),
    ])
    result = scoring.check_earnings_quality(quarters)
    assert result["verdict"] == cfg.QUALITY_GAP_STABLE, result["verdict"]


def test_too_few_pairs_is_unknown():
    """짝이 모자라면 추세를 말하지 않습니다 — 지어내지 않습니다"""
    quarters = _eps_quarters([(1.00, 0.90), (1.10, 0.98)])
    result = scoring.check_earnings_quality(quarters)
    assert result["verdict"] == cfg.QUALITY_GAP_UNKNOWN


def test_quality_check_ignores_quarters_missing_one_side():
    """한쪽 EPS 만 있는 분기는 격차를 낼 수 없으므로 제외합니다"""
    quarters = _eps_quarters([
        (1.00, 0.90), (1.10, 0.98), (1.40, 0.90), (1.60, 1.00),
    ])
    quarters.append({"period_label": "Q5", "adj_eps": 2.00, "gaap_eps": None})
    result = scoring.check_earnings_quality(quarters)
    assert len(result["trace"]["pairs"]) == 4


def test_earnings_quality_is_not_scored():
    """이익의 질은 아직 검증 전이므로 **점수에 반영하지 않습니다**.

    검증되지 않은 신호에 점수를 주지 않는다는 이 저장소의 원칙입니다.
    """
    clean = _quarters([10, 12, 14, 16, 18, 20], cfg.SRC_DIRECT)
    widening = [dict(q) for q in clean]
    for i, q in enumerate(widening):
        q["adj_eps"] = 1.0 + i * 0.5      # 격차가 크게 벌어지는 회사
        q["gaap_eps"] = 0.9
    for q in clean:
        q["adj_eps"] = 1.0
        q["gaap_eps"] = 0.95              # 격차가 일정한 회사

    forward = {"forward_op_income": None, "basis": None}
    a = scoring.score_fundamental(clean, forward)["total"]
    b = scoring.score_fundamental(widening, forward)["total"]
    assert a == b, f"이익의 질이 점수를 움직였습니다: {a} vs {b}"


# ---------------------------------------------------------------------------
# ⑦ 진단 기록 — 배포 후 성공률을 재기 위한 준비
# ---------------------------------------------------------------------------
def test_report_counts_eps_separately():
    """논갭 영업이익과 조정 EPS 중 어느 쪽이 잘 읽히는지 따로 세어야 합니다.

    이 두 숫자를 배포 환경에서 비교해 보고 모델의 기준자를 바꿀지 결정합니다.
    """
    report = sf.new_report("TEST")
    for key in ("op_income_ok", "adj_eps_ok", "gaap_eps_ok"):
        assert key in report, f"진단 기록에 {key} 가 없습니다"
        assert report[key] == 0


def test_eps_survives_the_merge_into_quarter_rows():
    """보도자료에서 읽은 EPS 가 최종 분기 자료까지 살아서 도착하는가"""
    row = {"op_income": 100 * M, "source": cfg.SRC_APPROX}
    press = {"adj_eps": 1.00, "gaap_eps": -0.83, "op_income": None}
    sf._apply_press_to_row(row, press)
    assert row["adj_eps"] == 1.00
    assert row["gaap_eps"] == -0.83


def test_eps_only_release_is_still_accepted():
    """매출·영업이익을 못 읽어도 조정 EPS 가 있으면 실적발표로 인정합니다.

    규정상 존재가 보장된 값이므로, 이것만 잡히는 보도자료를 버리면
    수집 성공률을 재는 것 자체가 불가능해집니다.
    """
    text = "Non-GAAP earnings per diluted share was $1.00 for the quarter."
    result = sf.parse_press_release(text)
    assert result["adj_eps"] == 1.00
    assert result["revenue"] is None and result["op_income"] is None


# ---------------------------------------------------------------------------
# ⑧ 기준자(어느 숫자로 재는가)에 따라 크기 검사가 달라져야 한다
# ---------------------------------------------------------------------------
def _basis_quarters(values, basis):
    """같은 '성장 모양'을 서로 다른 단위로 만듭니다"""
    return [
        {
            "period_label": f"Q{i + 1}",
            "fiscal_quarter": i % 4 + 1,
            "period_end": f"202{3 + i // 4}-{(i % 4) * 3 + 3:02d}-28",
            "op_income": v,
            "revenue": abs(v) * 5 + 1e8,
            "source": cfg.SRC_DIRECT,
            "basis": basis,
        }
        for i, v in enumerate(values)
    ]


def test_low_base_guard_uses_the_right_threshold_per_basis():
    """같은 성장 모양이면 기준자가 달라도 델타 점수가 같아야 합니다.

    '저기저 함정' 방지 장치는 "최근 이익이 $50M 미만이면 점수를 절반까지만"
    이라는 규칙입니다. 조정 EPS 는 숫자가 $8.53 이라 이 문턱에 **무조건** 걸립니다.
    실제로 확인했더니 같은 성장인데 점수가 18.0 → 15.0 으로 깎였고,
    화면에는 "$0M" 이라고만 나와 원인을 알 수도 없었습니다.
    """
    shape = [1.00, 1.15, 1.32, 1.52, 1.75, 2.01, 2.31, 2.66]
    money = _basis_quarters([g * 100 * M for g in shape], cfg.BASIS_OP_INCOME)
    per_share = _basis_quarters(list(shape), cfg.BASIS_ADJ_EPS)

    a = scoring.score_delta_acceleration(money)
    b = scoring.score_delta_acceleration(per_share)

    assert a["score"] == b["score"], f"기준자에 따라 점수가 달라짐: {a['score']} vs {b['score']}"
    assert a["trace"]["capped"] is False and b["trace"]["capped"] is False
    assert b["trace"]["metric_basis"] == cfg.BASIS_ADJ_EPS


def test_low_base_guard_still_fires_on_a_genuinely_tiny_eps():
    """진짜로 손익분기 근처인 EPS 는 여전히 제한해야 합니다 (장치를 끈 게 아님)"""
    tiny = _basis_quarters([0.010, 0.012, 0.014, 0.017, 0.020, 0.024, 0.029, 0.035],
                           cfg.BASIS_ADJ_EPS)
    result = scoring.score_delta_acceleration(tiny)
    assert result["trace"]["capped"] is True, "주당 1~3센트인데 제한이 걸리지 않았습니다"


def test_amount_is_written_in_the_right_unit():
    """조정 EPS 를 백만 단위로 나눠 '$0M' 으로 쓰면 안 됩니다"""
    assert cfg.fmt_amount(1_352_000_000, cfg.BASIS_OP_INCOME) == "$1,352.0M"
    assert cfg.fmt_amount(8.53, cfg.BASIS_ADJ_EPS) == "$8.53/주"
    assert cfg.fmt_amount(None, cfg.BASIS_ADJ_EPS) == "-"


def test_basis_defaults_to_operating_income():
    """표시가 없는 옛 자료는 지금까지처럼 논갭 영업이익으로 봅니다"""
    assert cfg.quarters_basis([]) == cfg.BASIS_OP_INCOME
    assert cfg.quarters_basis([{"op_income": 1.0}]) == cfg.BASIS_OP_INCOME


# ---------------------------------------------------------------------------
# ⑨ 구조대 경로 — 논갭 영업이익이 모자랄 때만 조정 EPS 로 판정
# ---------------------------------------------------------------------------
def _mixed(op_values, eps_values):
    """영업이익과 EPS 를 각각 원하는 만큼 채운 분기 목록"""
    rows = []
    for i in range(max(len(op_values), len(eps_values))):
        rows.append({
            "period_label": f"Q{i + 1}",
            "fiscal_quarter": i % 4 + 1,
            "period_end": f"202{3 + i // 4}-{(i % 4) * 3 + 3:02d}-28",
            "op_income": op_values[i] if i < len(op_values) else None,
            "adj_eps": eps_values[i] if i < len(eps_values) else None,
            "revenue": 1e9,
            "source": cfg.SRC_DIRECT,
        })
    return rows


def test_operating_income_wins_when_it_is_available():
    """논갭 영업이익이 충분하면 EPS 가 있어도 영업이익을 씁니다 — 1순위 유지"""
    rows = _mixed([10 * M] * 8, [1.0] * 8)
    out = pipeline.apply_metric_basis(rows, {})
    assert cfg.quarters_basis(out) == cfg.BASIS_OP_INCOME
    assert out[0]["op_income"] == 10 * M


def test_eps_rescues_when_operating_income_is_missing():
    """영업이익이 모자라면 조정 EPS 로 판정합니다 — 빈칸으로 두는 것보다 낫습니다"""
    rows = _mixed([10 * M, 11 * M], [1.0, 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7])
    report = {}
    out = pipeline.apply_metric_basis(rows, report)

    assert cfg.quarters_basis(out) == cfg.BASIS_ADJ_EPS
    assert len(out) == 8
    assert out[-1]["op_income"] == 1.7            # EPS 값이 판정에 들어갑니다
    assert out[-1]["source"] == cfg.SRC_ADJ_EPS
    assert report["metric_basis"] == cfg.BASIS_ADJ_EPS


def test_rescue_needs_enough_eps_quarters_too():
    """EPS 도 모자라면 억지로 바꾸지 않습니다"""
    rows = _mixed([10 * M, 11 * M], [1.0, 1.1])
    out = pipeline.apply_metric_basis(rows, {})
    assert cfg.quarters_basis(out) == cfg.BASIS_OP_INCOME


def test_basis_is_never_mixed_within_one_ticker():
    """한 종목 안에서 두 기준을 섞으면 안 됩니다.

    레벨이 다른 숫자를 이어 붙이면 실제로는 아무 일도 없는데
    거대한 가짜 증가율이 생깁니다.
    """
    rows = _mixed([10 * M, 11 * M, 12 * M], [1.0, 1.1, 1.2, 1.3, 1.4, 1.5, 1.6])
    out = pipeline.apply_metric_basis(rows, {})
    assert len({q["basis"] for q in out}) == 1, "한 종목 안에 기준자가 섞였습니다"


def test_original_operating_income_is_kept_for_comparison():
    """바꿔치기해도 원래 영업이익은 화면 대조용으로 남겨 둡니다"""
    rows = _mixed([10 * M], [1.0, 1.1, 1.2, 1.3, 1.4, 1.5])
    out = pipeline.apply_metric_basis(rows, {})
    assert out[0]["op_income_original"] == 10 * M


# ---------------------------------------------------------------------------
# ⑩ 자사주 방어장치 — 구조대 경로에서 '가속'이 자사주가 만든 것일 때
# ---------------------------------------------------------------------------
def _accelerating_eps_quarters():
    """증가율이 빨라지는 EPS 시리즈 (가속 판정이 나오도록)"""
    rows = _mixed([], [1.00, 1.10, 1.25, 1.45, 1.72, 2.10, 2.60, 3.30])
    return pipeline.apply_metric_basis(rows, {})


def test_heavy_buyback_warns_but_does_not_change_the_score():
    """자사주가 크면 **경고만** 하고 점수는 건드리지 않습니다.

    처음에는 델타 점수를 절반으로 깎았습니다. 그런데 재검증에서 근거가
    무너졌습니다 — 자사주 종목 표본에서는 상관 -0.590 인데 다른 표본에서는
    +0.022 로 관계가 사라졌고, 문턱으로 잡았던 -8% 는 회귀선상 근거가
    -13.7% 였습니다. '결정적 사례'로 인용했던 EBAY 2021-12-31 은 두 기준의
    전망 라벨이 똑같이 '가속 둔화' 였습니다.

    검증되지 않은 신호에 점수를 주지 않는다는 원칙에 따라 철회했습니다.
    """
    quarters = _accelerating_eps_quarters()
    plain = scoring.score_fundamental(
        quarters, {"forward_op_income": None, "basis": None, "consensus": {}})
    buyback = scoring.score_fundamental(
        quarters,
        {"forward_op_income": None, "basis": None,
         "consensus": {"shares_shrink_pct": -11.7}},
    )
    assert buyback["delta"]["score"] == plain["delta"]["score"], "점수를 건드렸습니다"
    assert buyback["delta"].get("buyback_warned") is True
    assert "자사주" in buyback["delta"]["detail"]
    assert "점수에는 반영하지 않았습니다" in buyback["delta"]["detail"]


def test_mild_buyback_is_not_even_warned():
    """조금 줄어든 정도로는 경고도 하지 않습니다"""
    quarters = _accelerating_eps_quarters()
    mild = scoring.score_fundamental(
        quarters,
        {"forward_op_income": None, "basis": None,
         "consensus": {"shares_shrink_pct": -3.0}},
    )
    assert mild["delta"].get("buyback_warned") is not True


def test_buyback_warning_does_not_apply_to_operating_income_basis():
    """논갭 영업이익은 주식수로 나눈 값이 아니므로 자사주와 무관합니다"""
    rows = _mixed([100 * M, 110 * M, 125 * M, 145 * M, 172 * M, 210 * M, 260 * M, 330 * M], [])
    quarters = pipeline.apply_metric_basis(rows, {})
    b = scoring.score_fundamental(
        quarters,
        {"forward_op_income": None, "basis": None,
         "consensus": {"shares_shrink_pct": -20.0}},
    )
    assert b["delta"].get("buyback_warned") is not True


def test_phase_detail_uses_per_share_wording_on_eps_basis():
    """구조대 경로에서 국면 설명이 '$0M' 으로 깨지면 안 됩니다"""
    rows = _mixed([], [1.0, 1.1, 1.2, 1.3, 1.5, 1.8])
    quarters = pipeline.apply_metric_basis(rows, {})
    detail = scoring.score_phase(quarters)["detail"]
    assert "/주" in detail, detail
    assert "$0M" not in detail, detail


def test_delta_history_table_uses_per_share_columns_on_eps_basis():
    """근거 표가 전부 0.0 이 되면 숫자를 확인할 수가 없습니다"""
    import explain

    rows = _mixed([], [1.00, 1.10, 1.25, 1.45, 1.72, 2.10, 2.60, 3.30])
    quarters = pipeline.apply_metric_basis(rows, {})
    delta = scoring.score_delta_acceleration(quarters)
    table = explain.explain_delta({
        "fundamental": {"delta": delta}, "quarters": quarters,
        "forecast_detail": {}, "delta_forecast": None,
    })["history"]

    assert table, "이력 표가 비었습니다"
    assert "조정EPS($/주)" in table[0], list(table[0])
    assert table[0]["조정EPS($/주)"] > 0, table[0]


def test_forecast_uses_the_same_ruler_as_the_history():
    """구조대 경로에서 전망도 **주당 달러**여야 합니다 (LITE 루멘텀 사고).

    과거는 주당 $0.87 인데 전망만 달러($60,000,000)로 만들면
    증가율이 수십억 %가 되어 통째로 버려지거나, 평균 마진이 0.0000002% 로
    계산돼 전망이 0 이 됩니다. 실제 화면에서 그렇게 나왔습니다.
    """
    import forward_estimates as fe

    rows = _mixed([], [0.60, 0.65, 0.72, 0.80, 0.84, 0.87])
    quarters = pipeline.apply_metric_basis(rows, {})
    output = {
        "forward_op_income": None, "basis": None,
        "forward_op_income_2": None, "basis_2": None,
        "detail": "", "detail_2": "",
    }
    consensus = {"eps_0q": 0.95, "eps_1q": 1.02, "analysts_0q": 12, "errors": []}
    result = fe._forward_on_eps_basis(output, consensus, quarters)

    assert result["forward_op_income"] == 0.95, result["forward_op_income"]
    assert result["forward_op_income_2"] == 1.02
    assert "/주" in result["detail"], result["detail"]


def test_dollar_forecast_is_rejected_on_eps_basis():
    """달러 전망이 섞여 들어오면 크기 검사가 걸러 내야 합니다"""
    import forward_estimates as fe

    rows = _mixed([], [0.60, 0.65, 0.72, 0.80, 0.84, 0.87])
    quarters = pipeline.apply_metric_basis(rows, {})
    output = {
        "forward_op_income": None, "basis": None,
        "forward_op_income_2": None, "basis_2": None,
        "detail": "", "detail_2": "",
    }
    # 달러 값이 EPS 자리에 들어온 상황 (자가 어긋난 경우)
    consensus = {"eps_0q": 60_000_000.0, "eps_1q": None, "errors": []}
    result = fe._forward_on_eps_basis(output, consensus, quarters)

    assert result["forward_op_income"] is None, "자가 어긋난 전망이 통과했습니다"
    assert any("제외" in e for e in consensus["errors"]), consensus["errors"]


def test_no_eps_consensus_leaves_forecast_empty():
    """EPS 컨센서스가 없으면 억지로 만들지 않고 비워 둡니다"""
    import forward_estimates as fe

    rows = _mixed([], [0.60, 0.65, 0.72, 0.80, 0.84, 0.87])
    quarters = pipeline.apply_metric_basis(rows, {})
    output = {
        "forward_op_income": None, "basis": None,
        "forward_op_income_2": None, "basis_2": None,
        "detail": "", "detail_2": "",
    }
    consensus = {"eps_0q": None, "eps_1q": None, "errors": []}
    result = fe._forward_on_eps_basis(output, consensus, quarters)

    assert result["forward_op_income"] is None
    assert any("EPS 컨센서스" in e for e in consensus["errors"]), consensus["errors"]


def test_measured_numbers_are_recorded_in_config():
    """검증 결과를 코드에 남겨 둡니다 — 나중에 근거를 찾을 수 있도록"""
    assert cfg.EPS_BASIS_DIRECTION_MATCH == 0.857     # 30/35 (오염 분기 제외 후)
    assert cfg.EPS_BASIS_MATCH_NO_BUYBACK == 0.96     # 48/50 (자사주 없는 종목)
    assert cfg.EPS_BASIS_BUYBACK_CORR < 0             # 자사주 종목 표본에서는 음의 상관
    # ⚠️ 그런데 다른 표본에서는 관계가 사라집니다 — 그래서 점수에 반영하지 않습니다
    assert abs(cfg.EPS_BASIS_BUYBACK_CORR_OTHER) < 0.1
    # 조정 EPS 의 신뢰도는 직접공시(95%)보다 낮고 근사치(55%)보다 높아야 합니다
    assert cfg.CONFIDENCE_PCT[cfg.SRC_APPROX] < cfg.CONFIDENCE_PCT[cfg.SRC_ADJ_EPS]
    assert cfg.CONFIDENCE_PCT[cfg.SRC_ADJ_EPS] < cfg.CONFIDENCE_PCT[cfg.SRC_DIRECT]


# ---------------------------------------------------------------------------
# 실물 보도자료 검증 — 연료 파이프라인(data/measure/raw/)이 가져온 진짜 문장
# ---------------------------------------------------------------------------
# 지금까지 파서가 계속 어긋난 이유는 "지어낸 예제로만 테스트해서"였습니다.
# 아래 문장들은 배포된 앱이 저장소로 커밋해 준 **실제 보도자료**에서 그대로
# 따온 것이고, 기대값은 그 문서에 인쇄된 실제 숫자입니다.
def test_real_crdo_diluted_before_net():
    """실물 CRDO 2026-06-01 — 'non-GAAP **diluted** net income per share' 어순"""
    text = (
        "GAAP diluted net income per share of $0.88 and "
        "non-GAAP diluted net income per share of $1.16"
    )
    result = sf.parse_press_release(text)
    assert result["adj_eps"] == 1.16, result["adj_eps"]
    assert result["gaap_eps"] == 0.88, result["gaap_eps"]


def test_real_sndk_paren_value_before_label():
    """실물 샌디스크 2026-08-05 — 값이 이름 **앞 괄호 안**에 있는 형식.

    고치기 전에는 이름 뒤쪽을 훑다가 뒤 문장의 숫자를 물어
    GAAP EPS 가 202.0 으로 나왔습니다.
    """
    text = (
        "Fiscal fourth quarter revenue was $8.97 billion, up 51% sequentially, "
        "with GAAP net income reported at $6.90 billion ($43.97 diluted net income "
        "per share). Sequential revenue growth came approximately one-third from "
        "higher volumes and two-thirds from higher pricing. Fourth quarter "
        "Non-GAAP diluted net income per share was $39.25."
    )
    result = sf.parse_press_release(text)
    assert result["adj_eps"] == 39.25, result["adj_eps"]      # EPS 상한(1000) 안의 큰 EPS
    assert result["gaap_eps"] == 43.97, result["gaap_eps"]


def test_real_amd_combo_reconciliation_row():
    """실물 AMD 2026-08-04 — '순이익 / EPS' 묶음 대조표.

    한 줄에 [순이익 $2,760] [EPS $1.66] 이 짝으로 반복되므로,
    소수점 없는 순이익 칸을 건너뛰고 EPS 칸을 집어야 합니다.
    """
    text = (
        "GAAP net income / earnings per share $2,297 $1.38 $1,383 $0.84 $872 $0.54\n"
        "Non-GAAP net income / earnings per share $2,760 $1.66 $2,265 $1.37 $781 $0.48"
    )
    result = sf.parse_press_release(text)
    assert result["adj_eps"] == 1.66, result["adj_eps"]
    assert result["gaap_eps"] == 1.38, result["gaap_eps"]


def test_amd_combo_small_net_income_is_not_eaten():
    """순이익이 크기 상한(1000) **아래**인 분기 — 소수점 규칙이 지켜 줍니다.

    $781 은 상한 아래라 크기 검사로는 못 거르지만, 소수점이 없어 EPS 칸이
    아닙니다. 이 규칙이 없으면 EPS 가 781.0 이 됩니다.
    """
    text = "Non-GAAP net income / earnings per share $781 $0.48"
    assert sf.find_eps_value(text, sf.LABELS_ADJUSTED_EPS) == 0.48


def test_real_zeta_has_no_adjusted_eps():
    """실물 ZETA 2026-08-04 — 조정 EPS 를 발표하지 않는 회사.

    조정 EBITDA 만 발표하므로 adj_eps 는 '없음'이 정답입니다.
    없는 숫자를 만들어 내면 안 됩니다 (창작 금지).
    """
    text = (
        "Achieved positive GAAP net income of $8 million, and GAAP earnings per "
        "share of $0.03. Generated $92 million of adjusted EBITDA and expanded "
        "adjusted EBITDA margin by 170 bps Y/Y to 20.7%."
    )
    result = sf.parse_press_release(text)
    assert result["adj_eps"] is None, result["adj_eps"]
    assert result["gaap_eps"] == 0.03, result["gaap_eps"]


def test_partnership_8k_is_not_kept_as_raw():
    """실물 LITE·COHR 2026-03-02 — 파트너십 발표는 실적이 아닙니다.

    실적으로 판별되지 않아야 원문 보관 상한(종목당 2건)을 차지하지 않습니다.
    """
    text = (
        "NVIDIA Announces Strategic Partnership with Lumentum to Develop "
        "State-of-the-Art Optics Technology. NVIDIA to invest $2B in Lumentum "
        "to grow capacity and deepen R&D collaboration in data center optics."
    )
    assert not sf._looks_like_earnings(text)


if __name__ == "__main__":
    tests = [
        (n, f) for n, f in sorted(globals().items())
        if n.startswith("test_") and callable(f)
    ]
    passed = failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  ✅ {name}")
            passed += 1
        except AssertionError as e:
            print(f"  ❌ {name} — {e}")
            failed += 1
        except Exception as e:
            print(f"  💥 {name} — {type(e).__name__}: {e}")
            failed += 1
    print(f"\nEPS 읽기·이익의 질 검증: {passed}개 통과, {failed}개 실패")
    sys.exit(1 if failed else 0)
