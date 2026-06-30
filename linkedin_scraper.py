from __future__ import annotations

import os
import re
import time
import logging
from datetime import datetime, timezone, date
from typing import List, Dict, Optional, Tuple

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
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

# Canonical column order for all outputs. `status` and `notes` are user-owned
# and preserved across runs (see merge_with_existing).
COLUMNS = [
    "job_id",
    "status",
    "notes",
    "role_keyword",
    "title",
    "company",
    "location",
    "posted",
    "posted_days",
    "posted_date",
    "seniority",
    "employment_type",
    "fit_score",
    "tags",
    "link",
    "description",
    "first_seen_utc",
    "last_seen_utc",
]

# User-owned columns: never overwritten by the scraper once set by the user.
USER_COLUMNS = ["status", "notes"]

DEFAULT_STATUS = "NEW"


class LinkedInScraper:
    """Scrape LinkedIn job postings for specified roles and locations."""

    SEARCH_URL = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
    DETAIL_URL = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}"

    def __init__(
        self,
        roles: List[str],
        location: str = "United States",
        pages: int = 1,
        pause: float = 2.0,
        proxy: Optional[str] = None,
        max_posted_days: int = 7,
        fetch_details: bool = True,
        profile: Optional[Dict] = None,
    ) -> None:
        """
        roles: Role keywords to search for.
        location: Location string used to filter jobs.
        pages: Number of pages per role (each page ~25 cards).
        pause: Seconds between requests (safety throttle). Enforced minimum = 2.0.
        proxy: Optional proxy URL.
        max_posted_days: Drop jobs older than this many days.
        fetch_details: Fetch each job's detail page for full description + criteria.
        profile: Scoring profile (skills, must_have, exclude_keywords, scoring).
        """
        self.roles = [r.strip() for r in roles if r.strip()]
        self.location = location
        self.pages = max(1, pages)
        self.pause = max(2.0, float(pause))
        self.max_posted_days = int(max_posted_days)
        self.fetch_details = fetch_details
        self.profile = {**DEFAULT_PROFILE, **(profile or {})}
        self.scoring = {**DEFAULT_PROFILE["scoring"], **self.profile.get("scoring", {})}

        self.session = requests.Session()
        if proxy:
            self.session.proxies.update({"http": proxy, "https": proxy})

        # Retry on transient errors and rate limits with exponential backoff.
        retry = Retry(
            total=4,
            backoff_factor=2.0,  # waits 0s, 2s, 4s, 8s ...
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET"],
            respect_retry_after_header=True,
        )
        adapter = HTTPAdapter(max_retries=retry)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)

        self.session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept-Language": "en-US,en;q=0.9",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            }
        )

    # -----------------------------
    # Scraping
    # -----------------------------
    def scrape(self) -> pd.DataFrame:
        """Scrape all roles and return a deduped, freshness-filtered DataFrame."""
        all_jobs: List[Dict[str, object]] = []
        scraped_at = datetime.now(timezone.utc).isoformat()

        for role in self.roles:
            logger.info(f"Scraping role: {role}")
            jobs = self._scrape_role(role, scraped_at_utc=scraped_at)
            logger.info(f"  -> {len(jobs)} cards parsed for '{role}'")
            all_jobs.extend(jobs)

        df = pd.DataFrame(all_jobs)
        if df.empty:
            return df

        # Dedup by stable job_id (fall back to link).
        key = "job_id" if "job_id" in df.columns else "link"
        df = df.drop_duplicates(subset=[key])

        # Keep jobs whose age is unknown or within the freshness window.
        if "posted_days" in df.columns:
            df = df[(df["posted_days"].isna()) | (df["posted_days"] <= self.max_posted_days)]

        # Sort newest first, then by fit score.
        if "posted_days" in df.columns and "fit_score" in df.columns:
            df = df.sort_values(by=["posted_days", "fit_score"], ascending=[True, False])

        return df.reset_index(drop=True)

    def _scrape_role(self, role: str, scraped_at_utc: str) -> List[Dict[str, object]]:
        jobs: List[Dict[str, object]] = []

        for page in range(self.pages):
            start = page * 25
            params = {"keywords": role, "location": self.location, "start": start}

            try:
                resp = self.session.get(self.SEARCH_URL, params=params, timeout=20)
                if resp.status_code == 429:
                    logger.warning("Rate limited (429). Backing off and stopping this role.")
                    break
                if resp.status_code != 200:
                    logger.warning(
                        f"Status {resp.status_code} for role '{role}' page {page + 1}; stopping role."
                    )
                    break

                soup = BeautifulSoup(resp.text, "html.parser")
                cards = soup.find_all("div", class_="base-card")
                if not cards:
                    cards = soup.find_all("li")
                if not cards:
                    logger.info(f"  No cards on page {page + 1}; reached end of results.")
                    break

                for card in cards:
                    job = self._parse_job_card(card, role, scraped_at_utc=scraped_at_utc)
                    if job:
                        jobs.append(job)

                time.sleep(self.pause)

            except requests.RequestException as exc:
                logger.error(f"Error fetching jobs for '{role}': {exc}")
                break

        return jobs

    def _parse_job_card(
        self, element, role_keyword: str, scraped_at_utc: str
    ) -> Optional[Dict[str, object]]:
        """Parse a single job card into a dict, or None if it's not a job card."""
        try:
            job_id = self._extract_job_id(element)

            title = self._text(element, "h3", "base-search-card__title")
            company = self._text(element, "h4", "base-search-card__subtitle")
            location = self._text(element, "span", "job-search-card__location")

            # Posted date: prefer the machine-readable datetime attribute.
            posted_text = ""
            posted_date = ""
            time_elem = element.find("time")
            if time_elem:
                posted_text = time_elem.get_text(strip=True)
                posted_date = (time_elem.get("datetime") or "").strip()

            posted_days = self._posted_days(posted_date, posted_text)

            # Link to job posting.
            link = ""
            link_tag = element.find("a", href=True)
            if link_tag:
                href = link_tag["href"].strip()
                link = href if href.startswith("http") else "https://www.linkedin.com" + href
                link = link.split("?")[0]

            if not (title or job_id):
                return None  # not a real job card (e.g. a stray <li>)

            # Full description + criteria from the detail endpoint.
            description, seniority, employment_type = "", "", ""
            if self.fetch_details and job_id:
                description, seniority, employment_type = self._fetch_details(job_id)

            fit_score, tags = self.classify_job(title, description, role_keyword)

            return {
                "job_id": job_id,
                "status": DEFAULT_STATUS,
                "notes": "",
                "role_keyword": role_keyword,
                "title": title,
                "company": company,
                "location": location,
                "posted": posted_text,
                "posted_days": posted_days,
                "posted_date": posted_date,
                "seniority": seniority,
                "employment_type": employment_type,
                "fit_score": int(fit_score),
                "tags": ", ".join(tags),
                "link": link,
                "description": description,
                "first_seen_utc": scraped_at_utc,
                "last_seen_utc": scraped_at_utc,
            }

        except Exception as exc:
            logger.debug(f"Failed to parse job card: {exc}")
            return None

    @staticmethod
    def _text(element, tag: str, class_name: str) -> str:
        """Return stripped text of the first matching element, or ''."""
        found = element.find(tag, class_=class_name)
        return found.get_text(strip=True) if found else ""

    @staticmethod
    def _extract_job_id(element) -> str:
        """Pull the stable jobPosting id from data-entity-urn or the link."""
        card = element
        if not card.get("data-entity-urn"):
            card = element.find(attrs={"data-entity-urn": True}) or element
        urn = card.get("data-entity-urn", "") if hasattr(card, "get") else ""
        m = re.search(r"jobPosting:(\d+)", urn)
        if m:
            return m.group(1)
        link_tag = element.find("a", href=True)
        if link_tag:
            m = re.search(r"-(\d+)(?:\?|$)", link_tag["href"])
            if m:
                return m.group(1)
        return ""

    def _fetch_details(self, job_id: str) -> Tuple[str, str, str]:
        """Fetch full description + (seniority, employment_type) for a job id."""
        url = self.DETAIL_URL.format(job_id=job_id)
        try:
            resp = self.session.get(url, timeout=20)
            if resp.status_code != 200:
                return "", "", ""
            soup = BeautifulSoup(resp.text, "html.parser")

            desc_elem = soup.find("div", class_=re.compile("show-more-less-html__markup|description__text"))
            description = desc_elem.get_text(" ", strip=True) if desc_elem else ""

            criteria: Dict[str, str] = {}
            for item in soup.find_all("li", class_=re.compile("description__job-criteria-item")):
                header = item.find("h3", class_=re.compile("description__job-criteria-subheader"))
                value = item.find("span", class_=re.compile("description__job-criteria-text"))
                if header and value:
                    criteria[header.get_text(strip=True).lower()] = value.get_text(strip=True)

            seniority = criteria.get("seniority level", "")
            employment_type = criteria.get("employment type", "")

            time.sleep(self.pause)
            return description, seniority, employment_type
        except requests.RequestException as exc:
            logger.debug(f"Failed to fetch details for {job_id}: {exc}")
            return "", "", ""

    def _posted_days(self, posted_date: str, posted_text: str) -> Optional[int]:
        """Days since posting. Prefer the ISO date; fall back to relative text."""
        if posted_date:
            try:
                d = date.fromisoformat(posted_date)
                return max(0, (datetime.now(timezone.utc).date() - d).days)
            except ValueError:
                pass
        return self._parse_posted_days(posted_text)

    def _parse_posted_days(self, text: str) -> Optional[int]:
        """Convert relative 'posted' text (e.g. '2 weeks ago') to integer days."""
        if not text:
            return None
        t = text.strip().lower()
        if t in {"just now", "today"}:
            return 0
        if t == "yesterday":
            return 1
        m = re.search(r"(\d+)\s*(hour|day|week|month|year)s?\s+ago", t)
        if not m:
            return None
        value, unit = int(m.group(1)), m.group(2)
        return {
            "hour": 0,
            "day": value,
            "week": value * 7,
            "month": value * 30,
            "year": value * 365,
        }.get(unit)

    # -----------------------------
    # Classification
    # -----------------------------
    def classify_job(self, title: str, description: str, role_keyword: str) -> Tuple[int, List[str]]:
        """Weighted fit score driven by the profile.

        Returns (score 0-100, matched-skill tags). Dealbreaker matches subtract
        from the score and are tagged with a leading '!' so they're easy to spot.
        """
        tags: List[str] = []
        score = 0
        haystack = f"{title or ''} {description or ''}".lower()

        if role_keyword.lower() in (title or "").lower():
            score += self.scoring["title_role_match"]

        for kw in self.profile.get("must_have", []):
            if kw.lower() in haystack:
                tags.append(kw)
                score += self.scoring["must_have_points"]

        for kw in self.profile.get("skills", []):
            if kw.lower() in haystack:
                tags.append(kw)
                score += self.scoring["skill_points"]

        for kw in self.profile.get("exclude_keywords", []):
            if kw.lower() in haystack:
                tags.append(f"!{kw}")
                score -= self.scoring["exclude_penalty"]

        return max(0, min(score, 100)), tags

    # -----------------------------
    # Persistence / merge
    # -----------------------------
    @staticmethod
    def merge_with_existing(new_df: pd.DataFrame, existing_df: pd.DataFrame) -> pd.DataFrame:
        """Merge a fresh scrape with prior results.

        - Preserves user-owned columns (status, notes) for jobs already tracked.
        - Preserves first_seen_utc; updates last_seen_utc and re-scraped fields.
        - Adds brand-new jobs with status=NEW.
        """
        if existing_df is None or existing_df.empty:
            return new_df.reset_index(drop=True)
        if new_df is None or new_df.empty:
            return existing_df.reset_index(drop=True)

        existing = existing_df.set_index("job_id", drop=False)
        merged_rows: List[Dict[str, object]] = []
        seen_ids = set()

        for _, row in new_df.iterrows():
            jid = row["job_id"]
            seen_ids.add(jid)
            record = row.to_dict()
            if jid in existing.index:
                prior = existing.loc[jid]
                # Keep user edits.
                for col in USER_COLUMNS:
                    prior_val = prior.get(col, "")
                    if pd.notna(prior_val) and str(prior_val).strip():
                        record[col] = prior_val
                # Preserve original discovery time.
                first_seen = prior.get("first_seen_utc", "")
                if pd.notna(first_seen) and str(first_seen).strip():
                    record["first_seen_utc"] = first_seen
            merged_rows.append(record)

        # Carry forward previously seen jobs that didn't appear this run.
        for jid, prior in existing.iterrows():
            if jid not in seen_ids:
                merged_rows.append(prior.to_dict())

        merged = pd.DataFrame(merged_rows)
        if "posted_days" in merged.columns and "fit_score" in merged.columns:
            merged = merged.sort_values(
                by=["posted_days", "fit_score"], ascending=[True, False], na_position="last"
            )
        return merged.reset_index(drop=True)

    @staticmethod
    def _order_columns(df: pd.DataFrame) -> pd.DataFrame:
        """Reorder to canonical COLUMNS, keeping any extras at the end."""
        cols = [c for c in COLUMNS if c in df.columns]
        extras = [c for c in df.columns if c not in COLUMNS]
        return df[cols + extras]

    # -----------------------------
    # Export functions
    # -----------------------------
    def save_to_csv(self, df: pd.DataFrame, path: str) -> None:
        df = self._order_columns(df)
        df.to_csv(path, index=False, encoding="utf-8-sig")
        logger.info(f"Saved {len(df)} jobs to CSV at {path}")

    @staticmethod
    def read_csv(path: str) -> pd.DataFrame:
        """Read an existing CSV of jobs, or return an empty frame."""
        if os.path.exists(path):
            try:
                return pd.read_csv(path, dtype={"job_id": str})
            except Exception as exc:
                logger.warning(f"Could not read existing CSV {path}: {exc}")
        return pd.DataFrame()

    def _gspread_client(self):
        if gspread is None or Credentials is None:
            raise ImportError(
                "gspread/google-auth not installed. Run: pip install gspread google-auth"
            )
        creds_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
        if not creds_path:
            raise RuntimeError("GOOGLE_APPLICATION_CREDENTIALS is not set.")
        creds = Credentials.from_service_account_file(creds_path, scopes=SCOPES)
        return gspread.authorize(creds)

    def read_google_sheet(self, sheet_id: str, worksheet_name: str = "Jobs") -> pd.DataFrame:
        """Read existing rows from a worksheet, or return an empty frame."""
        client = self._gspread_client()
        try:
            worksheet = client.open_by_key(sheet_id).worksheet(worksheet_name)
        except Exception:
            return pd.DataFrame()
        records = worksheet.get_all_records()
        df = pd.DataFrame(records)
        if not df.empty and "job_id" in df.columns:
            df["job_id"] = df["job_id"].astype(str)
        return df

    def push_to_google_sheet(self, df: pd.DataFrame, sheet_id: str, worksheet_name: str = "Jobs") -> None:
        """Overwrite the worksheet with `df` (already merged with prior rows)."""
        client = self._gspread_client()
        sheet = client.open_by_key(sheet_id)
        try:
            worksheet = sheet.worksheet(worksheet_name)
        except Exception:
            worksheet = sheet.add_worksheet(title=worksheet_name, rows="1000", cols="30")

        worksheet.clear()
        if df.empty:
            worksheet.append_row(COLUMNS)
            logger.info(f"No jobs to write. Cleared '{worksheet_name}' and wrote header.")
            return

        df_out = self._order_columns(df)
        worksheet.append_row(list(df_out.columns))
        rows = df_out.fillna("").astype(str).values.tolist()
        worksheet.append_rows(rows, value_input_option="USER_ENTERED")
        logger.info(f"Wrote {len(rows)} rows to worksheet '{worksheet_name}'.")

    def push_to_notion(self, df: pd.DataFrame, notion_token: str, database_id: str) -> None:
        if NotionClient is None:
            raise ImportError("notion-client not installed. Run: pip install notion-client")

        notion = NotionClient(auth=notion_token)
        for _, row in df.iterrows():
            properties = {
                "Name": {"title": [{"text": {"content": f"{row['title']} @ {row['company']}"}}]},
                "Role Keyword": {"rich_text": [{"text": {"content": str(row["role_keyword"])}}]},
                "Company": {"rich_text": [{"text": {"content": str(row["company"])}}]},
                "Location": {"rich_text": [{"text": {"content": str(row["location"])}}]},
                "Posted": {"rich_text": [{"text": {"content": str(row.get("posted", ""))}}]},
                "Link": {"url": str(row.get("link", ""))},
                "Fit Score": {"number": int(row.get("fit_score", 0))},
                "Tags": {
                    "multi_select": [
                        {"name": t.strip()} for t in str(row.get("tags", "")).split(",") if t.strip()
                    ]
                },
                "Description": {
                    "rich_text": [{"text": {"content": str(row.get("description", ""))[:2000]}}]
                },
            }
            notion.pages.create(parent={"database_id": database_id}, properties=properties)
            time.sleep(0.3)

        logger.info(f"Inserted {len(df)} rows into Notion database")


# Fallback profile used when no profile.yaml is supplied.
DEFAULT_PROFILE = {
    "skills": [
        "ROS", "ROS 2", "C++", "Python", "SLAM", "state estimation",
        "localization", "navigation", "MPC", "optimization", "control theory",
        "reinforcement learning", "simulation", "perception", "machine learning",
    ],
    "must_have": ["robotics", "autonomy", "controls"],
    "exclude_keywords": [],
    "scoring": {
        "title_role_match": 30,
        "skill_points": 4,
        "must_have_points": 12,
        "exclude_penalty": 25,
    },
}
