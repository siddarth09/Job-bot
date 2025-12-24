from __future__ import annotations

import os
import re
import time
import logging
from datetime import datetime, timezone
from typing import List, Dict, Optional, Tuple

import requests
from bs4 import BeautifulSoup
import pandas as pd

try:
    import gspread  # type: ignore
    from google.oauth2.service_account import Credentials  # type: ignore
except Exception:
    gspread = None  # type: ignore
    Credentials = None  # type: ignore

try:
    from notion_client import Client as NotionClient  # type: ignore
except Exception:
    NotionClient = None  # type: ignore

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


class LinkedInScraper:
    """Scrape LinkedIn job postings for specified roles and locations."""

    BASE_URL = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"

    def __init__(
        self,
        roles: List[str],
        location: str = "United States",
        pages: int = 1,
        pause: float = 2.0,
        proxy: Optional[str] = None,
        max_posted_days: int = 7,
    ) -> None:
        """
        roles: Role keywords to search for.
        location: Location string used to filter jobs.
        pages: Number of pages per role (each page ~25 cards).
        pause: Seconds between requests (safety throttle). Enforced minimum = 2.0.
        proxy: Optional proxy URL.
        max_posted_days: Drop jobs older than this many days.
        """
        self.roles = [r.strip() for r in roles if r.strip()]
        self.location = location
        self.pages = max(1, pages)
        self.pause = max(2.0, float(pause))
        self.max_posted_days = int(max_posted_days)

        self.session = requests.Session()
        if proxy:
            self.session.proxies.update({"http": proxy, "https": proxy})

        self.session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept-Language": "en-US,en;q=0.9",
            }
        )

    # -----------------------------
    # Scraping
    # -----------------------------
    def scrape(self) -> pd.DataFrame:
        """
        Returns a dataframe with columns:
        role_keyword, title, company, location, posted, posted_days, link,
        description, fit_score, tags, scraped_at_utc
        """
        all_jobs: List[Dict[str, object]] = []
        scraped_at = datetime.now(timezone.utc).isoformat()

        for role in self.roles:
            logger.info(f"Scraping role: {role}")
            jobs = self._scrape_role(role, scraped_at_utc=scraped_at)
            all_jobs.extend(jobs)

        df = pd.DataFrame(all_jobs)

        if df.empty:
            return df

        # Drop duplicates by link
        if "link" in df.columns:
            df = df.drop_duplicates(subset=["link"])

        # Keep only jobs with a parsable age <= max_posted_days
        if "posted_days" in df.columns:
            df = df[(df["posted_days"].isna()) | (df["posted_days"] <= 7)]

        # Sort: newest first, then score
        if "posted_days" in df.columns and "fit_score" in df.columns:
            df = df.sort_values(by=["posted_days", "fit_score"], ascending=[True, False])

        return df.reset_index(drop=True)

    def _scrape_role(self, role: str, scraped_at_utc: str) -> List[Dict[str, object]]:
        jobs: List[Dict[str, object]] = []

        for page in range(self.pages):
            start = page * 25
            params = {"keywords": role, "location": self.location, "start": start}

            try:
                resp = self.session.get(self.BASE_URL, params=params, timeout=15)
                if resp.status_code != 200:
                    logger.warning(
                        f"Received status {resp.status_code} for role {role} page {page + 1}"
                    )
                    break

                soup = BeautifulSoup(resp.text, "html.parser")
                cards = soup.find_all("li")
                if not cards:
                    cards = soup.find_all("div", class_=re.compile("job-card|result"))

                for card in cards:
                    job = self._parse_job_card(card, role, scraped_at_utc=scraped_at_utc)
                    if job:
                        jobs.append(job)

                time.sleep(self.pause)

            except requests.RequestException as exc:
                logger.error(f"Error fetching jobs for {role}: {exc}")
                break

        return jobs

    def _parse_job_card(
        self, element, role_keyword: str, scraped_at_utc: str
    ) -> Optional[Dict[str, object]]:
        """
        Parse a single job card element into a dict.
        Returns None if essential fields cannot be found.
        """
        try:
            # Title
            title_elem = element.find("h3")
            title = title_elem.get_text(strip=True) if title_elem else ""

            # Company
            company_elem = element.find("h4")
            company = company_elem.get_text(strip=True) if company_elem else ""

            # Location + "posted" text
            metadata = element.find_all("span")
            location = ""
            posted_text = ""

            for span in metadata:
                text = span.get_text(strip=True)
                if re.search(r"\bago\b", text.lower()) or text.lower() in {
                    "just now",
                    "today",
                    "yesterday",
                }:
                    posted_text = text
                elif not location:
                    location = text

            posted_days = self._parse_posted_days(posted_text)

            # Link to job posting (robust)
            link = ""
            link_tag = element.find("a", href=True)
            if link_tag:
                href = link_tag["href"].strip()
                if href.startswith("http"):
                    link = href
                else:
                    link = "https://www.linkedin.com" + href

                # Remove tracking params for stable dedup keys
                link = link.split("?")[0]

            # Description snippet (guest endpoint gives short snippet)
            desc = ""
            desc_elem = element.find("p")
            if desc_elem:
                desc = desc_elem.get_text(" ", strip=True)

            # Compute fit score and tags
            fit_score, tags = self.classify_job(title, desc, role_keyword)

            # If we couldn't parse posted_days, keep it but it will be filtered out later
            return {
                "role_keyword": role_keyword,
                "title": title,
                "company": company,
                "location": location,
                "posted": posted_text,
                "posted_days": posted_days,
                "link": link,
                "description": desc,
                "fit_score": int(fit_score),
                "tags": ", ".join(tags),
                "scraped_at_utc": scraped_at_utc,
            }

        except Exception as exc:
            logger.debug(f"Failed to parse job card: {exc}")
            return None

    def _parse_posted_days(self, text: str) -> Optional[int]:
        """
        Convert LinkedIn 'posted' text like:
        - '6 days ago'
        - '2 weeks ago'
        - '3 hours ago'
        - 'Just now'
        - 'Today'
        - 'Yesterday'
        to integer days.

        Returns None if unknown/unparseable.
        """
        if not text:
            return None

        t = text.strip().lower()

        if t == "just now":
            return 0
        if t == "today":
            return 0
        if t == "yesterday":
            return 1

        # Common: "X days ago", "X weeks ago", "X hours ago"
        m = re.search(r"(\d+)\s*(hour|day|week|month|year)s?\s+ago", t)
        if not m:
            return None

        value = int(m.group(1))
        unit = m.group(2)

        if unit == "hour":
            return 0
        if unit == "day":
            return value
        if unit == "week":
            return value * 7
        if unit == "month":
            return value * 30
        if unit == "year":
            return value * 365

        return None

    # -----------------------------
    # Classification
    # -----------------------------
    def classify_job(self, title: str, description: str, role_keyword: str) -> Tuple[int, List[str]]:
        tags: List[str] = []
        score = 0

        if role_keyword.lower() in title.lower():
            score += 40

        keywords = [
            "ROS",
            "ROS 2",
            "robotics",
            "autonomy",
            "controls",
            "control",
            "reinforcement learning",
            "rl",
            "simulation",
            "control theory",
            "optimization",
            "MPC",
            "SLAM",
            "navigation",
            "state estimation",
            "localization",
            "Python",
            "C++",
            "machine learning",
        ]

        desc_lower = (description or "").lower()
        title_lower = (title or "").lower()

        for kw in keywords:
            if kw.lower() in title_lower or kw.lower() in desc_lower:
                tags.append(kw)
                score += 5

        score = min(score, 100)
        return score, tags

    # -----------------------------
    # Export functions
    # -----------------------------
    def save_to_csv(self, df: pd.DataFrame, path: str) -> None:
        df.to_csv(path, index=False, encoding="utf-8-sig")
        logger.info(f"Saved {len(df)} jobs to CSV at {path}")

    def push_to_google_sheet(self, df: pd.DataFrame, sheet_id: str, worksheet_name: str = "Jobs") -> None:
        """
        Overwrite the worksheet contents each run (removes old jobs automatically).

        Requirements:
          - GOOGLE_APPLICATION_CREDENTIALS points to service account json.
          - Google Sheets API enabled in the GCP project.
          - Sheet shared with the service account email.
        """
        if gspread is None or Credentials is None:
            raise ImportError(
                "gspread/google-auth not installed. "
                "Run: pip install gspread google-auth"
            )

        creds_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
        if not creds_path:
            raise RuntimeError("GOOGLE_APPLICATION_CREDENTIALS is not set.")

        creds = Credentials.from_service_account_file(
            creds_path,
            scopes=SCOPES,
        )

        client = gspread.authorize(creds)
        sheet = client.open_by_key(sheet_id)

        # Ensure worksheet exists
        try:
            worksheet = sheet.worksheet(worksheet_name)
        except Exception:
            worksheet = sheet.add_worksheet(title=worksheet_name, rows="1000", cols="30")

        # Build rows
        if df.empty:
            # Clear sheet but keep header (optional)
            worksheet.clear()
            worksheet.append_row(
                [
                    "role_keyword",
                    "title",
                    "company",
                    "location",
                    "posted",
                    "posted_days",
                    "link",
                    "description",
                    "fit_score",
                    "tags",
                    "scraped_at_utc",
                ]
            )
            logger.info(f"No jobs to write. Cleared worksheet {worksheet_name} and wrote header.")
            return

        df_out = df.copy()

        # Overwrite sheet contents each run
        worksheet.clear()
        worksheet.append_row(list(df_out.columns))
        rows = df_out.fillna("").values.tolist()
        worksheet.append_rows(rows, value_input_option="USER_ENTERED")

        logger.info(
            f"Overwrote worksheet '{worksheet_name}' with {len(rows)} rows "
            f"(filtered to <= {self.max_posted_days} days old)."
        )

    def push_to_notion(self, df: pd.DataFrame, notion_token: str, database_id: str) -> None:
        if NotionClient is None:
            raise ImportError("notion-client not installed. Run: pip install notion-client")

        notion = NotionClient(auth=notion_token)
        for _, row in df.iterrows():
            properties = {
                "Name": {
                    "title": [{"text": {"content": f"{row['title']} @ {row['company']}"}}]
                },
                "Role Keyword": {"rich_text": [{"text": {"content": str(row["role_keyword"])}}]},
                "Company": {"rich_text": [{"text": {"content": str(row["company"])}}]},
                "Location": {"rich_text": [{"text": {"content": str(row["location"])}}]},
                "Posted": {"rich_text": [{"text": {"content": str(row.get("posted", ""))}}]},
                "Link": {"url": str(row.get("link", ""))},
                "Fit Score": {"number": int(row.get("fit_score", 0))},
                "Tags": {
                    "multi_select": [{"name": t.strip()} for t in str(row.get("tags", "")).split(",") if t.strip()]
                },
                "Description": {
                    "rich_text": [{"text": {"content": str(row.get("description", ""))[:2000]}}]
                },
            }
            notion.pages.create(parent={"database_id": database_id}, properties=properties)
            time.sleep(0.3)

        logger.info(f"Inserted {len(df)} rows into Notion database")
