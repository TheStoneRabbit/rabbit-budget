# Rabbit Budget

Rabbit Budget is a small Flask application that helps categorize credit-card transactions, track budgets per category, and email CSV summaries. It exposes a browser UI for uploading statements and maintaining category rules, a REST-ish API for automation, and a CLI entry point for batch processing.

## Features

- Upload Citi CSV exports; transactions are cleaned, categorized via keyword rules or OpenAI GPT fallback, and the categorized file is emailed to the requester (attachment name includes profile + timestamp).
- Maintain category budgets and text-matching rules per profile through either the UI or dedicated CRUD endpoints.
- Import/export category budgets as CSV (additive import; existing categories are preserved).
- Optional account-level privacy: require a password to select a profile and view its data; change/delete profile actions also respect the password.
- Persist profile data in SQLite (seeded from the legacy `profiles/*/*.json` files on first run). A lightweight schema check runs on startup to add missing columns.
- Background worker thread handles long-running processing and email delivery.

## Requirements

- Python 3.11+
- [uv](https://github.com/astral-sh/uv) (recommended for dependency management)
- SQLite (bundled with Python) or any SQLAlchemy-compatible database if `DATABASE_URL` is overridden.
- An OpenAI API key (for categorization fallback).
- SMTP credentials for transactional email.

## Setup

```bash
# Install dependencies
uv sync

# Create a .env file (see Environment section)
cp .env.example .env  # if you create one

# Run the web app
uv run python app.py  # defaults to http://0.0.0.0:5001
```

The first launch bootstraps `rabbit.db` from JSON files in `profiles/`. Updating data from that point forward happens through the database.

### CLI processing

You can categorize a CSV without the web UI:

```bash
uv run python main.py uploads/source.csv uploads/output.csv --profile Mason
```

## Environment

| Variable            | Required | Description                                              |
|---------------------|----------|----------------------------------------------------------|
| `FLASK_SECRET_KEY`  | yes      | Secret key for session cookies.                          |
| `SMTP_SERVER`       | yes      | SMTP host used to send result emails.                    |
| `SMTP_PORT`         | no       | SMTP port (defaults to 587).                             |
| `EMAIL_ADDRESS`     | yes      | Account used as the sender.                              |
| `EMAIL_PASSWORD`    | yes      | SMTP password or app token.                              |
| `OPENAI_API_KEY`    | yes      | Used for GPT-based categorization fallback.              |
| `DATABASE_URL`      | no       | SQLAlchemy URI (defaults to `sqlite:///rabbit.db`).      |

## Data model

- **Profile**: Named workspace (e.g., `default`, `Mason`). Seeded from directory names in `profiles/`.
- **Category**: Budget buckets tied to a profile. Each has a unique name and numeric budget.
- **Rule**: Keyword-to-category mapping per profile (stored uppercase for case-insensitive matching).

Deleting a category or rule via the API removes the corresponding database row. The processor automatically writes "NEEDS CATEGORY" rules when it encounters uncategorized descriptions.

## API reference

All endpoints are namespace by profile: `/default/...`, `/Mason/...`, etc. Responses are JSON unless otherwise noted.

### Profiles

| Method | Path          | Description                                  |
|--------|---------------|----------------------------------------------|
| GET    | `/`           | Redirects to the first available profile.    |
| GET    | `/<profile>`  | Renders the HTML dashboard for that profile. |

### Categories

| Method | Path                                      | Description                                                | Body / Query                                                     |
|--------|-------------------------------------------|------------------------------------------------------------|------------------------------------------------------------------|
| GET    | `/<profile>/categories`                   | List categories for a profile.                             | –                                                                |
| POST   | `/<profile>/categories`                   | Create a category.                                         | `{ "name": "Groceries", "budget": 500 }`                         |
| PATCH  | `/<profile>/categories/<category_name>`   | Update a category’s name and/or budget.                    | `{ "name": "Eating Out", "budget": 300 }` (fields optional)      |
| DELETE | `/<profile>/categories/<category_name>`   | Delete a category.                                         | –                                                                |

Rules for duplicate names or missing profiles return 409/404 respectively.

### Rules

| Method | Path                                   | Description                                                | Body / Query                                                  |
|--------|----------------------------------------|------------------------------------------------------------|---------------------------------------------------------------|
| GET    | `/<profile>/rules`                     | List keyword rules as a keyword → category map.            | –                                                             |
| POST   | `/<profile>/rules`                     | Create a rule.                                             | `{ "keyword": "STARBUCKS", "category": "Coffee" }`             |
| PATCH  | `/<profile>/rules/<rule_keyword>`      | Rename a rule and/or change its category.                  | `{ "keyword": "SAFEWAY", "category": "Groceries" }`            |
| DELETE | `/<profile>/rules/<rule_keyword>`      | Delete a rule.                                             | –                                                             |

Keywords are stored uppercase for case-insensitive matching. Name collisions raise 409.

### Uploads

| Method | Path                    | Description                                                                 |
|--------|-------------------------|-----------------------------------------------------------------------------|
| POST   | `/<profile>/upload`     | Multipart form upload (`file` + `email`). Triggers async processing + mail. |

Successful uploads redirect back to the profile dashboard. Processing happens on a background thread; when finished, an email is sent with the categorized CSV.

## UI workflow

1. Visit `http://localhost:5001/<profile>` after starting the app.
2. Upload a Citi CSV and supply the recipient email.
3. Expand “Your Budget” (categories) or “Manage Category Rules” to edit entries. Press “Save” to sync edits with the database.
4. Use “Download CSV” / “Import CSV” in “Your Budget” to export or add budgets in bulk.
5. In “Settings”, make the account private (sets/uses a password), change the password, or delete the profile (password required when private).

## Development notes

- The OpenAI client uses `gpt-4-turbo`. Consider rate limiting or batching if you expand throughput.
- Background work currently uses Python threads; migrating to a proper task queue (Celery, RQ) would improve robustness for production use.
- To wipe seed data, delete `rabbit.db` and restart—the bootstrap process will re-import JSON files if the database is empty.
