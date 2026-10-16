"""
Website (FastAPI):
  /        Home: about the project, Version 1 and Version 2, the developer
  /guide   How to use the system (for beginners)
  /chat    Chatbot that runs the tool from plain-English messages
  /review  Review dashboard with tabs: Products, Collections, Image alt text
  /audit   SEO audit & score: full audit of the store + free audit of any store (V3)
  /stores  Connect client stores and pick which store to work on (owner, V3)
  /shopify/install, /shopify/callback   where Shopify sends a client store during connecting
  /login   Owner login (only needed when the website is online)

Run:   python app.py
Open:  http://127.0.0.1:8001

Who can do what is decided in auth.py: the owner can do everything; visitors
get a read-only demo (when PUBLIC_DEMO=true).

Long jobs (generate / check / push) run in a background thread; the pages poll
/api/job to show progress. Only one job runs at a time.

API (where {table} is products, collections or images):
  GET  /api/{table}/rows             all rows of that CSV              (viewer)
  POST /api/{table}/update           edit / approve one row             (owner)
  POST /api/{table}/generate         start an AI job                    (owner)
  POST /api/{table}/check            start the guess checker            (owner)
  POST /api/{table}/remove_guesses   delete flagged guesses from a row  (owner)
  POST /api/{table}/push             publish approved rows / dry run    (owner)
  POST /api/{table}/undo             put the original back on Shopify   (owner, V3)
  GET  /api/{table}/backups          which rows have a backup           (viewer, V3)
  GET  /download/{table}             download the CSV                   (viewer)
  GET  /download/backups             download backups.csv               (owner, V3)
  GET  /api/audit                    last full audit report             (viewer, V3)
  POST /api/audit/run                start a full audit                 (owner, V3)
  POST /api/audit/public             free audit of any store URL        (viewer, rate limited, V3)
  POST /api/chat                     chatbot (visitors: questions only, rate limited)
  GET  /api/me, POST /api/login, POST /api/logout
"""
import contextvars
import html
import os
import sys
import threading
from typing import Literal

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import audit
import auth
import backups
import chatbot
import config
import csv_store
import pipeline
import shopify_oauth
import stores

# The Windows console can't show some symbols (like ⚠) - replace them instead of crashing
try:
    sys.stdout.reconfigure(errors="replace")
except AttributeError:
    pass  # some hosts replace stdout with an object that can't be reconfigured

app = FastAPI(title="Shopify AI SEO", docs_url=None, redoc_url=None, openapi_url=None)
HERE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(HERE, "static")
app.mount("/static", StaticFiles(directory=STATIC), name="static")

Table = Literal["products", "collections", "images"]
OWNER = [Depends(auth.require_owner)]
VIEWER = [Depends(auth.require_viewer)]


@app.middleware("http")
async def pick_store(request: Request, call_next):
    """Version 3: the owner works on the store picked on the Stores page;
    visitors always see the main (demo) store, never a client's data."""
    token = stores.use(stores.active_domain() if auth.is_owner(request) else stores.main_domain())
    try:
        return await call_next(request)
    finally:
        stores.reset(token)


# ---------- background job state ----------
job = {"running": False, "kind": None, "table": None, "log": [], "error": None, "store": None}
job_lock = threading.Lock()


def _log(message: str) -> None:
    print(message)
    job["log"].append(message)


def start_job(kind: str, table: str, func, **kwargs) -> None:
    """Run func(progress=..., table=..., **kwargs) in a background thread,
    on the store this request works on."""
    with job_lock:
        if job["running"]:
            raise HTTPException(409, "A job is already running - wait for it to finish.")
        job.update(running=True, kind=kind, table=table, log=[], error=None, store=stores.current())

    def runner():
        try:
            func(progress=_log, table=table, **kwargs)
        except Exception as e:
            job["error"] = str(e)
            _log(f"ERROR: {e}")
        finally:
            job["running"] = False

    # copy_context: the thread keeps working on the same store
    threading.Thread(target=contextvars.copy_context().run, args=(runner,), daemon=True).start()


def _require_idle() -> None:
    if job["running"]:
        raise HTTPException(409, "Wait for the current job to finish before editing.")


def _find_row(rows: list[dict], table: str, key: str) -> dict:
    key_field = csv_store.TABLES[table]["key"]
    row = next((r for r in rows if r[key_field] == key), None)
    if row is None:
        raise HTTPException(404, "Not found in CSV")
    return row


# ---------- request bodies ----------
class GenerateRequest(BaseModel):
    limit: int | None = None
    regenerate: bool = False


class CheckRequest(BaseModel):
    recheck: bool = False


class PushRequest(BaseModel):
    dry_run: bool = False


class RowUpdate(BaseModel):
    key: str                              # product_id / collection_id / media_id
    fields: dict[str, str] | None = None  # e.g. {"new_meta_title": "..."}
    approved: bool | None = None


class RowKey(BaseModel):
    key: str


class UndoRequest(BaseModel):
    keys: list[str] | None = None            # pick items by ID (Review page)...
    titles: list[str] | Literal["all"] | None = None   # ...or by name (chat)


class StoreName(BaseModel):
    shop: str = Field(max_length=200)


class StoreAdd(BaseModel):
    shop: str = Field(max_length=200)
    client_id: str = Field(max_length=200)
    client_secret: str = Field(max_length=300)
    label: str = Field(default="", max_length=100)


class PublicAuditRequest(BaseModel):
    url: str = Field(max_length=300)


class ChatMessage(BaseModel):
    role: str                                  # "user" or "assistant"
    content: str = Field(max_length=4000)


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(max_length=30)


class LoginRequest(BaseModel):
    password: str = Field(max_length=200)


# ---------- pages ----------
def _page(name: str):
    return FileResponse(os.path.join(STATIC, name))


@app.get("/")
def home_page():
    return _page("home.html")


@app.get("/guide")
def guide_page():
    return _page("guide.html")


@app.get("/chat")
def chat_page():
    return _page("chat.html")


@app.get("/review")
def review_page():
    return _page("review.html")


@app.get("/login")
def login_page():
    return _page("login.html")


@app.get("/audit")
def audit_page():
    return _page("audit.html")


@app.get("/stores")
def stores_page():
    return _page("stores.html")


# ---------- login ----------
@app.get("/api/me")
def me(request: Request):
    owner = auth.is_owner(request)
    return {
        "owner": owner,
        "can_view": auth.can_view(request),
        "login_enabled": bool(config.ADMIN_PASSWORD) and not config.DEMO_ONLY,
        "demo_only": config.DEMO_ONLY,
        # where buyers go instead of your email (e.g. Fiverr gig); empty = show email
        "hire_url": config.HIRE_URL,
        "hire_label": config.HIRE_LABEL,
        # Version 3: which store the pages show (client stores: owner only)
        "store": stores.current(),
        "is_main_store": stores.current() == stores.main_domain(),
    }


@app.post("/api/login")
def login(body: LoginRequest, request: Request, response: Response):
    if config.DEMO_ONLY:
        raise HTTPException(400, "This is a read-only demo. The owner works on their own computer.")
    if not config.ADMIN_PASSWORD:
        raise HTTPException(400, "Owner login is not set up yet (ADMIN_PASSWORD is empty).")
    if not auth.login_attempts.allow(auth.client_ip(request)):
        raise HTTPException(429, "Too many tries. Please wait 15 minutes and try again.")
    if not auth.check_password(body.password):
        raise HTTPException(401, "Wrong password.")
    response.set_cookie(
        auth.COOKIE, auth.make_session_token(), max_age=auth.SESSION_SECONDS,
        httponly=True, samesite="lax", secure=request.url.scheme == "https",
    )
    return {"ok": True}


@app.post("/api/logout")
def logout(response: Response):
    response.delete_cookie(auth.COOKIE)
    return {"ok": True}


# ---------- chatbot ----------
@app.post("/api/chat")
def chat(req: ChatRequest, request: Request):
    """Ask the AI, then run the action it chose (owner only).

    Returns {"reply", "job_started", "confirm_push", "confirm_undo"}. A real push
    or undo is never run from here: confirm_push holds the table (confirm_undo
    the table and titles), and the page shows a confirm button that calls
    /api/{table}/push or /api/{table}/undo itself.
    """
    owner = auth.is_owner(request)
    if not owner:
        if not config.PUBLIC_DEMO:
            raise HTTPException(401, "Please log in to use the chat.")
        if not (auth.visitor_chat.allow(auth.client_ip(request)) and auth.visitor_chat_total.allow("all")):
            raise HTTPException(429, "The demo chat has reached its limit for now. Please try again in an hour.")

    history = [
        {"role": m.role if m.role in ("user", "assistant") else "user", "content": m.content}
        for m in req.messages
    ]
    try:
        reply, action = chatbot.ask(history, job, owner=owner)
    except Exception as e:
        raise HTTPException(502, f"The AI didn't answer: {e}")

    name, args = action["name"], action["args"]
    result = {"reply": reply, "job_started": False, "confirm_push": None, "confirm_undo": None}

    if not owner:
        if name != "none":
            result["reply"] += ("\n\n🔒 This is a read-only demo, so I can't run that here. "
                                "The owner can do it after logging in.")
        return result

    table = args.get("target") if args.get("target") in csv_store.TABLES else "products"
    if name != "none" and job["running"]:
        result["reply"] += "\n\n⏳ A job is still running - please wait for it to finish."
        return result

    if name == "generate":
        limit = args.get("limit")
        limit = int(limit) if isinstance(limit, (int, float)) and limit > 0 else None
        start_job("generate", table, pipeline.generate, limit=limit, regenerate=bool(args.get("regenerate")))
        result["job_started"] = True

    elif name == "check_guesses" and table != "images":
        start_job("check", table, pipeline.check_guesses, recheck=bool(args.get("recheck")))
        result["job_started"] = True

    elif name == "remove_guesses" and table != "images":
        result["reply"] += "\n\n✅ " + chatbot.remove_guesses(table, args.get("titles", "all"))

    elif name in ("approve", "unapprove"):
        message = chatbot.set_approval(table, args.get("titles", []), approve=(name == "approve"))
        result["reply"] += f"\n\n✅ {message}"

    elif name == "push":
        if args.get("dry_run", True):
            start_job("push", table, pipeline.push_approved, dry_run=True)
            result["job_started"] = True
        else:
            result["confirm_push"] = table

    elif name == "audit":
        start_job("audit", "audit", audit.run_full)
        result["job_started"] = True

    elif name == "undo":
        titles = args.get("titles") or "all"
        if titles != "all":
            titles = [str(t) for t in (titles if isinstance(titles, list) else [titles])]
        result["confirm_undo"] = {"table": table, "titles": titles}

    return result


# ---------- data API ----------
@app.get("/api/{table}/rows", dependencies=VIEWER)
def get_rows(table: Table):
    return csv_store.read_rows(table)


@app.post("/api/{table}/update", dependencies=OWNER)
def update_row(table: Table, update: RowUpdate):
    _require_idle()
    rows = csv_store.read_rows(table)
    row = _find_row(rows, table, update.key)

    changed = False
    for field, value in (update.fields or {}).items():
        if field not in csv_store.TABLES[table]["editable"]:
            raise HTTPException(400, f"Field '{field}' can't be edited")
        if row[field] != value:
            row[field] = value
            changed = True

    # Text changed after publishing -> it has to be approved and pushed again
    if changed and row["status"] == "published":
        row["status"] = "edited"
        row["approved"] = ""
    if update.approved is not None:
        row["approved"] = "yes" if update.approved else ""

    csv_store.write_rows(rows, table)
    return row


@app.post("/api/{table}/remove_guesses", dependencies=OWNER)
def remove_guesses(table: Table, body: RowKey):
    if table == "images":
        raise HTTPException(400, "Images don't have guess checks")
    _require_idle()
    rows = csv_store.read_rows(table)
    row = _find_row(rows, table, body.key)
    before = row["new_description_html"]
    pipeline.remove_guesses(row)
    if row["new_description_html"] != before and row["status"] == "published":
        row["status"], row["approved"] = "edited", ""
    csv_store.write_rows(rows, table)
    return row


@app.post("/api/{table}/generate", dependencies=OWNER)
def start_generate(table: Table, req: GenerateRequest):
    start_job("generate", table, pipeline.generate, limit=req.limit, regenerate=req.regenerate)
    return {"started": True}


@app.post("/api/{table}/check", dependencies=OWNER)
def start_check(table: Table, req: CheckRequest):
    if table == "images":
        raise HTTPException(400, "Images don't need the guess checker")
    start_job("check", table, pipeline.check_guesses, recheck=req.recheck)
    return {"started": True}


@app.post("/api/{table}/push", dependencies=OWNER)
def start_push(table: Table, req: PushRequest):
    start_job("push", table, pipeline.push_approved, dry_run=req.dry_run)
    return {"started": True}


@app.post("/api/{table}/undo", dependencies=OWNER)
def start_undo(table: Table, req: UndoRequest):
    """Version 3: put the backed-up original back on Shopify."""
    if req.keys is not None:
        keys = req.keys
    elif req.titles in (None, "all"):
        keys = None
    else:
        found, _ = chatbot._match_rows(csv_store.read_rows(table), table, req.titles)
        keys = [r[csv_store.TABLES[table]["key"]] for r in found]
        if not keys:
            raise HTTPException(404, "None of those names were found.")
    start_job("undo", table, pipeline.undo, keys=keys)
    return {"started": True}


@app.get("/api/{table}/backups", dependencies=VIEWER)
def get_backups(table: Table):
    """{key: {"count", "last"}} - which items have a backup Undo can use."""
    return backups.info_by_key(table)


@app.get("/api/job", dependencies=VIEWER)
def get_job(request: Request):
    if not auth.is_owner(request) and job["store"] not in (None, stores.main_domain()):
        # a job on a client store: visitors only see that something is running
        return {**job, "kind": None, "table": None, "log": [], "error": None, "store": None}
    return job


# ---------- client stores (Version 3) ----------
@app.get("/api/stores", dependencies=OWNER)
def list_stores():
    return {
        "stores": stores.list_all(),
        # what to paste into the Dev Dashboard app for a client store
        "app_url": shopify_oauth.app_url(),
        "redirect_uri": shopify_oauth.redirect_uri(),
        "scopes": config.SHOPIFY_SCOPES,
        "is_local": config.PUBLIC_URL.startswith(("http://127.0.0.1", "http://localhost")),
    }


@app.post("/api/stores/add", dependencies=OWNER)
def add_store(body: StoreAdd):
    try:
        stores.add(body.shop, body.client_id, body.client_secret, body.label)
    except stores.StoreError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "shop": stores.normalize_shop(body.shop)}


@app.post("/api/stores/connect", dependencies=OWNER)
def connect_store(body: StoreName):
    """The Shopify page where the store approves the app (opened by the owner)."""
    try:
        return {"url": shopify_oauth.authorize_url(stores.normalize_shop(body.shop))}
    except (stores.StoreError, shopify_oauth.OAuthError) as e:
        raise HTTPException(400, str(e))


@app.post("/api/stores/activate", dependencies=OWNER)
def activate_store(body: StoreName):
    _require_idle()
    try:
        stores.set_active(body.shop)
    except stores.StoreError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@app.post("/api/stores/remove", dependencies=OWNER)
def remove_store(body: StoreName):
    _require_idle()
    if body.shop == stores.main_domain():
        raise HTTPException(400, "The main store comes from .env and can't be removed here.")
    stores.remove(body.shop)
    return {"ok": True}


def _message_page(title: str, text: str, ok: bool) -> HTMLResponse:
    """A small page for people coming back from Shopify (often the client, not the owner)."""
    colour = "#008060" if ok else "#d72c0d"
    return HTMLResponse(f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{html.escape(title)}</title>
<link rel="stylesheet" href="/static/style.css"></head>
<body><div style="max-width:520px;margin:80px auto;padding:0 16px">
<div style="background:#fff;border:1px solid var(--line);border-radius:14px;padding:28px">
<h1 style="font-size:22px;margin:0 0 10px;color:{colour}">{html.escape(title)}</h1>
<p style="margin:0">{html.escape(text)}</p></div></div></body></html>""", status_code=200 if ok else 400)


@app.get("/shopify/install")
def shopify_install(request: Request):
    """The app's "App URL": Shopify opens it after the install link, and when
    someone clicks the app in their Shopify admin."""
    params = dict(request.query_params)
    try:
        shop = shopify_oauth.check_signed_request(params)
    except shopify_oauth.OAuthError as e:
        return _message_page("Can't connect this store", str(e), ok=False)
    if stores.is_connected(shop):
        return _message_page("Store connected ✓", "This store is connected to the SEO tool. "
                             "Nothing changes on your store until you approve it. You can close this tab.", ok=True)
    return RedirectResponse(shopify_oauth.authorize_url(shop))


@app.get("/shopify/callback")
def shopify_callback(request: Request):
    """Shopify sends the browser here after the store approved the app."""
    try:
        shop = shopify_oauth.finish(dict(request.query_params))
    except (shopify_oauth.OAuthError, Exception) as e:
        return _message_page("Couldn't finish connecting", str(e), ok=False)
    return _message_page("Store connected ✓", f"{shop} is now connected to the SEO tool. "
                         "Nothing changes on the store until the content is reviewed and approved. "
                         "You can close this tab.", ok=True)


# ---------- SEO audit (Version 3) ----------
@app.get("/api/audit", dependencies=VIEWER)
def get_audit():
    """The last full audit of the connected store (null if never run)."""
    return audit.last_report()


@app.post("/api/audit/run", dependencies=OWNER)
def start_audit():
    start_job("audit", "audit", audit.run_full)
    return {"started": True}


@app.post("/api/audit/public", dependencies=VIEWER)
def public_audit(body: PublicAuditRequest, request: Request):
    """Free audit of any Shopify store from its public pages (visitors: rate limited)."""
    if not auth.is_owner(request):
        if not (auth.public_audit.allow(auth.client_ip(request)) and auth.public_audit_total.allow("all")):
            raise HTTPException(429, "You've used the free audit a few times already. Please try again in an hour.")
    try:
        return audit.run_public(body.url)
    except audit.AuditError as e:
        raise HTTPException(400, str(e))


@app.get("/download/backups", dependencies=OWNER)
def download_backups():
    if not os.path.exists(backups.path()):
        raise HTTPException(404, "No backups yet - they are made when you publish.")
    return FileResponse(backups.path(), filename=os.path.basename(backups.path()), media_type="text/csv")


@app.get("/download/{table}", dependencies=VIEWER)
def download_csv(table: Table):
    path = csv_store.path_for(table)
    if not os.path.exists(path):
        raise HTTPException(404, "No CSV yet - run Generate first.")
    return FileResponse(path, filename=os.path.basename(path), media_type="text/csv")


if __name__ == "__main__":
    # Locally: 127.0.0.1 = only reachable from your own computer.
    # Online (HOST=0.0.0.0): trust the hosting provider's proxy for the visitor's IP/https.
    port = int(os.getenv("PORT", "8001"))
    print(f"Website: http://{'127.0.0.1' if config.IS_LOCAL else config.HOST}:{port}")
    if not config.IS_LOCAL and not config.ADMIN_PASSWORD:
        print("Note: ADMIN_PASSWORD is empty - the website is a read-only demo for everyone.")
    uvicorn.run(app, host=config.HOST, port=port,
                proxy_headers=not config.IS_LOCAL,
                forwarded_allow_ips="*" if not config.IS_LOCAL else None)
