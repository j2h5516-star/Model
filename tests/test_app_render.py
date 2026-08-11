"""
test_app_render.py — v2 계기판이 실제로 그려지는지 (뷰어 전용 앱)
================================================================

앱은 저장소의 실제 스냅샷(data/measure/snapshot.json — 로봇이 커밋)을
읽으므로, 테스트도 그 실물 파일로 돌립니다.

실행: python3 tests/test_app_render.py
"""

import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from streamlit.testing.v1 import AppTest  # noqa: E402

import config as cfg  # noqa: E402

APP_PATH = os.path.join(os.path.dirname(__file__), "..", "app.py")


def _run_app():
    import streamlit as st
    st.cache_data.clear()          # 앞 테스트가 캐시한 스냅샷이 새 조건을 가리지 않게
    at = AppTest.from_file(APP_PATH)
    at.run(timeout=120)
    return at


def _all_text(at) -> str:
    parts = [m.value for m in at.markdown]
    parts += [c.value for c in at.caption]
    parts += [i.value for i in at.info]
    return " ".join(parts)


def test_renders_with_real_snapshot():
    """실물 스냅샷으로 계기판 전체가 에러 없이 그려져야 함."""
    at = _run_app()
    assert not at.exception, [e.value for e in at.exception]
    text = _all_text(at)
    assert "장세 게이지" in text, "게이지 배너가 없음"
    assert "종목 상태표" in text, "상태표 제목이 없음"
    assert len(at.dataframe) >= 1, "상태표가 없음"
    assert len(at.selectbox) >= 1, "종목 선택이 없음"


def test_honesty_notes_are_visible():
    """측정 결과(가속=매수 아님)와 데이터 시각이 화면에 있어야 함."""
    at = _run_app()
    text = _all_text(at)
    assert str(cfg.MEASURED_ACCEL_SURGE_PCT) in text, "가속 측정치가 없음"
    assert "상태 설명" in text, "'상태 설명' 정직 문구가 없음"
    assert "데이터 수집 시각" in text, "데이터 나이 표시가 없음"


def test_no_score_no_save_button():
    """v2 계기판에는 점수와 저장 버튼이 없어야 함 (측정 결론의 이행)."""
    at = _run_app()
    text = _all_text(at)
    assert "종합 점수" not in text
    assert "신뢰도" not in text          # v1 신뢰도 등급 철거 확인
    button_labels = [b.label for b in at.button]
    assert not any("저장" in label for label in button_labels), button_labels


def test_verdict_panel_renders():
    """자동 판정 패널 — 기록이 없으면 안내가, 있으면 판정 결과가 보여야 함."""
    at = _run_app()
    text = _all_text(at)
    assert "판정 표본" in text, "자동 판정 패널이 그려지지 않음"
    verdict_path = os.path.join(cfg.MEASURE_DIR, "verdict.json")
    if not os.path.exists(verdict_path):
        assert "아직 판정 기록이 없습니다" in text, "기록 없음 안내가 없음"
    else:
        warnings = " ".join(w.value for w in at.warning)
        assert ("판정 시각" in text) or ("판정 실행이 실패" in warnings), \
            "판정 결과도 실패 경고도 없음"


def test_missing_snapshot_shows_robot_guide():
    """스냅샷이 없으면 (로봇 첫 실행 전) 안내가 나와야 함."""
    with patch.object(cfg, "MEASURE_DIR", "/tmp/claude-0/없는폴더"):
        at = _run_app()
    assert not at.exception, [e.value for e in at.exception]
    text = _all_text(at)
    assert "수집 로봇" in text, "로봇 안내가 없음"


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
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
    print(f"\n계기판 렌더링 검증: {passed}개 통과, {failed}개 실패")
    sys.exit(1 if failed else 0)
