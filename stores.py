"""
Version 3: more than one Shopify store.

  - The "main" store is the one in .env (SHOPIFY_STORE_DOMAIN). It is the demo
    store, and its files stay in this folder as before.
  - Client stores are connected through Shopify's install screen (see
    shopify_oauth.py). Each one is saved in stores.json with its own app keys
    and access token, and keeps its files in stores/<store-name>/.

The owner picks the "active" store on the Stores page; every page and job then
works on that store. Visitors of the public demo always see the main store.
"""
import json
import os
import re
from contextvars import ContextVar
from datetime import datetime

import config

SHOP_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9\-]*\.myshopify\.com$")

# The store this request / job works on (set by app.py for each request)
_current: ContextVar[str | None] = ContextVar("store", default=None)


class StoreError(Exception):
    pass


def normalize_shop(text: str) -> str:
    """'https://My-Shop.myshopify.com/admin' or 'my-shop' -> 'my-shop.myshopify.com'."""
    shop = (text or "").strip().lower()
    shop = re.sub(r"^https?://", "", shop).split("/")[0]
    if shop and "." not in shop:
        shop += ".myshopify.com"
    if not SHOP_RE.fullmatch(shop):
        raise StoreError("Use the store's .myshopify.com address, e.g. client-shop.myshopify.com "
                         "(in Shopify admin: Settings → Domains).")
    return shop


# ---------------------------------------------------------------------------
# stores.json
# ---------------------------------------------------------------------------
def _load() -> dict:
    if not os.path.exists(config.STORES_FILE):
        return {"active": "", "stores": {}}
    with open(config.STORES_FILE, encoding="utf-8") as f:
        data = json.load(f)
    data.setdefault("active", "")
    data.setdefault("stores", {})
    return data


def _save(data: dict) -> None:
    tmp = config.STORES_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)
    os.replace(tmp, config.STORES_FILE)


def get(shop: str) -> dict | None:
    """A client store's saved settings (None for the main store)."""
    return _load()["stores"].get(shop)


def update(shop: str, **fields) -> dict:
    data = _load()
    entry = data["stores"].setdefault(shop, {})
    entry.update(fields)
    _save(data)
    return entry


def add(shop: str, client_id: str, client_secret: str, label: str) -> None:
    shop = normalize_shop(shop)
    if shop == main_domain():
        raise StoreError("That's the main store from your .env file - it's already connected.")
    if not client_id.strip() or not client_secret.strip():
        raise StoreError("Paste the Client ID and the Client secret of the app you made for this store.")
    old = get(shop) or {}
    update(shop, label=label.strip() or shop.split(".")[0], client_id=client_id.strip(),
           client_secret=client_secret.strip(), added_at=old.get("added_at") or _now(),
           # new keys = connect again
           **({} if old.get("client_id") == client_id.strip() else
              {"access_token": "", "refresh_token": "", "expires_at": 0, "scope": "", "connected_at": ""}))


def remove(shop: str) -> None:
    """Forget a client store's keys and token. Its files (CSVs, backups) stay on disk."""
    data = _load()
    data["stores"].pop(shop, None)
    if data["active"] == shop:
        data["active"] = ""
    _save(data)


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------------------
# Which store are we working on?
# ---------------------------------------------------------------------------
def main_domain() -> str:
    return config.SHOPIFY_STORE_DOMAIN


def is_connected(shop: str) -> bool:
    entry = get(shop)
    return bool(entry and entry.get("access_token"))


def active_domain() -> str:
    """The store the owner picked (falls back to the main store)."""
    active = _load()["active"]
    return active if active and is_connected(active) else main_domain()


def set_active(shop: str) -> None:
    if shop != main_domain() and not is_connected(shop):
        raise StoreError("Connect this store first.")
    data = _load()
    data["active"] = "" if shop == main_domain() else shop
    _save(data)


def current() -> str:
    return _current.get() or active_domain()


def use(shop: str):
    """Work on this store for the rest of the request/job. Returns a token for reset()."""
    return _current.set(shop)


def reset(token) -> None:
    _current.reset(token)


def data_file(filename: str) -> str:
    """Where the current store keeps a file (CSV, backups, audit)."""
    if config.DEMO_ONLY:
        return os.path.join(config.DEMO_DATA_DIR, os.path.basename(filename))
    shop = current()
    if not shop or shop == main_domain():
        return filename
    folder = os.path.join(config.STORES_DIR, shop.replace(".myshopify.com", ""))
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, os.path.basename(filename))


def list_all() -> list[dict]:
    """For the Stores page (never includes secrets or tokens)."""
    data = _load()
    active = active_domain()
    rows = [{"shop": main_domain(), "label": "Main store (from .env)", "main": True,
             "connected": True, "active": active == main_domain()}] if main_domain() else []
    for shop, entry in data["stores"].items():
        rows.append({
            "shop": shop, "label": entry.get("label") or shop, "main": False,
            "connected": bool(entry.get("access_token")), "active": shop == active,
            "connected_at": entry.get("connected_at", ""), "added_at": entry.get("added_at", ""),
            "scope": entry.get("scope", ""),
        })
    return rows
