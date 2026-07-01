import os
import io
import json
import uuid
import datetime

import pandas as pd
from flask import (
    Flask, render_template, request, redirect,
    url_for, send_file, flash, session, jsonify
)
from werkzeug.utils import secure_filename

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
CLEANED_DIR = os.path.join(BASE_DIR, "cleaned")
ALLOWED_EXTENSIONS = {"csv", "xlsx", "xls", "json", "tsv"}

os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(CLEANED_DIR, exist_ok=True)

app = Flask(__name__)
app.secret_key = "smart-data-cleaner-secret-key"
app.config["MAX_CONTENT_LENGTH"] = 32 * 1024 * 1024  # 32 MB

# In-memory store of job metadata (per process). Good enough for a single-user local tool.
JOBS = {}


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def read_any(filepath, ext):
    if ext == "csv":
        return pd.read_csv(filepath)
    if ext == "tsv":
        return pd.read_csv(filepath, sep="\t")
    if ext in ("xlsx", "xls"):
        return pd.read_excel(filepath)
    if ext == "json":
        return pd.read_json(filepath)
    raise ValueError("Unsupported file type")


def write_any(df, filepath, ext):
    if ext == "csv":
        df.to_csv(filepath, index=False)
    elif ext == "tsv":
        df.to_csv(filepath, index=False, sep="\t")
    elif ext in ("xlsx", "xls"):
        df.to_excel(filepath, index=False)
    elif ext == "json":
        df.to_json(filepath, orient="records", indent=2)
    else:
        raise ValueError("Unsupported file type")


def is_date_like(series, sample=20):
    """Heuristic: does this object/string column look like dates?"""
    s = series.dropna().astype(str)
    if s.empty:
        return False
    sample_vals = s.sample(min(sample, len(s)), random_state=1) if len(s) > sample else s
    parsed = pd.to_datetime(sample_vals, errors="coerce")
    success_ratio = parsed.notna().mean()
    return success_ratio > 0.7


def clean_dataframe(df, options):
    """
    Generic, column-agnostic cleaning pipeline.
    options: dict of booleans controlling which steps run.
    Returns cleaned df + a stats dict describing what happened.
    """
    stats = {
        "rows_before": int(len(df)),
        "cols_before": int(len(df.columns)),
        "missing_before": int(df.isnull().sum().sum()),
        "duplicates_before": int(df.duplicated().sum()),
        "steps": [],
        "per_column_missing_before": df.isnull().sum().to_dict(),
    }

    df = df.copy()

    # 1. Trim column names
    if options.get("trim_headers", True):
        df.columns = [str(c).strip() for c in df.columns]
        stats["steps"].append("Trimmed and normalized column headers")

    # 2. Drop fully empty rows/cols
    if options.get("drop_empty", True):
        before_r, before_c = df.shape
        df = df.dropna(how="all")
        df = df.dropna(axis=1, how="all")
        if df.shape != (before_r, before_c):
            stats["steps"].append("Removed fully empty rows/columns")

    # 3. Remove duplicate rows
    if options.get("remove_duplicates", True):
        dup_count = int(df.duplicated().sum())
        if dup_count > 0:
            df = df.drop_duplicates()
            stats["steps"].append(f"Removed {dup_count} duplicate row(s)")

    # 4. Standardize text columns (strip + title case) & fill missing numerics/text
    for col in df.columns:
        if pd.api.types.is_numeric_dtype(df[col]):
            if options.get("fill_numeric", True) and df[col].isnull().any():
                fill_val = df[col].mean()
                df[col] = df[col].fillna(round(fill_val, 2))
        elif pd.api.types.is_object_dtype(df[col]) or pd.api.types.is_string_dtype(df[col]):
            non_null = df[col].dropna()
            looks_date = False
            if options.get("standardize_dates", True) and len(non_null) > 0:
                looks_date = is_date_like(df[col])

            if looks_date:
                parsed = pd.to_datetime(df[col], errors="coerce")
                df[col] = parsed.dt.strftime("%Y-%m-%d")
            else:
                if options.get("standardize_text", True):
                    df[col] = df[col].astype(str).where(df[col].notna(), df[col])
                    df[col] = df[col].apply(
                        lambda v: v.strip().title() if isinstance(v, str) and v.lower() != "nan" else v
                    )
                    df[col] = df[col].replace("Nan", pd.NA)
                if options.get("fill_text", True) and df[col].isnull().any():
                    df[col] = df[col].fillna("Unknown")

    if options.get("standardize_text", True):
        stats["steps"].append("Standardized text casing and trimmed whitespace")
    if options.get("standardize_dates", True):
        stats["steps"].append("Detected and standardized date columns to YYYY-MM-DD")
    if options.get("fill_numeric", True):
        stats["steps"].append("Filled missing numeric values with column mean")
    if options.get("fill_text", True):
        stats["steps"].append("Filled missing text values with 'Unknown'")

    stats["rows_after"] = int(len(df))
    stats["cols_after"] = int(len(df.columns))
    stats["missing_after"] = int(df.isnull().sum().sum())
    stats["duplicates_after"] = int(df.duplicated().sum())
    stats["per_column_missing_after"] = df.isnull().sum().to_dict()

    denom = (stats["missing_before"] + stats["duplicates_before"])
    if denom > 0:
        improved = (stats["missing_before"] - stats["missing_after"]) + \
                   (stats["duplicates_before"] - stats["duplicates_after"])
        stats["cleaning_score"] = round((improved / denom) * 100, 2)
    else:
        stats["cleaning_score"] = 100.0

    return df, stats


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/upload", methods=["POST"])
def upload():
    if "file" not in request.files:
        flash("No file part in request.")
        return redirect(url_for("index"))

    file = request.files["file"]
    if file.filename == "":
        flash("No file selected.")
        return redirect(url_for("index"))

    if not allowed_file(file.filename):
        flash("Unsupported file type. Please upload CSV, TSV, XLSX, XLS, or JSON.")
        return redirect(url_for("index"))

    ext = file.filename.rsplit(".", 1)[1].lower()
    job_id = uuid.uuid4().hex[:10]
    safe_name = secure_filename(file.filename)
    raw_path = os.path.join(UPLOAD_DIR, f"{job_id}_{safe_name}")
    file.save(raw_path)

    try:
        df = read_any(raw_path, ext)
    except Exception as e:
        flash(f"Could not read file: {e}")
        return redirect(url_for("index"))

    JOBS[job_id] = {
        "raw_path": raw_path,
        "ext": ext,
        "original_filename": file.filename,
        "uploaded_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "n_rows": int(len(df)),
        "n_cols": int(len(df.columns)),
        "columns": list(map(str, df.columns)),
    }

    return redirect(url_for("preview", job_id=job_id))


@app.route("/preview/<job_id>")
def preview(job_id):
    job = JOBS.get(job_id)
    if not job:
        flash("Upload session not found or expired. Please upload again.")
        return redirect(url_for("index"))

    df = read_any(job["raw_path"], job["ext"])
    preview_html = df.head(10).to_html(
        classes="data-table", index=False, na_rep="—", border=0
    )

    missing_total = int(df.isnull().sum().sum())
    duplicate_total = int(df.duplicated().sum())

    return render_template(
        "preview.html",
        job_id=job_id,
        job=job,
        preview_html=preview_html,
        missing_total=missing_total,
        duplicate_total=duplicate_total,
    )


@app.route("/clean/<job_id>", methods=["POST"])
def clean(job_id):
    job = JOBS.get(job_id)
    if not job:
        flash("Upload session not found or expired. Please upload again.")
        return redirect(url_for("index"))

    options = {
        "trim_headers": "trim_headers" in request.form,
        "drop_empty": "drop_empty" in request.form,
        "remove_duplicates": "remove_duplicates" in request.form,
        "standardize_text": "standardize_text" in request.form,
        "standardize_dates": "standardize_dates" in request.form,
        "fill_numeric": "fill_numeric" in request.form,
        "fill_text": "fill_text" in request.form,
    }
    output_format = request.form.get("output_format", job["ext"])
    if output_format not in ALLOWED_EXTENSIONS:
        output_format = job["ext"]

    df = read_any(job["raw_path"], job["ext"])
    cleaned_df, stats = clean_dataframe(df, options)

    out_name = f"{job_id}_cleaned.{output_format}"
    out_path = os.path.join(CLEANED_DIR, out_name)
    write_any(cleaned_df, out_path, output_format)

    job["cleaned_path"] = out_path
    job["cleaned_filename"] = f"cleaned_{job['original_filename'].rsplit('.', 1)[0]}.{output_format}"
    job["stats"] = stats
    job["options"] = options
    job["output_format"] = output_format

    preview_html = cleaned_df.head(10).to_html(
        classes="data-table", index=False, na_rep="—", border=0
    )
    job["cleaned_preview_html"] = preview_html

    return redirect(url_for("results", job_id=job_id))


@app.route("/results/<job_id>")
def results(job_id):
    job = JOBS.get(job_id)
    if not job or "stats" not in job:
        flash("Cleaning results not found. Please upload and clean a file first.")
        return redirect(url_for("index"))

    return render_template(
        "results.html",
        job_id=job_id,
        job=job,
        stats=job["stats"],
    )


@app.route("/download/<job_id>")
def download(job_id):
    job = JOBS.get(job_id)
    if not job or "cleaned_path" not in job:
        flash("No cleaned file available for download.")
        return redirect(url_for("index"))

    return send_file(
        job["cleaned_path"],
        as_attachment=True,
        download_name=job["cleaned_filename"],
    )


@app.errorhandler(413)
def too_large(e):
    flash("File too large. Maximum upload size is 32 MB.")
    return redirect(url_for("index"))


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
