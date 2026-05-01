# health-sync

AWS Lambda functions that pipe Apple Health data into Notion databases.

---

## Architecture

```
1. Workouts occur and are tracked either via the Whoop app directly (for functional workouts) or Apple Watch (for runs).
  - Whoop sends a single workout to Apple Health
  - Apple Watch records the run and sends to Whoop, which then sends back to Apple Health (resulting in duplicate workouts logged)
1. Apple Health data lives locally on the device and DOES NOT have an accessible API or SDK
1. Health Auto Export (iOS) can sit on the device and sync with third parties
1. Automated, scheduled payloads are sent from device to two Lambda Function URLs
1. Each Lambda deduplicates, maps fields, and checks Notion before inserting to stay idempotent
```

**health-ingest** handles the workouts payload — deduplicates entries, computes per-zone pace and time from per-minute heart rate data, and writes to the Workouts DB.

**metrics-ingest** handles the daily metrics payload — maps body composition and recovery metrics to date-keyed rows across two DBs.

Both deploy automatically via GitHub Actions on push to `main`, and PRs run the e2e test suite.

---

## Notion database schemas

### Workouts DB (`WORKOUTS_DB_ID`)

| Property | Type | Notes |
|---|---|---|
| `Workout Name` | Title | |
| `Date` | Date | |
| `Type` | Select | Options: Run, Hike, Strength, Recovery |
| `Status` | Select | Options: Completed |
| `Duration` | Number | Minutes |
| `Distance` | Number | Miles (runs only) |
| `Avg Pace` | Text | Format: `MM:SS/mi` (runs only) |
| `Avg HR (bpm)` | Number | |
| `Max HR (bpm)` | Number | |
| `Calories` | Number | |
| `Avg Cadence` | Number | Steps/min |
| `Temperature (°F)` | Number | |
| `Source ID` | Text | Apple Health UUID — used for idempotency |
| `Z1 Min`–`Z5 Min` | Number | Minutes in each HR zone (runs only) |
| `Z1 Pace (min/mi)`–`Z5 Pace (min/mi)` | Number | Avg pace in each HR zone (runs only) |

### Body Metrics DB (`BODY_METRICS_DB_ID`)

One row per weigh-in date. Source: Wyze scale via Apple Health.

| Property | Type | Notes |
|---|---|---|
| `Name` | Title | Date string (YYYY-MM-DD) |
| `Date` | Date | Used for idempotency |
| `Weight (lbs)` | Number | |
| `Lean Body Mass (lbs)` | Number | |
| `Body Fat (%)` | Number | |
| `BMI` | Number | |

### Daily Recovery DB (`DAILY_RECOVERY_DB_ID`)

One row per day. Sources: WHOOP and Apple Watch via Apple Health.

| Property | Type | Notes |
|---|---|---|
| `Name` | Title | Date string (YYYY-MM-DD) |
| `Date` | Date | Used for idempotency |
| `Resting HR (bpm)` | Number | From WHOOP |
| `Sleep Duration (hrs)` | Number | Total sleep from WHOOP |
| `Respiratory Rate (rpm)` | Number | From WHOOP |
| `VO2 Max (ml/kg/min)` | Number | From Apple Watch |
| `Cardio Recovery (bpm)` | Number | From Apple Watch |
| `Avg HR (bpm)` | Number | Daily average |

---

## First-time setup

### 1. Prerequisites

- Node 24 + Python 3.14
- AWS CLI configured (`aws configure`)
- Serverless Framework v4 (`npm install`)
- A Serverless Framework account and access key (v4 requires one — get it at [app.serverless.com](https://app.serverless.com) under **org → Access Keys**)
- A Notion internal integration token with access to the target databases

### 2. Environment variables

Copy `.env.example` to `.env` and fill in:

```bash
cp .env.example .env
```

| Variable | Description |
|---|---|
| `NOTION_TOKEN` | Notion internal integration token (`ntn_...`) |
| `WORKOUTS_DB_ID` | Notion Workouts database page ID |
| `BODY_METRICS_DB_ID` | Notion Body Metrics database page ID |
| `DAILY_RECOVERY_DB_ID` | Notion Daily Recovery database page ID |

### 3. Connect Notion integration

In Notion, open each target database and go to `...` → **Connections** → add your integration:
- Workouts
- Body Metrics
- Daily Recovery

### 4. Wire up Health Auto Export

After deploy (see below), paste both Function URLs into Health Auto Export as separate webhooks:

**Workouts webhook** (`health-ingest` URL):
- **Format:** JSON
- **Data types:** Workouts — with Heart Rate (per-interval) and Heart Rate Recovery enabled; Route and other timeseries disabled

**Metrics webhook** (`metrics-ingest` URL):
- **Format:** JSON
- **Data types:** weight_body_mass, lean_body_mass, body_fat_percentage, body_mass_index, resting_heart_rate, sleep_analysis, respiratory_rate, vo2_max, cardio_recovery, heart_rate
- **Aggregation:** Daily

---

## Local dev

The local dev server wraps both Lambda handlers in a lightweight Flask app on port 9000 — workouts at `/`, metrics at `/metrics`.
It uses `python:3.14-slim` rather than the official AWS Lambda runtime image — the Lambda-specific runtime behavior (cold starts, RIC invocation format) isn't something the handler logic depends on, and the slim image is significantly smaller.
The gap that matters — Python version and installed packages — is covered either way.

```bash
docker compose up
```

The container hot-reloads handler changes via volume mounts.
Set `NOTION_API_BASE` in `docker-compose.yml` to point at a mock if you don't want live Notion writes during manual testing.

---

## Testing

E2e tests run Jest outside the container, send payloads to the local Lambda on port 9000, and assert against a mock Notion server on port 3001.

```bash
# Start the Lambda container first
docker compose up -d

# Run tests
cd tests && npm test
```

Tests cover deduplication, zone pace calculation, idempotency, core property mapping, body metrics insertion, and recovery metrics insertion.
They also run automatically on every PR via GitHub Actions.

---

## Deploy

### GitHub Actions (recommended)

Add these secrets to **GitHub → repo → Settings → Secrets and variables → Actions**:

| Secret | Value |
|---|---|
| `AWS_ACCESS_KEY_ID` | AWS IAM key |
| `AWS_SECRET_ACCESS_KEY` | AWS IAM secret |
| `NOTION_TOKEN` | Notion integration token |
| `SERVERLESS_ACCESS_KEY` | Serverless Framework access key (required by v4) |

Push to `main` — the workflow deploys automatically.

### Manual deploy

```bash
npm install
npx serverless deploy --verbose
```
