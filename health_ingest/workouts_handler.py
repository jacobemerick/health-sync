"""
Webhook handler for Health Auto Export (iOS app) — workouts payload.

Receives POST with shape:
  { "data": { "workouts": [ { ...workout fields... } ] } }
"""

import json
import os
from health_ingest import notion
from health_ingest.utils import parse_date

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


def get_zone(hr_bpm):
    for zone, low, high in ZONE_BOUNDARIES:
        if low is None and hr_bpm <= high:
            return zone
        if high is None and hr_bpm >= low:
            return zone
        if low is not None and high is not None and low <= hr_bpm <= high:
            return zone
    return "Z5"


def dedup_workouts(workouts):
    by_start = {}
    for w in workouts:
        start = w.get("start", "")
        if start not in by_start:
            by_start[start] = w
        else:
            existing = by_start[start]
            if w.get("distance") is not None and existing.get("distance") is None:
                by_start[start] = w
    return list(by_start.values())


def compute_zone_stats(heart_rate_data, distance_data):
    dist_lookup = {e.get("date", ""): e.get("qty", 0) for e in distance_data}

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
            result[zone] = {"minutes": mins, "pace_min_per_mi": round(mins / dist, 2)}
    return result


def format_pace(min_per_mi):
    mins = int(min_per_mi)
    secs = round((min_per_mi - mins) * 60)
    if secs == 60:
        mins += 1
        secs = 0
    return f"{mins}:{secs:02d}/mi"


def build_workout_properties(workout, zone_stats):
    name = workout.get("name", "Workout")
    date_iso = parse_date(workout.get("start", ""))
    workout_type = WORKOUT_TYPE_MAP.get(name, "Strength")
    source_id = workout.get("id", "")
    duration_min = round(workout.get("duration", 0) / 60, 1)

    props = {
        "Workout Name": {"title": [{"text": {"content": name}}]},
        "Date": {"date": {"start": date_iso}},
        "Type": {"select": {"name": workout_type}},
        "Status": {"select": {"name": "Completed"}},
        "Duration": {"number": duration_min},
        "Source ID": {"rich_text": [{"text": {"content": source_id}}]},
    }

    distance_field = workout.get("distance")
    if distance_field is not None:
        props["Distance"] = {"number": round(distance_field.get("qty", 0), 2)}
        speed_field = workout.get("speed")
        if speed_field is not None:
            speed_mph = speed_field.get("qty", 0)
            if speed_mph > 0:
                props["Avg Pace"] = {
                    "rich_text": [{"text": {"content": format_pace(60.0 / speed_mph)}}]
                }

    avg_hr = workout.get("avgHeartRate") or workout.get("heartRate", {}).get("avg")
    if avg_hr is not None:
        props["Avg HR (bpm)"] = {"number": int(round(avg_hr.get("qty", 0)))}

    max_hr = workout.get("maxHeartRate") or workout.get("heartRate", {}).get("max")
    if max_hr is not None:
        props["Max HR (bpm)"] = {"number": int(round(max_hr.get("qty", 0)))}

    calories = workout.get("activeEnergyBurned")
    if calories is not None:
        props["Calories"] = {"number": int(round(calories.get("qty", 0)))}

    cadence = workout.get("stepCadence")
    if cadence is not None:
        props["Avg Cadence"] = {"number": round(cadence.get("qty", 0), 1)}

    temp = workout.get("temperature")
    if temp is not None:
        props["Temperature (°F)"] = {"number": round(temp.get("qty", 0), 1)}

    for zone in ("Z1", "Z2", "Z3", "Z4", "Z5"):
        stats = zone_stats.get(zone)
        if stats:
            props[f"{zone} Min"] = {"number": stats["minutes"]}
            props[f"{zone} Pace (min/mi)"] = {"number": stats["pace_min_per_mi"]}

    return props


def handler(event, context):
    print("workouts-ingest invoked")

    try:
        body = json.loads(event.get("body") or "{}")
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
            if source_id and notion.page_exists_by_source_id(db_id, source_id):
                print(f"Skipping already-ingested workout: {name} ({source_id})")
                skipped += 1
                continue

            hr_data = workout.get("heartRateData", [])
            dist_data = workout.get("walkingAndRunningDistance", [])
            zone_stats = compute_zone_stats(hr_data, dist_data) if hr_data and dist_data else {}

            notion.create_page(db_id, build_workout_properties(workout, zone_stats))
            print(f"Ingested workout: {name} ({source_id})")
            ingested += 1
        except Exception as e:
            print(f"Error processing workout {name} ({source_id}): {e}")

    return {"statusCode": 200, "body": json.dumps({"ok": True, "ingested": ingested, "skipped": skipped})}
