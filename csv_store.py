"""
Reading and writing the review CSVs.

There is one CSV ("table") per kind of content:
  products     -> seo_results.csv        (Version 1)
  collections  -> collection_results.csv (Version 2)
  images       -> alt_text_results.csv   (Version 2)

You review them in Excel / Google Sheets / the website, type "yes" in the
`approved` column, then push approved rows to Shopify.

Each connected store has its own set of CSVs (see stores.py, Version 3).
"""
import csv
import os

import config
import stores

PRODUCT_FIELDS = [
    "product_id",                 # Shopify ID (gid://shopify/Product/123...)
    "handle",
    "title",
    "current_meta_title",         # what is on Shopify now (for comparison)
    "current_meta_description",
    "current_description",        # plain text version of the current description
    "original_description",       # V2: description before the AI ever touched it (used as facts)
    "new_meta_title",             # AI suggestion - you can edit these
    "new_meta_description",
    "new_description_html",
    "guess_flags",                # V2: JSON list of possible guesses ("" = not checked)
    "published_description",      # V2: plain text we last published (to spot our own text on Shopify)
    "approved",                   # type "yes" to approve
    "status",                     # generated / edited / error: ... / published / publish_error: ...
    "updated_at",
]

COLLECTION_FIELDS = [
    "collection_id",
    "handle",
    "title",
    "products_count",
    "current_meta_title",
    "current_meta_description",
    "current_description",
    "original_description",
    "new_meta_title",
    "new_meta_description",
    "new_description_html",
    "guess_flags",
    "published_description",
    "approved",
    "status",
    "updated_at",
]

IMAGE_FIELDS = [
    "media_id",                   # Shopify image ID (gid://shopify/MediaImage/123...)
    "product_id",
    "product_title",
    "image_url",
    "current_alt",
    "new_alt",
    "approved",
    "status",
    "updated_at",
]

# Everything the rest of the app needs to know about each table
TABLES = {
    "products": {
        "path": lambda: stores.data_file(config.CSV_PATH),
        "key": "product_id",
        "title": "title",
        "fields": PRODUCT_FIELDS,
        "editable": ["new_meta_title", "new_meta_description", "new_description_html"],
    },
    "collections": {
        "path": lambda: stores.data_file(config.COLLECTIONS_CSV_PATH),
        "key": "collection_id",
        "title": "title",
        "fields": COLLECTION_FIELDS,
        "editable": ["new_meta_title", "new_meta_description", "new_description_html"],
    },
    "images": {
        "path": lambda: stores.data_file(config.IMAGES_CSV_PATH),
        "key": "media_id",
        "title": "product_title",
        "fields": IMAGE_FIELDS,
        "editable": ["new_alt"],
    },
}

FIELDS = PRODUCT_FIELDS  # kept for older code
APPROVED_VALUES = {"yes", "y", "true", "1", "x", "approved"}


def is_approved(row: dict) -> bool:
    return (row.get("approved") or "").strip().lower() in APPROVED_VALUES


def path_for(table: str) -> str:
    return TABLES[table]["path"]()


def read_csv(path: str, fields: list[str]) -> list[dict]:
    """Load all rows of a CSV (empty list if it doesn't exist yet)."""
    if not os.path.exists(path):
        return []
    # utf-8-sig handles the invisible BOM that Excel adds when saving
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    # make sure every expected column exists, even in older/edited files
    return [{field: (row.get(field) or "") for field in fields} for row in rows]


def write_csv(path: str, fields: list[str], rows: list[dict]) -> None:
    """Save all rows. Writes to a temp file first so a crash can't corrupt the CSV."""
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    try:
        os.replace(tmp_path, path)
    except PermissionError:
        raise PermissionError(f"Can't save {path} - is it open in Excel? Close it and try again.")


def read_rows(table: str = "products") -> list[dict]:
    """Load all rows of a table (empty list if its CSV doesn't exist yet)."""
    return read_csv(path_for(table), TABLES[table]["fields"])


def write_rows(rows: list[dict], table: str = "products") -> None:
    write_csv(path_for(table), TABLES[table]["fields"], rows)
