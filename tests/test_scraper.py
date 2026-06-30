"""Unit tests for LinkedInScraper — pure logic, no network calls."""
import os
import sys
import unittest

import pandas as pd
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from linkedin_scraper import LinkedInScraper  # noqa: E402


# A trimmed-down real guest job card.
SAMPLE_CARD = """
<li>
  <div class="base-card" data-entity-urn="urn:li:jobPosting:4387392881">
    <a class="base-card__full-link" href="https://www.linkedin.com/jobs/view/robotic-engineer-at-kickmaker-4387392881?position=1&pageNum=0">
      <span class="sr-only">Robotic Engineer</span>
    </a>
    <div class="base-search-card__info">
      <h3 class="base-search-card__title">Robotic Engineer</h3>
      <h4 class="base-search-card__subtitle"><a href="#">Kickmaker</a></h4>
      <div class="base-search-card__metadata">
        <span class="job-search-card__location">Austin, TX</span>
        <time class="job-search-card__listdate" datetime="2026-06-12">2 weeks ago</time>
      </div>
    </div>
  </div>
</li>
"""


class TestParsing(unittest.TestCase):
    def setUp(self):
        # fetch_details off so parsing stays offline.
        self.s = LinkedInScraper(roles=["Robotics Engineer"], fetch_details=False)

    def test_parse_card_fields(self):
        card = BeautifulSoup(SAMPLE_CARD, "html.parser").find("li")
        job = self.s._parse_job_card(card, "Robotics Engineer", scraped_at_utc="t0")
        self.assertEqual(job["job_id"], "4387392881")
        self.assertEqual(job["title"], "Robotic Engineer")
        self.assertEqual(job["company"], "Kickmaker")
        self.assertEqual(job["location"], "Austin, TX")  # not the title!
        self.assertEqual(job["posted_date"], "2026-06-12")
        self.assertEqual(job["status"], "NEW")
        self.assertTrue(job["link"].startswith("https://www.linkedin.com/jobs/view/"))
        self.assertNotIn("?", job["link"])

    def test_extract_job_id_from_urn(self):
        card = BeautifulSoup(SAMPLE_CARD, "html.parser").find("li")
        self.assertEqual(self.s._extract_job_id(card), "4387392881")


class TestPostedDays(unittest.TestCase):
    def setUp(self):
        self.s = LinkedInScraper(roles=["x"], fetch_details=False)

    def test_relative_text(self):
        self.assertEqual(self.s._parse_posted_days("just now"), 0)
        self.assertEqual(self.s._parse_posted_days("Yesterday"), 1)
        self.assertEqual(self.s._parse_posted_days("3 days ago"), 3)
        self.assertEqual(self.s._parse_posted_days("2 weeks ago"), 14)
        self.assertEqual(self.s._parse_posted_days("1 month ago"), 30)
        self.assertIsNone(self.s._parse_posted_days(""))
        self.assertIsNone(self.s._parse_posted_days("garbage"))

    def test_iso_date_preferred(self):
        # An ISO date should be used over the relative text.
        days = self.s._posted_days("2026-06-12", "nonsense")
        self.assertIsInstance(days, int)
        self.assertGreaterEqual(days, 0)


class TestClassify(unittest.TestCase):
    def test_scoring_and_dealbreaker(self):
        profile = {
            "skills": ["Python", "ROS"],
            "must_have": ["robotics"],
            "exclude_keywords": ["security clearance"],
            "scoring": {"title_role_match": 30, "skill_points": 4,
                        "must_have_points": 12, "exclude_penalty": 25},
        }
        s = LinkedInScraper(roles=["Robotics Engineer"], fetch_details=False, profile=profile)

        score, tags = s.classify_job(
            "Robotics Engineer", "We use Python and ROS for robotics.", "Robotics Engineer"
        )
        # 30 (title) + 12 (robotics) + 4 + 4 (python, ros) = 50
        self.assertEqual(score, 50)
        self.assertIn("robotics", tags)
        self.assertIn("Python", tags)

        score2, tags2 = s.classify_job(
            "Robotics Engineer", "Requires an active security clearance.", "Robotics Engineer"
        )
        # 30 (title) + 12 (robotics in title) - 25 (clearance) = 17
        self.assertEqual(score2, 17)
        self.assertIn("!security clearance", tags2)


class TestMerge(unittest.TestCase):
    def _row(self, job_id, **kw):
        base = {c: "" for c in [
            "job_id", "status", "notes", "title", "fit_score",
            "posted_days", "first_seen_utc", "last_seen_utc"]}
        base.update(job_id=job_id, status="NEW", fit_score=50,
                    posted_days=1, first_seen_utc="t1", last_seen_utc="t1")
        base.update(kw)
        return base

    def test_preserves_user_columns_and_adds_new(self):
        existing = pd.DataFrame([
            self._row("100", status="APPLIED", notes="referred", first_seen_utc="day1"),
        ])
        fresh = pd.DataFrame([
            self._row("100", status="NEW", notes="", last_seen_utc="day2"),  # re-seen
            self._row("200", status="NEW"),                                  # brand new
        ])
        merged = LinkedInScraper.merge_with_existing(fresh, existing)
        by_id = merged.set_index("job_id")

        # User edits on the re-seen job survive.
        self.assertEqual(by_id.loc["100", "status"], "APPLIED")
        self.assertEqual(by_id.loc["100", "notes"], "referred")
        self.assertEqual(by_id.loc["100", "first_seen_utc"], "day1")
        # New job added as NEW.
        self.assertEqual(by_id.loc["200", "status"], "NEW")
        self.assertEqual(len(merged), 2)

    def test_carries_forward_unseen_jobs(self):
        existing = pd.DataFrame([self._row("100", status="DISMISSED")])
        fresh = pd.DataFrame([self._row("200")])
        merged = LinkedInScraper.merge_with_existing(fresh, existing)
        self.assertEqual(set(merged["job_id"]), {"100", "200"})

    def test_empty_existing(self):
        fresh = pd.DataFrame([self._row("1")])
        merged = LinkedInScraper.merge_with_existing(fresh, pd.DataFrame())
        self.assertEqual(len(merged), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
