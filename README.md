# health-sync

AWS Lambda function that pipes workout data into a Notion database.

---

## Architecture

```
1. Workouts occur and are tracked either via the Whoop app directly (for functional workouts) or Apple Watch (for runs).
  - Whoop sends a single workout to Apple Health
  - Apple Watch records the run and sends to Whoop, which then sends back to Apple Health (resulting in duplicate workouts logged)
1. Apple Health data lives locally on the device and DOES NOT have an accessible API or SDK
1. Health Auto Export (iOS) can sit on the device and sync with third parties
1. Automated, scheduled payload is sent from device to health-ingest lambda
1. Lambda then de-duplicates, manipulates, and checks Notion database before insertion
```

This scriopt deduplicates workouts, computes per-zone pace and time from per-minute heart rate data, and checks for existing rows before inserting to stay idempotent.
Deploys automatically via GitHub Actions on push to `main`, and PRs run the e2e test suite.

---

## First-time setup

### 1. Prerequisites

- Node 24 + Python 3.14
- AWS CLI configured (`aws configure`)
- Serverless Framework v4 (`npm install`)
- A Notion internal integration token with access to the target databases

### 2. Environment variables

Copy `.env.example` to `.env` and fill in:

```bash
cp .env.example .env
```

| Variable | Description |
|---|---|
| `NOTION_TOKEN` | Notion internal integration token (`ntn_...`) |
| `WORKOUTS_DB_ID` | Notion Sessions database page ID |
| `BODY_METRICS_DB_ID` | Notion Body Metrics database page ID |

### 3. Connect Notion integration

In Notion, open each target database and go to `...` → **Connections** → add your integration:
- Sessions
- Body Metrics

### 4. Wire up Health Auto Export

After deploy (see below), paste the `health-ingest` Function URL into Health Auto Export:
- **Format:** JSON
- **URL:** the Lambda Function URL (from `npx serverless info`)
- **Data types:** Workouts — with Heart Rate (per-interval) and Heart Rate Recovery enabled; Route and other timeseries disabled

---

## Local dev

The local dev server wraps the Lambda handler in a lightweight Flask HTTP endpoint on port 9000.
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

Tests cover deduplication, zone pace calculation, idempotency, and core property mapping.
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

Push to `main` — the workflow deploys automatically.

### Manual deploy

```bash
npm install
npx serverless deploy --verbose
```
