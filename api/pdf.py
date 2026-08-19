"""Playwright headless로 SPA의 /report 라우트를 인쇄해 PDF를 만든다.

데이터는 URL 쿼리(길이 한계)나 서버 임시 저장(stateless 위반)이 아니라
add_init_script로 window.__REPORT_DATA__에 주입한다 -- 페이지 스크립트보다
먼저 실행되므로 React가 마운트될 때 이미 값이 있다.

리포트 페이지는 레이아웃이 끝나면 window.__REPORT_READY__ = true를 세우고,
여기서는 그것을 기다린다. 고정 sleep은 느리면 깨지고 빠르면 낭비다.
"""
import json


async def render_report_pdf(payload: dict, base_url: str, timeout_ms: int = 30_000) -> bytes:
    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        try:
            page = await browser.new_page(viewport={"width": 1280, "height": 900})
            await page.add_init_script(
                f"window.__REPORT_DATA__ = {json.dumps(payload, ensure_ascii=False)};")
            await page.goto(f"{base_url}/report", wait_until="load")
            await page.wait_for_function("window.__REPORT_READY__ === true", timeout=timeout_ms)
            return await page.pdf(
                format="A4", print_background=True,
                margin={"top": "12mm", "bottom": "12mm", "left": "12mm", "right": "12mm"})
        finally:
            await browser.close()
