"""
Generates SEO content (products, collections, image alt text) and checks it
for guesses, using a FREE AI provider.

Both providers speak the same "OpenAI-compatible" chat API, so we can
call them with plain `requests` and no extra SDKs:
  - gemini : Google Gemini free tier   (key from aistudio.google.com)
  - groq   : Groq free tier            (key from console.groq.com)
"""
import base64
import html
import json
import re
import time

import requests

import config

PROVIDERS = {
    "gemini": {
        "url": "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
        "model": "gemini-flash-lite-latest",
        "key": config.GEMINI_API_KEY,
    },
    "groq": {
        "url": "https://api.groq.com/openai/v1/chat/completions",
        "model": "llama-3.3-70b-versatile",
        "key": config.GROQ_API_KEY,
    },
}

# Google's recommended maximum lengths for search results
META_TITLE_MAX = 60
META_DESCRIPTION_MAX = 160

SYSTEM_PROMPT = """You are an expert e-commerce SEO copywriter for Shopify stores.
You write product copy that ranks well on Google AND convinces shoppers to buy.

Rules:
- Only use facts given in the product data. Never invent materials, sizes,
  certifications, prices or guarantees that are not mentioned.
- Use the product's main keyword naturally (no keyword stuffing).
- seo_description_html: 120-250 words of simple HTML. Use <p>, <ul>/<li> and
  <strong> only. Start with a short benefit-led paragraph, then a bullet list of
  key features, then a closing sentence. No <h1>, no inline styles, no scripts.
- meta_title: max 60 characters, include the main keyword near the start.
- meta_description: 140-160 characters, include the keyword and a reason to click.

Reply with ONLY a JSON object, no extra text, in exactly this shape:
{"seo_description_html": "...", "meta_title": "...", "meta_description": "..."}"""


class AIError(Exception):
    pass


def strip_html(text: str) -> str:
    """Turn HTML into plain text (used to show the AI the current description)."""
    text = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def _clip(text: str, limit: int) -> str:
    """Shorten text to `limit` characters without cutting a word in half."""
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    cut = text[: limit + 1].rsplit(" ", 1)[0]
    return cut.rstrip(" ,.;:-|")[:limit]


def _clean_description(desc_html: str) -> str:
    """Remove anything dangerous or unwanted that the AI might have added."""
    desc_html = re.sub(r"(?is)<(script|style|iframe)[^>]*>.*?</\1>", "", desc_html or "")
    desc_html = re.sub(r"(?i)\son\w+\s*=\s*(\"[^\"]*\"|'[^']*')", "", desc_html)  # onclick= etc.
    return desc_html.strip()


def _parse_json(text: str) -> dict:
    """Pull the JSON object out of the AI reply (models sometimes wrap it in ```json)."""
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise AIError(f"AI reply had no JSON: {text[:200]}")
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError as e:
        raise AIError(f"AI reply was not valid JSON ({e}): {text[:200]}")


def chat(messages: list[dict]) -> str:
    """Send a chat request to the chosen provider and return the reply text."""
    provider = PROVIDERS.get(config.AI_PROVIDER)
    if not provider:
        raise AIError(f"Unknown AI_PROVIDER '{config.AI_PROVIDER}'. Use gemini or groq.")
    if not provider["key"]:
        raise AIError(f"Missing API key for {config.AI_PROVIDER} in .env")

    payload = {
        "model": config.AI_MODEL or provider["model"],
        "messages": messages,
        "temperature": 0.7,
    }
    headers = {"Authorization": f"Bearer {provider['key']}"}

    # Free tiers have rate limits - on 429/5xx wait a bit longer each time and retry.
    for attempt in range(5):
        try:
            resp = requests.post(provider["url"], json=payload, headers=headers, timeout=180)
        except requests.ConnectionError as e:
            raise AIError(f"Could not reach {config.AI_PROVIDER}: {e}")

        if resp.status_code in (429, 500, 502, 503):
            time.sleep(15 * (attempt + 1))
            continue
        if not resp.ok:
            raise AIError(f"AI API error {resp.status_code}: {resp.text[:300]}")
        return resp.json()["choices"][0]["message"]["content"] or ""

    raise AIError("AI provider kept rate-limiting; wait a few minutes or raise AI_DELAY_SECONDS.")


COLLECTION_PROMPT = """You are an expert e-commerce SEO copywriter for Shopify stores.
You write copy for a COLLECTION page (a category page that lists several products).

Rules:
- Only use facts given in the collection data (its name, current description and
  the product names in it). Never invent materials, prices, guarantees or features.
- seo_description_html: 80-150 words of simple HTML. Use <p>, <ul>/<li> and
  <strong> only. Describe what shoppers will find in this collection.
  No <h1>, no inline styles, no scripts.
- meta_title: max 60 characters, include the collection's main keyword near the start.
- meta_description: 140-160 characters, include the keyword and a reason to click.

Reply with ONLY a JSON object, no extra text, in exactly this shape:
{"seo_description_html": "...", "meta_title": "...", "meta_description": "..."}"""

CHECK_PROMPT = """You are a strict fact-checker for online store copy.
You get FACTS (the only things known to be true about an item) and TEXT written by an AI.

Find every specific claim in TEXT that the FACTS do not support, for example:
materials, sizes, features, quality claims ("durable", "high-quality construction"),
who it is for ("for beginners", "all levels"), how it is used ("terrain park",
"freeriding"), or anything that misunderstands the item's name.

The title, vendor, product type, tags and product names ARE facts. Words that simply
restate them are fine (e.g. "winter" when tagged Winter, "premium" when tagged Premium).

Do NOT flag general marketing phrases that make no factual claim, such as
"hit the slopes", "upgrade your gear today" or "perfect for your next adventure".

For each problem, copy the shortest exact phrase from TEXT (character for character)
and give a short, simple reason.
Reply with ONLY JSON: {"flags": [{"text": "exact phrase", "reason": "why it's a guess"}]}
If everything is supported, reply {"flags": []}"""

ALT_TEXT_PROMPT = """Write alt text for this product photo in an online store.
Product name: "{title}".

Rules:
- Describe what is actually visible: the product, colours, view or angle, key details.
- Start with the product name if it fits naturally.
- Max 110 characters, one complete phrase. No "image of" or "picture of". No keyword stuffing.
- Don't invent anything you can't see.
Reply with ONLY the alt text, nothing else."""

ALT_TEXT_MAX = 125


def _with_store_context(text: str) -> str:
    if config.STORE_CONTEXT:
        return f"About the store / brand voice: {config.STORE_CONTEXT}\n\n{text}"
    return text


def _seo_from_ai(system_prompt: str, facts: dict, label: str) -> dict:
    """Shared by products and collections: ask the AI, then clean and validate the reply."""
    user_prompt = _with_store_context(
        f"{label} data:\n" + json.dumps(facts, ensure_ascii=False, indent=2)
    )
    data = _parse_json(chat([
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]))
    result = {
        "seo_description_html": _clean_description(str(data.get("seo_description_html", ""))),
        "meta_title": _clip(str(data.get("meta_title", "")), META_TITLE_MAX),
        "meta_description": _clip(str(data.get("meta_description", "")), META_DESCRIPTION_MAX),
    }
    if not all(result.values()):
        raise AIError("AI reply was missing one of the fields")
    return result


def product_facts(product: dict) -> dict:
    """The facts the AI is allowed to use for a product (also used by the guess checker)."""
    return {
        "title": product.get("title", ""),
        "vendor": product.get("vendor", ""),
        "product_type": product.get("productType", ""),
        "tags": product.get("tags", []),
        "current_description": strip_html(product.get("descriptionHtml", ""))[:3000],
    }


def collection_facts(collection: dict) -> dict:
    return {
        "title": collection.get("title", ""),
        "number_of_products": (collection.get("productsCount") or {}).get("count", 0),
        "example_products": [p["title"] for p in collection.get("products", {}).get("nodes", [])],
        "current_description": strip_html(collection.get("descriptionHtml", ""))[:3000],
    }


def generate_seo(facts: dict) -> dict:
    """Return {"seo_description_html", "meta_title", "meta_description"} from product facts."""
    return _seo_from_ai(SYSTEM_PROMPT, facts, "Product")


def generate_collection_seo(facts: dict) -> dict:
    """Same as generate_seo, but for a collection page (Version 2)."""
    return _seo_from_ai(COLLECTION_PROMPT, facts, "Collection")


# ---------------------------------------------------------------------------
# Version 2: guess checker
# ---------------------------------------------------------------------------
def check_guesses(facts: dict, description_html: str, meta_title: str, meta_description: str) -> list[dict]:
    """Ask the AI which phrases in the generated text are NOT backed by the facts.
    Returns [{"text": phrase, "reason": why}, ...] - only phrases really found in the text."""
    text = (
        f"META TITLE: {meta_title}\n"
        f"META DESCRIPTION: {meta_description}\n"
        f"DESCRIPTION: {strip_html(description_html)}"
    )
    data = _parse_json(chat([
        {"role": "system", "content": CHECK_PROMPT},
        {"role": "user", "content": "FACTS:\n" + json.dumps(facts, ensure_ascii=False, indent=2)
                                    + "\n\nTEXT:\n" + text},
    ]))
    flags, seen = [], set()
    for flag in data.get("flags") or []:
        if not isinstance(flag, dict):
            continue
        phrase = " ".join(str(flag.get("text", "")).split())
        # ignore phrases the AI "quoted" that aren't really in the text
        if phrase and phrase.lower() in text.lower() and phrase.lower() not in seen:
            seen.add(phrase.lower())
            flags.append({"text": phrase, "reason": str(flag.get("reason", "")).strip()})
    return flags


def remove_guesses(description_html: str, flags: list[dict]) -> str:
    """Delete flagged guesses from the description:
    a whole bullet if the guess is in a bullet, otherwise the sentence that contains it."""
    for flag in flags:
        phrase = re.escape(flag["text"]).replace(r"\ ", r"\s+")
        # 1) the bullet (<li>) that contains the phrase
        new = re.sub(r"<li>(?:(?!</li>).)*?" + phrase + r".*?</li>\s*", "", description_html,
                     count=1, flags=re.I | re.S)
        if new == description_html:
            # 2) the sentence that contains it (stays inside one paragraph)
            new = re.sub(r"[^.!?<>]*" + phrase + r"[^.!?<>]*[.!?]?\s*", "", description_html,
                         count=1, flags=re.I)
        description_html = new
    # tidy up anything left empty
    return re.sub(r"<(ul|p)>\s*</\1>", "", description_html).strip()


def flags_still_present(flags: list[dict], *texts: str) -> list[dict]:
    """Keep only the flags whose phrase still appears in any of the texts."""
    combined = " ".join(strip_html(t) for t in texts).lower()
    return [f for f in flags if f["text"].lower() in combined]


# ---------------------------------------------------------------------------
# Version 2: image alt text (the AI looks at the photo)
# ---------------------------------------------------------------------------
def generate_alt_text(product_title: str, image_url: str) -> str:
    """Download a small copy of the image and ask the AI to describe it as alt text."""
    # Shopify's image CDN can resize: a 512px version is plenty and keeps it fast
    small_url = image_url + ("&" if "?" in image_url else "?") + "width=512"
    try:
        img = requests.get(small_url, timeout=30)
        img.raise_for_status()
    except requests.RequestException as e:
        raise AIError(f"Could not download the image: {e}")
    content_type = img.headers.get("content-type", "image/jpeg").split(";")[0]
    data_uri = f"data:{content_type};base64,{base64.b64encode(img.content).decode()}"

    prompt = ALT_TEXT_PROMPT.format(title=product_title)
    reply = chat([{
        "role": "user",
        "content": [
            {"type": "text", "text": _with_store_context(prompt)},
            {"type": "image_url", "image_url": {"url": data_uri}},
        ],
    }])
    alt = " ".join(reply.strip().strip('"').strip("'").split())
    if len(alt) > ALT_TEXT_MAX:
        # Too long: cut at the last comma/semicolon so it still reads as a complete phrase
        cut = alt[:ALT_TEXT_MAX]
        at = max(cut.rfind(","), cut.rfind(";"))
        alt = cut[:at] if at > 60 else _clip(alt, ALT_TEXT_MAX)
    if not alt:
        raise AIError("AI returned empty alt text")
    return alt


def test_ai() -> str:
    """Tiny request used by `python cli.py check`."""
    return chat([{"role": "user", "content": "Reply with just the word: OK"}]).strip()
