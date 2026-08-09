"""
storage.py — 종목 목록 저장/불러오기
====================================

"저장 버튼을 누르면 다음에 앱을 켜도 종목이 유지되게" 하기 위한 모듈입니다.

저장 위치는 세 겹입니다 (안전한 것부터):
  ① 주소(URL)            — 홈 화면에 추가해 두면 항상 유지 (기존 방식)
  ② 서버 파일            — data/saved_tickers.json. 앱이 잠들었다 깨어나도 유지되지만,
                            코드를 새로 올리거나 서버가 재배포되면 사라집니다
  ③ GitHub 저장소 커밋   — user_tickers.json 을 저장소에 직접 저장. 어떤 경우에도
                            유지되는 완전 영구 방식. Streamlit Secrets에
                            GITHUB_TOKEN 을 넣어야 켜집니다 (선택 사항)

불러올 때는 ① → ② → ③ → 기본 목록 순서로 찾습니다.
"""

from __future__ import annotations

import base64
import json
import os

import config as cfg

# 서버 파일 (재배포 전까지 유지)
LOCAL_FILE = os.path.join(cfg.CACHE_DIR, "saved_tickers.json")

# 저장소 커밋 파일 (완전 영구 — 재배포 후에는 코드와 함께 배포됩니다)
REPO_FILE = "user_tickers.json"


def _clean(tickers: list[str]) -> list[str]:
    """대문자로 통일하고 중복·빈 값을 제거합니다."""
    out: list[str] = []
    for piece in tickers:
        symbol = str(piece).strip().upper()
        if symbol and symbol not in out:
            out.append(symbol)
    return out


# ---------------------------------------------------------------------------
# 불러오기
# ---------------------------------------------------------------------------
def load_saved_tickers() -> list[str] | None:
    """저장된 종목 목록을 찾습니다. 없으면 None (호출자가 기본 목록 사용)."""
    # ② 서버 파일 (가장 최근 저장)
    for path in (LOCAL_FILE, REPO_FILE):
        try:
            if os.path.exists(path):
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
                tickers = _clean(data.get("tickers", []))
                if tickers:
                    return tickers
        except Exception:
            continue  # 파일이 깨졌으면 다음 후보로
    return None


# ---------------------------------------------------------------------------
# 저장하기
# ---------------------------------------------------------------------------
def save_tickers_local(tickers: list[str]) -> bool:
    """서버 파일에 저장합니다 (재배포 전까지 유지)."""
    try:
        os.makedirs(cfg.CACHE_DIR, exist_ok=True)
        with open(LOCAL_FILE, "w", encoding="utf-8") as f:
            json.dump({"tickers": _clean(tickers)}, f, ensure_ascii=False, indent=1)
        return True
    except OSError:
        return False


def commit_tickers_github(
    tickers: list[str],
    token: str,
    repo: str,
    opener=None,
) -> tuple[bool, str]:
    """GitHub 저장소에 user_tickers.json 을 커밋합니다 (완전 영구 저장).

    반환: (성공 여부, 사용자에게 보여줄 메시지)
    opener 는 테스트에서 가짜 네트워크로 갈아끼우기 위한 것입니다.
    """
    import urllib.error
    import urllib.request

    if opener is None:
        opener = urllib.request.urlopen

    url = f"https://api.github.com/repos/{repo}/contents/{REPO_FILE}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "trend-dashboard",
    }

    # 이미 파일이 있으면 그 버전 번호(sha)가 필요합니다
    sha = None
    try:
        request = urllib.request.Request(url, headers=headers)
        with opener(request, timeout=15) as response:
            sha = json.loads(response.read().decode()).get("sha")
    except urllib.error.HTTPError as exc:
        if exc.code != 404:   # 404 = 아직 없음(정상). 그 외는 실패
            return False, f"GitHub 조회 실패 (HTTP {exc.code}) — 토큰 권한을 확인해 주세요"
    except Exception as exc:
        return False, f"GitHub 연결 실패: {type(exc).__name__}"

    content = json.dumps({"tickers": _clean(tickers)}, ensure_ascii=False, indent=1)
    payload: dict = {
        "message": "종목 목록 저장 (대시보드에서 저장 버튼)",
        "content": base64.b64encode(content.encode()).decode(),
    }
    if sha:
        payload["sha"] = sha

    try:
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode(),
            headers={**headers, "Content-Type": "application/json"},
            method="PUT",
        )
        with opener(request, timeout=20):
            pass
        return True, "GitHub 저장소에 저장했습니다 — 어떤 경우에도 유지됩니다"
    except urllib.error.HTTPError as exc:
        return False, f"GitHub 저장 실패 (HTTP {exc.code}) — 토큰에 쓰기 권한이 있는지 확인해 주세요"
    except Exception as exc:
        return False, f"GitHub 저장 실패: {type(exc).__name__}"
