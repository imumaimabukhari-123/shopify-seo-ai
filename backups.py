"""
Version 3: backup & undo.

Right before an item is published, push_approved() saves exactly what Shopify
had into backups.csv (one line per item per publish). Undo puts the newest
backup that wasn't used yet back on Shopify, so undoing twice steps back two
publishes.

Items published before Version 3 have no backup line. For those, Undo falls
back to the original text the review CSV remembered (plain text only, so any
bold/lists in the old description are lost).
"""
import html

import config
import csv_store
import stores

FIELDS = [
    "table",              # products / collections / images
    "key",                # product_id / collection_id / media_id
    "title",
    "description_html",   # products & collections
    "meta_title",
    "meta_description",
    "alt",                # images
    "source",             # "shopify" (exact copy) or "csv" (plain-text fallback)
    "backed_up_at",
    "restored_at",        # set when Undo used this backup
]


def path() -> str:
    return stores.data_file(config.BACKUPS_CSV_PATH)


def read_all() -> list[dict]:
    return csv_store.read_csv(path(), FIELDS)


def snapshot(item: dict, table: str) -> dict:
    """The parts of a Shopify item that publishing would overwrite."""
    if table == "images":
        return {"alt": item.get("alt") or ""}
    seo = item.get("seo") or {}
    return {
        "description_html": item.get("descriptionHtml") or "",
        "meta_title": seo.get("title") or "",
        "meta_description": seo.get("description") or "",
    }


def add(table: str, key: str, title: str, snap: dict, when: str) -> None:
    rows = read_all()
    rows.append({field: "" for field in FIELDS} | snap |
                {"table": table, "key": key, "title": title, "source": "shopify", "backed_up_at": when})
    csv_store.write_csv(path(), FIELDS, rows)


def latest(rows: list[dict], table: str, key: str) -> dict | None:
    """The newest backup of one item that Undo hasn't used yet."""
    unused = [b for b in rows if b["table"] == table and b["key"] == key and not b["restored_at"]]
    return unused[-1] if unused else None


def _norm(text: str) -> str:
    return " ".join((text or "").split()).lower()


def from_review_row(row: dict, table: str) -> dict:
    """Fallback for items published before Version 3: rebuild the old content
    from the 'current_...' columns the review CSV saved before publishing.

    If a column already holds our own published text (it was refreshed from
    Shopify after publishing), the original is unknown, so that field is left
    out and Undo won't touch it."""
    if table == "images":
        same = _norm(row["current_alt"]) == _norm(row["new_alt"])
        return {"source": "csv"} if same else {"alt": row["current_alt"], "source": "csv"}

    snap = {"source": "csv"}
    text = row["original_description"] or row["current_description"]
    if _norm(text) != _norm(row["published_description"]):
        snap["description_html"] = f"<p>{html.escape(text)}</p>" if text.strip() else ""
    for field in ("meta_title", "meta_description"):
        if _norm(row[f"current_{field}"]) != _norm(row[f"new_{field}"]):
            snap[field] = row[f"current_{field}"]
    return snap


def info_by_key(table: str) -> dict[str, dict]:
    """For the Review page: {key: {"count": unused backups, "last": date}}."""
    info: dict[str, dict] = {}
    for b in read_all():
        if b["table"] != table or b["restored_at"]:
            continue
        entry = info.setdefault(b["key"], {"count": 0, "last": ""})
        entry["count"] += 1
        entry["last"] = b["backed_up_at"]
    return info
