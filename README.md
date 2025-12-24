## Job Search Automation Project

This repository implements a simple, extensible pipeline for scraping jobs from LinkedIn, classifying them into pre‑defined categories (Robotics Engineer, Autonomy Engineer and Controls Engineer) and exporting the results into either Google Sheets or a Notion database.  It is designed to run from a command line and uses only widely available Python packages.

> **Important:**  LinkedIn imposes rate‑limits and does not provide a public API for job search.  This code relies on a guest endpoint (``/jobs-guest/jobs/api/seeMoreJobPostings/search``) that currently returns HTML fragments for job listings without authentication.  While this endpoint works at the time of writing, it is not officially supported and may change at any time.  Running this scraper too aggressively may result in blocked requests.  Use responsibly, cache results and respect LinkedIn’s terms of service.

### Features

* **Role filtering** – Specify a list of roles you are interested in.  Each role is turned into a keyword for the LinkedIn search.  Default roles are:
  * Robotics Engineer
  * Autonomy Engineer
  * Controls Engineer

* **Location filtering** – Specify a location string (default ``United States``) to focus the search geographically.

* **Pagination** – For each role the scraper iterates through result pages by incrementing the ``start`` parameter.  The number of pages to fetch is configurable.

* **Classification** – Each job is tagged with its corresponding role keyword and an arbitrary “fit score” computed from simple keyword overlaps in the description.  You can refine this logic in `classify_job`.

* **Output options** – Save the scraped data to a CSV file, update a Google Sheet or push rows into a Notion database.  Only the CSV export works out of the box; the Notion/Google integrations require API credentials.  See below for configuration details.

### Installation

Clone this repository and install the dependencies:

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Configuration

The primary configuration is done via command‑line arguments when running ``main.py``:

```
python main.py \
  --roles "Robotics Engineer,Autonomy Engineer,Controls Engineer" \
  --location "United States" \
  --pages 2 \
  --output csv \
  --csv_path scraped_jobs.csv
```

Parameters:

| Flag          | Description                                                                              | Default                   |
|--------------|------------------------------------------------------------------------------------------|---------------------------|
| ``--roles``   | Comma‑separated list of role keywords to search for                                     | ``Robotics Engineer,Autonomy Engineer,Controls Engineer`` |
| ``--location``| Location string to filter LinkedIn jobs (e.g. ``United States``, ``Boston, MA``)         | ``United States``         |
| ``--pages``   | Number of pages per role to fetch (each page contains ~25 jobs)                          | ``1``                     |
| ``--output``  | Export destination: ``csv`` (default), ``google`` or ``notion``                          | ``csv``                   |
| ``--csv_path``| Path of the CSV file to write when output is ``csv``                                     | ``scraped_jobs.csv``      |
| ``--google_sheet_id`` | ID of the Google Sheet to update when output is ``google`` (requires credentials) | ``None``                  |
| ``--google_worksheet`` | Name of the worksheet within the Google Sheet                                   | ``Jobs``                  |
| ``--notion_token`` | Notion API integration token when output is ``notion``                              | ``None``                  |
| ``--notion_database_id`` | ID of the Notion database                                                     | ``None``                  |

To enable Google Sheets integration you must create a service account in Google Cloud, share your sheet with the service account’s email and save the JSON credentials file locally.  Set the environment variable ``GOOGLE_APPLICATION_CREDENTIALS`` to point at this file.  See ``linkedin_scraper.py`` for details.

For Notion integration you need a Notion integration token and the ID of the database you wish to populate.  Share the database with your integration.

### Running the scraper

To scrape two pages of jobs for each role and save them to a CSV file:

```bash
python main.py --pages 2 --output csv --csv_path robotics_jobs.csv
```

To push the scraped jobs directly into a Google Sheet:

```bash
python main.py \
  --output google \
  --google_sheet_id YOUR_SHEET_ID \
  --google_worksheet Jobs \
  --pages 1
```

To push into a Notion database:

```bash
python main.py \
  --output notion \
  --notion_token YOUR_NOTION_TOKEN \
  --notion_database_id YOUR_DATABASE_ID \
  --pages 1
```

### Extending the classifier

The `LinkedInScraper.classify_job` method assigns a simple fit score and tags based on keyword overlap.  Feel free to enhance this with more sophisticated natural language processing, such as spaCy or scikit‑learn models, to better rank job relevancy.

### Disclaimer

This project is provided for educational purposes.  Use it at your own risk, abide by LinkedIn’s terms of service and do not abuse scraping endpoints.  The authors are not responsible for any consequences resulting from the misuse of this code.