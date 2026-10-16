"""
The jobs, shared by the command line (cli.py), the website (app.py) and the chatbot.

  generate(table)       Shopify -> AI -> CSV     (products, collections or images)
  check_guesses(table)  AI double-checks generated text for guesses (Version 2)
  remove_guesses(...)   delete flagged guesses from a description (Version 2)
  push_approved(table)  CSV rows marked approved -> Shopify (saves a backup first)
  undo(table)           put the backed-up original back on Shopify (Version 3)

table = "products" (Version 1), "collections" or "images" (Version 2).
"""
import json
import time
from datetime import datetime

import ai_generator
import backups
import config
import csv_store
from shopify_client import ShopifyClient

TABLE_LABELS = {"products": "products", "collections": "collections", "images": "product images"}


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _pause() -> None:
    """Wait between AI calls so we stay inside the free-tier rate limit."""
    if config.AI_DELAY_SECONDS > 0:
        time.sleep(config.AI_DELAY_SECONDS)


def load_flags(row: dict) -> list[dict]:
    try:
        flags = json.loads(row.get("guess_flags") or "[]")
        return flags if isinstance(flags, list) else []
    except json.JSONDecodeError:
        return []


# ---------------------------------------------------------------------------
# What is different for each kind of content
# ---------------------------------------------------------------------------
def _fetch(shop: ShopifyClient, table: str) -> list[dict]:
    if table == "products":
        return shop.fetch_all_products()
    if table == "collections":
        return shop.fetch_all_collections()
    return shop.fetch_all_product_images()


def _item_key(item: dict, table: str) -> str:
    return item["media_id"] if table == "images" else item["id"]


def _item_title(item: dict, table: str) -> str:
    return item["product_title"] if table == "images" else item["title"]


def _same_text(a: str, b: str) -> bool:
    norm = lambda s: " ".join((s or "").split()).lower()[:300]
    return bool(norm(a)) and norm(a) == norm(b)


def _facts(item: dict, table: str, row: dict | None = None) -> dict:
    """The facts the AI may use. If Shopify still shows the description WE published,
    that text is the AI's own writing, not a fact - so use the original description."""
    facts = ai_generator.product_facts(item) if table == "products" else ai_generator.collection_facts(item)
    if row:
        published = row.get("published_description") or (
            ai_generator.strip_html(row["new_description_html"]) if row.get("status") == "published" else ""
        )
        if published and _same_text(facts["current_description"], published):
            # older rows (before Version 2) kept the original in current_description
            facts["current_description"] = row.get("original_description") or row.get("current_description", "")
    return facts


def _current_columns(item: dict, table: str) -> dict:
    """Columns showing what is on Shopify right now (refreshed on every run)."""
    if table == "images":
        return {
            "media_id": item["media_id"],
            "product_id": item["product_id"],
            "product_title": item["product_title"],
            "image_url": item["image_url"],
            "current_alt": item["alt"],
        }
    seo = item.get("seo") or {}
    columns = {
        csv_store.TABLES[table]["key"]: item["id"],
        "handle": item["handle"],
        "title": item["title"],
        "current_meta_title": seo.get("title") or "",
        "current_meta_description": seo.get("description") or "",
        "current_description": ai_generator.strip_html(item.get("descriptionHtml", "")),
    }
    if table == "collections":
        columns["products_count"] = str((item.get("productsCount") or {}).get("count", ""))
    return columns


def _write_ai_content(item: dict, row: dict, table: str, progress) -> None:
    """Ask the AI for new content and put it in the row (raises on failure)."""
    if table == "images":
        row["new_alt"] = ai_generator.generate_alt_text(item["product_title"], item["image_url"])
        return

    facts = _facts(item, table, row)
    make = ai_generator.generate_seo if table == "products" else ai_generator.generate_collection_seo
    result = make(facts)
    row.update(
        new_meta_title=result["meta_title"],
        new_meta_description=result["meta_description"],
        new_description_html=result["seo_description_html"],
        guess_flags="",
    )
    if config.GUESS_CHECK:
        _pause()
        _check_row(row, facts, progress)


def _check_row(row: dict, facts: dict, progress) -> None:
    """Version 2 guess checker: store the flags as JSON in the row."""
    flags = ai_generator.check_guesses(
        facts, row["new_description_html"], row["new_meta_title"], row["new_meta_description"]
    )
    row["guess_flags"] = json.dumps(flags, ensure_ascii=False)
    if flags:
        progress(f"    ⚠ {len(flags)} possible guess(es) flagged")


def _send_to_shopify(shop: ShopifyClient, row: dict, table: str) -> None:
    if table == "products":
        shop.update_product(row["product_id"], row["new_description_html"],
                            row["new_meta_title"], row["new_meta_description"])
    elif table == "collections":
        shop.update_collection(row["collection_id"], row["new_description_html"],
                               row["new_meta_title"], row["new_meta_description"])
    else:
        shop.update_image_alt(row["media_id"], row["new_alt"])


def _has_content(row: dict, table: str) -> bool:
    return bool(row["new_alt"].strip() if table == "images" else row["new_description_html"].strip())


def _row_title(row: dict, table: str) -> str:
    return row[csv_store.TABLES[table]["title"]]


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------
def generate(limit: int | None = None, regenerate: bool = False, progress=print,
             table: str = "products") -> None:
    """Fetch items from Shopify, create AI content for each, and save to the CSV.

    By default items that already have generated content are skipped, so you
    can re-run safely (e.g. after hitting a free-tier limit) and it resumes.
    Use regenerate=True to redo everything.
    """
    label = TABLE_LABELS[table]
    shop = ShopifyClient()
    progress(f"Fetching {label} from Shopify...")
    items = _fetch(shop, table)
    progress(f"Found {len(items)} {label}.")

    rows = csv_store.read_rows(table)
    key_field = csv_store.TABLES[table]["key"]
    rows_by_key = {row[key_field]: row for row in rows}

    # Decide which items need AI work
    todo = []
    for item in items:
        existing = rows_by_key.get(_item_key(item, table))
        already_done = existing and _has_content(existing, table) and "error" not in existing["status"]
        if regenerate or not already_done:
            todo.append(item)
    if limit:
        todo = todo[:limit]

    if not todo:
        progress(f"Nothing to generate - all {label} already have content. "
                 "Use 'regenerate' to redo them.")
        return

    progress(f"Writing AI content for {len(todo)} {label} with {config.AI_PROVIDER}...")
    ok = failed = 0

    for i, item in enumerate(todo, start=1):
        progress(f"[{i}/{len(todo)}] {_item_title(item, table)}")

        row = rows_by_key.get(_item_key(item, table))
        if row is None:
            row = {field: "" for field in csv_store.TABLES[table]["fields"]}
            rows.append(row)
            rows_by_key[_item_key(item, table)] = row
        # Remember the description from before the AI ever touched it (set once)
        if table != "images" and not row["original_description"]:
            row["original_description"] = row["current_description"] or \
                ai_generator.strip_html(item.get("descriptionHtml", ""))
        row.update(_current_columns(item, table))

        try:
            _write_ai_content(item, row, table, progress)
            row.update(approved="", status="generated")  # new content needs a fresh review
            ok += 1
        except Exception as e:  # keep going - one bad item shouldn't stop the run
            row["status"] = f"error: {e}"[:300]
            progress(f"    ! failed: {e}")
            failed += 1

        row["updated_at"] = _now()
        csv_store.write_rows(rows, table)  # save after every item so nothing is lost

        if i < len(todo):
            _pause()

    progress(f"Done. {ok} generated, {failed} failed. Saved to {csv_store.path_for(table)}")


def check_guesses(recheck: bool = False, progress=print, table: str = "products") -> None:
    """Version 2: run the guess checker on text that was generated earlier."""
    if table == "images":
        progress("The guess checker is for descriptions - images don't need it.")
        return
    rows = csv_store.read_rows(table)
    todo = [r for r in rows if r["new_description_html"].strip() and (recheck or not r["guess_flags"])]
    if not todo:
        progress("Nothing to check - every text has already been checked.")
        return

    progress(f"Fetching {TABLE_LABELS[table]} facts from Shopify...")
    items = {_item_key(i, table): i for i in _fetch(ShopifyClient(), table)}
    key_field = csv_store.TABLES[table]["key"]
    progress(f"Checking {len(todo)} {TABLE_LABELS[table]} for guesses...")

    total_flags = 0
    for i, row in enumerate(todo, start=1):
        progress(f"[{i}/{len(todo)}] {row['title']}")
        item = items.get(row[key_field])
        if item is None:
            progress("    ! not found on Shopify any more - skipped")
            continue
        try:
            _check_row(row, _facts(item, table, row), progress)
            total_flags += len(load_flags(row))
        except Exception as e:
            progress(f"    ! check failed: {e}")
        csv_store.write_rows(rows, table)
        if i < len(todo):
            _pause()

    progress(f"Done. {total_flags} possible guess(es) flagged. "
             "Open the Review page to see them.")


def remove_guesses(row: dict) -> int:
    """Delete flagged guesses from one row's description. Returns how many flags are left
    (guesses in the meta title/description are left for you to edit by hand)."""
    flags = load_flags(row)
    if not flags:
        return 0
    row["new_description_html"] = ai_generator.remove_guesses(row["new_description_html"], flags)
    remaining = ai_generator.flags_still_present(
        flags, row["new_description_html"], row["new_meta_title"], row["new_meta_description"]
    )
    row["guess_flags"] = json.dumps(remaining, ensure_ascii=False)
    return len(remaining)


def push_approved(dry_run: bool = False, progress=print, table: str = "products") -> None:
    """Send every approved (and not yet published) row to Shopify."""
    label = TABLE_LABELS[table]
    rows = csv_store.read_rows(table)
    to_push = [r for r in rows if csv_store.is_approved(r) and r["status"] != "published"
               and _has_content(r, table)]

    if not to_push:
        progress(f"No approved {label} waiting to be published. "
                 "Approve some on the Review page or in the chat first.")
        return

    progress(f"{'[DRY RUN] ' if dry_run else ''}Publishing {len(to_push)} approved {label}...")
    shop = None if dry_run else ShopifyClient()
    if shop:
        progress("Saving a backup of what Shopify has now (for Undo)...")
        on_shopify = {_item_key(i, table): i for i in _fetch(shop, table)}
    key_field = csv_store.TABLES[table]["key"]
    ok = failed = 0

    for row in to_push:
        name = _row_title(row, table)
        preview = row["new_alt"] if table == "images" else row["new_meta_title"]
        if dry_run:
            progress(f"  would update: {name}  |  {preview}")
            continue
        try:
            item = on_shopify.get(row[key_field])
            if item is None:
                raise RuntimeError("not found on Shopify any more")
            backups.add(table, row[key_field], name, backups.snapshot(item, table), _now())
            _send_to_shopify(shop, row, table)
            row["status"] = "published"
            if table != "images":
                row["published_description"] = ai_generator.strip_html(row["new_description_html"])
            progress(f"  updated: {name}")
            ok += 1
        except Exception as e:
            row["status"] = f"publish_error: {e}"[:300]
            progress(f"  ! failed: {name}: {e}")
            failed += 1
        row["updated_at"] = _now()
        csv_store.write_rows(rows, table)

    if not dry_run:
        progress(f"Done. {ok} published, {failed} failed.")


def can_undo(row: dict, has_backup: bool) -> bool:
    """Our text is on Shopify (published, or published and then edited), or an
    earlier publish can still be stepped back."""
    return row["status"] in ("published", "edited") or (row["status"] == "restored" and has_backup)


def undo(keys: list[str] | None = None, progress=print, table: str = "products") -> None:
    """Version 3: put back what Shopify had before the last publish.
    keys=None means every item whose AI text is on Shopify now (items that
    were already undone are only stepped back further when picked by key)."""
    label = TABLE_LABELS[table]
    key_field = csv_store.TABLES[table]["key"]
    rows = csv_store.read_rows(table)
    all_backups = backups.read_all()
    if keys is None:
        targets = [r for r in rows if r["status"] in ("published", "edited")]
    else:
        targets = [r for r in rows if r[key_field] in keys
                   and can_undo(r, backups.latest(all_backups, table, r[key_field]) is not None)]
    if not targets:
        progress(f"Nothing to undo - none of these {label} have published AI text on Shopify.")
        return

    shop = ShopifyClient()
    progress(f"Undoing {len(targets)} {label}...")
    ok = failed = 0

    for row in targets:
        name = _row_title(row, table)
        backup = backups.latest(all_backups, table, row[key_field])
        old = backup if backup else backups.from_review_row(row, table)
        try:
            if table == "images":
                if "alt" in old:
                    shop.update_image_alt(row["media_id"], old["alt"])
            else:
                shop.restore_seo(table, row[key_field], old)
        except Exception as e:
            progress(f"  ! failed: {name}: {e}")
            failed += 1
            continue

        # The CSV now shows the restored text as "current"; the AI text stays
        # in the "new" columns, so it can be approved and published again.
        if table == "images":
            row["current_alt"] = old.get("alt", row["current_alt"])
        else:
            row["current_meta_title"] = old.get("meta_title", row["current_meta_title"])
            row["current_meta_description"] = old.get("meta_description", row["current_meta_description"])
            if "description_html" in old:
                row["current_description"] = ai_generator.strip_html(old["description_html"])
            row["published_description"] = ""
        row.update(status="restored", approved="", updated_at=_now())
        csv_store.write_rows(rows, table)
        if backup:
            backup["restored_at"] = _now()
            csv_store.write_csv(backups.path(), backups.FIELDS, all_backups)

        how = "from backup " + backup["backed_up_at"] if backup else "from the original text in the CSV"
        progress(f"  restored: {name} ({how})")
        ok += 1

    progress(f"Done. {ok} restored, {failed} failed.")
