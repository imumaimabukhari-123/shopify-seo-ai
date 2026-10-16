"""
Version 3: SEO audit & score.

Checks every product and collection against simple SEO rules and gives each
one a score out of 100 (100 minus the penalties of its problems). The store
score is the average. No AI is used, so an audit is free and fast.

Two ways to run it:
  run_full()       the connected store, through the Admin API. Sees everything,
                   including custom meta titles/descriptions. Saved to audit.json.
  run_public(url)  ANY Shopify store, from its public pages only (no access
                   needed): /products.json plus a few product pages for the
                   meta tags. Used for the free audit on the website.
"""
import ipaddress
import json
import os
import re
import socket
from datetime import datetime
from html import unescape
from urllib.parse import urljoin, urlparse

import requests

import ai_generator
import config
import stores

# id: (category, severity, penalty, what is wrong, how to fix, can this tool fix it?)
CHECKS = {
    "desc_missing":      ("Descriptions", "high", 30, "No description",
                          "Write a description of at least 100 words.", True),
    "desc_thin":         ("Descriptions", "medium", 15, "Description is short ({words} words)",
                          "Aim for 100+ words: what it is, who it's for, key features.", True),
    "meta_title_missing": ("Meta titles", "medium", 15, "No custom meta title",
                          "Google shows the plain name instead. Add a meta title with a keyword.", True),
    "meta_title_long":   ("Meta titles", "low", 8, "Meta title is too long ({length} characters)",
                          "Keep it under 60 characters so Google doesn't cut it off.", True),
    "meta_title_short":  ("Meta titles", "low", 5, "Meta title is very short ({length} characters)",
                          "Use 30-60 characters and include what the item is.", True),
    "meta_title_dup":    ("Meta titles", "medium", 10, "Same meta title as another page",
                          "Every page needs its own meta title.", True),
    "meta_desc_missing": ("Meta descriptions", "high", 20, "No meta description",
                          "Google picks random text from the page. Write 70-160 characters.", True),
    "meta_desc_long":    ("Meta descriptions", "low", 8, "Meta description is too long ({length} characters)",
                          "Keep it under 160 characters.", True),
    "meta_desc_short":   ("Meta descriptions", "low", 5, "Meta description is very short ({length} characters)",
                          "Use 70-160 characters with a reason to click.", True),
    "img_none":          ("Image alt text", "medium", 15, "No product photos",
                          "Add at least one photo.", False),
    "img_alt_missing":   ("Image alt text", "high", 20, "{missing} of {total} photos have no alt text",
                          "Describe each photo, for Google Images and screen readers.", True),
    "img_alt_long":      ("Image alt text", "low", 3, "Alt text is too long on {count} photo(s)",
                          "Keep alt text under 125 characters.", True),
    "title_long":        ("Descriptions", "low", 5, "Name is very long ({length} characters)",
                          "Shorter names are easier to read in search results.", False),
    "collection_empty":  ("Descriptions", "low", 5, "Collection has no products",
                          "Add products or hide the collection.", False),
}
# How a problem is named in "top problems" (where it is counted over many pages)
SUMMARY = {
    "desc_thin": "Description is short", "meta_title_long": "Meta title is too long",
    "meta_title_short": "Meta title is very short", "meta_desc_long": "Meta description is too long",
    "meta_desc_short": "Meta description is very short", "img_alt_missing": "Photos without alt text",
    "img_alt_long": "Alt text is too long", "title_long": "Name is very long",
}
CATEGORIES = ["Meta titles", "Meta descriptions", "Descriptions", "Image alt text"]
SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}


def path() -> str:
    return stores.data_file(config.AUDIT_JSON_PATH)


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _issue(check: str, **values) -> dict:
    category, severity, penalty, message, fix, auto = CHECKS[check]
    return {"check": check, "category": category, "severity": severity, "penalty": penalty,
            "message": message.format(**values), "fix": fix, "auto_fix": auto}


# ---------------------------------------------------------------------------
# The rules
# ---------------------------------------------------------------------------
def check_item(item: dict) -> list[dict]:
    """item = {kind, title, description_html, meta_title, meta_description, images,
    products_count}. A value of None means "unknown" and that rule is skipped."""
    issues = []
    words = len(ai_generator.strip_html(item.get("description_html") or "").split())
    min_words = 30 if item["kind"] == "collection" else 50
    if item.get("description_html") is not None:
        if words == 0:
            issues.append(_issue("desc_missing"))
        elif words < min_words:
            issues.append(_issue("desc_thin", words=words))

    meta_title = item.get("meta_title")
    if meta_title is not None:
        length = len(meta_title.strip())
        if not length:
            issues.append(_issue("meta_title_missing"))
        elif length > 60:
            issues.append(_issue("meta_title_long", length=length))
        elif length < 30:
            issues.append(_issue("meta_title_short", length=length))

    meta_desc = item.get("meta_description")
    if meta_desc is not None:
        length = len(meta_desc.strip())
        if not length:
            issues.append(_issue("meta_desc_missing"))
        elif length > 160:
            issues.append(_issue("meta_desc_long", length=length))
        elif length < 70:
            issues.append(_issue("meta_desc_short", length=length))

    images = item.get("images")
    if images is not None and item["kind"] == "product":
        if not images:
            issues.append(_issue("img_none"))
        else:
            missing = sum(1 for alt in images if not alt.strip())
            if missing:
                issues.append(_issue("img_alt_missing", missing=missing, total=len(images)))
            too_long = sum(1 for alt in images if len(alt) > 125)
            if too_long:
                issues.append(_issue("img_alt_long", count=too_long))

    if len(item["title"]) > 70:
        issues.append(_issue("title_long", length=len(item["title"])))
    if item["kind"] == "collection" and item.get("products_count") == 0:
        issues.append(_issue("collection_empty"))
    return issues


def build_report(items: list[dict], store: str, source: str, notes: list[str]) -> dict:
    """Score every item, then the store. Returns the report (a plain dict)."""
    # Duplicate meta titles can only be seen across all items
    seen: dict[str, int] = {}
    for item in items:
        mt = (item.get("meta_title") or "").strip().lower()
        if mt:
            seen[mt] = seen.get(mt, 0) + 1

    for item in items:
        item["issues"] = check_item(item)
        mt = (item.get("meta_title") or "").strip().lower()
        if mt and seen[mt] > 1:
            item["issues"].append(_issue("meta_title_dup"))
        item["issues"].sort(key=lambda i: SEVERITY_ORDER[i["severity"]])
        item["score"] = max(0, 100 - sum(i["penalty"] for i in item["issues"]))

    scored = [i for i in items if i["kind"] != "home"] or items
    score = round(sum(i["score"] for i in scored) / len(scored)) if scored else 0

    categories = {}
    for cat in CATEGORIES:
        relevant = [i for i in items if _checks_category(i, cat)]
        if relevant:
            ok = sum(1 for i in relevant if not any(x["category"] == cat for x in i["issues"]))
            categories[cat] = {"score": round(100 * ok / len(relevant)), "passed": ok, "total": len(relevant)}

    # The same problem on many pages = one line in "top problems"
    top: dict[str, dict] = {}
    for item in items:
        for issue in item["issues"]:
            entry = top.setdefault(issue["check"], {
                "check": issue["check"], "category": issue["category"], "severity": issue["severity"],
                "message": SUMMARY.get(issue["check"], CHECKS[issue["check"]][3]),
                "fix": issue["fix"], "auto_fix": issue["auto_fix"], "count": 0, "points": 0,
            })
            entry["count"] += 1
            entry["points"] += issue["penalty"]
    top_list = sorted(top.values(), key=lambda t: (SEVERITY_ORDER[t["severity"]], -t["points"]))

    for item in items:
        item["photos"] = len(item["images"]) if item.get("images") is not None else None
        for heavy in ("description_html", "images"):   # keep the saved file small
            item.pop(heavy, None)

    return {
        "store": store, "source": source, "ran_at": _now(), "score": score,
        "grade": _grade(score), "categories": categories, "top_issues": top_list,
        "counts": {k: sum(1 for i in items if i["kind"] == k) for k in ("product", "collection")},
        "notes": notes, "items": sorted(items, key=lambda i: i["score"]),
    }


def _checks_category(item: dict, category: str) -> bool:
    """Was this item checked for this category at all (not "unknown")?"""
    if category == "Meta titles":
        return item.get("meta_title") is not None
    if category == "Meta descriptions":
        return item.get("meta_description") is not None
    if category == "Image alt text":
        return item["kind"] == "product" and item.get("images") is not None
    return item["kind"] != "home"


def _grade(score: int) -> str:
    return "Great" if score >= 90 else "Good" if score >= 75 else "Needs work" if score >= 50 else "Poor"


# ---------------------------------------------------------------------------
# Full audit (connected store, Admin API)
# ---------------------------------------------------------------------------
def run_full(progress=print, table: str = "audit") -> dict:
    from shopify_client import ShopifyClient   # imported here so the public audit works without Shopify settings

    shop = ShopifyClient()
    progress("Reading products from Shopify...")
    products = shop.fetch_products_for_audit()
    hidden = [p for p in products if p["status"] != "ACTIVE"]
    progress("Reading collections from Shopify...")
    collections = shop.fetch_all_collections()

    items = []
    for p in products:
        if p["status"] != "ACTIVE":
            continue   # drafts and archived products aren't on Google
        seo = p.get("seo") or {}
        items.append({
            "kind": "product", "key": p["id"], "title": p["title"], "handle": p["handle"],
            "url": f"/products/{p['handle']}",
            "description_html": p.get("descriptionHtml") or "",
            "meta_title": seo.get("title") or "", "meta_description": seo.get("description") or "",
            "images": [" ".join((m.get("alt") or "").split()) for m in p["media"]["nodes"]
                       if m["mediaContentType"] == "IMAGE"],
        })
    for c in collections:
        seo = c.get("seo") or {}
        items.append({
            "kind": "collection", "key": c["id"], "title": c["title"], "handle": c["handle"],
            "url": f"/collections/{c['handle']}",
            "description_html": c.get("descriptionHtml") or "",
            "meta_title": seo.get("title") or "", "meta_description": seo.get("description") or "",
            "products_count": (c.get("productsCount") or {}).get("count"),
        })

    notes = []
    if hidden:
        notes.append(f"{len(hidden)} draft or archived product(s) were skipped (Google can't see them).")
    report = build_report(items, stores.current(), "full", notes)
    with open(path(), "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    progress(f"Done. Store score: {report['score']}/100 ({report['grade']}). "
             f"{sum(t['count'] for t in report['top_issues'])} problems found.")
    return report


def last_report() -> dict | None:
    if not os.path.exists(path()):
        return None
    with open(path(), encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Free public audit (any store, public pages only)
# ---------------------------------------------------------------------------
class AuditError(Exception):
    pass


MAX_BYTES = 4_000_000
PAGES_TO_SAMPLE = 6
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; ShopifySEOAudit/1.0)"}


def normalize_store_url(text: str) -> str:
    """'mystore.com', 'https://mystore.com/products/x' -> 'https://mystore.com'."""
    text = (text or "").strip()
    if not text:
        raise AuditError("Please type your store's web address.")
    if "://" not in text:
        text = "https://" + text
    parsed = urlparse(text)
    host = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme not in ("http", "https") or not re.fullmatch(r"[a-z0-9.-]+\.[a-z]{2,}", host):
        raise AuditError("That doesn't look like a web address. Try something like mystore.com")
    if parsed.port not in (None, 80, 443):
        raise AuditError("Please use the normal store address, without a port number.")
    return f"https://{host}"


def _check_public_host(host: str) -> None:
    """Refuse addresses inside a private network (so the audit can't be used
    to poke at the server's own network)."""
    try:
        addresses = {info[4][0] for info in socket.getaddrinfo(host, 443)}
    except socket.gaierror:
        raise AuditError(f"Couldn't find {host}. Check the spelling.")
    for addr in addresses:
        ip = ipaddress.ip_address(addr)
        if not ip.is_global:
            raise AuditError("That address isn't a public website.")


def _get(url: str, want_json: bool = False):
    """GET a public page: checks every redirect, limits the size and time."""
    for _ in range(5):
        host = urlparse(url).hostname or ""
        if urlparse(url).scheme not in ("http", "https"):
            raise AuditError("The store redirected somewhere unexpected.")
        _check_public_host(host)
        try:
            resp = requests.get(url, headers=HEADERS, timeout=12, allow_redirects=False, stream=True)
        except requests.RequestException:
            raise AuditError(f"Couldn't reach {host}. Is the store online?")
        if resp.is_redirect:
            url = urljoin(url, resp.headers.get("Location", ""))
            resp.close()
            continue
        body = b""
        for chunk in resp.iter_content(65536):
            body += chunk
            if len(body) > MAX_BYTES:
                resp.close()
                raise AuditError("The store's page is too big to check.")
        if want_json:
            try:
                return resp.status_code, json.loads(body), url
            except ValueError:
                return resp.status_code, None, url
        charset = resp.encoding if "charset" in resp.headers.get("Content-Type", "").lower() else "utf-8"
        return resp.status_code, body.decode(charset or "utf-8", errors="replace"), url
    raise AuditError("The store redirected too many times.")


def _meta(page: str, attr: str, name: str) -> str:
    """content="..." of <meta {attr}="{name}">, in either attribute order."""
    m = (re.search(rf'<meta[^>]+{attr}=["\']{name}["\'][^>]*content=(["\'])(.*?)\1', page, re.I | re.S)
         or re.search(rf'<meta[^>]+content=(["\'])(.*?)\1[^>]*{attr}=["\']{name}["\']', page, re.I | re.S))
    return " ".join(unescape(m.group(2)).split()) if m else ""


def _meta_tags(page: str) -> tuple[str, str]:
    """(title, meta description) from a page's HTML."""
    title = re.search(r"<title[^>]*>(.*?)</title>", page, re.I | re.S)
    return (" ".join(unescape(title.group(1)).split()) if title else ""), _meta(page, "name", "description")


def run_public(store_url: str) -> dict:
    base = normalize_store_url(store_url)
    status, data, final_url = _get(base + "/products.json?limit=250", want_json=True)
    final = urlparse(final_url)
    if "/password" in final.path:
        raise AuditError("This store is password-protected, so its pages aren't public yet.")
    if status != 200 or not isinstance(data, dict) or "products" not in data:
        raise AuditError("This doesn't look like a Shopify store (or it hides its product list). "
                         "The full audit can still check it once the store is connected.")
    base = f"https://{final.hostname}"
    products = data["products"]
    if not products:
        raise AuditError("This store has no public products to check.")

    # Home page meta tags
    _, home_html, _ = _get(base + "/")
    home_title, home_desc = _meta_tags(home_html)
    site_name = _meta(home_html, "property", "og:site_name")
    items = []
    if home_title or home_desc:
        items.append({"kind": "home", "key": "home", "title": "Home page", "handle": "", "url": "/",
                      "meta_title": home_title, "meta_description": home_desc})

    # A few product pages, for the meta title/description Google really sees
    sampled = {}
    for p in products[:PAGES_TO_SAMPLE]:
        try:
            code, page, _ = _get(f"{base}/products/{p['handle']}")
            tags = _meta_tags(page)
            if code == 200 and any(tags):   # no <title> at all = a bot-check page, not the real one
                sampled[p["handle"]] = tags
        except AuditError:
            pass

    for p in products:
        meta = sampled.get(p["handle"])
        items.append({
            "kind": "product", "key": str(p.get("id")), "title": p.get("title") or "",
            "handle": p.get("handle") or "", "url": f"/products/{p.get('handle')}",
            "description_html": p.get("body_html") or "",
            "meta_title": _strip_store_name(meta[0], site_name) if meta else None,
            "meta_description": meta[1] if meta else None,
            "images": [" ".join((img.get("alt") or "").split()) for img in p.get("images") or []],
        })

    notes = [f"Checked {len(products)} product(s) from the public product list"
             + (" (the first 250)." if len(products) >= 250 else "."),
             (f"Meta titles and descriptions were checked on {len(sampled)} product page(s)"
              + (" and the home page." if items[0]["kind"] == "home" else ".")
              if sampled or items[0]["kind"] == "home" else
              "The store's pages block automatic checks, so meta titles and descriptions weren't checked."),
             "A full audit (after connecting the store) also checks collections and every meta tag."]
    report = build_report(items, final.hostname, "public", notes)
    return report


def _strip_store_name(title: str, site_name: str) -> str:
    """Themes add ' – Store Name' to every page title; judge only the page part."""
    if site_name:
        m = re.fullmatch(rf"(.*?)\s*[–—|:-]\s*{re.escape(site_name)}", title, re.I)
        if m and m.group(1):
            return m.group(1)
    return title
