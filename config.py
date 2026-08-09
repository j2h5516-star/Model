"""
config.py — 대시보드 전체 설정값 모음
====================================

종목 목록, 점수 배점, 판정 기준선 등 "바꿀 만한 숫자"는 전부 여기에 모아 두었습니다.
다른 파일을 건드리지 않고 이 파일만 고치면 동작을 조절할 수 있습니다.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# 분석 대상
# ---------------------------------------------------------------------------

# 분석할 종목 (티커 = 미국 주식 종목 코드)
TICKERS: list[str] = [
    "CRDO",  # Credo Technology — 고속 인터커넥트
    "CLS",   # Celestica — 전자제품 위탁생산
    "MU",    # Micron Technology — 메모리 반도체
    "SNDK",  # SanDisk — 낸드 플래시
    "LITE",  # Lumentum — 광통신 부품
    "STRL",  # Sterling Infrastructure — 인프라 건설
    "MXL",   # MaxLinear — 통신 반도체
    "COHR",  # Coherent — 광학·레이저
    "TTMI",  # TTM Technologies — 인쇄회로기판
    "ICHR",  # Ichor Holdings — 반도체 장비 부품
    "AMD",   # Advanced Micro Devices — CPU/GPU
    "PLTR",  # Palantir — 데이터 분석 소프트웨어
    "ZETA",  # Zeta Global — 마케팅 소프트웨어
]

# 상대강도(RS) 비교 기준이 되는 지수 ETF
BENCHMARK = "SPY"  # S&P 500 지수를 따라가는 ETF

# 실적 수집 시작일 — "2025년 초 실적발표부터"
EARNINGS_START_DATE = "2025-01-01"

# SEC 전자공시(EDGAR)는 요청자 신원(이메일)을 헤더에 요구합니다.
# 실제 배포 시 본인 이메일로 바꿔도 되고, 그대로 두어도 동작합니다.
SEC_IDENTITY = "Trend Dashboard j2h5516@gmail.com"

# 내려받은 SEC 파일을 저장해 둘 폴더 (재실행 시 다시 받지 않음)
CACHE_DIR = "data"

# 캐시 유효 기간 (일). 이 기간이 지나면 새로 받아옵니다.
CACHE_TTL_DAYS = 1


# ---------------------------------------------------------------------------
# 주가 추세 설정 (v1에서 이어받은 값)
# ---------------------------------------------------------------------------

MA_WINDOWS = [4, 13, 26, 52]      # 주봉 이동평균선 기간 (주)
NEUTRAL_BAND_PCT = 3.0            # 26주선 ±3% 이내 = 중립
SLOPE_LOOKBACK_WEEKS = 4          # 26주선 기울기: 4주 전과 비교
SLOPE_FLAT_BAND_PCT = 0.3         # ±0.3% 이내 변화는 "횡보"
RS_LOOKBACK_WEEKS = 13            # 상대강도: 최근 13주 수익률 비교

# 추세 5단계 이름
S_FULL_UP = "완전 정배열"
S_SEMI_UP = "준정배열"
S_NEUTRAL = "중립"
S_DAMAGED = "추세 훼손"
S_FULL_DOWN = "완전 역배열"
S_UNKNOWN = "판정 불가"

# 추세 단계 번호 (1이 가장 강함 ~ 5가 가장 약함). 매트릭스 판정에 사용
TREND_STAGE = {
    S_FULL_UP: 1,
    S_SEMI_UP: 2,
    S_NEUTRAL: 3,
    S_DAMAGED: 4,
    S_FULL_DOWN: 5,
    S_UNKNOWN: 6,   # 판정 불가는 별도 취급
}


# ---------------------------------------------------------------------------
# 펀더멘털 점수 배점 (합계 100점)
# ---------------------------------------------------------------------------

W_DELTA_ACCEL = 40      # 델타 가속 — 이익 증가 속도가 빨라지는가
W_GM_DRIVER = 25        # GM% 드라이버 — 마진 개선이 어떤 성격인가
W_REVENUE_QUALITY = 20  # 매출 성장의 질
W_FORWARD = 15          # 포워드 신호 — 다음 분기 전망

# "저기저(저가 기저) 함정" 방지:
# 최근 분기 논갭 영업이익이 이 금액 미만이면 델타 가속 점수를 절반까지만 인정
LOW_BASE_THRESHOLD_USD = 50_000_000   # $50M
LOW_BASE_CAP_RATIO = 0.5              # 상한 50%

# GM% 드라이버 구간별 점수 (최근 분기 GM% − 직전 4개 분기 평균 GM%, 단위 %p)
GM_VOLUME_BAND = 1.0    # ±1%p 이내 = 물량형 (가장 건강)
GM_MIX_BAND = 3.0       # +1 ~ +3%p = 믹스형
GM_SCORE_VOLUME = 25    # 물량형 만점
GM_SCORE_MIX = 15       # 믹스형
GM_SCORE_PRICE = 5      # +3%p 초과 = 가격형 (지속성 낮음)
GM_SCORE_DECLINE = 8    # −1%p 넘는 하락


# ---------------------------------------------------------------------------
# 기술 점수 배점 (합계 100점)
# ---------------------------------------------------------------------------

W_TREND = 50        # 추세 5단계
W_SLOPE = 10        # 26주선 기울기
W_DISPARITY = 15    # 이격도
W_RS = 25           # 상대강도

# 추세 단계별 점수 (50점 만점을 5단계로 균등 배분)
TREND_SCORES = {
    S_FULL_UP: 50.0,
    S_SEMI_UP: 37.5,
    S_NEUTRAL: 25.0,
    S_DAMAGED: 12.5,
    S_FULL_DOWN: 0.0,
    S_UNKNOWN: 0.0,
}

# 26주선 기울기별 점수
SLOPE_SCORES = {"상승": 10.0, "횡보": 5.0, "하락": 0.0, "-": 0.0}

# 이격도 점수 기준
DISPARITY_OVERHEAT_PCT = 40.0   # +40% 초과 = 과열
DISPARITY_SCORE_NORMAL = 15.0   # 0 ~ +40% = 정상
DISPARITY_SCORE_OVERHEAT = 5.0  # 과열
DISPARITY_SCORE_NEGATIVE = 0.0  # 26주선 아래

# 상대강도(RS) 구간별 점수 — SPY 대비 13주 초과수익률(%p) 기준
# (아래에서부터 순서대로 검사해 처음 해당하는 구간의 점수를 줍니다)
RS_SCORE_BANDS = [
    (20.0, 25.0),   # +20%p 초과 → 25점 (시장을 크게 이김)
    (10.0, 20.0),   # +10 ~ +20%p → 20점
    (0.0, 14.0),    # 0 ~ +10%p → 14점 (시장보다 약간 나음)
    (-10.0, 7.0),   # -10 ~ 0%p → 7점
    (float("-inf"), 0.0),  # -10%p 미만 → 0점
]


# ---------------------------------------------------------------------------
# 최종 점수 및 매트릭스 판정
# ---------------------------------------------------------------------------

WEIGHT_FUNDAMENTAL = 0.6   # 최종점수 = 펀더 × 0.6 + 기술 × 0.4
WEIGHT_TECHNICAL = 0.4

FUND_STRONG = 70.0   # 펀더멘털 강함 기준선
FUND_WEAK = 40.0     # 펀더멘털 약함 기준선

# 판정 이름
V_BUY = "매수 후보"
V_WARN = "⚠️ 경고"
V_WATCH = "관찰"
V_MOMENTUM = "펀더 없는 모멘텀"
V_EXCLUDE = "제외"

# 판정별 색상 (다크 테마 기준)
VERDICT_COLORS = {
    V_BUY: "#22c55e",       # 초록
    V_WARN: "#f59e0b",      # 주황
    V_WATCH: "#3b82f6",     # 파랑
    V_MOMENTUM: "#a78bfa",  # 보라
    V_EXCLUDE: "#6b7280",   # 회색
}

# 데이터 출처 배지 이름
SRC_DIRECT = "직접공시"   # 보도자료에 논갭 수치가 그대로 있음
SRC_DERIVED = "역산"      # 매출 × GM% − opex 로 계산
SRC_APPROX = "근사치"     # XBRL GAAP 값 + 주식보상비 등으로 근사
SRC_GUIDANCE = "가이던스"  # 회사가 제시한 다음 분기 전망
SRC_ESTIMATE = "추정"     # 컨센서스 매출 × 과거 평균 마진
