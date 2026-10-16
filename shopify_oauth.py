"""
Version 3: connect a client store with Shopify's install screen
("authorization code grant").

  1. The owner adds the client store on the Stores page, with the Client ID +
     secret of the app made for that store in the Dev Dashboard.
  2. Someone with access to that store opens the install link (or the
     "Connect" button). Shopify shows what the app may do; they click Install.
  3. Shopify sends the browser back to /shopify/callback with a one-time code.
     We check Shopify's signature, swap the code for an access token and save
     it in stores.json.

Shopify docs: https://shopify.dev/docs/apps/build/authentication-authorization/access-tokens/authorization-code-grant
"""
import hashlib
import hmac
import json
import secrets
import time
from urllib.parse import urlencode

import requests

import config
import stores

STATE_SECONDS = 15 * 60
_states: dict[str, tuple[str, float]] = {}   # one-time "state" value -> (shop, expires)


class OAuthError(Exception):
    pass


def redirect_uri() -> str:
    return f"{config.PUBLIC_URL}/shopify/callback"


def app_url() -> str:
    return f"{config.PUBLIC_URL}/shopify/install"


def _keys(shop: str) -> dict:
    entry = stores.get(shop)
    if not entry or not entry.get("client_id"):
        raise OAuthError(f"{shop} hasn't been added on the Stores page.")
    return entry


def verify_hmac(params: dict, secret: str) -> bool:
    """Shopify signs the query string with the app's secret. Recreate the
    signature and compare (so nobody can fake a callback)."""
    given = params.get("hmac", "")

    def pairs():
        for key, value in params.items():
            if key in ("hmac", "signature"):
                continue
            if isinstance(value, list):
                value = json.dumps([str(v) for v in value])
            key = str(key).replace("%", "%25").replace("=", "%3D")
            value = str(value).replace("%", "%25")
            yield f"{key}={value}".replace("&", "%26")

    message = "&".join(sorted(pairs()))
    expected = hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()
    return bool(given) and hmac.compare_digest(expected, given)


def authorize_url(shop: str) -> str:
    """The Shopify page where the store approves the app."""
    keys = _keys(shop)
    now = time.time()
    for old in [s for s, (_, exp) in _states.items() if exp < now]:
        _states.pop(old, None)
    state = secrets.token_urlsafe(24)
    _states[state] = (shop, now + STATE_SECONDS)
    query = urlencode({"client_id": keys["client_id"], "scope": config.SHOPIFY_SCOPES,
                       "redirect_uri": redirect_uri(), "state": state})
    return f"https://{shop}/admin/oauth/authorize?{query}"


def check_signed_request(params: dict) -> str:
    """Validate a request Shopify sent to us. Returns the shop."""
    shop = params.get("shop", "")
    if not stores.SHOP_RE.fullmatch(shop):
        raise OAuthError("The store address in the link is not valid.")
    if not verify_hmac(params, _keys(shop)["client_secret"]):
        raise OAuthError("Shopify's signature didn't match. Check that the Client secret "
                         "on the Stores page belongs to this store's app.")
    return shop


def finish(params: dict) -> str:
    """Handle /shopify/callback: check everything, get and save the token."""
    shop = check_signed_request(params)
    saved = _states.pop(params.get("state", ""), None)
    if not saved or saved[0] != shop or saved[1] < time.time():
        raise OAuthError("This connect link has expired or was already used. "
                         "Start again from the Stores page.")
    keys = _keys(shop)
    resp = requests.post(f"https://{shop}/admin/oauth/access_token", timeout=30, data={
        "client_id": keys["client_id"], "client_secret": keys["client_secret"], "code": params.get("code", ""),
    })
    if not resp.ok:
        raise OAuthError(f"Shopify didn't give an access token ({resp.status_code}): {resp.text[:200]}")
    _save_token(shop, resp.json())
    stores.update(shop, connected_at=_now())

    missing = missing_scopes(stores.get(shop).get("scope", ""))
    if missing:
        raise OAuthError("Connected, but the store didn't allow: " + ", ".join(missing) +
                         ". Add these scopes to the app in the Dev Dashboard, release a new version, "
                         "and connect again.")
    return shop


def missing_scopes(granted: str) -> list[str]:
    have = {s.strip() for s in granted.split(",") if s.strip()}
    # write_x includes read_x
    return [s for s in config.SHOPIFY_SCOPES.split(",")
            if s.strip() and s.strip() not in have and s.strip().replace("read_", "write_") not in have]


def _save_token(shop: str, data: dict) -> None:
    if "access_token" not in data:
        raise OAuthError(f"Unexpected answer from Shopify: {str(data)[:200]}")
    expires_in = data.get("expires_in")
    stores.update(shop, access_token=data["access_token"], scope=data.get("scope", ""),
                  refresh_token=data.get("refresh_token", ""),
                  expires_at=time.time() + int(expires_in) if expires_in else 0)


def access_token(shop: str) -> str:
    """The saved token of a client store (refreshed first if it expires soon)."""
    entry = _keys(shop)
    if not entry.get("access_token"):
        raise OAuthError(f"{shop} isn't connected yet. Open the Stores page and click Connect.")
    expires_at = entry.get("expires_at") or 0
    if expires_at and time.time() > expires_at - 120:
        if not entry.get("refresh_token"):
            raise OAuthError(f"The connection to {shop} expired. Connect it again on the Stores page.")
        resp = requests.post(f"https://{shop}/admin/oauth/access_token", timeout=30, data={
            "client_id": entry["client_id"], "client_secret": entry["client_secret"],
            "grant_type": "refresh_token", "refresh_token": entry["refresh_token"],
        })
        if resp.status_code in (400, 401):
            stores.update(shop, access_token="", refresh_token="", expires_at=0)
            raise OAuthError(f"The connection to {shop} expired. Connect it again on the Stores page.")
        if not resp.ok:
            raise OAuthError(f"Couldn't refresh the connection to {shop} ({resp.status_code}). Try again.")
        _save_token(shop, resp.json())
        entry = stores.get(shop)
    return entry["access_token"]


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")
