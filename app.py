"""
Peyton & Charmed — Marketing Dashboard
Render service: peyton-marketing-dashboard
Pattern: Flask + gunicorn, cron-job.org triggered refresh
"""

import os
import json
import time
import threading
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

from flask import Flask, jsonify, request, render_template, abort

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
CACHE_PATH = Path(__file__).parent / "data" / "cache.json"
DASHBOARD_TOKEN = os.environ.get("DASHBOARD_TOKEN", "local-dev")
PA_LINE_TOKEN = os.environ.get("PA_LINE_TOKEN", "")
PA_LINE_GROUP_ID = os.environ.get("PA_LINE_GROUP_ID", "")

# GA4 property IDs
GA4_PROPERTY_BU1 = os.environ.get("GA4_PROPERTY_BU1", "538028937")
GA4_PROPERTY_BU2 = os.environ.get("GA4_PROPERTY_BU2", "537958860")
GA4_PROPERTY_BU3 = os.environ.get("GA4_PROPERTY_BU3", "")  # TBD

# Zoho credentials
TH_ZOHO_REFRESH_TOKEN = os.environ.get("TH_ZOHO_REFRESH_TOKEN", "")
TH_ZOHO_CLIENT_ID = os.environ.get("TH_ZOHO_CLIENT_ID", "")
TH_ZOHO_CLIENT_SECRET = os.environ.get("TH_ZOHO_CLIENT_SECRET", "")

# Google service account JSON (from env var)
GOOGLE_SHEETS_CREDENTIALS = os.environ.get("GOOGLE_SHEETS_CREDENTIALS", "")

# AI citations sheet
AI_CITATIONS_SHEET_ID = os.environ.get("AI_CITATIONS_SHEET_ID", "")

ICT = timezone(timedelta(hours=7))

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Flask app
# ---------------------------------------------------------------------------
app = Flask(__name__)


def _check_token():
    """Validate ?token= query parameter."""
    token = request.args.get("token", "")
    if token != DASHBOARD_TOKEN:
        abort(403)


def _default_data():
    """Return empty data skeleton — all zeros, no errors."""
    return {
        "last_refresh": None,
        "week_label": _week_label(),
        "bu1": {
            "active_users": 0, "sessions": 0, "page_views": 0,
            "top_page": "—", "top_page_views": 0,
            "bookings": 0,
            "traffic": {}
        },
        "bu2": {
            "active_users": 0, "sessions": 0, "page_views": 0,
            "top_page": "—", "top_page_views": 0,
            "bookings": 0,
            "traffic": {}
        },
        "bu3": {
            "active_users": 0, "sessions": 0, "page_views": 0,
            "top_page": "—", "top_page_views": 0,
            "enquiries": 0,
            "traffic": {}
        },
        "social": {
            "views": 0, "engagement": 0, "followers": 0  # TODO: Meta integration
        },
        "bookings_by_channel": {
            "Facebook": 0, "Instagram": 0, "TikTok": 0,
            "Web": 0, "Other": 0
        },
        "ai_citations": {
            "chatgpt": 0, "claude": 0, "perplexity": 0, "gemini": 0
        },
        "pillar_views": {
            "route": 0, "trust": 0, "tip": 0, "place": 0
        }
    }


def _week_label():
    """Return 'Week of DD Mon – DD Mon YYYY' in ICT."""
    now = datetime.now(ICT)
    start = now - timedelta(days=now.weekday())  # Monday
    end = start + timedelta(days=6)  # Sunday
    return f"Week of {start.strftime('%-d %b')} – {end.strftime('%-d %b %Y')}"


def _load_cache():
    """Load cached data or return defaults."""
    try:
        if CACHE_PATH.exists():
            with open(CACHE_PATH) as f:
                return json.load(f)
    except Exception as e:
        log.warning(f"Cache read failed: {e}")
    return _default_data()


def _save_cache(data):
    """Save data to cache file."""
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CACHE_PATH, "w") as f:
        json.dump(data, f, indent=2, default=str)
    log.info("Cache saved")


# ---------------------------------------------------------------------------
# Data fetch functions (stubbed — wired up in later steps)
# ---------------------------------------------------------------------------

def fetch_ga4_data(property_id):
    """Fetch GA4 metrics for a single property. Wired up in Step 2."""
    # TODO: implement GA4 fetch
    return {
        "active_users": 0, "sessions": 0, "page_views": 0,
        "top_page": "—", "top_page_views": 0,
        "traffic": {}
    }


def fetch_zoho_bookings():
    """Fetch bookings from Zoho CRM. Wired up in Step 4."""
    # TODO: implement Zoho fetch
    return {"Facebook": 0, "Instagram": 0, "TikTok": 0, "Web": 0, "Other": 0}


def fetch_social_metrics():
    """Fetch social media reach. TODO: Meta integration."""
    # TODO: Meta integration
    # TODO: TikTok integration
    return {"views": 0, "engagement": 0, "followers": 0}


def fetch_ai_citations():
    """Fetch AI citation counts from Google Sheet."""
    # TODO: implement Google Sheets fetch
    return {"chatgpt": 0, "claude": 0, "perplexity": 0, "gemini": 0}


def do_full_refresh():
    """Pull all data sources and update cache."""
    log.info("Starting full data refresh...")
    data = _default_data()
    data["last_refresh"] = datetime.now(ICT).isoformat()

    try:
        bu1_ga4 = fetch_ga4_data(GA4_PROPERTY_BU1)
        data["bu1"].update(bu1_ga4)
    except Exception as e:
        log.error(f"GA4 BU1 failed: {e}")

    try:
        bu2_ga4 = fetch_ga4_data(GA4_PROPERTY_BU2)
        data["bu2"].update(bu2_ga4)
    except Exception as e:
        log.error(f"GA4 BU2 failed: {e}")

    if GA4_PROPERTY_BU3:
        try:
            bu3_ga4 = fetch_ga4_data(GA4_PROPERTY_BU3)
            data["bu3"].update(bu3_ga4)
        except Exception as e:
            log.error(f"GA4 BU3 failed: {e}")

    try:
        data["bookings_by_channel"] = fetch_zoho_bookings()
        total = sum(data["bookings_by_channel"].values())
        data["bu1"]["bookings"] = total  # BU1+BU2 share Zoho bookings
        data["bu2"]["bookings"] = total
    except Exception as e:
        log.error(f"Zoho bookings failed: {e}")

    try:
        data["social"] = fetch_social_metrics()
    except Exception as e:
        log.error(f"Social metrics failed: {e}")

    try:
        data["ai_citations"] = fetch_ai_citations()
    except Exception as e:
        log.error(f"AI citations failed: {e}")

    _save_cache(data)
    log.info("Full refresh complete")
    return data


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def dashboard():
    """Serve the dashboard HTML."""
    _check_token()
    data = _load_cache()
    return render_template("dashboard.html", d=data)


@app.route("/api/data")
def api_data():
    """Return cached JSON data."""
    _check_token()
    return jsonify(_load_cache())


@app.route("/api/refresh")
def api_refresh():
    """Triggered by cron-job.org every 6 hours. Runs refresh in background thread."""
    _check_token()

    def _bg_refresh():
        try:
            do_full_refresh()
        except Exception as e:
            log.error(f"Background refresh failed: {e}")

    t = threading.Thread(target=_bg_refresh)
    t.start()

    return jsonify({"status": "refresh_started",
                     "time": datetime.now(ICT).isoformat()})


@app.route("/cron/weekly-line-report")
def weekly_line_report():
    """Triggered by cron-job.org Sunday 20:00 ICT. Sends summary to LINE."""
    _check_token()
    # TODO: implement in Step 7
    log.info("Weekly LINE report triggered (not yet implemented)")
    return jsonify({"status": "not_implemented_yet"})


@app.route("/health")
def health():
    """Health check — no token needed."""
    return jsonify({"status": "ok", "service": "peyton-marketing-dashboard"})


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
