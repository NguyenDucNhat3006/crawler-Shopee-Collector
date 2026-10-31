from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlsplit, quote

from .parsing import canonical, product_ids, extract_payload, jsonld_products


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def blocker(url, text):
    path = urlsplit(url).path.lower()
    if "/verify" in path or "/captcha" in path:
        return "captcha_or_verification"
    if "/buyer/login" in path or "/account/login" in path:
        return "login_required"
    text = text.lower()
    if any(x in text for x in ("verify you are human", "unusual traffic", "automated requests",
                                "xác nhận để tiếp tục", "truy cập bất thường")):
        return "verification_required"
    return None


class Collector:
    def __init__(self, store, config, rules):
        self.store, self.cfg, self.rules = store, config, rules
        self.tasks, self.response_errors = set(), []
        self.network_block = None
        self.page = None
        self.context = None
        self.accept_responses = False

    def response(self, response):
        if not self.accept_responses:
            return
        # Capture page identity at event time; never use a later page as fallback.
        page_url = self.page.url
        task = asyncio.create_task(self.read_response(response, page_url))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def drain(self):
        while self.tasks:
            await asyncio.gather(*list(self.tasks), return_exceptions=True)

    async def read_response(self, response, page_url):
        u = urlsplit(response.url)
        path = u.path.lower()
        if u.hostname not in {"shopee.vn", "www.shopee.vn"} or "/api/" not in path:
            return
        if not any(t in path for t in ("item", "pdp", "search", "rating", "shop", "category", "recommend")):
            return
        if any(t in path for t in ("account", "login", "order", "payment", "address", "wallet", "chat")):
            return
        if response.status in (401, 403, 429):
            self.network_block = f"http_{response.status}"
            return
        if response.status != 200 or "json" not in response.headers.get("content-type", ""):
            return
        try:
            size = response.headers.get("content-length")
            if size and int(size) > 5_000_000:
                return
            raw = await response.body()
            if len(raw) > 5_000_000:
                return
            payload = json.loads(raw)
            # Missing review identity can be filled only from a matching request.
            fallback = None
            ids = product_ids(page_url)
            q = parse_qs(u.query)
            if ids and "rating" in path and q.get("itemid") == [ids[1]] and q.get("shopid") == [ids[0]]:
                fallback = ids
            bundle = extract_payload(payload, divisor=self.cfg["network_price_divisor"], rules=self.rules,
                                     source_url=f"https://shopee.vn{u.path}", observed_at=now(),
                                     review_product=fallback)
            # Raw responses, request headers, cookies and author profiles are not stored.
            self.store.ingest(bundle)
        except Exception as exc:
            # Avoid exception strings which might include URLs with session parameters.
            self.response_errors.append(type(exc).__name__)
            self.store.event(now(), "response_parse_error", type(exc).__name__)

    async def check(self):
        text = await self.page.locator("body").inner_text(timeout=10000)
        return self.network_block or blocker(self.page.url, text[:20000])

    async def settle(self):
        await self.page.wait_for_timeout(self.cfg["settle_ms"])
        await self.drain()
        return await self.check()

    async def collect_dom(self):
        url = self.page.url
        if not product_ids(url):
            return
        scripts = await self.page.locator('script[type="application/ld+json"]').all_text_contents()
        for script in scripts:
            try:
                products = jsonld_products(json.loads(script), url, now(), self.rules)
                self.store.ingest({"products": products})
            except (ValueError, TypeError, AttributeError):
                self.store.event(now(), "jsonld_parse_error", "Unsupported JSON-LD shape")

    async def discover(self, include_categories=False):
        links = await self.page.locator("a[href]").evaluate_all(
            "els => els.map(e => ({href:e.href, text:(e.innerText || '').trim()}))")
        for link in links:
            try:
                url = canonical(link["href"])
            except ValueError:
                continue
            if not url:
                continue
            if product_ids(url):
                kind = "product"
            elif include_categories and re.search(r"-cat\.\d+", urlsplit(url).path):
                kind = "listing"
                cid = re.search(r"-cat\.(\d+)", urlsplit(url).path)[1]
                self.store.put("category_link", cid, {"category_id": cid, "name": link["text"], "url": url})
            else:
                continue
            count = self.store.db.execute("SELECT COUNT(*) FROM queue").fetchone()[0]
            if count >= self.cfg["max_queue_size"]:
                return
            self.store.enqueue(url, kind, now())

    async def scroll_and_collect(self, listing=False):
        for _ in range(self.cfg["scroll_steps"]):
            await self.page.mouse.wheel(0, 850)
            block = await self.settle()
            if block:
                return block
            if listing:
                await self.discover(include_categories=True)
        await self.collect_dom()
        return await self.check()

    async def next_visible(self, selector):
        """UI pagination only. No constructed API endpoints or fabricated page requests."""
        buttons = self.page.locator(selector)
        if await buttons.count() != 1:
            return False
        button = buttons.first
        classes = await button.get_attribute("class") or ""
        if (not await button.is_visible() or not await button.is_enabled()
                or await button.get_attribute("aria-disabled") == "true" or "disabled" in classes):
            return False
        await button.click(timeout=5000)
        return True

    async def visit(self, job):
        url, kind = job["url"], job["kind"]
        self.network_block = None
        self.store.status(url, "running", now())
        try:
            response = await self.page.goto(url, wait_until="domcontentloaded", timeout=45000)
            if response and response.status in (401, 403, 429):
                self.network_block = f"http_{response.status}"
            block = await self.settle()
            if block:
                self.store.status(url, "blocked", now(), block)
                return block
            if kind == "product":
                block = await self.scroll_and_collect()
                for _ in range(max(0, self.cfg["review_pages_per_product"] - 1)):
                    if block or not await self.next_visible(self.cfg["selectors"]["review_next"]):
                        break
                    block = await self.settle()
                key = ":".join(product_ids(url))
                p = self.store.get("product", key)
                # This records only a successful detail visit; full review coverage stays unknown.
                status = "partial" if p else "no_data"
                note = "detail_visit; coverage_unknown" if p else "No supported product payload or JSON-LD observed"
            else:
                for page_index in range(self.cfg["listing_pages_per_seed"]):
                    block = await self.scroll_and_collect(listing=True)
                    if block:
                        break
                    if page_index + 1 >= self.cfg["listing_pages_per_seed"]:
                        break
                    if not await self.next_visible(self.cfg["selectors"]["listing_next"]):
                        break
                    block = await self.settle()
                    if block:
                        break
                status, note = "visited", "discovery_attempt; coverage_unknown"
            if block:
                self.store.status(url, "blocked", now(), block)
                return block
            self.store.status(url, status, now(), note)
        except Exception as exc:
            self.store.status(url, "failed", now(), type(exc).__name__)
            print(f"Trang bị lỗi: {type(exc).__name__}; đã lưu tiến độ.")
        finally:
            await self.drain()
        return None

    async def run_queue(self):
        visited, products_visited = 0, 0
        while visited < self.cfg["max_pages_per_run"]:
            job = self.store.next_job()
            if not job:
                print("Đã hết URL đang chờ. Điều này không có nghĩa đã lấy toàn bộ Shopee.")
                break
            if job["kind"] == "product" and products_visited >= self.cfg["max_products_per_run"]:
                break
            print(f"[{visited + 1}] {job['kind']}: {job['url']}")
            block = await self.visit(job)
            visited += 1
            products_visited += int(job["kind"] == "product")
            if block:
                print(f"DỪNG: {block}. Không chuyển URL để né chặn. Dùng trình duyệt để xử lý thủ công,")
                print("sau đó lệnh retry sẽ kiểm tra lại trang hiện tại trước khi cho chạy tiếp.")
                self.accept_responses = False
                break
            await self.page.wait_for_timeout(self.cfg["delay_ms"])
        self.store.export()

    async def console(self):
        print("\nLệnh: run | capture | retry | stats | export | quit")
        print("Bạn có thể tự điều hướng trang, đăng nhập hoặc xử lý CAPTCHA trong cửa sổ trình duyệt.")
        while True:
            command = (await asyncio.to_thread(input, "shopee> ")).strip().lower()
            if command in {"quit", "q", "exit"}:
                break
            if command == "run":
                if not self.accept_responses:
                    print("Đang tạm dừng; xử lý trang hiện tại rồi dùng retry.")
                else:
                    await self.run_queue()
            elif command in {"capture", "retry"}:
                self.network_block = None
                block = await self.check()
                if block:
                    self.accept_responses = False
                    print(f"Vẫn bị chặn: {block}.")
                    continue
                self.accept_responses = True
                if command == "retry":
                    self.store.retry()
                    print("Đã đưa các trang bị thiếu/lỗi về hàng chờ; dùng run để tiếp tục.")
                else:
                    await self.collect_dom()
                    await self.discover(include_categories=True)
                    await self.drain()
                    self.store.export()
                    print("Đã ghi nhận dữ liệu được quan sát và link đang hiển thị.")
            elif command == "stats":
                print(json.dumps(self.store.report(), ensure_ascii=False, indent=2))
            elif command == "export":
                await self.drain()
                self.store.export()
                print("Đã xuất dữ liệu.")
            else:
                print("Dùng run, capture, retry, stats, export hoặc quit.")

    async def start(self, auto=False, remember=False):
        from playwright.async_api import async_playwright

        for seed in self.cfg.get("seeds", []):
            url = canonical(seed)
            if not url:
                raise ValueError("Seed must be an HTTPS Shopee VN URL")
            self.store.enqueue(url, "product" if product_ids(url) else "listing", now())
        for keyword in self.cfg.get("keywords", []):
            self.store.enqueue("https://shopee.vn/search?keyword=" + quote(keyword), "listing", now())
        async with async_playwright() as pw:
            browser = None
            opts = {"headless": False}
            if self.cfg.get("browser_channel"):
                opts["channel"] = self.cfg["browser_channel"]
            if remember:
                self.context = await pw.chromium.launch_persistent_context("browser-profile", **opts)
            else:
                browser = await pw.chromium.launch(**opts)
                self.context = await browser.new_context()
            self.page = await self.context.new_page()
            self.page.on("response", self.response)
            self.accept_responses = True
            try:
                if auto:
                    await self.run_queue()
                else:
                    job = self.store.next_job()
                    if job:
                        block = await self.visit(job)
                        if block:
                            self.accept_responses = False
                            print(f"Cần xử lý thủ công: {block}.")
                    await self.console()
            finally:
                self.accept_responses = False
                await self.drain()
                self.store.export()
                await self.context.close()
                if browser:
                    await browser.close()
