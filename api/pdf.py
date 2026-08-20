"""Playwright headless로 SPA의 /report 라우트를 인쇄해 PDF를 만든다.

데이터는 URL 쿼리(길이 한계)나 서버 임시 저장(stateless 위반)이 아니라
add_init_script로 window.__REPORT_DATA__에 주입한다 -- 페이지 스크립트보다
먼저 실행되므로 React가 마운트될 때 이미 값이 있다.

리포트 페이지는 레이아웃이 끝나면 window.__REPORT_READY__ = true를 세우고,
여기서는 그것을 기다린다. 고정 sleep은 느리면 깨지고 빠르면 낭비다.
"""
import json


class BrowserLaunchError(RuntimeError):
    """chromium 실행 파일이 없어 launch()가 실패했을 때만 던진다. report.py가
    이 경우(설치 미완료)를 503으로, 그 밖의 렌더링 실패(타임아웃 등)를 500으로
    구분해 돌려줄 수 있게 신호를 분리한다."""


async def render_report_pdf(payload: dict, base_url: str, timeout_ms: int = 30_000) -> bytes:
    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        try:
            browser = await pw.chromium.launch()
        except Exception as exc:                        # noqa: BLE001
            raise BrowserLaunchError(
                "chromium 실행 파일을 찾을 수 없다. `uv run playwright install chromium` "
                "후 다시 시도할 것.") from exc
        try:
            page = await browser.new_page(viewport={"width": 1280, "height": 900})
            # add_init_script는 CDP의 Page.addScriptToEvaluateOnNewDocument로
            # 전달된다 -- HTML 파서를 거치지 않으므로 payload 안에 "</script>"가
            # 있어도 스크립트가 끊기지 않고, U+2028/2029(ES2019+에서는 문자열
            # 리터럴 안에서 합법)도 안전하다. 이 f-string을 나중에 HTML의
            # 인라인 <script> 태그로 옮기면 이 안전성이 사라지므로 XSS를
            # 새로 만들지 않도록 이스케이프를 다시 검토할 것.
            await page.add_init_script(
                f"window.__REPORT_DATA__ = {json.dumps(payload, ensure_ascii=False)};")
            await page.goto(f"{base_url}/report", wait_until="load")
            await page.wait_for_function("window.__REPORT_READY__ === true", timeout=timeout_ms)
            return await page.pdf(
                format="A4", print_background=True,
                margin={"top": "12mm", "bottom": "12mm", "left": "12mm", "right": "12mm"})
        finally:
            await browser.close()
