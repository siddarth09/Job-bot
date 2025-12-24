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

import pandas as pd

from linkedin_scraper import LinkedInScraper


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="LinkedIn job scraper")
    parser.add_argument(
        "--roles",
        type=str,
        default="Robotics Engineer,Autonomy Engineer,Controls Engineer",
        help="Comma separated list of role keywords",
    )
    parser.add_argument(
        "--location",
        type=str,
        default="United States",
        help="Location to filter jobs",
    )
    parser.add_argument(
        "--pages",
        type=int,
        default=5,
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
        default=1.0,
        help="Seconds to wait between page requests",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    roles = [r.strip() for r in args.roles.split(",") if r.strip()]
    scraper = LinkedInScraper(
        roles=roles,
        location=args.location,
        pages=args.pages,
        pause=args.pause,
        proxy=args.proxy,
    )
    df = scraper.scrape()
    if df.empty:
        print("No jobs were scraped. Sheet will be cleared.")
        if args.output == "google" and args.google_sheet_id:
            scraper.push_to_google_sheet(df, args.google_sheet_id, args.google_worksheet)
        return 0
    # Output
    if args.output == "csv":
        path = Path(args.csv_path)
        scraper.save_to_csv(df, str(path))
    elif args.output == "google":
        if not args.google_sheet_id:
            raise ValueError("--google_sheet_id is required when output is 'google'")
        scraper.push_to_google_sheet(df, args.google_sheet_id, args.google_worksheet)
    elif args.output == "notion":
        if not args.notion_token or not args.notion_database_id:
            raise ValueError("--notion_token and --notion_database_id are required for Notion output")
        scraper.push_to_notion(df, args.notion_token, args.notion_database_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())