"""No calls to Shopee. Exercise the actual passive-response handler with fakes."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from shopee_collector.browser import Collector
from shopee_collector.storage import Store

ROOT = Path(__file__).resolve().parent.parent


class Response:
    def __init__(self, url, payload=None, status=200):
        self.url, self.status = url, status
        self.headers = {"content-type": "application/json"}
        self.payload = payload
        self.body_called = False

    async def body(self):
        self.body_called = True
        return json.dumps(self.payload).encode()


class PipelineTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(self.tmp.name)
        config = json.loads((ROOT / "config/settings.json").read_text(encoding="utf-8"))
        self.c = Collector(self.store, config, [])

    async def asyncTearDown(self):
        self.store.close()
        self.tmp.cleanup()

    async def test_matching_rating_request_can_supply_identity(self):
        r = Response("https://shopee.vn/api/test/rating?shopid=1&itemid=2",
                     {"ratings": [{"cmtid": 11, "rating_star": 5, "comment": "test"}]})
        await self.c.read_response(r, "https://shopee.vn/product/1/2")
        self.assertEqual(self.store.rows("review")[0]["product_key"], "1:2")

    async def test_other_rating_request_never_borrows_current_page_identity(self):
        r = Response("https://shopee.vn/api/test/rating?shopid=3&itemid=4",
                     {"ratings": [{"cmtid": 11, "rating_star": 5, "comment": "test"}]})
        await self.c.read_response(r, "https://shopee.vn/product/1/2")
        self.assertEqual(self.store.rows("review"), [])

    async def test_account_response_is_not_read(self):
        r = Response("https://shopee.vn/api/shop/account", {"secret": "do not capture"})
        await self.c.read_response(r, "https://shopee.vn/")
        self.assertFalse(r.body_called)

    async def test_external_response_is_not_read(self):
        r = Response("https://example.com/api/item", {"secret": "do not capture"})
        await self.c.read_response(r, "https://shopee.vn/")
        self.assertFalse(r.body_called)

    async def test_429_pauses_without_requesting_another_endpoint(self):
        r = Response("https://shopee.vn/api/test/item", {}, status=429)
        await self.c.read_response(r, "https://shopee.vn/")
        self.assertEqual(self.c.network_block, "http_429")
        self.assertFalse(r.body_called)

    async def test_stop_queue_on_first_block(self):
        self.store.enqueue("https://shopee.vn/", "listing", "test")
        self.store.enqueue("https://shopee.vn/search?keyword=test", "listing", "test")
        called = []

        async def blocked_visit(job):
            called.append(job["url"])
            self.store.status(job["url"], "blocked", "test", "captcha")
            return "captcha_or_verification"

        self.c.visit = blocked_visit
        self.c.accept_responses = True
        await self.c.run_queue()
        self.assertEqual(called, ["https://shopee.vn/"])
        self.assertFalse(self.c.accept_responses)
        self.assertEqual(self.store.report()["queue_status"], {"blocked": 1, "pending": 1})


if __name__ == "__main__":
    unittest.main()
