"""
mobile_check.py — 휴대폰 폭(412px)에서 실제로 렌더링해 보는 점검

주인은 **휴대폰으로만** 씁니다(CLAUDE.md 2장). 그래서 화면 작업은
"코드가 맞다"가 아니라 **412px 에서 실제로 떠 보는 것**으로 확인합니다.

무엇을 보나:
  · 문서 가로 폭이 412 를 넘지 않는가 (가로 스크롤 생김)
  · **요소마다** 오른쪽 끝이 412 를 넘지 않는가
    — 가로 스크롤만 보면 `overflow:hidden` 에 가려 놓칩니다.
    다만 일부러 옆으로 넘기게 만든 상자(overflow-x:auto) 안은 뺍니다.
  · 콘솔 오류(pageerror)가 0건인가

왜 저장소에 두나 (158차): 이 점검은 매일 도는데, 세션 컨테이너가
자주 초기화돼 임시 폴더에 두면 **매번 다시 만들어야** 했습니다.
실제로 그렇게 하루에 세 번 다시 만들었습니다. 절차의 일부이므로
저장소에 둡니다.

실행: python3 tools/mobile_check.py   (종료코드 0 = 이상 없음)
"""

from __future__ import annotations

import glob
import os
import subprocess
import sys
import time

TABS = ["#/home", "#/market", "#/stocks", "#/check"]
WIDTH = 412
PORT = 8899


def _chromium() -> str:
    """미리 깔린 크로미움 경로 (환경마다 판 번호가 달라 찾아서 씁니다)."""
    found = glob.glob("/opt/pw-browsers/chromium*/chrome-linux/chrome")
    return found[0] if found else "/opt/pw-browsers/chromium"


# 요소가 412 밖으로 나갔는지 보는 자 — 옆으로 넘기는 상자 안은 뺍니다
_OVERFLOW_JS = """() => {
    const out = [];
    for (const el of document.querySelectorAll('body *')) {
        const r = el.getBoundingClientRect();
        if (r.width === 0 || r.height === 0) continue;
        if (r.right > %d.5) {
            let a = el.parentElement, scrollable = false;
            while (a) {
                const s = getComputedStyle(a);
                if ((s.overflowX === 'auto' || s.overflowX === 'scroll')
                    && a.scrollWidth > a.clientWidth) { scrollable = true; break; }
                a = a.parentElement;
            }
            if (!scrollable) out.push(el.tagName + '.' + (el.className || '')
                                      + ' right=' + Math.round(r.right));
        }
    }
    return out.slice(0, 8);
}""" % WIDTH


def run(docs_dir: str | None = None, progress=print) -> int:
    """네 탭을 412px 로 띄워 봅니다. 이상이 있으면 1, 없으면 0."""
    if docs_dir is None:
        docs_dir = os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "docs")
    from playwright.sync_api import sync_playwright

    server = subprocess.Popen(
        ["python3", "-m", "http.server", str(PORT), "--directory", docs_dir],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1.5)
    나쁨 = 0
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=_chromium(),
                                        args=["--no-sandbox"])
            page = browser.new_page(viewport={"width": WIDTH, "height": 915})
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            for tab in TABS:
                page.goto(f"http://localhost:{PORT}/index.html{tab}")
                page.wait_for_timeout(2500)
                width = page.evaluate("document.documentElement.scrollWidth")
                over = page.evaluate(_OVERFLOW_JS)
                if width > WIDTH or over:
                    나쁨 += 1
                progress(f"{tab}: 문서폭 {width} · 밖으로 나간 요소 {len(over)}")
                for one in over:
                    progress(f"    {one}")
            progress(f"콘솔 오류: {len(errors)}건")
            for e in errors[:5]:
                progress(f"    {e[:200]}")
            if errors:
                나쁨 += 1
            browser.close()
    finally:
        server.terminate()
    progress("이상 없음" if 나쁨 == 0 else f"⛔ 살펴볼 화면 {나쁨}개")
    return 0 if 나쁨 == 0 else 1


if __name__ == "__main__":
    sys.exit(run())
