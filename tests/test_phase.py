"""
test_phase.py — 백테스트 결과를 반영한 v5 변경분 검증
=====================================================

20종목(성장 12 + 부진 7) 132개 시점 백테스트에서 확인한 세 가지를 코드가
정말로 그렇게 하고 있는지 봅니다.

  ① 델타 기준이 **1년치(TTM)** 로 바뀌었는가
     — 분기 단위 55.0% → 1년치 66.2% (방향 적중)
  ② **국면** 신호가 사이클 위치를 제대로 잡는가
     — 델타가 4분기 늦게 알아챈 슈퍼사이클을 시작 분기에 잡았는가
  ③ 이상 감지가 '크기'가 아니라 '증가 속도' 기준으로 바뀌었는가
     — 잘 크는 회사를 매 분기 '데이터 오류'로 지우지 않는가

실행: python3 tests/test_phase.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import config as cfg  # noqa: E402
import data_quality as dq  # noqa: E402
import pipeline  # noqa: E402
import scoring  # noqa: E402

M = 1_000_000.0


def make(values, revenue_multiple=5):
    """분기 목록 만들기 — 값은 백만 달러 단위로 줍니다."""
    return [
        {
            "period_label": f"{23 + i // 4} Q{i % 4 + 1}",
            "fiscal_quarter": i % 4 + 1,
            "year": 2023 + i // 4,
            "period_end": f"{2023 + i // 4}-{(i % 4) * 3 + 3:02d}-28",
            "op_income": v * M,
            "revenue": abs(v) * M * revenue_multiple + 100 * M,
            "source": cfg.SRC_DIRECT,
        }
        for i, v in enumerate(values)
    ]


# ---------------------------------------------------------------------------
# ① 델타 기준이 1년치(TTM)로 바뀌었는가
# ---------------------------------------------------------------------------
def test_ttm_is_used_once_there_are_enough_quarters():
    """분기가 6개 이상이면 1년치 합을 기준으로 판정해야 함"""
    result = scoring.score_delta_acceleration(make([100, 110, 120, 130, 145, 165, 190]))
    trace = result["trace"]

    assert trace["use_ttm"] is True, trace["basis"]
    assert "TTM" in trace["basis"], trace["basis"]


def test_falls_back_to_quarterly_when_history_is_short():
    """분기가 모자라면 옛 방식(분기 단위)으로 돌아가되, 그렇다고 밝혀야 함"""
    result = scoring.score_delta_acceleration(make([100, 110, 125]))
    trace = result["trace"]

    assert trace["use_ttm"] is False
    assert "TTM 대신" in trace["basis"], trace["basis"]


def test_ttm_absorbs_seasonality_without_switching_basis():
    """계절 장사도 1년치로 보면 따로 YoY 로 바꿀 필요가 없어야 함

    분기로는 100 → 130 → 90 → 160 처럼 요동치지만
    1년치로는 480 → 490 → 503 처럼 꾸준히 크는 회사입니다.
    """
    quarters = make([100, 130, 90, 160, 110, 143, 99, 176])
    result = scoring.score_delta_acceleration(quarters)

    assert result["trace"]["use_ttm"] is True
    assert result["direction"] == cfg.D_STEADY, result
    # 분기 단위로 봤다면 매년 '대폭 가속'과 '대폭 감속'을 번갈아 외쳤을 것입니다
    assert result["direction"] not in (cfg.D_ACCEL, cfg.D_DECEL)


def test_measured_hit_rate_is_carried_to_the_screen():
    """'감속'은 검증에서 40%밖에 못 맞혔음 — 그 사실을 화면까지 전달해야 함"""
    falling = scoring.score_delta_acceleration(
        make([300, 290, 270, 240, 200, 150, 90])
    )
    assert falling["direction"] in (cfg.D_DECEL, cfg.D_WEAK_DECEL), falling
    assert falling["trace"]["measured_hit"] == cfg.DELTA_MEASURED_HIT[cfg.D_DECEL]

    import explain

    lines = explain.explain_delta(
        {"fundamental": {"delta": falling}, "quarters": make([300, 290, 270]),
         "forecast_detail": {}}
    )["calc_lines"]
    assert any("믿을 것이 못 됩니다" in line for line in lines), lines


# ---------------------------------------------------------------------------
# ② 국면 신호
# ---------------------------------------------------------------------------
def test_turnaround_is_detected_when_ttm_crosses_into_profit():
    """1년치 이익이 적자에서 흑자로 바뀌면 '턴어라운드 진입'"""
    quarters = make([-200, -100, -50, -20, 40, 120, 90])
    # 1년치 합: -370 → -130 → +90 → +230.  흑자로 넘어선 것은 세 번째 시점입니다.
    result = scoring.score_phase(quarters[:6])

    assert result["trace"]["previous"] < 0 < result["trace"]["now"], result["trace"]
    assert result["phase"] == cfg.PH_TURNAROUND, result
    assert result["score"] == cfg.W_PHASE * cfg.PHASE_RATIO[cfg.PH_TURNAROUND]

    # 흑자가 이어져 최고 기록을 갈아치우면 그때는 '3년내 최고'로 넘어갑니다
    assert scoring.score_phase(quarters)["phase"] == cfg.PH_NEW_HIGH


def test_new_high_is_detected():
    """1년치 이익이 수집 기간 안의 최고를 넘으면 '3년내 최고'"""
    result = scoring.score_phase(make([100, 110, 120, 130, 150, 170, 200]))

    assert result["phase"] == cfg.PH_NEW_HIGH, result
    assert result["score"] == cfg.W_PHASE   # 만점


def test_rollover_is_detected():
    """1년치 이익이 과거 최고의 80% 아래로 내려오면 '고점 이탈'"""
    result = scoring.score_phase(make([200, 210, 220, 230, 120, 80, 50]))

    assert result["phase"] == cfg.PH_ROLLOVER, result
    assert result["score"] < cfg.W_PHASE * 0.3


def test_persistent_losses_are_not_called_a_middle_position():
    """계속 적자인 회사를 '중간 자리'라고 하면 안 됨

    고점과 바닥 사이에 서 있는 멀쩡한 회사와 같은 표시를 받게 됩니다.
    """
    result = scoring.score_phase(make([-100, -110, -105, -120, -115, -108, -112]))

    assert result["phase"] == cfg.PH_LOSS, result
    assert result["score"] <= cfg.W_PHASE * 0.15, result


def test_new_high_is_labelled_as_three_year_not_all_time():
    """'역대 최고'가 아니라 '3년내 최고'라고 밝혀야 함

    3년치만 모으므로, 예전에 훨씬 잘 벌다 무너진 회사가 조금 회복하면
    여기에 들어옵니다(인텔). 이름과 설명에 그 한계를 박아 둡니다.
    """
    result = scoring.score_phase(make([100, 110, 120, 130, 150, 170, 200]))

    assert "3년" in result["phase"], result["phase"]
    assert "역대" not in result["phase"]
    assert "3년" in result["detail"], result["detail"]


def test_middle_ground_is_not_dressed_up():
    """고점도 바닥도 아니면 '중간 자리' — 좋은 척하지 않아야 함"""
    # 최고를 찍고 살짝 물러난 상태 (80~100% 사이)
    result = scoring.score_phase(make([100, 110, 120, 130, 125, 115, 110]))

    assert result["phase"] == cfg.PH_NONE, result
    assert result["score"] < cfg.W_PHASE * 0.5


def test_phase_is_withheld_when_history_is_short():
    """분기가 모자라면 아는 척하지 않아야 함"""
    result = scoring.score_phase(make([100, 110, 120]))

    assert result["phase"] == cfg.PH_UNKNOWN, result


def test_phase_catches_the_supercycle_the_delta_missed():
    """실제 사례 — 마이크론이 폭증을 시작한 분기를 국면 신호가 잡는가

    델타는 이 계열에서 FY25 Q4 가 되어서야 '가속'이라고 말했습니다.
    폭증이 시작된 것은 FY25 Q1 로, **4분기 늦은 판정**이었습니다.
    """
    micron = [-2068, -1463, -1203, -955, 204, 941, -1744, 2395, 2007, 2493, 3961]
    surge_starts = 7            # FY25 Q1 (목록에서 8번째)

    quarters = make(micron)
    phase_at_surge = scoring.score_phase(quarters[: surge_starts + 1])["phase"]
    delta_at_surge = scoring.score_delta_acceleration(
        quarters[: surge_starts + 1]
    )["direction"]

    assert phase_at_surge == cfg.PH_TURNAROUND, phase_at_surge
    # 델타는 같은 시점에 아직 가속이라고 말하지 못합니다 — 그래서 국면이 필요합니다
    assert delta_at_surge != cfg.D_ACCEL, delta_at_surge


def test_phase_weight_keeps_the_total_at_100():
    """배점을 더해 100점이 유지돼야 함 (한 항목을 늘렸으면 다른 곳을 줄여야 함)"""
    total = (
        cfg.W_DELTA_ACCEL + cfg.W_PHASE + cfg.W_GM_DRIVER
        + cfg.W_REVENUE_QUALITY + cfg.W_FORWARD
    )
    assert total == 100, total


def test_phase_is_in_the_ranking_table_and_the_score():
    """국면이 점수와 순위 표에 실제로 실려야 함"""
    quarters = make([100, 110, 120, 130, 150, 170, 200])
    score = scoring.build_score(
        "TEST", quarters, {"forward_op_income": None},
        {"stage": 1, "state": cfg.S_FULL_UP, "rs": 5.0, "close": 10.0},
    )

    assert score["phase"] == cfg.PH_NEW_HIGH
    assert score["fundamental"]["phase"]["score"] > 0

    table = pipeline.build_ranking_table({"TEST": score})
    assert "국면" in table.columns
    assert "최고" in table.iloc[0]["국면"]


# ---------------------------------------------------------------------------
# ③ 델타예측 — 검증된 것만 점수에 반영하는가
# ---------------------------------------------------------------------------
def _forward_case(values, forward_musd):
    quarters = make(values)
    forward = {"forward_op_income": forward_musd * M, "basis": cfg.SRC_GUIDANCE}
    return quarters, forward


def test_forecast_compares_like_with_like():
    """전망도 실적과 **같은 잣대**로 재야 함 (1년치 vs 분기를 섞으면 안 됨)

    델타를 1년치(TTM)로 판정해 놓고 전망만 분기 증가율로 재면, 꾸준히 크는
    회사는 1년치 증가율이 분기 증가율보다 항상 커서 **거의 모두 '가속 둔화'**
    로 찍힙니다. 실제로 전체 판정의 28%가 그렇게 나왔습니다.
    """
    quarters, forward = _forward_case([100, 110, 120, 130, 145, 165, 190], 215)
    delta = scoring.score_delta_acceleration(quarters)
    forecast = scoring.predict_delta(quarters, forward, delta)

    assert delta["trace"]["use_ttm"] is True
    assert forecast["growth_unit"] == "1년치", forecast

    # 1년치끼리 비교했으므로 last 와 next 가 같은 크기 대역에 있어야 합니다
    assert abs(forecast["next_qoq"] - forecast["last_qoq"]) < 30, forecast


def test_slowdown_signal_is_measured_against_the_right_thing():
    """'가속 둔화'는 **이익** 정점이 아니라 **주가** 정점을 짚는 신호

    여기서 채점 기준을 한 번 크게 틀렸습니다. 처음에는 '이익이 정점인가'로
    쟀고 결과는 실패였습니다(16.7% vs 기준선 21.1%). 그런데 이익 레벨은
    사이클 후반까지 계속 신고점을 찍으므로 그 잣대 자체가 틀린 것이었습니다.
    주가는 그보다 먼저 꺾입니다.

    주가 정점으로 다시 재니 51.4% [35.9~66.6] vs 기준선 31.1% 로 유의했습니다.
    (표본 7개 → 11개로 늘려도 유지)
    2021~2022 사이클 실제 사례 — 주가가 꺾인 그 분기의 판정:
        마이크론 2022-01 · 엔비디아 2021-11 · ZIM 2022-03 · 엔페이즈 2022-12
        솔라엣지 2021-11 · 줌 2020-10 · 엣시 2021-11
        일곱 종목 모두 '가속 둔화' + '3년내 최고' 였습니다.
    """
    # 이익 정점으로는 기준선을 못 넘습니다 — 그 사실을 그대로 남겨 둡니다
    assert cfg.FORECAST_MEASURED_PEAK[cfg.F_ACCEL_SLOW] < cfg.FORECAST_PEAK_BASELINE

    # 주가 정점으로는 기준선을 넘습니다
    assert (cfg.FORECAST_MEASURED_PRICE_PEAK[cfg.F_ACCEL_SLOW]
            > cfg.FORECAST_PRICE_PEAK_BASELINE)

    # 그래서 점수를 깎습니다 (근거가 생겼으므로)
    assert cfg.FORECAST_SCORE_MULT[cfg.F_ACCEL_SLOW] < 1.0

    # 다만 '감속 지속'보다는 약하게 — 표본이 작기 때문입니다
    assert (cfg.FORECAST_SCORE_MULT[cfg.F_ACCEL_SLOW]
            > cfg.FORECAST_SCORE_MULT[cfg.F_DECEL_KEEP])


def test_both_peak_measurements_are_shown_to_the_user():
    """화면에 **이익 정점**과 **주가 정점** 두 수치를 모두 보여줘야 함

    하나만 보여주면 오해합니다. "이익 정점은 못 잡지만 주가 정점은 잡는다"가
    이 신호의 정확한 성격입니다.
    """
    import explain

    quarters = make([100, 110, 120, 130, 145, 165, 190])
    delta = scoring.score_delta_acceleration(quarters)
    forecast = scoring.predict_delta(
        quarters, {"forward_op_income": 185 * M, "basis": cfg.SRC_GUIDANCE}, delta)
    assert forecast["label"] == cfg.F_ACCEL_SLOW, forecast["label"]

    lines = explain.explain_delta({
        "fundamental": {"delta": delta},
        "quarters": quarters,
        "forecast_detail": forecast,
    })["forecast_lines"]
    text = " ".join(lines)

    assert "이익" in text and "주가" in text, lines
    assert "51.4%" in text or "51." in text, lines


def test_validated_forecasts_do_change_the_score():
    """검증된 두 신호는 점수에 반영돼야 함

    감속 지속 ↘↘ : 정점 66.7% · 2분기 뒤 이익 감소 83.3%  → 깎음
    가속 지속 ↗↗ : 정점  0.0%                              → 올림
    """
    assert cfg.FORECAST_SCORE_MULT[cfg.F_DECEL_KEEP] < 1.0
    assert cfg.FORECAST_SCORE_MULT[cfg.F_ACCEL_KEEP] > 1.0

    rising = [100, 110, 120, 130, 145, 165, 190]
    quarters = make(rising)
    plain = scoring.score_delta_acceleration(quarters)

    # 전망이 좋을 때와 나쁠 때 델타 점수가 실제로 달라져야 합니다
    good = scoring.score_fundamental(
        quarters, {"forward_op_income": 260 * M, "basis": cfg.SRC_GUIDANCE})
    bad = scoring.score_fundamental(
        quarters, {"forward_op_income": 120 * M, "basis": cfg.SRC_GUIDANCE})

    assert good["forecast"]["label"] != bad["forecast"]["label"], (
        good["forecast"]["label"], bad["forecast"]["label"])
    assert good["delta"]["score"] >= plain["score"] * 0.999
    assert bad["delta"]["score"] <= good["delta"]["score"]


def test_delta_score_never_exceeds_its_weight():
    """배수를 곱해도 배점을 넘으면 안 됨 (합계 100점이 깨집니다)"""
    quarters = make([100, 110, 125, 145, 170, 205, 250])
    result = scoring.score_fundamental(
        quarters, {"forward_op_income": 340 * M, "basis": cfg.SRC_GUIDANCE})

    assert result["delta"]["score"] <= cfg.W_DELTA_ACCEL
    assert result["total"] <= 100.0


def test_build_score_reuses_one_forecast():
    """예측을 두 번 계산하면 배수가 곱해진 델타를 근거로 삼아 값이 어긋남"""
    quarters = make([100, 110, 120, 130, 145, 165, 190])
    score = scoring.build_score(
        "TEST", quarters,
        {"forward_op_income": 120 * M, "basis": cfg.SRC_GUIDANCE},
        {"stage": 1, "state": cfg.S_FULL_UP, "rs": 5.0, "close": 10.0},
    )
    assert score["delta_forecast"] == score["fundamental"]["forecast"]["label"]


def test_the_disproven_claim_is_gone_from_the_docs():
    """틀린 것으로 밝혀진 '정점 근처 신호' 표현이 문서에 남아 있으면 안 됨"""
    import os
    root = os.path.join(os.path.dirname(__file__), "..")
    readme = open(os.path.join(root, "README.md"), encoding="utf-8").read()

    # 표에 단정적으로 남아 있으면 안 됩니다 (정정 문단에서 인용하는 것은 허용)
    assert "| `가속 둔화 ↗↘` | 정점 근처 가능성 |" not in readme
    assert "정정했습니다" in readme or "정정합니다" in readme, (
        "무엇이 틀렸는지 밝히는 문단이 있어야 합니다")

    # 2차 정정: 이익 정점이 아니라 주가 정점으로 재야 한다는 사실이 적혀야 합니다
    assert "주가 정점" in readme, "채점 기준을 바로잡은 근거가 있어야 합니다"


def test_eps_substitution_finding_is_recorded():
    """조정 EPS 로 바꿔도 방향이 96% 일치한다는 측정 결과가 남아 있어야 함

    같은 종목·같은 분기를 두 기준으로 판정해 비교한 결과입니다
    (MU·NVDA·AMD·ENPH·ZIM 2020~2023, 50개 시점).
    이 수치가 있어야 "EPS 로 전망을 만들어도 된다"는 판단에 근거가 생깁니다.
    """
    assert cfg.EPS_VS_OI_DIRECTION_MATCH >= 0.9, cfg.EPS_VS_OI_DIRECTION_MATCH


# ---------------------------------------------------------------------------
# ④ 이상 감지 — '크기'가 아니라 '증가 속도' 기준
# ---------------------------------------------------------------------------
def test_steady_hypergrowth_is_not_flagged_as_an_error():
    """매 분기 40%씩 크는 회사를 '데이터 오류'로 지우면 안 됨

    이것이 실제로 일어났던 사고입니다. 크기를 이웃과 견주면 3분기 전 대비
    2배가 되므로, 잘 크는 회사의 **모든 분기**가 오류로 찍혔습니다
    (CRDO 6개 분기 연속).
    """
    found = dq.detect_anomalies(make([100, 140, 196, 274, 384, 538, 753]))

    assert found["spikes"] == [], found
    assert found["steps"] == [], found
    assert found["pending"] == [], found


def test_one_off_spike_is_still_caught():
    """한 분기만 튀었다 되돌아오는 것은 여전히 데이터 오류로 잡아야 함"""
    found = dq.detect_anomalies(make([100, 105, 110, 400, 112, 118, 124]))

    assert 3 in found["spikes"], found


def test_acquisition_step_is_still_caught():
    """올라가서 그 수준을 지키면 계단(인수합병) — 분기는 살리고 증가율만 뺌"""
    found = dq.detect_anomalies(make([100, 105, 110, 330, 340, 350, 360]))

    assert 3 in found["steps"], found
    assert 3 not in found["spikes"], found


def test_a_real_collapse_is_left_alone():
    """이익이 무너져 그대로 눌러앉는 것은 **지우면 안 되는 신호**

    이것까지 '구조 변화'라며 계산에서 빼면, 모델은 나빠지는 회사를 보고도
    아무 말을 하지 않게 됩니다.
    """
    found = dq.detect_anomalies(make([300, 310, 305, 90, 85, 88, 80]))

    assert found["spikes"] == [], found
    assert found["steps"] == [], found


def test_seasonal_weak_quarter_is_not_deleted():
    """계절 장사의 비수기를 매년 '데이터 오류'로 지우면 안 됨

    ZETA 는 매년 1분기에 이익이 -40% 로 꺼졌다가 2분기에 복구됩니다.
    전체 평균과 견주면 이 정상적인 계절 흐름이 매년 오류로 찍혀
    1분기가 통째로 사라집니다.
    """
    zeta_like = make([50, 70, 95, 125, 30, 88, 119, 156, 38])
    found = dq.detect_anomalies(zeta_like)

    assert dq.detect_seasonality(zeta_like)["seasonal"] is True
    assert found["spikes"] == [], found["reasons"]


def test_a_single_step_does_not_masquerade_as_seasonality():
    """한 번뿐인 인수합병이 '계절성'으로 둔갑하면 안 됨

    두 번 이상 관찰되지 않은 분기를 계절성 판정에 넣으면, 인수합병으로
    한 분기만 뛴 회사가 계절 장사로 찍힙니다. 그러면 이상 감지가
    "계절 흐름이겠지" 하며 인수합병을 그냥 지나칩니다.
    """
    stepped = make([50, 55, 60, 130, 140, 150, 160])

    assert dq.detect_seasonality(stepped)["seasonal"] is False
    assert 3 in dq.detect_anomalies(stepped)["steps"]

# ---------------------------------------------------------------------------
# 재검증에서 나온 '치명' 4건 — 점수가 실제로 틀리게 나오던 것들
# ---------------------------------------------------------------------------
def _series(values, basis=None):
    """백만 달러 단위 분기 목록"""
    return [
        {"period_label": f"FY{2023 + i // 4} Q{(i % 4) + 1}",
         "fiscal_quarter": (i % 4) + 1,
         "period_end": f"20{23 + i // 4}-{(i % 4) * 3 + 3:02d}-28",
         "op_income": v * M, "revenue": abs(v) * M * 5 + 1e8,
         "source": cfg.SRC_DIRECT, "basis": basis or cfg.BASIS_OP_INCOME}
        for i, v in enumerate(values)
    ]


def test_low_base_cap_is_not_undone_by_the_forecast_multiplier():
    """상한을 걸어 놓고 배수로 되돌리면 안 됩니다.

    상한은 score_delta_acceleration 안에서, 배수는 그 바깥에서 나중에 곱해집니다.
    곱한 뒤의 min() 이 만점(30)만 막아서, 상한 15.0 에 배수 1.10 을 곱해 16.5 가
    되면서 화면에는 그대로 "50%로 제한했습니다" 라고 떴습니다. 실제로는 55% 입니다.
    """
    quarters = dq.validate_quarters(_series([10, 13, 16, 19, 23, 28]), {})
    result = scoring.score_fundamental(
        quarters,
        {"forward_op_income": 40 * M, "forward_op_income_2": 60 * M,
         "basis": cfg.SRC_GUIDANCE, "consensus": {}},
    )
    delta = result["delta"]
    assert delta["trace"]["capped"] is True, "저기저 상한이 걸리지 않았습니다"
    limit = cfg.W_DELTA_ACCEL * cfg.LOW_BASE_CAP_RATIO
    assert delta["score"] <= limit, f"상한 {limit} 을 넘었습니다: {delta['score']}"


def test_ttm_growth_does_not_bridge_a_gap():
    """계산할 수 없는 자리를 건너뛰고 이어붙이면 없는 가속이 생깁니다.

    10분기 동안 1년치 이익이 400 → 400 으로 한 푼도 안 늘어난 회사가
    '가속' 판정을 받았습니다(2년 떨어진 두 증가율이 이웃이 되어서).
    """
    quarters = _series([100, 100, 100, 100, -1000, 100, 100, 100, 100, 100])
    result = scoring.score_delta_acceleration(quarters)
    assert "가속" not in result["direction"], (
        f"제자리 걸음인데 {result['direction']} 로 판정했습니다")


def test_ttm_growth_last_value_is_actually_the_latest():
    """구멍이 뒤쪽에 있으면 두 분기 전 증가율이 '최근'으로 쓰이면 안 됩니다"""
    quarters = _series([100, 100, 100, 100, 110, 120, -600, 200, 300])
    result = scoring.score_delta_acceleration(quarters)
    # 최근 1년치가 -170 → +20 으로 회복 중인데 '감속'이라 하면 안 됩니다
    assert "감속" not in result["direction"], result["direction"]


def test_forecast_is_converted_with_the_same_ruler():
    """전망 증가율을 과거와 **같은 자**로 환산해야 합니다.

    예전에는 use_ttm(참/거짓)만 넘겨서, TTM 이 아니면 무조건 '전분기 대비'로
    환산했습니다. 그런데 계절 종목은 '작년 같은 분기 대비'라 자가 어긋났습니다.
    """
    quarters = _series([100, 50, 60, 200, 120])
    forward = {"forward_op_income": 70 * M, "basis": cfg.SRC_GUIDANCE}

    expected = {"QoQ": "분기", "YoY": "작년 같은 분기", "TTM": "1년치"}
    for mode, unit in expected.items():
        fake = {"trace": {"use_ttm": mode == "TTM", "growth_mode": mode,
                          "growths": [10.0, 20.0]}, "direction": cfg.D_ACCEL}
        got = scoring.predict_delta(quarters, forward, fake)
        assert got["growth_unit"] == unit, (mode, got["growth_unit"])

    # 계절 경로는 작년 같은 분기(50)와 견줘야 합니다 — 전분기(120)가 아니라
    fake_yoy = {"trace": {"use_ttm": False, "growth_mode": "YoY",
                          "growths": [10.0, 20.0]}, "direction": cfg.D_ACCEL}
    assert abs(scoring.predict_delta(quarters, forward, fake_yoy)["next_qoq"] - 40.0) < 0.1


def test_pending_quarter_keeps_the_ruler():
    """판정을 미룬 경로에서도 어느 자로 쟀는지 남겨야 합니다"""
    quarters = _series([100, 110, 125, 140, 160, 180, 400])
    quarters[-1]["anomaly"] = "pending"
    trace = scoring.score_delta_acceleration(quarters)["trace"]
    assert "growth_mode" in trace, "보류 경로에 자 표시가 없습니다"
    assert "use_ttm" in trace


def test_unknown_never_scores_higher_than_bad():
    """'모르는 것'이 '나쁜 것'보다 유리하면 안 됩니다.

    자료가 하나도 없는 종목이 42.6점을 받아 FUND_WEAK(40.0)를 넘고 자동으로
    '관심' 판정에 들어갔습니다. 고점이탈 + 감속 + 마진악화인 진짜 종목
    (약 28.6점)보다 14점 높았습니다.
    """
    nothing = scoring.score_fundamental(
        [], {"forward_op_income": None, "basis": None, "consensus": {}})
    assert nothing["total"] < cfg.FUND_WEAK, (
        f"자료 없는 종목이 {nothing['total']:.1f}점으로 보통 기준선을 넘습니다")
    assert nothing["total"] < 28.0, (
        f"자료 없는 종목({nothing['total']:.1f})이 진짜 나쁜 종목(약 28.6)보다 높습니다")


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
    print(f"\n국면·TTM 델타 검증: {passed}개 통과, {failed}개 실패")
    sys.exit(1 if failed else 0)
