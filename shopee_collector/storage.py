from __future__ import annotations

import csv
import json
import sqlite3
from collections import Counter
from pathlib import Path


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def csv_value(value):
    if isinstance(value, (list, dict)):
        value = encode(value)
    # Product names/reviews are untrusted; prevent formula execution in Excel.
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


class Store:
    def __init__(self, folder):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.folder / "catalog.sqlite3")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS entities(kind TEXT, key TEXT, data TEXT, PRIMARY KEY(kind,key));
          CREATE TABLE IF NOT EXISTS observations(id INTEGER PRIMARY KEY, product_key TEXT, data TEXT);
          CREATE TABLE IF NOT EXISTS queue(url TEXT PRIMARY KEY, kind TEXT, status TEXT DEFAULT 'pending',
             attempts INTEGER DEFAULT 0, last_error TEXT, updated_at TEXT);
          CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, at TEXT, kind TEXT, message TEXT);
        ''')
        self.db.execute("UPDATE queue SET status='pending' WHERE status='running'")
        self.db.commit()

    def get(self, kind, key):
        r = self.db.execute("SELECT data FROM entities WHERE kind=? AND key=?", (kind, key)).fetchone()
        return json.loads(r[0]) if r else None

    def rows(self, kind):
        return [json.loads(r[0]) for r in self.db.execute(
            "SELECT data FROM entities WHERE kind=? ORDER BY key", (kind,))]

    def put(self, kind, key, data):
        self.db.execute("INSERT INTO entities VALUES(?,?,?) ON CONFLICT(kind,key) DO UPDATE SET data=excluded.data",
                        (kind, key, encode(data)))

    def ingest(self, bundle):
        for shop in bundle.get("shops", []):
            old = self.get("shop", shop["shop_id"]) or {}
            old.update({k: v for k, v in shop.items() if v is not None})
            self.put("shop", shop["shop_id"], old)
        for p in bundle.get("products", []):
            self.db.execute("INSERT INTO observations(product_key,data) VALUES(?,?)", (p["product_key"], encode(p)))
            old = self.get("product", p["product_key"]) or {}
            sources = old.get("field_observations", {})
            price_fields = {"price", "price_raw", "price_min", "price_max", "price_divisor",
                            "price_unit_status", "currency", "currency_assumed"}
            has_price = any(p.get(k) is not None for k in ("price", "price_min", "price_max"))
            if has_price:
                # Price values and units are one observation: do not mix JSON-LD units
                # with an older scaled API value.
                for k in price_fields:
                    old[k] = p.get(k)
                    sources[k] = {"at": p["observed_at"], "source": p["source_kind"]}
            for k, v in p.items():
                if k in price_fields:
                    continue
                if v is None or v == [] or v == {} or v == "":
                    continue
                if k == "category_path" and any(c.get("name") for c in old.get(k, [])) and not any(c.get("name") for c in v):
                    continue
                if k in {"record_level", "variant_capture_status", "classification_method"} and old.get(k):
                    if k == "record_level" and old[k] == "detail" and v != "detail":
                        continue
                    if k == "variant_capture_status" and v == "not_observed":
                        continue
                    if k == "classification_method" and old[k] == "shopee_category_observed" and v != old[k]:
                        continue
                old[k] = v
                sources[k] = {"at": p["observed_at"], "source": p["source_kind"]}
            old["field_observations"] = sources
            self.put("product", p["product_key"], old)
        for review in bundle.get("reviews", []):
            self.put("review", review["review_key"], review)
        self.db.commit()

    def enqueue(self, url, kind, now):
        self.db.execute("INSERT OR IGNORE INTO queue(url,kind,updated_at) VALUES(?,?,?)", (url, kind, now))
        self.db.commit()

    def next_job(self):
        r = self.db.execute("SELECT url,kind FROM queue WHERE status='pending' ORDER BY rowid LIMIT 1").fetchone()
        return {"url": r[0], "kind": r[1]} if r else None

    def status(self, url, status, now, error=None):
        self.db.execute("UPDATE queue SET status=?, updated_at=?, last_error=?, attempts=attempts+? WHERE url=?",
                        (status, now, error, int(status == "running"), url))
        self.db.commit()

    def event(self, now, kind, message):
        self.db.execute("INSERT INTO events(at,kind,message) VALUES(?,?,?)", (now, kind, message))
        self.db.commit()

    def retry(self):
        self.db.execute("UPDATE queue SET status='pending' WHERE status IN ('blocked','failed','no_data','partial')")
        self.db.commit()

    def report(self):
        products, reviews = self.rows("product"), self.rows("review")
        with_reviews = {r["product_key"] for r in reviews}
        missing = Counter()
        for p in products:
            for field in ("shop_name", "rating_average", "sold_total_reported", "variants"):
                val = p.get(field)
                if field == "shop_name" and not val:
                    val = (self.get("shop", p["shop_id"]) or {}).get("shop_name")
                if val is None or val == []:
                    missing[field] += 1
            if p["product_key"] not in with_reviews:
                missing["reviews_not_observed"] += 1
        return {
            "products_observed": len(products), "reviews_observed": len(reviews),
            "variants_observed": sum(len(p.get("variants") or []) for p in products),
            "queue_status": dict(self.db.execute("SELECT status,COUNT(*) FROM queue GROUP BY status").fetchall()),
            "missing_fields_count": dict(missing),
            "classification_methods": dict(Counter(p.get("classification_method", "unknown") for p in products)),
            "coverage": "unknown; discovered subset only; never whole-market coverage",
            "price_note": "browser-response units are configured assumptions; verify raw price against UI",
            "review_note": "only observed review pages; counts do not prove all reviews were collected",
            "merge_note": "latest nonempty fields merged; field_observations records each field freshness",
        }

    def export(self):
        products, reviews = self.rows("product"), self.rows("review")
        shops = {s["shop_id"]: s for s in self.rows("shop")}
        variants = []
        categories = {c["category_id"]: c for c in self.rows("category_link")}
        for p in products:
            s = shops.get(p["shop_id"], {})
            if not p.get("shop_name") and s.get("shop_name"):
                p["shop_name"] = s["shop_name"]
            shops.setdefault(p["shop_id"], {"shop_id": p["shop_id"], "shop_name": p.get("shop_name"),
                                            "shop_username": p.get("shop_username")})
            for v in p.get("variants") or []:
                variants.append({"product_key": p["product_key"], **v,
                                 "observed_at": p.get("field_observations", {}).get("variants", {}).get("at")})
            for c in p.get("category_path") or []:
                key = c.get("category_id") or c.get("name")
                previous = categories.get(key) or {}
                categories[key] = {**previous, **{k: v for k, v in c.items() if v is not None}}
        fields = {
            "products": ["product_key", "item_id", "shop_id", "url", "name", "description", "shop_name",
                         "shop_username", "currency", "currency_assumed", "price", "price_raw", "price_min",
                         "price_max", "price_divisor", "price_unit_status", "rating_average", "rating_count",
                         "sold_period_reported", "sold_total_reported", "sold_semantics", "category_path",
                         "suggested_category", "classification_method", "classification_evidence", "attributes",
                         "images", "variant_capture_status", "record_level", "source_kind", "source_url",
                         "observed_at", "field_observations"],
            "variants": ["product_key", "variant_id", "variant_name", "options", "price", "price_raw",
                         "price_divisor", "currency", "stock", "observed_at"],
            "reviews": ["review_key", "review_id", "product_key", "shop_id", "item_id", "rating", "content",
                        "created_at_raw", "variant_name", "observed_at", "source_url"],
            "shops": ["shop_id", "shop_name", "shop_username", "observed_at"],
            "categories": ["category_id", "name"],
        }
        datasets = {"products": products, "variants": variants, "reviews": reviews,
                    "shops": list(shops.values()), "categories": list(categories.values())}
        for name, data in datasets.items():
            with (self.folder / (name + ".csv")).open("w", encoding="utf-8-sig", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=fields[name], extrasaction="ignore")
                writer.writeheader()
                writer.writerows({k: csv_value(v) for k, v in row.items()} for row in data)
        with (self.folder / "products.jsonl").open("w", encoding="utf-8") as f:
            for p in products:
                f.write(encode(p) + "\n")
        report = self.report()
        (self.folder / "coverage_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        return report

    def close(self):
        self.db.close()
