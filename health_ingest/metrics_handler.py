"""
Webhook handler for Health Auto Export (iOS app) — daily metrics payload.

Receives POST with shape:
  { "data": { "metrics": [ { "name": "...", "data": [...] } ] } }

Writes to two Notion DBs per date:
  - Body Metrics DB: weight, lean body mass, body fat %, BMI
  - Daily Recovery DB: resting HR, sleep, respiratory rate, VO2 max, cardio recovery, avg HR

Each date is upserted rather than inserted: the app POSTs a running snapshot of
the current day, so a row created early in the morning has to keep absorbing the
rest of that day's numbers as they sync.
"""

import json
import os
from health_ingest import notion
from health_ingest.utils import parse_date

BODY_METRIC_NAMES = {
    "weight_body_mass",
    "lean_body_mass",
    "body_fat_percentage",
    "body_mass_index",
}
RECOVERY_METRIC_NAMES = {
    "resting_heart_rate",
    "sleep_analysis",
    "respiratory_rate",
    "vo2_max",
    "cardio_recovery",
    "heart_rate",
    "heart_rate_variability",
}


def _entry_for_date(entries, date_str):
    for entry in entries:
        if parse_date(entry.get("date", "")) == date_str:
            return entry
    return None


def build_body_metrics_properties(date_str, metrics_by_name):
    props = {}

    weight = _entry_for_date(metrics_by_name.get("weight_body_mass", []), date_str)
    if weight:
        props["Weight (lbs)"] = {"number": round(weight["qty"], 1)}

    lean = _entry_for_date(metrics_by_name.get("lean_body_mass", []), date_str)
    if lean:
        props["Lean Body Mass (lbs)"] = {"number": round(lean["qty"], 1)}

    fat = _entry_for_date(metrics_by_name.get("body_fat_percentage", []), date_str)
    if fat:
        props["Body Fat (%)"] = {"number": round(fat["qty"], 1)}

    bmi = _entry_for_date(metrics_by_name.get("body_mass_index", []), date_str)
    if bmi:
        props["BMI"] = {"number": round(bmi["qty"], 2)}

    if not props:
        return None

    props["Name"] = {"title": [{"text": {"content": date_str}}]}
    props["Date"] = {"date": {"start": date_str}}
    return props


def build_daily_recovery_properties(date_str, metrics_by_name):
    props = {}

    rhr = _entry_for_date(metrics_by_name.get("resting_heart_rate", []), date_str)
    if rhr:
        props["Resting HR (bpm)"] = {"number": round(rhr["qty"])}

    sleep = _entry_for_date(metrics_by_name.get("sleep_analysis", []), date_str)
    if sleep:
        props["Sleep Duration (hrs)"] = {"number": round(sleep.get("totalSleep", 0), 2)}

    resp_rate = _entry_for_date(metrics_by_name.get("respiratory_rate", []), date_str)
    if resp_rate:
        props["Respiratory Rate (rpm)"] = {"number": round(resp_rate["qty"], 1)}

    vo2 = _entry_for_date(metrics_by_name.get("vo2_max", []), date_str)
    if vo2:
        props["VO2 Max (ml/kg/min)"] = {"number": round(vo2["qty"], 1)}

    cardio = _entry_for_date(metrics_by_name.get("cardio_recovery", []), date_str)
    if cardio:
        props["Cardio Recovery (bpm)"] = {"number": round(cardio["qty"], 1)}

    hrv = _entry_for_date(metrics_by_name.get("heart_rate_variability", []), date_str)
    if hrv:
        props["HRV (ms)"] = {"number": round(hrv["qty"], 1)}

    hr = _entry_for_date(metrics_by_name.get("heart_rate", []), date_str)
    if hr and hr.get("Avg") is not None:
        props["Avg HR (bpm)"] = {"number": round(hr["Avg"])}

    if not props:
        return None

    props["Name"] = {"title": [{"text": {"content": date_str}}]}
    props["Date"] = {"date": {"start": date_str}}
    return props


def _new_counts():
    return {"created": 0, "updated": 0, "skipped": 0}


def _upsert_date(db_id, date_str, build_props, metrics_by_name, label, counts):
    """Upsert one date's row into one DB, tallying the outcome.

    One bad date should not cost us the rest of the payload, so the build and the
    write are both inside the try: a single malformed entry is logged and counted
    as nothing rather than raised.
    """
    try:
        props = build_props(date_str, metrics_by_name)
        if not props:
            return

        status = notion.upsert_page(
            db_id,
            props,
            lambda: notion.find_pages_by_date(db_id, date_str),
        )
    except Exception as e:
        print(f"Error processing {label} for {date_str}: {e}")
        return

    counts[status] += 1
    print(f"{status.capitalize()} {label} for {date_str}")


def handler(event, context):
    print("metrics-ingest invoked")

    try:
        body = json.loads(event.get("body") or "{}")
    except (json.JSONDecodeError, TypeError) as e:
        print(f"JSON parse error: {e}")
        return {
            "statusCode": 400,
            "body": json.dumps({"ok": False, "error": "Invalid JSON"}),
        }

    metrics_list = body.get("data", {}).get("metrics", [])
    if not metrics_list:
        print("No metrics in payload")
        return {
            "statusCode": 200,
            "body": json.dumps({"ok": True, "body_metrics": 0, "recovery": 0}),
        }

    metrics_by_name = {m["name"]: m.get("data", []) for m in metrics_list}

    all_dates = set()
    for entries in metrics_by_name.values():
        for entry in entries:
            d = parse_date(entry.get("date", ""))
            if d:
                all_dates.add(d)

    body_metrics_db = os.environ["BODY_METRICS_DB_ID"]
    daily_recovery_db = os.environ["DAILY_RECOVERY_DB_ID"]
    body_counts = _new_counts()
    recovery_counts = _new_counts()

    for date_str in sorted(all_dates):
        _upsert_date(
            body_metrics_db,
            date_str,
            build_body_metrics_properties,
            metrics_by_name,
            "body metrics",
            body_counts,
        )
        _upsert_date(
            daily_recovery_db,
            date_str,
            build_daily_recovery_properties,
            metrics_by_name,
            "recovery",
            recovery_counts,
        )

    return {
        "statusCode": 200,
        "body": json.dumps(
            {
                "ok": True,
                "body_metrics": body_counts["created"],
                "body_metrics_updated": body_counts["updated"],
                "body_metrics_skipped": body_counts["skipped"],
                "recovery": recovery_counts["created"],
                "recovery_updated": recovery_counts["updated"],
                "recovery_skipped": recovery_counts["skipped"],
            }
        ),
    }
