# Job Search Automation

A small, dependable pipeline that scrapes LinkedIn job postings for the roles you
care about, scores each one against **your** profile, and keeps a running tracker
of everything you've seen — in a CSV, a Google Sheet, or Notion.

It's built to run **daily** (locally or via GitHub Actions) and is designed so the
output is something you actually work out of: mark a job `APPLIED`, jot a note, and
those edits survive every future run.

> **Note on LinkedIn:** there's no public job-search API. This uses LinkedIn's
> public *guest* endpoints (the same ones that serve logged-out job pages). They
> work today but are unofficial and rate-limited — scrape gently (daily, not
> hourly) and respect LinkedIn's terms of service.

## What it does

1. **Search** — one query per role keyword, paged through results.
2. **Enrich** — fetches each posting's detail page for the *full* description plus
   seniority level and employment type (the search cards alone don't include these).
3. **Score** — a transparent, weighted fit score (0–100) from your `profile.yaml`:
   skills you have, must-have signals, and dealbreakers that subtract points.
4. **Track** — merges with prior results by stable LinkedIn job ID. Your
   `status` and `notes` columns are **never overwritten**; new postings show up as
   `NEW`, and jobs you've already triaged stay put.
5. **Export** — CSV (default), Google Sheets, or Notion.

## Quick start

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# Edit profile.yaml to describe yourself, then:
python main.py --config profile.yaml --output csv --csv_path jobs.csv
```

Open `jobs.csv`, sort by `fit_score`, and start triaging. Set a job's `status`
to `APPLIED` / `INTERVIEWING` / `DISMISSED` and add `notes` — re-run any time and
your edits are preserved while new postings are appended.

## Your profile (`profile.yaml`)

`profile.yaml` is the one file you edit. It controls what gets searched and how
jobs are scored:

```yaml
roles: [Robotics Engineer, Controls Engineer]
location: United States
pages: 5
max_posted_days: 7

skills: [ROS, C++, Python, SLAM, state estimation]   # +skill_points each
must_have: [robotics, autonomy]                       # +must_have_points each
exclude_keywords: [security clearance, principal]     # -exclude_penalty each

scoring:
  title_role_match: 30
  skill_points: 4
  must_have_points: 12
  exclude_penalty: 25
```

CLI flags override config values, e.g. `--pages 2` for a quick test run.

## Output columns

| Column | Meaning |
|---|---|
| `job_id` | Stable LinkedIn posting ID (dedup key) |
| `status` | **You own this** — NEW / APPLIED / INTERVIEWING / DISMISSED |
| `notes` | **You own this** — free text, preserved across runs |
| `fit_score` | 0–100 from your profile |
| `tags` | Matched skills; dealbreakers prefixed with `!` |
| `title`, `company`, `location` | |
| `posted`, `posted_days`, `posted_date` | Freshness (parsed from the posting's date) |
| `seniority`, `employment_type` | From the detail page |
| `link`, `description` | |
| `first_seen_utc`, `last_seen_utc` | When the bot first/last saw it |

## Google Sheets (recommended for daily use)

The Sheet becomes your living tracker — edit `status`/`notes` in the browser and
the bot preserves them.

1. Create a Google Cloud service account, enable the Sheets API, download its JSON key.
2. Share your Sheet with the service account's email.
3. Point `GOOGLE_APPLICATION_CREDENTIALS` at the key file.

```bash
export GOOGLE_APPLICATION_CREDENTIALS=credentials.json
python main.py --config profile.yaml --output google \
  --google_sheet_id YOUR_SHEET_ID --google_worksheet Jobs
```

## Notion

```bash
python main.py --config profile.yaml --output notion \
  --notion_token YOUR_TOKEN --notion_database_id YOUR_DB_ID
```

## Run it on a schedule (GitHub Actions)

`.github/workflows/job_bot.yaml` runs the scraper once a day and writes to your
Sheet. Add two repository secrets:

- `GOOGLE_CREDENTIALS_JSON` — the full service-account JSON
- `GOOGLE_SHEET_ID` — your Sheet's ID

## Tests

```bash
python -m unittest discover -s tests -v
```

Parsing, date handling, scoring, and the merge/dedup logic are covered with
offline unit tests (no network). CI runs them on every push (`.github/workflows/tests.yaml`).

## Disclaimer

For educational use. Scrape responsibly, respect LinkedIn's terms of service, and
don't hammer their endpoints.
