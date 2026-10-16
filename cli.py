"""
Command-line interface.

  python cli.py check                          test Shopify + AI connections
  python cli.py generate                       products -> seo_results.csv
  python cli.py generate --limit 3             only do 3 (good for a first test)
  python cli.py generate --regenerate          redo items that already have content
  python cli.py generate --type collections    collection pages (Version 2)
  python cli.py generate --type images         image alt text (Version 2)
  python cli.py guesses                        run the guess checker (Version 2)
  python cli.py push --dry-run                 show what would be published
  python cli.py push                           publish rows with approved = yes
  python cli.py undo                           put the originals back (Version 3)
  python cli.py demo-snapshot                  copy the main store's results into demo_data/
                                               (the online demo shows these; commit them)

Every command except "check" accepts --type products|collections|images.
"""
import argparse
import os
import shutil
import sys

import ai_generator
import config
import pipeline
import stores
from shopify_client import ShopifyClient


def check() -> None:
    print(f"Shopify store: {config.SHOPIFY_STORE_DOMAIN or '(not set)'}")
    try:
        print(f"  OK - connected to '{ShopifyClient().get_shop_name()}'")
    except Exception as e:
        print(f"  FAILED - {e}")

    print(f"AI provider: {config.AI_PROVIDER}")
    try:
        print(f"  OK - model replied: {ai_generator.test_ai()[:40]}")
    except Exception as e:
        print(f"  FAILED - {e}")


def demo_snapshot() -> None:
    """The online demo can't read the real CSVs (they're kept out of git), so it
    shows a copy of the MAIN store's results. Only product text - no secrets."""
    stores.use(stores.main_domain())   # never copy a client's data into the public demo
    os.makedirs(config.DEMO_DATA_DIR, exist_ok=True)
    for name in (config.CSV_PATH, config.COLLECTIONS_CSV_PATH, config.IMAGES_CSV_PATH,
                 config.AUDIT_JSON_PATH):
        if os.path.exists(name):
            shutil.copy(name, os.path.join(config.DEMO_DATA_DIR, os.path.basename(name)))
            print(f"  copied {name}")
    print(f"Done. Commit the {config.DEMO_DATA_DIR}/ folder to update the online demo.")


def main() -> None:
    # The Windows console can't show some symbols (like ⚠) - replace them instead of crashing
    sys.stdout.reconfigure(errors="replace")
    parser = argparse.ArgumentParser(description="Shopify AI SEO generator")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("check", help="test Shopify and AI connections")

    def add_type(p):
        p.add_argument("--type", default="products", choices=["products", "collections", "images"],
                       help="what to work on (default: products)")

    gen = sub.add_parser("generate", help="generate AI content into the CSV")
    add_type(gen)
    gen.add_argument("--limit", type=int, help="only process this many items")
    gen.add_argument("--regenerate", action="store_true", help="redo already generated items")

    guesses = sub.add_parser("guesses", help="flag guesses in generated text (Version 2)")
    add_type(guesses)
    guesses.add_argument("--recheck", action="store_true", help="check again, even if checked before")

    push = sub.add_parser("push", help="publish approved rows to Shopify")
    add_type(push)
    push.add_argument("--dry-run", action="store_true", help="show what would change, change nothing")

    undo = sub.add_parser("undo", help="put back what Shopify had before publishing (Version 3)")
    add_type(undo)

    sub.add_parser("demo-snapshot", help="copy the main store's results into demo_data/ for the online demo")

    args = parser.parse_args()
    try:
        if args.command == "check":
            check()
        elif args.command == "generate":
            pipeline.generate(limit=args.limit, regenerate=args.regenerate, table=args.type)
        elif args.command == "guesses":
            pipeline.check_guesses(recheck=args.recheck, table=args.type)
        elif args.command == "push":
            pipeline.push_approved(dry_run=args.dry_run, table=args.type)
        elif args.command == "undo":
            pipeline.undo(table=args.type)
        elif args.command == "demo-snapshot":
            demo_snapshot()
    except Exception as e:
        print(f"ERROR: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
