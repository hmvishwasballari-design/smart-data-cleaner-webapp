# Smart Data Cleaner — Web App

A Flask web front-end for the smart-data-cleaner project. Upload any
supported data file, preview it, choose cleaning options, and download
a perfectly cleaned file — all from the browser.

## Features

- **Upload page** — drag & drop or click to browse. Accepts CSV, TSV,
  Excel (.xlsx/.xls), and JSON, up to 32MB.
- **Preview page** — see the first 10 rows, missing-value count,
  duplicate-row count, and column count before cleaning. Toggle which
  cleaning steps to apply and pick an output format.
- **Cleaning dashboard** — cleaning score, before/after stats, list of
  steps applied, cleaned-data preview, and a download button.
- Generic, column-agnostic cleaning engine (not hardcoded to specific
  column names like the original CLI script):
  - Trims/normalizes headers
  - Drops fully empty rows/columns
  - Removes duplicate rows
  - Standardizes text casing & whitespace
  - Auto-detects and standardizes date-like columns to `YYYY-MM-DD`
  - Fills missing numeric values with the column mean
  - Fills missing text values with `"Unknown"`

## Run locally

```bash
cd app
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

Then open http://localhost:5000 in your browser.

## Project structure

```
app/
├── app.py                 # Flask routes + cleaning engine
├── requirements.txt
├── templates/
│   ├── base.html
│   ├── index.html         # Upload page
│   ├── preview.html       # Preview + cleaning options
│   └── results.html       # Cleaning dashboard
├── static/
│   ├── css/style.css      # Dark theme
│   └── js/main.js
├── uploads/                # Raw uploaded files (runtime)
└── cleaned/                 # Cleaned output files (runtime)
```

## Notes / next steps

- Job metadata is currently kept in-memory (`JOBS` dict in `app.py`),
  so it resets on server restart — fine for local/single-user use.
  For production, swap this for a database or signed session tokens.
- To deploy, point a WSGI server (gunicorn/uwsgi) at `app:app` and
  put it behind nginx; remember to set a stronger `app.secret_key`.
- Possible next features: AI-powered cleaning suggestions, column-level
  cleaning rules, charts on the dashboard, auth for multi-user use.
