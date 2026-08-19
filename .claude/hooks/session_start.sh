#!/bin/bash
# 세션 시작 훅 (110차) — 컨테이너 초기화 복구 + 운영 규칙 주입
# =============================================================
#
# 왜 필요한가: 이 세션의 컨테이너는 수시로 초기화되고, 그때마다 저장소가
# 옛 커밋(클론 시점)으로 되돌아가 있습니다. 2026-08-18~19 하루에만 여섯
# 번, 매번 사람이(모델이) 손으로 fetch·checkout 을 반복했습니다.
#
# 무엇을 하나:
#   ① 상태 보고 — HEAD·origin/main·미커밋 개수·로봇 마지막 수집 시각
#   ② 안전한 경우에만 자동 복구 — "미커밋 0개 AND 로컬에만 있는 커밋 0개"
#      일 때만 origin/main 으로 맞춥니다. 이 두 조건이 참이면 잃을 것이
#      아무것도 없음이 증명되므로 복구가 무손실입니다.
#      하나라도 거짓이면 절대 건드리지 않고 보고만 합니다.
#   ③ 세션들이 실측으로 배운 운영 규칙 세 개를 매 세션에 주입
#
# 출력은 새 세션의 문맥에 들어갑니다. 10초 안에 끝나야 하므로 fetch 에
# 시간 제한을 겁니다. 실패해도 세션을 막지 않습니다(항상 종료코드 0).

cd "${MODEL_DIR:-/home/user/Model}" 2>/dev/null || exit 0

timeout 15 git fetch -q origin main 2>/dev/null

LOCAL=$(git rev-parse --short HEAD 2>/dev/null)
REMOTE=$(git rev-parse --short origin/main 2>/dev/null)
BEHIND=$(git rev-list --count HEAD..origin/main 2>/dev/null || echo "?")
AHEAD=$(git rev-list --count origin/main..HEAD 2>/dev/null || echo "?")
DIRTY=$(git status --porcelain 2>/dev/null | wc -l | tr -d ' ')
RAN=$(python3 -c "import json;print(json.load(open('data/measure/robot_log.json'))['ran_at'][:16])" 2>/dev/null || echo "확인불가")

echo "[세션 시작 점검] HEAD ${LOCAL:-?} · origin/main ${REMOTE:-?} · 뒤처짐 ${BEHIND}개 · 앞섬 ${AHEAD}개 · 미커밋 ${DIRTY}개 · 로봇 마지막 수집 ${RAN}Z"

if [ "${BEHIND}" != "?" ] && [ "${BEHIND}" -gt 0 ] && [ "${AHEAD}" = "0" ] && [ "${DIRTY}" = "0" ]; then
  # 잃을 것이 없음이 증명된 경우에만 — 무손실 복구
  git checkout -q -B main origin/main 2>/dev/null \
    && echo "→ 컨테이너 초기화 감지 · origin/main 으로 자동 복구했습니다 ($(git rev-parse --short HEAD))" \
    || echo "→ 자동 복구 실패 — 수동으로: git fetch origin main && git checkout -B main origin/main"
elif [ "${BEHIND}" != "?" ] && [ "${BEHIND}" -gt 0 ]; then
  echo "⚠️ origin/main 보다 ${BEHIND}커밋 뒤인데 로컬 변경(미커밋 ${DIRTY} · 로컬 커밋 ${AHEAD})이 있어 건드리지 않았습니다. 병합 여부를 직접 판단하세요."
fi

echo "운영 규칙(실측으로 배움): ① 실행 결과를 해석하기 전에 그 실행의 head_sha 부터 확인(93차) ② 수집 런은 마지막 성공 뒤 6시간 안에 다시 띄우지 않기 — SEC 429 로 둘 다 잃음(99차) ③ 짐작하기 전에 robot_log.json 의 계기(xbrl_calls·xbrl_orphan·건강검진)부터 읽기(91·98·106차) ④ 새 시험은 일부러 깨뜨려 빨간 불을 본 뒤에만 믿기(하루에 가짜 초록불 5건)"
exit 0
