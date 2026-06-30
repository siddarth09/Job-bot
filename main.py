#!/usr/bin/env python3
"""
Entry point for running the LinkedIn job scraper.

Example usage:

    python main.py \
        --roles "Robotics Engineer,Autonomy Engineer,Controls Engineer" \
        --location "United States" \
        --pages 2 \
        --output csv \
        --csv_path scraped_jobs.csv

See README.md for more details.
"""

import argparse
import sys
from pathlib import Path

from linkedin_scraper import LinkedInScraper


def load_config(path: str | None) -> dict:
    """Load a profile.yaml if given (or a profile.yaml in CWD), else {}."""
    if path is None and Path("profile.yaml").exists():
        path = "profile.yaml"
    if not path:
        return {}
    try:
        import yaml  # type: ignore
    except ImportError:
        print("PyYAML not installed; ignoring config. Run: pip install PyYAML", file=sys.stderr)
        return {}
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="LinkedIn job scraper")
    parser.add_argument(
        "--config",
        type=str,
        default=None,
        help="Path to a profile.yaml. CLI flags override values from it.",
    )
    parser.add_argument(
        "--roles",
        type=str,
        default=None,
        help="Comma separated list of role keywords (overrides config)",
    )
    parser.add_argument(
        "--location",
        type=str,
        default=None,
        help="Location to filter jobs (overrides config)",
    )
    parser.add_argument(
        "--pages",
        type=int,
        default=None,
        help="Number of pages per role to scrape (each page is ~25 jobs)",
    )
    parser.add_argument(
        "--output",
        choices=["csv", "google", "notion"],
        default="csv",
        help="Output destination: csv, google or notion",
    )
    parser.add_argument(
        "--csv_path",
        type=str,
        default="scraped_jobs.csv",
        help="CSV file path for output when --output=csv",
    )
    parser.add_argument(
        "--google_sheet_id",
        type=str,
        default=None,
        help="Google Sheet ID when --output=google",
    )
    parser.add_argument(
        "--google_worksheet",
        type=str,
        default="Jobs",
        help="Worksheet name in Google Sheet",
    )
    parser.add_argument(
        "--notion_token",
        type=str,
        default=None,
        help="Notion integration token when --output=notion",
    )
    parser.add_argument(
        "--notion_database_id",
        type=str,
        default=None,
        help="Notion database ID when --output=notion",
    )
    parser.add_argument(
        "--proxy",
        type=str,
        default=None,
        help="Optional HTTP proxy URL (e.g. http://localhost:8080)",
    )
    parser.add_argument(
        "--pause",
        type=float,
        default=2.0,
        help="Seconds to wait between requests (min 2.0)",
    )
    parser.add_argument(
        "--max_posted_days",
        type=int,
        default=None,
        help="Drop jobs older than this many days (overrides config)",
    )
    parser.add_argument(
        "--no_details",
        action="store_true",
        help="Skip fetching full job descriptions (faster, but weaker fit scores)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    config = load_config(args.config)

    # Resolve each value: CLI flag wins, then config, then hardcoded default.
    if args.roles is not None:
        roles = [r.strip() for r in args.roles.split(",") if r.strip()]
    else:
        roles = config.get("roles") or ["Robotics Engineer", "Robotics Autonomy Engineer"]

    location = args.location or config.get("location") or "United States"
    pages = args.pages if args.pages is not None else config.get("pages", 5)
    max_posted_days = (
        args.max_posted_days if args.max_posted_days is not None
        else config.get("max_posted_days", 7)
    )

    scraper = LinkedInScraper(
        roles=roles,
        location=location,
        pages=pages,
        pause=args.pause,
        proxy=args.proxy,
        max_posted_days=max_posted_days,
        fetch_details=not args.no_details,
        profile=config,
    )

    fresh = scraper.scrape()
    print(f"Scraped {len(fresh)} fresh jobs (after dedup + freshness filter).")

    # Merge with prior results so user-set status/notes survive across runs.
    if args.output == "csv":
        path = Path(args.csv_path)
        existing = scraper.read_csv(str(path))
        merged = scraper.merge_with_existing(fresh, existing)
        scraper.save_to_csv(merged, str(path))
        new_count = len(merged) - len(existing)
        print(f"Total tracked: {len(merged)} ({max(new_count, 0)} new this run).")
    elif args.output == "google":
        if not args.google_sheet_id:
            raise ValueError("--google_sheet_id is required when output is 'google'")
        existing = scraper.read_google_sheet(args.google_sheet_id, args.google_worksheet)
        merged = scraper.merge_with_existing(fresh, existing)
        scraper.push_to_google_sheet(merged, args.google_sheet_id, args.google_worksheet)
    elif args.output == "notion":
        if not args.notion_token or not args.notion_database_id:
            raise ValueError("--notion_token and --notion_database_id are required for Notion output")
        if fresh.empty:
            print("No new jobs to push to Notion.")
            return 0
        scraper.push_to_notion(fresh, args.notion_token, args.notion_database_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())