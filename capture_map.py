from __future__ import annotations

import os
from pathlib import Path

from playwright.sync_api import sync_playwright


MAP_HTML_PATH = Path("output/map.html").resolve()
MAP_PNG_PATH = Path("output/map.png").resolve()


def main() -> None:
    if not MAP_HTML_PATH.exists():
        raise FileNotFoundError(f"Map HTML not found: {MAP_HTML_PATH}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1600, "height": 1000})

        page.goto(MAP_HTML_PATH.as_uri(), wait_until="load")
        page.wait_for_timeout(4000)

        # 타이틀 박스/지도 타일/폴리라인 등이 모두 렌더될 시간을 약간 더 줌
        page.screenshot(path=str(MAP_PNG_PATH), full_page=True)

        browser.close()

    print(f"[INFO] map image saved to {MAP_PNG_PATH}")


if __name__ == "__main__":
    main()
