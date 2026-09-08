import re
import traceback

from playwright.async_api import async_playwright

DEFAULT_API_KEY = "C60A7FB54FB465A6E0530718000A1B96"

async def get_nstu_tokens_auto(username: str, password: str) -> dict | None:
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox"]
        )
        context = await browser.new_context()
        page = await context.new_page()

        bearer_token = None
        captured_api_key = None
        captured_id_card = None

        async def handle_request(request):
            nonlocal bearer_token, captured_api_key, captured_id_card
            url = request.url

            match = re.search(r'id_card=(\d+)', url)
            if match:
                if "get_sections" in url or not captured_id_card:
                    captured_id_card = match.group(1)

            auth_header = request.headers.get("authorization")
            if auth_header and "Bearer " in auth_header:
                bearer_token = auth_header.replace("Bearer ", "")

            x_apikey = request.headers.get("x-apikey") or request.headers.get("apikey")
            if x_apikey:
                captured_api_key = x_apikey

        page.on("request", handle_request)

        try:
            await page.goto("https://e-service.ciu.nstu.ru/services", timeout=60000)

            username_input = page.locator('input[type="text"], input[name="username"]').filter(visible=True).first
            await username_input.wait_for(timeout=30000)
            await username_input.fill(username)

            password_input = page.locator('input[type="password"], input[name="password"]').filter(visible=True).first
            await password_input.fill(password)

            login_button = page.locator('#kc-login, input[type="submit"], button[type="submit"]').filter(
                visible=True).first

            await login_button.click()

            await page.wait_for_url(
                lambda u: "login" not in u and "auth" not in u and "e-service.ciu.nstu.ru" in u,
                timeout=60000
            )

            await page.wait_for_timeout(2000)

            if not captured_id_card:
                try:
                    captured_id_card = await page.evaluate("""() => {
                        return localStorage.getItem('id_card') || 
                               sessionStorage.getItem('id_card') || 
                               localStorage.getItem('user_id') || null;
                    }""")
                except Exception:
                    pass

            cookies_list = await context.cookies()
            cookies_dict = {c["name"]: c["value"] for c in cookies_list}

            await browser.close()

            return {
                "bearer_token": bearer_token,
                "api_key": captured_api_key or DEFAULT_API_KEY,
                "cookies": cookies_dict,
                "id_card": str(captured_id_card) if captured_id_card else None
            }

        except Exception as e:
            traceback.print_exc()
            await browser.close()
            return None