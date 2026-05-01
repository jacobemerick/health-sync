"""
Webhook handler for Health Auto Export (iOS app).

Receives POST from Health Auto Export with shape:
  {
    "data": {
      "workouts": [ { ...workout fields... } ]
    }
  }

Configure Health Auto Export webhook URL to the Lambda Function URL from `sls deploy`.
"""

import json
import os
import requests
from datetime import datetime, timezone

NOTION_VERSION = "2022-06-28"

WORKOUT_TYPE_MAP = {
    "Run": "Run",
    "Outdoor Run": "Run",
    "Trail Run": "Run",
    "Functional Strength Training": "Strength",
    "Traditional Strength Training": "Strength",
    "Hiking": "Hike",
    "Walking": "Hike",
    "Yoga": "Recovery",
    "Pilates": "Recovery",
    "Stretching": "Recovery",
    "Meditation": "Recovery",
}

# Karvonen HR zones: MaxHR=190, RestHR=65
ZONE_BOUNDARIES = [
    ("Z1", None, 127),
    ("Z2", 128, 153),
    ("Z3", 154, 164),
    ("Z4", 165, 174),
    ("Z5", 175, None),
]


def _notion_base():
    return os.environ.get("NOTION_API_BASE", "https://api.notion.com/v1").rstrip("/")


def _notion_headers():
    return {
        "Authorization": f"Bearer {os.environ['NOTION_TOKEN']}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


def get_zone(hr_bpm):
    """Return HR zone string ('Z1'–'Z5') for a given bpm value."""
    for zone, low, high in ZONE_BOUNDARIES:
        if low is None and hr_bpm <= high:
            return zone
        if high is None and hr_bpm >= low:
            return zone
        if low is not None and high is not None and low <= hr_bpm <= high:
            return zone
    return "Z5"


def parse_date(date_str):
    """Parse a date string like '2026-04-28 17:57:55 -0700' to ISO date string 'YYYY-MM-DD'."""
    # Try with timezone offset first
    for fmt in ("%Y-%m-%d %H:%M:%S %z", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(date_str.strip(), fmt)
            return dt.date().isoformat()
        except ValueError:
            continue
    # Fallback: just grab first 10 chars
    return date_str[:10]


def dedup_workouts(workouts):
    """
    Dedup workouts by exact start timestamp.
    When duplicates share a start time, prefer the one with a 'distance' field.
    """
    by_start = {}
    for w in workouts:
        start = w.get("start", "")
        if start not in by_start:
            by_start[start] = w
        else:
            existing = by_start[start]
            # Prefer workout that has distance
            existing_has_dist = existing.get("distance") is not None
            new_has_dist = w.get("distance") is not None
            if new_has_dist and not existing_has_dist:
                by_start[start] = w
            # If both or neither have distance, keep existing (first seen)
    return list(by_start.values())


def compute_zone_stats(heart_rate_data, distance_data):
    """
    Compute per-zone stats from minute-interval HR and distance arrays.

    Both arrays have entries with matching timestamps. For each minute:
      - Find the distance entry matching that timestamp
      - Skip minutes where distance <= 0 (standing still)
      - Assign the minute to a zone based on Avg HR
      - Accumulate distance and minute count per zone

    Returns dict: { "Z1": {"minutes": N, "pace_min_per_mi": X}, ... }
    Only includes zones that had at least one active minute.
    """
    # Build distance lookup: timestamp -> qty in miles
    dist_lookup = {}
    for entry in distance_data:
        ts = entry.get("date", "")
        qty = entry.get("qty", 0)
        dist_lookup[ts] = qty

    zone_minutes = {}
    zone_distance = {}

    for hr_entry in heart_rate_data:
        ts = hr_entry.get("date", "")
        avg_hr = hr_entry.get("Avg")
        if avg_hr is None:
            continue

        dist_mi = dist_lookup.get(ts, 0)
        if dist_mi <= 0:
            continue

        zone = get_zone(int(round(avg_hr)))
        zone_minutes[zone] = zone_minutes.get(zone, 0) + 1
        zone_distance[zone] = zone_distance.get(zone, 0.0) + dist_mi

    result = {}
    for zone in ("Z1", "Z2", "Z3", "Z4", "Z5"):
        mins = zone_minutes.get(zone, 0)
        dist = zone_distance.get(zone, 0.0)
        if mins > 0 and dist > 0:
            pace = mins / dist
            result[zone] = {
                "minutes": mins,
                "pace_min_per_mi": round(pace, 2),
            }
    return result


def format_pace(min_per_mi):
    """Convert float pace (e.g. 12.75) to 'MM:SS/mi' string."""
    mins = int(min_per_mi)
    secs = round((min_per_mi - mins) * 60)
    if secs == 60:
        mins += 1
        secs = 0
    return f"{mins}:{secs:02d}/mi"


def notion_page_exists(source_id):
    """Return True if a page with the given Source ID already exists in the Sessions DB."""
    db_id = os.environ["WORKOUTS_DB_ID"]
    url = f"{_notion_base()}/databases/{db_id}/query"
    payload = {
        "filter": {
            "property": "Source ID",
            "rich_text": {"equals": source_id},
        }
    }
    resp = requests.post(url, headers=_notion_headers(), json=payload)
    resp.raise_for_status()
    data = resp.json()
    return len(data.get("results", [])) > 0


def notion_create_page(database_id, properties):
    """Create a new page in the given Notion database."""
    url = f"{_notion_base()}/pages"
    resp = requests.post(
        url,
        headers=_notion_headers(),
        json={"parent": {"database_id": database_id}, "properties": properties},
    )
    resp.raise_for_status()
    return resp.json()


def build_workout_properties(workout, zone_stats):
    """
    Build the Notion properties dict for a workout.

    zone_stats: output of compute_zone_stats(), or {} if not computed.
    """
    name = workout.get("name", "Workout")
    start_str = workout.get("start", "")
    date_iso = parse_date(start_str)
    workout_type = WORKOUT_TYPE_MAP.get(name, "Strength")
    source_id = workout.get("id", "")

    # Duration: payload gives seconds as float
    duration_sec = workout.get("duration", 0)
    duration_min = round(duration_sec / 60, 1)

    props = {
        "Workout Name": {"title": [{"text": {"content": name}}]},
        "Date": {"date": {"start": date_iso}},
        "Type": {"select": {"name": workout_type}},
        "Status": {"select": {"name": "Completed"}},
        "Duration": {"number": duration_min},
        "Source ID": {"rich_text": [{"text": {"content": source_id}}]},
    }

    # Distance (runs with GPS)
    distance_field = workout.get("distance")
    if distance_field is not None:
        dist_mi = round(distance_field.get("qty", 0), 2)
        props["Distance"] = {"number": dist_mi}

        # Avg Pace from speed field: speed is in mi/hr → pace = 60/speed
        speed_field = workout.get("speed")
        if speed_field is not None:
            speed_mph = speed_field.get("qty", 0)
            if speed_mph > 0:
                avg_pace = 60.0 / speed_mph
                props["Avg Pace"] = {
                    "rich_text": [{"text": {"content": format_pace(avg_pace)}}]
                }

    # HR fields
    avg_hr_field = workout.get("avgHeartRate") or workout.get("heartRate", {}).get("avg")
    if avg_hr_field is not None:
        props["Avg HR (bpm)"] = {"number": int(round(avg_hr_field.get("qty", 0)))}

    max_hr_field = workout.get("maxHeartRate") or workout.get("heartRate", {}).get("max")
    if max_hr_field is not None:
        props["Max HR (bpm)"] = {"number": int(round(max_hr_field.get("qty", 0)))}

    # Calories
    calories_field = workout.get("activeEnergyBurned")
    if calories_field is not None:
        props["Calories"] = {"number": int(round(calories_field.get("qty", 0)))}

    # Cadence
    cadence_field = workout.get("stepCadence")
    if cadence_field is not None:
        props["Avg Cadence"] = {"number": round(cadence_field.get("qty", 0), 1)}

    # Temperature
    temp_field = workout.get("temperature")
    if temp_field is not None:
        props["Temperature (°F)"] = {"number": round(temp_field.get("qty", 0), 1)}

    # Zone stats (only for runs with HR+distance data)
    if zone_stats:
        for zone in ("Z1", "Z2", "Z3", "Z4", "Z5"):
            stats = zone_stats.get(zone)
            if stats:
                mins = stats["minutes"]
                pace_float = stats["pace_min_per_mi"]

                props[f"{zone} Min"] = {"number": mins}
                props[f"{zone} Pace (min/mi)"] = {"number": pace_float}

    return props


def handler(event, context):
    print("health-ingest invoked")

    try:
        raw_body = event.get("body") or "{}"
        body = json.loads(raw_body)
    except (json.JSONDecodeError, TypeError) as e:
        print(f"JSON parse error: {e}")
        return {"statusCode": 400, "body": json.dumps({"ok": False, "error": "Invalid JSON"})}

    workouts_raw = body.get("data", {}).get("workouts", [])
    if not workouts_raw:
        print("No workouts in payload")
        return {"statusCode": 200, "body": json.dumps({"ok": True, "ingested": 0, "skipped": 0})}

    workouts = dedup_workouts(workouts_raw)
    print(f"After dedup: {len(workouts)} workouts (was {len(workouts_raw)})")

    db_id = os.environ["WORKOUTS_DB_ID"]
    ingested = 0
    skipped = 0

    for workout in workouts:
        source_id = workout.get("id", "")
        name = workout.get("name", "Workout")

        try:
            # Idempotency check
            if source_id and notion_page_exists(source_id):
                print(f"Skipping already-ingested workout: {name} ({source_id})")
                skipped += 1
                continue

            # Compute zone stats if we have the required data
            hr_data = workout.get("heartRateData", [])
            dist_data = workout.get("walkingAndRunningDistance", [])
            zone_stats = {}
            if hr_data and dist_data:
                zone_stats = compute_zone_stats(hr_data, dist_data)

            properties = build_workout_properties(workout, zone_stats)
            notion_create_page(db_id, properties)
            print(f"Ingested workout: {name} ({source_id})")
            ingested += 1

        except Exception as e:
            print(f"Error processing workout {name} ({source_id}): {e}")
            # Continue processing remaining workouts

    return {
        "statusCode": 200,
        "body": json.dumps({"ok": True, "ingested": ingested, "skipped": skipped}),
    }
