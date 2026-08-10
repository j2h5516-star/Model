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
