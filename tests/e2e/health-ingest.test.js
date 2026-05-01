const axios = require('axios');
const NotionMock = require('../setup/notion-mock');
const workoutPayload = require('../fixtures/workout-payload.json');

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
