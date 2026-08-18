"""
run_tests.py — 모든 자동 검증을 한 번에 실행
============================================

인터넷 없이 가상 데이터로 계산 로직과 화면을 전부 점검합니다.

실행 방법 (프로젝트 폴더에서):
    python run_tests.py          (Windows)
    python3 run_tests.py         (Mac)
"""

import subprocess
import sys
from pathlib import Path

# v3 재건축 0단계 — 수집 계층만 남았습니다.
# 측정·판정·화면 테스트는 그 코드와 함께 철거했고 (v2-final 브랜치에 봉인),
# v3 4·5·6단계에서 새 코드와 함께 새로 씁니다.
TEST_FILES = [
    "tests/test_parsing.py",     # 보도자료 숫자 뽑기
    "tests/test_market.py",      # 주가·추세·상대강도
    "tests/test_quality.py",     # 숫자 검사 단계 (단위·범위·전망 배수)
    "tests/test_eps_parse.py",   # 보도자료 EPS 읽기 + 이익의 질
    "tests/test_measure.py",     # 수집 결과 꾸리기 (snapshot.json 만들기)
    "tests/test_collect_job.py", # 수집 로봇 (반쯤 깨진 날 방어·파일 기록)
    "tests/test_consensus.py",   # 야후 컨센서스 원장 (추가 전용·오염 차단)
    "tests/test_vendor_feed.py", # 두 번째 자 — 야후 분기표 (75차, 섞지 않는 대조용)
    "tests/test_vendor_compare.py",  # 두 자 대조 (75차 — 재기만 하고 고치지 않음)
    "tests/test_dataset.py",     # 데이터 계층 (v3 4단계 — 재료 손질)
    "tests/test_audit_data.py",  # 재료 오염 전수조사 (73차 — 지우지 않고 세기)
    "tests/test_measure_engine.py",  # 측정 장치 (v3 5단계 — 11차 등록 구현)
    "tests/test_sector_model.py",    # 정배열 장치 (39·43차 등록 — 완성·이격도)
    "tests/test_leadership.py",      # 주도섹터 모델 (44차 등록 — 판정·전환·분기점)
    "tests/test_judge.py",       # 자동 판정 (v3 5단계 — 채택 기준 적용)
    "tests/test_app.py",         # 계기판 도우미 (v3 6단계 — 정직화 표시)
]

if __name__ == "__main__":
    root = Path(__file__).parent
    total_failed = 0

    for test_file in TEST_FILES:
        print(f"\n{'=' * 60}\n▶ {test_file}\n{'=' * 60}")
        result = subprocess.run(
            [sys.executable, str(root / test_file)],
            capture_output=True,
            text=True,
        )
        # 경고 메시지는 걸러내고 결과만 보여줍니다
        for line in result.stdout.splitlines():
            if "ScriptRunContext" not in line and "WARNING" not in line:
                print(line)
        if result.returncode != 0:
            total_failed += 1
            print(result.stderr[-800:])

    print(f"\n{'=' * 60}")
    if total_failed == 0:
        print("🎉 모든 검증을 통과했습니다.")
    else:
        print(f"⚠️ {total_failed}개 파일에서 실패가 있습니다.")
    sys.exit(1 if total_failed else 0)
