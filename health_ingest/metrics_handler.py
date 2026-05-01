"""
Webhook handler for Health Auto Export (iOS app) — daily metrics payload.

Receives POST with shape:
  { "data": { "metrics": [ { "name": "...", "data": [...] } ] } }

Writes to two Notion DBs per date:
  - Body Metrics DB: weight, lean body mass, body fat %, BMI
  - Daily Recovery DB: resting HR, sleep, respiratory rate, VO2 max, cardio recovery, avg HR
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

    hr = _entry_for_date(metrics_by_name.get("heart_rate", []), date_str)
    if hr and hr.get("Avg") is not None:
        props["Avg HR (bpm)"] = {"number": round(hr["Avg"])}

    if not props:
        return None

    props["Name"] = {"title": [{"text": {"content": date_str}}]}
    props["Date"] = {"date": {"start": date_str}}
    return props


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
    body_inserted = body_skipped = recovery_inserted = recovery_skipped = 0

    for date_str in sorted(all_dates):
        try:
            props = build_body_metrics_properties(date_str, metrics_by_name)
            if props:
                if notion.page_exists_by_date(body_metrics_db, date_str):
                    print(f"Skipping body metrics for {date_str} (already exists)")
                    body_skipped += 1
                else:
                    notion.create_page(body_metrics_db, props)
                    print(f"Ingested body metrics for {date_str}")
                    body_inserted += 1
        except Exception as e:
            print(f"Error processing body metrics for {date_str}: {e}")

        try:
            props = build_daily_recovery_properties(date_str, metrics_by_name)
            if props:
                if notion.page_exists_by_date(daily_recovery_db, date_str):
                    print(f"Skipping recovery for {date_str} (already exists)")
                    recovery_skipped += 1
                else:
                    notion.create_page(daily_recovery_db, props)
                    print(f"Ingested recovery for {date_str}")
                    recovery_inserted += 1
        except Exception as e:
            print(f"Error processing recovery for {date_str}: {e}")

    return {
        "statusCode": 200,
        "body": json.dumps(
            {
                "ok": True,
                "body_metrics": body_inserted,
                "body_metrics_skipped": body_skipped,
                "recovery": recovery_inserted,
                "recovery_skipped": recovery_skipped,
            }
        ),
    }
