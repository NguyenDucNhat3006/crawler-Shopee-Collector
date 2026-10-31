from __future__ import annotations

import re
import unicodedata
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit, urljoin


def clean(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def fold(value):
    value = unicodedata.normalize("NFD", clean(value).lower().replace("đ", "d"))
    return "".join(c for c in value if unicodedata.category(c) != "Mn")


def ident(value):
    s = str(value) if value is not None else ""
    return s if re.fullmatch(r"[0-9]+", s) and int(s) > 0 else None


def canonical(url, base="https://shopee.vn/"):
    """Accept only Shopee VN URLs; strip tracking from product URLs."""
    p = urlsplit(urljoin(base, url))
    if p.scheme != "https" or p.hostname not in {"shopee.vn", "www.shopee.vn"}:
        return None
    if p.username or p.password or p.port not in (None, 443):
        return None
    m = re.search(r"(?:-i\.|/product/)(\d+)[./](\d+)(?:/|$)", p.path)
    if m:
        return f"https://shopee.vn/product/{m[1]}/{m[2]}"
    if any(s in p.path.lower() for s in ("/buyer", "/user", "/verify", "/cart", "/checkout")):
        return None
    return "https://shopee.vn" + (p.path or "/") + ("?" + p.query if p.query else "")


def product_ids(url):
    url = canonical(url)
    if not url:
        return None
    m = re.fullmatch(r"https://shopee.vn/product/(\d+)/(\d+)", url)
    return (m[1], m[2]) if m else None


def price(value, divisor):
    """Keep zero, reject negative/sentinel/NaN. Never guess units by magnitude."""
    if value is None or isinstance(value, bool):
        return None
    try:
        d, scale = Decimal(str(value)), Decimal(str(divisor))
        if not d.is_finite() or not scale.is_finite() or d < 0 or scale <= 0:
            return None
        result = format(d / scale, "f")
        return result.rstrip("0").rstrip(".") if "." in result else result
    except (InvalidOperation, ValueError, TypeError):
        return None


def integer(value):
    if isinstance(value, bool):
        return None
    try:
        d = Decimal(str(value))
        return int(d) if d.is_finite() and d >= 0 and d == int(d) else None
    except (InvalidOperation, ValueError, TypeError, OverflowError):
        return None


def suggested_category(name, rules):
    text = " " + fold(name) + " "
    hits = []
    for rule in rules:
        matched = [w for w in rule["keywords"] if " " + fold(w) + " " in text]
        if matched:
            hits.append((len(matched), rule["label"], matched))
    if not hits:
        return {"suggested_category": "Chưa phân loại", "classification_method": "unclassified"}
    hits.sort(key=lambda h: (-h[0], h[1]))
    if len(hits) > 1 and hits[0][0] == hits[1][0]:
        return {"suggested_category": "Cần kiểm tra", "classification_method": "ambiguous_keyword"}
    return {"suggested_category": hits[0][1], "classification_method": "keyword_heuristic",
            "classification_evidence": ", ".join(hits[0][2])}


def walk(value, depth=0):
    if depth > 35:
        return
    if isinstance(value, dict):
        yield value
        for v in value.values():
            yield from walk(v, depth + 1)
    elif isinstance(value, list):
        for v in value:
            yield from walk(v, depth + 1)


def categories_from(item):
    result = []
    for c in item.get("categories") or item.get("category_path") or []:
        if isinstance(c, dict):
            cid, name = c.get("catid", c.get("category_id")), c.get("display_name") or c.get("name")
            if name or cid:
                result.append({"category_id": str(cid) if cid is not None else None,
                               "name": clean(name) or None})
        elif isinstance(c, str):
            result.append({"category_id": None, "name": c})
    if not result and item.get("catid") is not None:
        result = [{"category_id": str(item["catid"]), "name": None}]
    return result


def normalize_item(item, divisor, rules, source_url, observed_at):
    sid, iid = ident(item.get("shopid")), ident(item.get("itemid"))
    if not sid or not iid or not clean(item.get("name")):
        return None
    tiers = item.get("tier_variations") or []
    models = item.get("models")
    variants = []
    if isinstance(models, list):
        for m in models:
            if not isinstance(m, dict):
                continue
            options = {}
            for index, option_index in enumerate((m.get("extinfo") or {}).get("tier_index") or []):
                if index < len(tiers) and isinstance(tiers[index], dict):
                    tier = tiers[index]
                    opts = tier.get("options") or []
                    if isinstance(option_index, int) and 0 <= option_index < len(opts):
                        options[clean(tier.get("name")) or f"tier_{index}"] = opts[option_index]
            variant_price = m.get("price")
            if variant_price is None:
                variant_price = (m.get("price_info") or {}).get("price")
            variants.append({"variant_id": ident(m.get("modelid")), "variant_name": clean(m.get("name")),
                             "options": options, "price_raw": variant_price,
                             "price": price(variant_price, divisor),
                             "stock": integer(m.get("stock")),
                             "currency": item.get("currency") or "VND",
                             "price_divisor": divisor})
    rating = item.get("item_rating") or {}
    shop = item.get("shop_info") or item.get("shop") or {}
    cats = categories_from(item)
    attr = item.get("attributes") or []
    attr = [{"name": a.get("name"), "value": a.get("value") or a.get("values")}
            for a in attr if isinstance(a, dict)]
    product = {
        "product_key": f"{sid}:{iid}", "shop_id": sid, "item_id": iid,
        "url": f"https://shopee.vn/product/{sid}/{iid}", "name": clean(item.get("name")),
        "description": item.get("description"), "currency": item.get("currency") or "VND",
        "currency_assumed": not bool(item.get("currency")),
        "price_raw": item.get("price"), "price": price(item.get("price"), divisor),
        "price_min": price(item.get("price_min"), divisor),
        "price_max": price(item.get("price_max"), divisor),
        "price_divisor": divisor, "price_unit_status": "configured_adapter_assumption",
        "shop_name": clean(shop.get("name") or item.get("shop_name")) or None,
        "shop_username": clean(shop.get("username")) or None,
        "rating_average": rating.get("rating_star"),
        "rating_count": (rating.get("rating_count") or [None])[0] if isinstance(rating.get("rating_count"), list)
                        else integer(rating.get("rating_count")),
        "sold_period_reported": integer(item.get("sold")),
        "sold_total_reported": integer(item.get("historical_sold")),
        "sold_semantics": "upstream_fields_unverified; do not equate period and total",
        "category_path": cats, "attributes": attr, "images": item.get("images") or [],
        "variants": variants,
        "variant_capture_status": "models_observed" if variants else "not_observed",
        "record_level": "detail" if any(k in item for k in ("description", "models", "tier_variations", "attributes")) else "listing",
        "source_kind": "browser_response", "source_url": source_url,
        "observed_at": observed_at,
    }
    product.update(suggested_category(product["name"], rules))
    if any(c.get("name") for c in cats):
        product["classification_method"] = "shopee_category_observed"
    return product


def extract_payload(payload, *, divisor=100000, rules=(), source_url="", observed_at="", review_product=None):
    """Adapter for *observed* JSON. Shapes are provisional, not official API contracts."""
    products, reviews, shops = [], [], []
    for node in walk(payload):
        p = normalize_item(node, divisor, rules, source_url, observed_at)
        if p:
            products.append(p)
        sid = ident(node.get("shopid"))
        if sid and not node.get("itemid") and (node.get("username") or node.get("shop_name")):
            shops.append({"shop_id": sid, "shop_name": clean(node.get("shop_name") or node.get("name")) or None,
                          "shop_username": clean(node.get("username")) or None, "observed_at": observed_at})
        # An unrelated response must never be assigned to the current product.
        # Fallback identity is provided only for a matching ratings request.
        if "rating_star" in node and ("cmtid" in node or "comment" in node):
            iid = ident(node.get("itemid"))
            review_sid = ident(node.get("shopid"))
            if review_product:
                if iid and iid != review_product[1] or review_sid and review_sid != review_product[0]:
                    continue
                iid, review_sid = iid or review_product[1], review_sid or review_product[0]
            if not iid or not review_sid:
                continue
            rid = ident(node.get("cmtid"))
            text = str(node.get("comment") or "")
            # Without a stable source ID, do not merge potentially distinct same-text reviews.
            if not rid:
                continue
            reviews.append({"review_key": f"{review_sid}:{iid}:{rid}", "review_id": rid,
                            "product_key": f"{review_sid}:{iid}", "shop_id": review_sid, "item_id": iid,
                            "rating": node.get("rating_star"), "content": text,
                            "created_at_raw": node.get("ctime"), "variant_name": node.get("product_items"),
                            "observed_at": observed_at, "source_url": source_url})
    return {"products": products, "reviews": reviews, "shops": shops}


def jsonld_products(payload, url, observed_at, rules=()):
    ids = product_ids(url)
    if not ids:
        return []
    result = []
    for node in walk(payload):
        types = node.get("@type") or []
        if isinstance(types, str):
            types = [types]
        if "Product" not in types or not node.get("name"):
            continue
        # Refuse embedded recommendations whose URL explicitly points elsewhere.
        if node.get("url") and product_ids(node["url"]) != ids:
            continue
        offer = node.get("offers") or {}
        offer = offer[0] if isinstance(offer, list) and offer else offer
        if not isinstance(offer, dict):
            offer = {}
        aggregate = node.get("aggregateRating") or {}
        p = normalize_item({"shopid": ids[0], "itemid": ids[1], "name": node["name"],
                            "description": node.get("description"), "currency": offer.get("priceCurrency"),
                            "price": offer.get("price"), "price_min": offer.get("lowPrice"),
                            "price_max": offer.get("highPrice"),
                            "item_rating": {"rating_star": aggregate.get("ratingValue"),
                                            "rating_count": aggregate.get("ratingCount", aggregate.get("reviewCount"))}},
                           1, rules, url, observed_at)
        p.update(source_kind="jsonld", price_unit_status="jsonld_currency_units",
                 variant_capture_status="not_observed", record_level="jsonld")
        result.append(p)
    return result
