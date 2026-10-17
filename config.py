"""
Settings for the app, loaded from the .env file.
Nothing secret is written in the code - everything comes from .env.
"""
import os

from dotenv import load_dotenv

# Read the .env file in this folder (if it exists) into environment variables
load_dotenv()


def _get(name: str, default: str = "") -> str:
    """Read an environment variable and trim spaces."""
    return (os.getenv(name) or default).strip()


# ---------- Shopify ----------
# Accept "https://my-store.myshopify.com/" as well as "my-store.myshopify.com"
SHOPIFY_STORE_DOMAIN = (
    _get("SHOPIFY_STORE_DOMAIN").replace("https://", "").replace("http://", "").strip("/")
)
SHOPIFY_ACCESS_TOKEN = _get("SHOPIFY_ACCESS_TOKEN")    # Option B: shpat_... token
SHOPIFY_CLIENT_ID = _get("SHOPIFY_CLIENT_ID")          # Option A: Dev Dashboard app
SHOPIFY_CLIENT_SECRET = _get("SHOPIFY_CLIENT_SECRET")
SHOPIFY_API_VERSION = _get("SHOPIFY_API_VERSION", "2026-07")

# ---------- Client stores (Version 3) ----------
# Connected client stores (with their access tokens) are saved in STORES_FILE;
# each store's CSVs, backups and audit live in its own folder under STORES_DIR.
STORES_FILE = _get("STORES_FILE", "stores.json")
STORES_DIR = _get("STORES_DIR", "stores")
SHOPIFY_SCOPES = _get("SHOPIFY_SCOPES", "read_products,write_products,read_files,write_files")

# ---------- AI ----------
AI_PROVIDER = _get("AI_PROVIDER", "gemini").lower()
AI_MODEL = _get("AI_MODEL")                            # blank = provider default
GEMINI_API_KEY = _get("GEMINI_API_KEY")
GROQ_API_KEY = _get("GROQ_API_KEY")
AI_DELAY_SECONDS = float(_get("AI_DELAY_SECONDS", "5") or 0)
STORE_CONTEXT = _get("STORE_CONTEXT")
# Version 2: after writing text, ask the AI to flag any guesses (costs 1 extra AI call)
GUESS_CHECK = _get("GUESS_CHECK", "true").lower() in ("true", "1", "yes")

# ---------- Website & owner login ----------
# Local (default): the website only listens on your own PC (127.0.0.1).
# Online: set HOST=0.0.0.0 - then visitors get a read-only demo and the owner
# logs in with ADMIN_PASSWORD to generate, edit, approve and publish.
HOST = _get("HOST", "127.0.0.1")
# Vercel sets VERCEL=1. Hosted there, the site is ALWAYS a read-only demo:
# nobody is the owner (Vercel can't save files, so nothing could be changed anyway).
ON_VERCEL = _get("VERCEL") == "1"
DEMO_ONLY = ON_VERCEL or _get("DEMO_ONLY", "false").lower() in ("true", "1", "yes")
IS_LOCAL = HOST in ("127.0.0.1", "localhost") and not DEMO_ONLY
# The demo reads a snapshot of the main store's results from this folder
# (made with: python cli.py demo-snapshot), because the real CSVs aren't uploaded.
DEMO_DATA_DIR = _get("DEMO_DATA_DIR", "demo_data")
# Online, buyers are sent here instead of to your email (e.g. your Fiverr gig).
HIRE_URL = _get("HIRE_URL", "https://www.fiverr.com/umaima886")
HIRE_LABEL = _get("HIRE_LABEL", "Hire me on Fiverr")
ADMIN_PASSWORD = _get("ADMIN_PASSWORD")
SESSION_SECRET = _get("SESSION_SECRET")       # optional: keeps you logged in across restarts
# true = visitors may look at the results (read-only) and use the demo chat
PUBLIC_DEMO = DEMO_ONLY or _get("PUBLIC_DEMO", "false").lower() in ("true", "1", "yes")
PORT = int(_get("PORT", "8001") or 8001)
# The address people use to open this website. Shopify sends client stores back
# here after they approve the app (Version 3), e.g. https://my-seo-tool.onrender.com
PUBLIC_URL = _get("PUBLIC_URL", f"http://127.0.0.1:{PORT}").rstrip("/")

# ---------- Output (one CSV per kind of content) ----------
CSV_PATH = _get("CSV_PATH", "seo_results.csv")                          # products
COLLECTIONS_CSV_PATH = _get("COLLECTIONS_CSV_PATH", "collection_results.csv")
IMAGES_CSV_PATH = _get("IMAGES_CSV_PATH", "alt_text_results.csv")
# Version 3: what Shopify had before each publish (used by Undo)
BACKUPS_CSV_PATH = _get("BACKUPS_CSV_PATH", "backups.csv")
# Version 3: the last full SEO audit of the store
AUDIT_JSON_PATH = _get("AUDIT_JSON_PATH", "audit.json")
