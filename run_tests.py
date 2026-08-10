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
    "tests/test_scoring.py",     # 점수 계산과 판정
    "tests/test_market.py",      # 주가·추세·상대강도
    "tests/test_explain.py",     # "어떻게 계산됐는지" 설명 생성
    "tests/test_v4.py",          # 컨센서스·델타가속예측·증분캐시·저장
    "tests/test_quality.py",     # 숫자 검사 단계 (단위·범위·전망 배수)
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
