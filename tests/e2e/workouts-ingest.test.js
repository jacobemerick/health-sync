const axios = require('axios');
const NotionMock = require('../setup/notion-mock');
const workoutPayload = require('../fixtures/workouts-payload.json');

const LAMBDA_URL = 'http://localhost:9000';
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

describe('deduplication', () => {
  test('keeps Outdoor Run and drops duplicate Run for same start time', async () => {
    await sendPayload(workoutPayload);
    const pages = notionMock.getPageCalls();
    const runPages = pages.filter(p =>
      p.properties?.Type?.select?.name === 'Run'
    );

    // 2 run sessions in fixture (one pair of duplicates + one standalone)
    // after dedup: should be 2 run pages total (not 3)
    expect(runPages.length).toBe(2);
  });

  test('deduped run record has Distance populated', async () => {
    await sendPayload(workoutPayload);
    const pages = notionMock.getPageCalls();
    const runPages = pages.filter(p => p.properties?.Type?.select?.name === 'Run');
    runPages.forEach(p => {
      expect(p.properties['Distance']?.number).toBeGreaterThan(0);
    });
  });
});

describe('zone calculation', () => {
  test('populates Z2 Pace and Z2 Min for outdoor runs with HR data', async () => {
    await sendPayload(workoutPayload);
    const pages = notionMock.getPageCalls();
    const runWithZones = pages.find(p =>
      p.properties?.Type?.select?.name === 'Run' &&
      p.properties?.['Z2 Min']?.number > 0
    );
    expect(runWithZones).toBeDefined();
    expect(runWithZones.properties['Z2 Pace (min/mi)']?.number).toBeGreaterThan(0);
  });

  test('Z2 pace is a realistic running pace (between 8 and 20 min/mi)', async () => {
    await sendPayload(workoutPayload);
    const pages = notionMock.getPageCalls();
    const runPage = pages.find(p =>
      p.properties?.['Z2 Pace (min/mi)']?.number > 0
    );
    if (runPage) {
      const pace = runPage.properties['Z2 Pace (min/mi)'].number;
      expect(pace).toBeGreaterThan(8);
      expect(pace).toBeLessThan(20);
    }
  });

  test('strength sessions do not have zone pace fields', async () => {
    await sendPayload(workoutPayload);
    const pages = notionMock.getPageCalls();
    const strengthPage = pages.find(p => p.properties?.Type?.select?.name === 'Strength');
    expect(strengthPage).toBeDefined();
    expect(strengthPage.properties['Z2 Pace (min/mi)']).toBeUndefined();
    expect(strengthPage.properties['Z2 Min']).toBeUndefined();
  });
});

describe('idempotency', () => {
  test('skips insertion when Source ID already exists in Notion', async () => {
    await sendPayload(workoutPayload);
    const firstCallCount = notionMock.getPageCalls().length;

    // Second send: mock already has the pages, so queries return results → all skipped
    await sendPayload(workoutPayload);
    expect(notionMock.getPageCalls().length).toBe(firstCallCount);
  });
});

describe('core properties', () => {
  test('all sessions have required properties', async () => {
    await sendPayload(workoutPayload);
    const pages = notionMock.getPageCalls();
    pages.forEach(page => {
      expect(page.properties['Workout Name']?.title).toBeDefined();
      expect(page.properties['Date']?.date?.start).toMatch(/^\d{4}-\d{2}-\d{2}/);
      expect(page.properties['Type']?.select?.name).toBeDefined();
      expect(page.properties['Status']?.select?.name).toBe('Completed');
      expect(page.properties['Duration']?.number).toBeGreaterThan(0);
      expect(page.properties['Source ID']?.rich_text?.[0]?.text?.content).toBeTruthy();
    });
  });

  test('run sessions have Avg Pace formatted correctly', async () => {
    await sendPayload(workoutPayload);
    const pages = notionMock.getPageCalls();
    const runPage = pages.find(p => p.properties?.Type?.select?.name === 'Run');
    const avgPace = runPage?.properties?.['Avg Pace']?.rich_text?.[0]?.text?.content;
    if (avgPace) {
      expect(avgPace).toMatch(/\d+:\d{2}\/mi/);
    }
  });
});

describe('concurrent duplicate delivery', () => {
  test('two simultaneous identical POSTs leave one live page per Source ID', async () => {
    // Health Auto Export occasionally POSTs the same payload twice at once.
    // Both invocations can pass the exists-check before either writes, which is
    // what produced the duplicate rows on 2026-08-23, 08-25 and 08-31.
    await Promise.all([sendPayload(workoutPayload), sendPayload(workoutPayload)]);

    const live = notionMock.getLivePages();
    const sourceIds = live.map(
      p => p.properties?.['Source ID']?.rich_text?.[0]?.text?.content
    );

    expect(sourceIds.length).toBeGreaterThan(0);
    expect(new Set(sourceIds).size).toBe(sourceIds.length);
  });

  test('a duplicate that slips through is archived, not left in the database', async () => {
    await Promise.all([sendPayload(workoutPayload), sendPayload(workoutPayload)]);

    // Whichever way the race fell, every page written beyond the first for a
    // given Source ID must have been archived back out. Both racers can pick the
    // same loser and each send an archive for it, so count distinct pages —
    // archiving twice is deliberately harmless.
    const created = notionMock.getPageCalls().length;
    const archived = new Set(notionMock.getArchiveCalls()).size;
    const live = notionMock.getLivePages().length;
    expect(live).toBe(created - archived);
  });
});
