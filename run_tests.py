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

TEST_FILES = [
    "tests/test_parsing.py",     # 보도자료 숫자 뽑기
    "tests/test_status.py",      # 상태 판정 핵심 (TTM·델타 방향·신고점)
    "tests/test_market.py",      # 주가·추세·상대강도
    "tests/test_quality.py",     # 숫자 검사 단계 (단위·범위·전망 배수)
    "tests/test_eps_parse.py",   # 보도자료 EPS 읽기 + 이익의 질
    "tests/test_measure.py",     # 측정용 실데이터 저장 (연료 파이프라인)
    "tests/test_measurement.py", # 2단계 측정 장치 (창·검열·미래 보기 차단)
    "tests/test_collect_job.py", # 수집 로봇 (반쯤 깨진 날 방어·파일 기록)
    "tests/test_verdict.py",     # 자동 판정 (채택 규칙·표본 분리)
    "tests/test_app_render.py",  # 화면이 실제로 그려지는지
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
