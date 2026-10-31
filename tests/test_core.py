import copy
import csv
import json
import tempfile
import unittest
from pathlib import Path

from shopee_collector.parsing import (canonical, product_ids, price, extract_payload,
                                      suggested_category, jsonld_products)
from shopee_collector.storage import Store
from shopee_collector.browser import blocker


FIXTURE = json.loads((Path(__file__).parent / "synthetic_payload.json").read_text(encoding="utf-8"))


def bundle(payload=None):
    return extract_payload(payload or FIXTURE, observed_at="TEST_TIME", source_url="synthetic://test")


class ParsingTests(unittest.TestCase):
    def test_product_urls_and_tracking(self):
        self.assertEqual(canonical("https://shopee.vn/ao-demo-i.123.456?tracking=x"),
                         "https://shopee.vn/product/123/456")
        self.assertEqual(product_ids("/product/123/456"), ("123", "456"))
        self.assertIsNone(canonical("https://shopee.vn.evil.test/product/123/456"))
        self.assertIsNone(canonical("https://shopee.vn@evil.test/product/123/456"))
        self.assertIsNone(canonical("https://shopee.vn/buyer/login"))

    def test_price_units_and_zero(self):
        self.assertEqual(price(9900000000, 100000), "99000")
        self.assertEqual(price("99000.50", 1), "99000.5")
        self.assertEqual(price(0, 100000), "0")
        for raw in (-1, None, "NaN", "Infinity", True, "abc"):
            self.assertIsNone(price(raw, 100000))

    def test_variants_are_observed_not_cartesian_invented(self):
        p = bundle()["products"][0]
        self.assertEqual(len(p["variants"]), 2)  # 2x2 options do not imply four valid models.
        self.assertEqual(p["variants"][0]["options"], {"Màu": "Đen", "Size": "M"})
        self.assertEqual(p["variants"][1]["price"], "129000")
        self.assertEqual(p["variants"][1]["stock"], 0)

    def test_absent_models_are_unknown(self):
        fixture = copy.deepcopy(FIXTURE)
        del fixture["data"]["item"]["models"]
        p = bundle(fixture)["products"][0]
        self.assertEqual(p["variants"], [])
        self.assertEqual(p["variant_capture_status"], "not_observed")

    def test_review_association_and_no_author_export(self):
        data = bundle()
        self.assertEqual(len(data["reviews"]), 2)
        self.assertNotIn("DO_NOT_EXPORT", json.dumps(data))
        self.assertTrue(all(r["product_key"] == "99900001:99900002" for r in data["reviews"]))

    def test_review_fallback_does_not_attach_foreign_product(self):
        wrong = {"ratings": [{"cmtid": 5, "itemid": 200, "shopid": 100, "rating_star": 5, "comment": "hello"}]}
        self.assertEqual(extract_payload(wrong, review_product=("101", "201"))["reviews"], [])
        missing = {"ratings": [{"cmtid": 5, "rating_star": 5, "comment": "hello"}]}
        self.assertEqual(extract_payload(missing)["reviews"], [])
        self.assertEqual(extract_payload(missing, review_product=("101", "201"))["reviews"][0]["product_key"], "101:201")

    def test_no_fabricated_review_ids(self):
        r = {"itemid": 2, "shopid": 1, "rating_star": 5, "comment": "same text"}
        self.assertEqual(extract_payload(r)["reviews"], [])

    def test_classification_ambiguity(self):
        rules = [{"label": "A", "keywords": ["điện thoại"]}, {"label": "B", "keywords": ["tai nghe"]}]
        self.assertEqual(suggested_category("ĐIỆN THOẠI mới", rules)["suggested_category"], "A")
        self.assertEqual(suggested_category("điện thoại tai nghe", rules)["classification_method"], "ambiguous_keyword")
        self.assertEqual(suggested_category("không khớp", rules)["classification_method"], "unclassified")

    def test_jsonld_price_not_divided_again(self):
        payload = {"@type": "Product", "name": "demo", "offers": {"price": "99000", "priceCurrency": "VND"}}
        p = jsonld_products(payload, "https://shopee.vn/product/1/2", "test")[0]
        self.assertEqual(p["price"], "99000")
        self.assertEqual(p["price_divisor"], 1)
        payload["url"] = "https://shopee.vn/product/3/4"
        self.assertEqual(jsonld_products(payload, "https://shopee.vn/product/1/2", "test"), [])

    def test_block_detection(self):
        self.assertEqual(blocker("https://shopee.vn/verify/captcha?secret=redacted", ""), "captcha_or_verification")
        self.assertEqual(blocker("https://shopee.vn/buyer/login", ""), "login_required")
        self.assertIsNone(blocker("https://shopee.vn/", "Sản phẩm"))


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(self.tmp.name)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_review_deduplication_and_price_history(self):
        self.store.ingest(bundle())
        self.store.ingest(bundle())
        self.assertEqual(len(self.store.rows("review")), 2)
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM observations").fetchone()[0], 2)

    def test_resume_does_not_skip_blocked(self):
        self.store.enqueue("https://shopee.vn/", "listing", "test")
        self.store.status("https://shopee.vn/", "blocked", "test", "captcha")
        self.assertIsNone(self.store.next_job())
        self.store.retry()
        self.assertIsNotNone(self.store.next_job())

    def test_detail_not_erased_by_summary(self):
        self.store.ingest(bundle())
        summary = {"shopid": 99900001, "itemid": 99900002, "name": "new name", "price": 5000000000}
        self.store.ingest(bundle(summary))
        p = self.store.rows("product")[0]
        self.assertEqual(p["record_level"], "detail")
        self.assertEqual(len(p["variants"]), 2)
        self.assertEqual(p["price"], "50000")
        self.assertEqual(p["classification_method"], "shopee_category_observed")

    def test_price_bundle_uses_same_units(self):
        self.store.ingest(bundle())
        p = jsonld_products({"@type": "Product", "name": "demo", "offers": {"price": "101000", "priceCurrency": "VND"}},
                            "https://shopee.vn/product/99900001/99900002", "later")[0]
        self.store.ingest({"products": [p]})
        row = self.store.rows("product")[0]
        self.assertEqual(row["price_divisor"], 1)
        self.assertEqual(row["price_raw"], "101000")
        self.assertIsNone(row["price_max"])  # no stale API-scaled range.
        self.assertEqual(row["variants"][0]["price_divisor"], 100000)

    def test_export_schema_utf8_and_excel_formula_safety(self):
        fixture = copy.deepcopy(FIXTURE)
        fixture["data"]["ratings"][0]["comment"] = '=HYPERLINK("https://example.com")'
        self.store.ingest(bundle(fixture))
        report = self.store.export()
        self.assertEqual(report["products_observed"], 1)
        self.assertEqual(report["variants_observed"], 2)
        with open(Path(self.tmp.name) / "reviews.csv", encoding="utf-8-sig", newline="") as f:
            reviews = list(csv.DictReader(f))
        self.assertTrue(reviews[0]["content"].startswith("'="))
        self.assertNotIn("author_username", reviews[0])
        self.assertTrue((Path(self.tmp.name) / "products.csv").read_bytes().startswith(b"\xef\xbb\xbf"))
        self.assertIn("unknown", report["coverage"])

    def test_empty_export_does_not_make_fake_data(self):
        self.assertEqual(self.store.export()["products_observed"], 0)
        with open(Path(self.tmp.name) / "products.csv", encoding="utf-8-sig", newline="") as f:
            self.assertEqual(list(csv.DictReader(f)), [])


if __name__ == "__main__":
    unittest.main()
