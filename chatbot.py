"""
Chat assistant for the website.

It reads what the user typed, looks at the current data (from the CSVs),
and replies. If the user asks it to DO something, it also returns an action
that app.py then runs.

It uses the same free AI provider as the SEO generator (Gemini by default).
"""
import config
import csv_store
import ai_generator
import audit
import pipeline

SYSTEM_PROMPT = """You are the friendly assistant inside "Shopify AI SEO Generator",
a tool built by Syeda Umaima. You help a Shopify store owner improve their SEO.
The user is a beginner: reply in short, simple English.

The tool works on three kinds of content ("target"):
- "products"    : product descriptions, meta titles, meta descriptions   (Version 1)
- "collections" : collection page descriptions, meta titles, meta descriptions (Version 2)
- "images"      : alt text for product photos, written by an AI that looks at the photo (Version 2)
Version 2 also has a guess checker: it flags sentences in descriptions that are not
backed by the real product/collection facts, and can remove them.

Actions you can run (always include "target"):
- generate       : write AI content. args: {"target": ..., "limit": number or null, "regenerate": true/false}
                   (regenerate=false skips items that already have content)
- check_guesses  : run the guess checker on existing text (products or collections).
                   args: {"target": ..., "recheck": true/false}
- remove_guesses : delete flagged guesses from descriptions (products or collections).
                   args: {"target": ..., "titles": ["title", ...] or "all"}
- approve        : mark items as approved for publishing.
                   args: {"target": ..., "titles": ["title", ...] or "all"}
                   ("all" = every item that has new content and is not published yet).
                   For images, titles are product names (approves all photos of that product).
- unapprove      : undo approval. Same args as approve.
- push           : publish approved items to Shopify. args: {"target": ..., "dry_run": true/false}
                   dry_run=true only shows what would change. For dry_run=false the
                   website shows the user a "Yes, publish" button to confirm.
- undo           : (Version 3) put back what was on Shopify before we published (from the
                   backup saved at publish time). args: {"target": ..., "titles": ["title", ...] or "all"}
                   The website shows the user a "Yes, undo" button to confirm.
- audit          : (Version 3) run a full SEO audit of the store: a score out of 100 and a
                   list of problems. args: {}. The results appear on the Audit page.
- none           : just reply, do nothing.

Rules:
- Use the DATA below for questions about counts, names, statuses, flags, text or the SEO score.
- Status "restored" means Undo put the original text back on Shopify; the AI text can be
  approved and published again.
- Only run an action when the user clearly asks for it. If unsure, ask.
- If the user doesn't say which target, assume "products".
- Don't start a job while one is running; say to wait.
- Before a real push, remind the user to review the text (and any flagged guesses) first.
- If items have flagged guesses, mention it and suggest removing them before approving.

Reply with ONLY a JSON object, no other text:
{"reply": "message for the user", "action": {"name": "none", "args": {}}}"""

MAX_ROWS_IN_CONTEXT = 60


def _summary_for(table: str) -> str:
    rows = csv_store.read_rows(table)
    if not rows:
        return f"[{table}] nothing generated yet."

    counts: dict[str, int] = {}
    for r in rows:
        key = "error" if "error" in r["status"] else (r["status"] or "new")
        counts[key] = counts.get(key, 0) + 1
    waiting = sum(1 for r in rows if csv_store.is_approved(r) and r["status"] != "published")
    lines = [f"[{table}] total {len(rows)}, by status {counts}, approved but not published: {waiting}."]

    for r in rows[:MAX_ROWS_IN_CONTEXT]:
        approved = "yes" if csv_store.is_approved(r) else "no"
        if table == "images":
            lines.append(f"- {r['product_title']} | {r['status']} | approved {approved} | alt: {r['new_alt']}")
        else:
            flags = pipeline.load_flags(r)
            checked = "not checked" if not r["guess_flags"] else (
                f"{len(flags)} guess(es): " + "; ".join(f['text'] for f in flags) if flags else "no guesses")
            lines.append(f"- {r['title']} | {r['status']} | approved {approved} | {checked}"
                         f" | meta title: {r['new_meta_title']}")
    if len(rows) > MAX_ROWS_IN_CONTEXT:
        lines.append(f"... and {len(rows) - MAX_ROWS_IN_CONTEXT} more")
    return "\n".join(lines)


def _audit_summary() -> str:
    report = audit.last_report()
    if not report:
        return "[audit] never run."
    lines = [f"[audit] last run {report['ran_at']}: score {report['score']}/100 ({report['grade']}). "
             f"Categories: " + ", ".join(f"{k} {v['score']}%" for k, v in report["categories"].items())]
    for t in report["top_issues"][:8]:
        lines.append(f"- {t['severity']}: {t['message']} on {t['count']} page(s). Fix: {t['fix']}")
    worst = [f"{i['title']} ({i['score']})" for i in report["items"][:5] if i["score"] < 100]
    if worst:
        lines.append("Lowest scores: " + ", ".join(worst))
    return "\n".join(lines)


def _context(job: dict) -> str:
    """A short text snapshot of all the data, so the AI can answer questions."""
    parts = [_summary_for(t) for t in csv_store.TABLES] + [_audit_summary()]
    job_text = (
        f"A '{job['kind']}' job on {job['table']} is RUNNING now. Latest log: {job['log'][-2:]}"
        if job["running"] else "No job is running."
    )
    return "DATA\n" + "\n\n".join(parts) + "\n\nJOB STATUS\n" + job_text


VISITOR_NOTE = """
IMPORTANT: You are talking to a VISITOR of the public demo, not the owner.
- Never run actions: always use {"name": "none", "args": {}}.
- Answer questions about SEO, about this tool and about the demo results.
- The demo uses a free Shopify test store with sample snowboard products.
- If they want to do something (generate, approve, publish), explain that the demo is
  read-only and that they can {contact} to set it up for their own store.
- Version 3: anyone can get a free SEO score for their own Shopify store on the Audit page
  (/audit) by typing the store's address. Suggest it when they ask about their own store.
- Stay on topic (SEO, Shopify, this tool). Politely decline anything unrelated."""


def ask(history: list[dict], job: dict, owner: bool = True) -> tuple[str, dict]:
    """Send the conversation to the AI. Returns (reply_text, action_dict)."""
    contact = (f'hire the developer, Syeda Umaima, through the "{config.HIRE_LABEL}" button on the Home page'
               if config.HIRE_URL else "contact the developer, Syeda Umaima (imumaimabukhari@gmail.com),")
    system = SYSTEM_PROMPT + ("" if owner else "\n" + VISITOR_NOTE.replace("{contact}", contact))
    messages = [
        {"role": "system", "content": system + "\n\n" + _context(job)},
        *history[-12:],  # last few messages are enough context
    ]
    raw = ai_generator.chat(messages)

    try:
        data = ai_generator._parse_json(raw)
    except ai_generator.AIError:
        # The model answered in plain text - just show it, no action.
        return raw.strip(), {"name": "none", "args": {}}

    action = data.get("action") or {}
    if not isinstance(action, dict):
        action = {}
    return str(data.get("reply", "")).strip(), {
        "name": str(action.get("name") or "none"),
        "args": action.get("args") if isinstance(action.get("args"), dict) else {},
    }


def _match_rows(rows: list[dict], table: str, titles) -> tuple[list[dict], list[str]]:
    """Find rows by title: exact match first, otherwise "contains".
    For images one product title can match several photos."""
    title_field = csv_store.TABLES[table]["title"]
    if isinstance(titles, str):
        titles = [titles]
    found, not_found = [], []
    for wanted in titles:
        w = str(wanted).strip().lower()
        matches = [r for r in rows if r[title_field].lower() == w] or \
                  [r for r in rows if w and w in r[title_field].lower()]
        if table != "images":
            matches = matches[:1]
        if not matches:
            not_found.append(str(wanted))
        for r in matches:
            if r not in found:
                found.append(r)
    return found, not_found


def _name_list(rows: list[dict], table: str) -> str:
    title_field = csv_store.TABLES[table]["title"]
    names = list(dict.fromkeys(r[title_field] for r in rows))  # unique, keep order
    return ", ".join(names[:8]) + (" ..." if len(names) > 8 else "")


def set_approval(table: str, titles, approve: bool) -> str:
    """Approve / unapprove items by title (or "all"). Returns a short result message."""
    rows = csv_store.read_rows(table)
    has_content = lambda r: (r["new_alt"] if table == "images" else r["new_description_html"]).strip()

    if titles == "all":
        if approve:
            targets = [r for r in rows if has_content(r) and r["status"] != "published"
                       and not csv_store.is_approved(r)]
        else:
            targets = [r for r in rows if csv_store.is_approved(r) and r["status"] != "published"]
        not_found = []
    else:
        targets, not_found = _match_rows(rows, table, titles)
        if approve:
            targets = [r for r in targets if has_content(r)]

    for r in targets:
        r["approved"] = "yes" if approve else ""
    if targets:
        csv_store.write_rows(rows, table)

    msg = f"{'Approved' if approve else 'Un-approved'} {len(targets)} {table}"
    if targets:
        msg += f": {_name_list(targets, table)}"
    if not_found:
        msg += f". Not found: {', '.join(not_found)}"
    return msg + "."


def remove_guesses(table: str, titles) -> str:
    """Remove flagged guesses from descriptions (one, several or "all")."""
    rows = csv_store.read_rows(table)
    if titles == "all":
        targets, not_found = [r for r in rows if pipeline.load_flags(r)], []
    else:
        targets, not_found = _match_rows(rows, table, titles)
        targets = [r for r in targets if pipeline.load_flags(r)]

    cleaned, left = [], 0
    for r in targets:
        before = r["new_description_html"]
        left += pipeline.remove_guesses(r)
        if r["new_description_html"] != before:
            cleaned.append(r)
            if r["status"] == "published":
                r["status"], r["approved"] = "edited", ""
    if targets:
        csv_store.write_rows(rows, table)

    msg = f"Removed guesses from {len(cleaned)} {table}"
    if cleaned:
        msg += f": {_name_list(cleaned, table)}"
    if left:
        msg += f". {left} flag(s) are in a meta title/description - edit those on the Review page"
    if not_found:
        msg += f". Not found: {', '.join(not_found)}"
    return msg + "."
