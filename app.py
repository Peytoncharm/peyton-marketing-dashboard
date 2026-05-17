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
from google.oauth2.service_account import Credentials as SACredentials
from google.analytics.data_v1beta import BetaAnalyticsDataClient
from google.analytics.data_v1beta.types import (
    RunReportRequest, DateRange, Dimension, Metric, OrderBy, FilterExpression,
    Filter,
)

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
GA4_PROPERTY_BU3 = os.environ.get("GA4_PROPERTY_BU3", "301161975")

# Zoho credentials — Thailand (BU1 + BU2)
TH_ZOHO_REFRESH_TOKEN = os.environ.get("TH_ZOHO_REFRESH_TOKEN", "")
TH_ZOHO_CLIENT_ID = os.environ.get("TH_ZOHO_CLIENT_ID", "")
TH_ZOHO_CLIENT_SECRET = os.environ.get("TH_ZOHO_CLIENT_SECRET", "")

# Zoho credentials — UK (BU3)
UK_ZOHO_REFRESH_TOKEN = os.environ.get("UK_ZOHO_REFRESH_TOKEN", "")
UK_ZOHO_CLIENT_ID = os.environ.get("UK_ZOHO_CLIENT_ID", "")
UK_ZOHO_CLIENT_SECRET = os.environ.get("UK_ZOHO_CLIENT_SECRET", "")

# Google service account JSON (supports both env var names)
GOOGLE_SA_JSON = (os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")
                  or os.environ.get("GOOGLE_SHEETS_CREDENTIALS", ""))

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
    """Soft token check — logs warning if missing/wrong, never rejects."""
    token = request.args.get("token", "")
    if token and token == DASHBOARD_TOKEN:
        return  # valid token
    if not token:
        log.info(f"Request without token: {request.path}")
    else:
        log.warning(f"Invalid token on {request.path}")


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

def _ga4_client():
    """Build an authenticated GA4 Data API client."""
    if not GOOGLE_SA_JSON:
        raise RuntimeError("No Google service account JSON configured")
    sa_info = json.loads(GOOGLE_SA_JSON)
    creds = SACredentials.from_service_account_info(sa_info, scopes=[
        "https://www.googleapis.com/auth/analytics.readonly",
    ])
    log.info(f"GA4 service account: {sa_info.get('client_email', '?')}")
    return BetaAnalyticsDataClient(credentials=creds)


def fetch_ga4_data(property_id):
    """Fetch GA4 metrics for a single property (last 7 days)."""
    if not property_id:
        return {"active_users": 0, "sessions": 0, "page_views": 0,
                "top_page": "—", "top_page_views": 0, "traffic": {}}

    client = _ga4_client()
    prop = f"properties/{property_id}"

    # --- Report 1: summary metrics ---
    summary = client.run_report(RunReportRequest(
        property=prop,
        date_ranges=[DateRange(start_date="7daysAgo", end_date="today")],
        metrics=[
            Metric(name="activeUsers"),
            Metric(name="sessions"),
            Metric(name="screenPageViews"),
        ],
    ))
    active_users = sessions = page_views = 0
    if summary.rows:
        r = summary.rows[0]
        active_users = int(r.metric_values[0].value)
        sessions = int(r.metric_values[1].value)
        page_views = int(r.metric_values[2].value)
    log.info(f"GA4 {property_id}: users={active_users} sessions={sessions} views={page_views}")

    # --- Report 2: top page ---
    top_page_report = client.run_report(RunReportRequest(
        property=prop,
        date_ranges=[DateRange(start_date="7daysAgo", end_date="today")],
        dimensions=[Dimension(name="pagePath")],
        metrics=[Metric(name="screenPageViews")],
        order_bys=[OrderBy(metric=OrderBy.MetricOrderBy(metric_name="screenPageViews"),
                           desc=True)],
        limit=1,
    ))
    top_page = "—"
    top_page_views = 0
    if top_page_report.rows:
        top_page = top_page_report.rows[0].dimension_values[0].value
        top_page_views = int(top_page_report.rows[0].metric_values[0].value)

    # --- Report 3: traffic sources ---
    traffic_report = client.run_report(RunReportRequest(
        property=prop,
        date_ranges=[DateRange(start_date="7daysAgo", end_date="today")],
        dimensions=[Dimension(name="sessionDefaultChannelGroup")],
        metrics=[Metric(name="sessions")],
        order_bys=[OrderBy(metric=OrderBy.MetricOrderBy(metric_name="sessions"),
                           desc=True)],
        limit=10,
    ))
    traffic = {}
    for row in traffic_report.rows:
        channel = row.dimension_values[0].value
        count = int(row.metric_values[0].value)
        traffic[channel] = count

    return {
        "active_users": active_users,
        "sessions": sessions,
        "page_views": page_views,
        "top_page": top_page,
        "top_page_views": top_page_views,
        "traffic": traffic,
    }


def fetch_zoho_bookings():
    """Fetch Koh_Chang_Orders created in last 7 days, grouped by Chanel_of_booking."""
    import requests as rq

    if not TH_ZOHO_REFRESH_TOKEN:
        log.warning("Zoho credentials not set, skipping")
        return {"Facebook": 0, "Instagram": 0, "TikTok": 0, "Web": 0, "Other": 0}

    # Get access token
    token_resp = rq.post("https://accounts.zoho.eu/oauth/v2/token", params={
        "refresh_token": TH_ZOHO_REFRESH_TOKEN,
        "client_id": TH_ZOHO_CLIENT_ID,
        "client_secret": TH_ZOHO_CLIENT_SECRET,
        "grant_type": "refresh_token",
    }, timeout=10)
    access_token = token_resp.json().get("access_token", "")
    if not access_token:
        log.error(f"Zoho token error: {token_resp.text}")
        return {"Facebook": 0, "Instagram": 0, "TikTok": 0, "Web": 0, "Other": 0}

    # Query orders created in last 7 days via COQL
    seven_days_ago = (datetime.now(ICT) - timedelta(days=7)).strftime("%Y-%m-%dT00:00:00+07:00")
    query = f"SELECT Chanel_of_booking FROM Koh_Chang_Orders WHERE Created_Time >= '{seven_days_ago}' LIMIT 200"
    headers = {"Authorization": f"Zoho-oauthtoken {access_token}",
               "Content-Type": "application/json"}
    resp = rq.post(
        "https://www.zohoapis.eu/crm/v2/coql",
        headers=headers,
        json={"select_query": query},
        timeout=15,
    )

    result = {"Facebook": 0, "Instagram": 0, "TikTok": 0, "Web": 0, "Other": 0}
    if resp.status_code == 204:
        log.info("Zoho: no orders in last 7 days")
        return result
    if resp.status_code != 200:
        log.error(f"Zoho COQL error {resp.status_code}: {resp.text}")
        return result

    records = resp.json().get("data", [])
    for rec in records:
        channel = (rec.get("Chanel_of_booking") or "Other").strip()
        if channel == "Foot Path":
            continue  # skip test bookings
        if channel in result:
            result[channel] += 1
        else:
            result["Other"] += 1

    log.info(f"Zoho bookings (7d): {result}")
    return result


def fetch_uk_zoho_enquiries():
    """Fetch Leads created in last 7 days from UK Zoho CRM (BU3)."""
    import requests as rq

    if not UK_ZOHO_REFRESH_TOKEN:
        log.warning("UK Zoho credentials not set, skipping")
        return 0

    # Get access token
    token_resp = rq.post("https://accounts.zoho.eu/oauth/v2/token", params={
        "refresh_token": UK_ZOHO_REFRESH_TOKEN,
        "client_id": UK_ZOHO_CLIENT_ID,
        "client_secret": UK_ZOHO_CLIENT_SECRET,
        "grant_type": "refresh_token",
    }, timeout=10)
    access_token = token_resp.json().get("access_token", "")
    if not access_token:
        log.error(f"UK Zoho token error: {token_resp.text}")
        return 0

    # Query leads created in last 7 days via COQL
    seven_days_ago = (datetime.now(ICT) - timedelta(days=7)).strftime("%Y-%m-%dT00:00:00+07:00")
    query = f"SELECT id FROM Leads WHERE Created_Time >= '{seven_days_ago}' LIMIT 200"
    headers = {"Authorization": f"Zoho-oauthtoken {access_token}",
               "Content-Type": "application/json"}
    resp = rq.post(
        "https://www.zohoapis.eu/crm/v2/coql",
        headers=headers,
        json={"select_query": query},
        timeout=15,
    )

    if resp.status_code == 204:
        log.info("UK Zoho: no leads in last 7 days")
        return 0
    if resp.status_code != 200:
        log.error(f"UK Zoho COQL error {resp.status_code}: {resp.text}")
        return 0

    count = len(resp.json().get("data", []))
    log.info(f"UK Zoho enquiries (7d): {count}")
    return count


def fetch_social_metrics():
    """Fetch social media reach. TODO: Meta integration."""
    # TODO: Meta integration
    # TODO: TikTok integration
    return {"views": 0, "engagement": 0, "followers": 0}


def fetch_ai_citations():
    """Fetch AI citation counts from Google Sheet (last row = most recent week)."""
    import gspread

    if not AI_CITATIONS_SHEET_ID or not GOOGLE_SA_JSON:
        log.warning("AI citations sheet ID or Google credentials not set, skipping")
        return {"chatgpt": 0, "claude": 0, "perplexity": 0, "gemini": 0}

    sa_info = json.loads(GOOGLE_SA_JSON)
    creds = SACredentials.from_service_account_info(sa_info, scopes=[
        "https://www.googleapis.com/auth/spreadsheets.readonly",
    ])
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(AI_CITATIONS_SHEET_ID)
    ws = sh.sheet1

    rows = ws.get_all_records()
    if not rows:
        log.info("AI citations sheet: no data rows")
        return {"chatgpt": 0, "claude": 0, "perplexity": 0, "gemini": 0}

    last = rows[-1]
    result = {
        "chatgpt": int(last.get("chatgpt", 0) or 0),
        "claude": int(last.get("claude", 0) or 0),
        "perplexity": int(last.get("perplexity", 0) or 0),
        "gemini": int(last.get("gemini", 0) or 0),
    }
    log.info(f"AI citations (latest row): {result}")
    return result


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
        data["bu3"]["enquiries"] = fetch_uk_zoho_enquiries()
    except Exception as e:
        log.error(f"UK Zoho enquiries failed: {e}")

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


def _send_line_push(token, group_id, message):
    """Push a text message to a LINE group."""
    import requests as rq
    resp = rq.post(
        "https://api.line.me/v2/bot/message/push",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json={
            "to": group_id,
            "messages": [{"type": "text", "text": message}],
        },
        timeout=10,
    )
    log.info(f"LINE push status={resp.status_code}")
    if resp.status_code != 200:
        log.error(f"LINE push error: {resp.text}")
    return resp.status_code == 200


def _build_weekly_report(data):
    """Build Thai LINE message from cached dashboard data."""
    bu1 = data.get("bu1", {})
    bu2 = data.get("bu2", {})
    bu3 = data.get("bu3", {})
    bk = data.get("bookings_by_channel", {})
    ai = data.get("ai_citations", {})
    social = data.get("social", {})

    total_users = bu1.get("active_users", 0) + bu2.get("active_users", 0) + bu3.get("active_users", 0)
    total_sessions = bu1.get("sessions", 0) + bu2.get("sessions", 0) + bu3.get("sessions", 0)
    total_bookings = sum(bk.values())
    total_ai = ai.get("chatgpt", 0) + ai.get("claude", 0) + ai.get("perplexity", 0) + ai.get("gemini", 0)

    lines = [
        f"📊 Weekly Marketing Report",
        f"{data.get('week_label', '')}",
        "",
        f"👥 ผู้เข้าชมทั้งหมด: {total_users:,} users / {total_sessions:,} sessions",
        "",
        f"🚐 BU1 Transfers",
        f"   Users: {bu1.get('active_users', 0):,}  |  Sessions: {bu1.get('sessions', 0):,}",
        f"   Top page: {bu1.get('top_page', '—')}",
        "",
        f"🏝️ BU2 Tours",
        f"   Users: {bu2.get('active_users', 0):,}  |  Sessions: {bu2.get('sessions', 0):,}",
        f"   Top page: {bu2.get('top_page', '—')}",
        "",
        f"🎓 BU3 UK Students",
        f"   Users: {bu3.get('active_users', 0):,}  |  Sessions: {bu3.get('sessions', 0):,}",
        f"   Top page: {bu3.get('top_page', '—')}",
        "",
        f"📦 Bookings (7 วัน): {total_bookings}",
    ]
    if total_bookings > 0:
        for ch, cnt in bk.items():
            if cnt > 0:
                lines.append(f"   {ch}: {cnt}")

    # Social — stubbed for now
    if social.get("views", 0) > 0:
        lines.extend([
            "",
            f"📱 Social Reach: {social['views']:,} views / {social['engagement']:,} engagement",
        ])

    lines.extend([
        "",
        f"🤖 AI Citations: {total_ai}",
    ])
    if total_ai > 0:
        for name, key in [("ChatGPT", "chatgpt"), ("Claude", "claude"),
                          ("Perplexity", "perplexity"), ("Gemini", "gemini")]:
            cnt = ai.get(key, 0)
            if cnt > 0:
                lines.append(f"   {name}: {cnt}")
    else:
        lines.append("   Crawler กำลัง index — รอ 2-4 สัปดาห์")

    lines.extend([
        "",
        "📈 Dashboard:",
        "https://peyton-marketing-dashboard.onrender.com/",
    ])

    return "\n".join(lines)


@app.route("/cron/weekly-line-report")
def weekly_line_report():
    """Triggered by cron-job.org Sunday 20:00 ICT. Sends summary to LINE."""
    _check_token()

    # Refresh data first, then build report
    data = do_full_refresh()
    message = _build_weekly_report(data)
    log.info(f"Weekly report message:\n{message}")

    if not PA_LINE_TOKEN or not PA_LINE_GROUP_ID:
        log.warning("PA_LINE_TOKEN or PA_LINE_GROUP_ID not set, skipping LINE push")
        return jsonify({"status": "no_line_credentials", "message": message})

    success = _send_line_push(PA_LINE_TOKEN, PA_LINE_GROUP_ID, message)
    return jsonify({"status": "sent" if success else "send_failed", "message": message})


@app.route("/test/weekly-line-report")
def test_weekly_line_report():
    """Dry run — builds the report message but does NOT send to LINE."""
    _check_token()
    data = _load_cache()
    message = _build_weekly_report(data)
    return f"<pre>{message}</pre>"


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
