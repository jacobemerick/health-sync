const axios = require('axios');
const NotionMock = require('../setup/notion-mock');
const metricsPayload = require('../fixtures/metrics-payload.json');

const LAMBDA_URL = 'http://localhost:9000/metrics';
let notionMock;

beforeAll(async () => {
  notionMock = new NotionMock();
  await notionMock.start(3001);
});

afterAll(async () => {
  await notionMock.stop();
});

beforeEach(() => {
  notionMock.clear();
});

async function sendPayload(payload) {
  return axios.post(LAMBDA_URL, payload, {
    headers: { 'Content-Type': 'application/json' },
    validateStatus: () => true,
  });
}

describe('body metrics ingestion', () => {
  test('creates one page per weigh-in date', async () => {
    await sendPayload(metricsPayload);
    const pages = notionMock.getPageCalls();
    const bodyPages = pages.filter(p => p.properties?.['Weight (lbs)'] !== undefined);
    // fixture has weigh-ins on 2026-04-28 and 2026-04-30
    expect(bodyPages.length).toBe(2);
  });

  test('body metrics page has all Wyze fields populated', async () => {
    await sendPayload(metricsPayload);
    const pages = notionMock.getPageCalls();
    const bodyPage = pages.find(p => p.properties?.['Weight (lbs)'] !== undefined);
    expect(bodyPage.properties['Weight (lbs)'].number).toBeGreaterThan(0);
    expect(bodyPage.properties['Lean Body Mass (lbs)'].number).toBeGreaterThan(0);
    expect(bodyPage.properties['Body Fat (%)'].number).toBeGreaterThan(0);
    expect(bodyPage.properties['BMI'].number).toBeGreaterThan(0);
  });

  test('body metrics page has correct date and name', async () => {
    await sendPayload(metricsPayload);
    const pages = notionMock.getPageCalls();
    const bodyPage = pages.find(p =>
      p.properties?.['Weight (lbs)'] !== undefined &&
      p.properties?.['Date']?.date?.start === '2026-04-28'
    );
    expect(bodyPage).toBeDefined();
    expect(bodyPage.properties['Name'].title[0].text.content).toBe('2026-04-28');
  });
});

describe('daily recovery ingestion', () => {
  test('creates one recovery page per date that has recovery data', async () => {
    await sendPayload(metricsPayload);
    const pages = notionMock.getPageCalls();
    const recoveryPages = pages.filter(p => p.properties?.['Resting HR (bpm)'] !== undefined ||
      p.properties?.['Sleep Duration (hrs)'] !== undefined);
    // fixture has recovery data on 2026-04-27, 2026-04-28, 2026-04-29, 2026-04-30
    expect(recoveryPages.length).toBe(4);
  });

  test('recovery page has sleep duration from totalSleep field', async () => {
    await sendPayload(metricsPayload);
    const pages = notionMock.getPageCalls();
    const recoveryPage = pages.find(p =>
      p.properties?.['Sleep Duration (hrs)'] !== undefined &&
      p.properties?.['Date']?.date?.start === '2026-04-28'
    );
    expect(recoveryPage).toBeDefined();
    // fixture totalSleep for 2026-04-28 is 7.43
    expect(recoveryPage.properties['Sleep Duration (hrs)'].number).toBeCloseTo(7.43, 1);
  });

  test('recovery page includes VO2 max and cardio recovery when available', async () => {
    await sendPayload(metricsPayload);
    const pages = notionMock.getPageCalls();
    const recoveryPage = pages.find(p =>
      p.properties?.['Date']?.date?.start === '2026-04-27' &&
      p.properties?.['VO2 Max (ml/kg/min)'] !== undefined
    );
    expect(recoveryPage).toBeDefined();
    expect(recoveryPage.properties['VO2 Max (ml/kg/min)'].number).toBeCloseTo(41.9, 0);
    expect(recoveryPage.properties['Cardio Recovery (bpm)'].number).toBeCloseTo(29.6, 0);
  });

  test('recovery page includes avg HR when heart_rate metric is present', async () => {
    await sendPayload(metricsPayload);
    const pages = notionMock.getPageCalls();
    const recoveryPage = pages.find(p =>
      p.properties?.['Date']?.date?.start === '2026-04-27' &&
      p.properties?.['Avg HR (bpm)'] !== undefined
    );
    expect(recoveryPage).toBeDefined();
    expect(recoveryPage.properties['Avg HR (bpm)'].number).toBeGreaterThan(0);
  });
});

describe('cross-db date separation', () => {
  test('date with only recovery data does not create a body metrics page', async () => {
    await sendPayload(metricsPayload);
    const pages = notionMock.getPageCalls();
    // 2026-04-27 has recovery data but no weigh-in
    const bodyPageForRecoveryOnlyDate = pages.find(p =>
      p.properties?.['Weight (lbs)'] !== undefined &&
      p.properties?.['Date']?.date?.start === '2026-04-27'
    );
    expect(bodyPageForRecoveryOnlyDate).toBeUndefined();
  });
});

describe('idempotency', () => {
  test('second send skips all dates already in Notion', async () => {
    await sendPayload(metricsPayload);
    const firstCount = notionMock.getPageCalls().length;

    await sendPayload(metricsPayload);
    expect(notionMock.getPageCalls().length).toBe(firstCount);
  });

  test('response reports skipped counts on second send', async () => {
    await sendPayload(metricsPayload);
    const res = await sendPayload(metricsPayload);
    expect(res.data.body_metrics).toBe(0);
    expect(res.data.recovery).toBe(0);
    expect(res.data.body_metrics_skipped).toBeGreaterThan(0);
    expect(res.data.recovery_skipped).toBeGreaterThan(0);
  });
});

describe('partial-day backfill', () => {
  // Health Auto Export POSTs a running snapshot of today, so the first payload
  // of the morning is a stub. This is the 2026-09-07 shape: the row was written
  // at 04:07 local with 41 minutes of sleep and nothing else, then a 15-hour
  // hike happened and the real numbers arrived later the same day.
  const DAY = '2026-09-07 00:00:00 -0700';
  const morningStub = {
    data: {
      metrics: [
        { name: 'sleep_analysis', units: 'hr', data: [{ date: DAY, totalSleep: 0.68 }] },
      ],
    },
  };
  const fullDay = {
    data: {
      metrics: [
        { name: 'sleep_analysis', units: 'hr', data: [{ date: DAY, totalSleep: 3.7 }] },
        { name: 'resting_heart_rate', units: 'count/min', data: [{ date: DAY, qty: 58 }] },
        { name: 'respiratory_rate', units: 'count/min', data: [{ date: DAY, qty: 15.4 }] },
        { name: 'heart_rate', units: 'count/min', data: [{ date: DAY, Avg: 121 }] },
      ],
    },
  };

  function recoveryRow() {
    return notionMock
      .getLivePages()
      .find(p => p.properties?.['Date']?.date?.start === '2026-09-07');
  }

  test('later payload fills fields the morning stub never had', async () => {
    await sendPayload(morningStub);
    await sendPayload(fullDay);

    const row = recoveryRow();
    expect(row.properties['Resting HR (bpm)'].number).toBe(58);
    expect(row.properties['Respiratory Rate (rpm)'].number).toBeCloseTo(15.4, 1);
    expect(row.properties['Avg HR (bpm)'].number).toBe(121);
  });

  test('later payload corrects a partial value the stub already held', async () => {
    await sendPayload(morningStub);
    expect(recoveryRow().properties['Sleep Duration (hrs)'].number).toBeCloseTo(0.68, 2);

    await sendPayload(fullDay);
    expect(recoveryRow().properties['Sleep Duration (hrs)'].number).toBeCloseTo(3.7, 2);
  });

  test('updating reuses the existing row instead of adding one', async () => {
    await sendPayload(morningStub);
    await sendPayload(fullDay);

    expect(notionMock.getPageCalls().length).toBe(1);
    const rows = notionMock
      .getLivePages()
      .filter(p => p.properties?.['Date']?.date?.start === '2026-09-07');
    expect(rows.length).toBe(1);
  });

  test('only the changed properties are sent in the patch', async () => {
    await sendPayload(morningStub);
    await sendPayload(fullDay);

    const patched = notionMock.getUpdateCalls();
    expect(patched.length).toBe(1);
    // Name and Date round-trip unchanged, so they must not be rewritten.
    expect(Object.keys(patched[0].properties).sort()).toEqual([
      'Avg HR (bpm)',
      'Respiratory Rate (rpm)',
      'Resting HR (bpm)',
      'Sleep Duration (hrs)',
    ]);
  });

  test('re-sending an unchanged payload writes nothing', async () => {
    await sendPayload(fullDay);
    const res = await sendPayload(fullDay);

    expect(res.data.recovery).toBe(0);
    expect(res.data.recovery_updated).toBe(0);
    expect(res.data.recovery_skipped).toBe(1);
    expect(notionMock.getUpdateCalls().length).toBe(0);
  });

  test('response reports the update', async () => {
    await sendPayload(morningStub);
    const res = await sendPayload(fullDay);

    expect(res.data.recovery).toBe(0);
    expect(res.data.recovery_updated).toBe(1);
    expect(res.data.recovery_skipped).toBe(0);
  });

  test('concurrent updates converge on the same values', async () => {
    await sendPayload(morningStub);
    await Promise.all([sendPayload(fullDay), sendPayload(fullDay)]);

    const rows = notionMock
      .getLivePages()
      .filter(p => p.properties?.['Date']?.date?.start === '2026-09-07');
    expect(rows.length).toBe(1);
    expect(rows[0].properties['Resting HR (bpm)'].number).toBe(58);
    expect(rows[0].properties['Sleep Duration (hrs)'].number).toBeCloseTo(3.7, 2);
  });
});

describe('empty payload', () => {
  test('returns 200 with zero counts for empty metrics array', async () => {
    const res = await sendPayload({ data: { metrics: [] } });
    expect(res.status).toBe(200);
    expect(res.data.ok).toBe(true);
    expect(res.data.body_metrics).toBe(0);
    expect(res.data.recovery).toBe(0);
  });
});

describe('concurrent duplicate delivery', () => {
  test('two simultaneous identical POSTs leave one live page per date and DB', async () => {
    await Promise.all([sendPayload(metricsPayload), sendPayload(metricsPayload)]);

    // Body Metrics and Daily Recovery both key on date, so check them apart.
    const live = notionMock.getLivePages();
    const bodyDates = live
      .filter(p => p.properties?.['Weight (lbs)'] !== undefined)
      .map(p => p.properties['Date'].date.start);
    const recoveryDates = live
      .filter(p => p.properties?.['Weight (lbs)'] === undefined)
      .map(p => p.properties['Date'].date.start);

    expect(bodyDates.length).toBeGreaterThan(0);
    expect(new Set(bodyDates).size).toBe(bodyDates.length);
    expect(recoveryDates.length).toBeGreaterThan(0);
    expect(new Set(recoveryDates).size).toBe(recoveryDates.length);
  });
});
