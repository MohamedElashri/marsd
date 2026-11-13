import { test, expect, type Page, type Response } from '@playwright/test';

type Json = Record<string, any>;
const files = [
  {
    id: 'file-one',
    version_id: 'version-one',
    name: 'one.h5',
    path: '/data/one.h5',
    format: 'HDF5',
    identity_strength: 'stat_weak',
    stale: false,
  },
  {
    id: 'file-two',
    version_id: 'version-two',
    name: 'two.h5',
    path: '/data/two.h5',
    format: 'HDF5',
    identity_strength: 'stat_weak',
    stale: false,
  },
];
const runs = ['one', 'two'].map((name, index) => ({
  id: `run-${name}`,
  script: `/scripts/${name}.py`,
  state: index ? 'SUCCEEDED' : 'FAILED',
  started_at: '2026-01-01T12:00:00Z',
}));
function runDetail(name: string) {
  const run = runs.find((item) => item.id === name)!;
  return {
    ...run,
    code: { path: run.script, sha256: 'fixture-hash' },
    environment: { python: 'fixture' },
    exit_code: run.state === 'FAILED' ? 1 : 0,
    text_capture: 'off',
    references: [],
    limitation: 'Synthetic UI fixture.',
  };
}
function graph(label: string) {
  return {
    direction: 'upstream',
    nodes: [{ id: label, label, kind: 'entity', depth: 0, metadata: {} }],
    edges: [],
    coverage: { complete: true },
  };
}
async function fixture(page: Page, options: { failStructure?: boolean } = {}) {
  await page.addInitScript(() => sessionStorage.setItem('marsd-session', 'ui-fixture'));
  const jobs = new Map<string, Json>();
  let failed = false;
  await page.route('**/api/**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const endpoint = url.pathname.slice('/api/'.length);
    let data: unknown;
    if (endpoint === 'project')
      data = {
        name: 'UI regression study',
        path: '/study',
        source_roots: ['/data'],
        outputs: '/study/.marsd/reports',
      };
    else if (endpoint === 'artifacts') data = files;
    else if (['contracts', 'reports'].includes(endpoint)) data = [];
    else if (endpoint === 'jobs' && request.method() === 'POST') {
      const parameters = request.postDataJSON();
      const source = files.find((file) => file.version_id === parameters.version_id)!;
      const count = source.version_id === 'version-one' ? 10 : 1;
      const nodes = [
        { path: '/', kind: 'group' },
        { path: '/measurement', kind: 'group' },
        ...Array.from({ length: count }, (_, index) => ({
          path: `/measurement/dataset_${index}`,
          kind: 'dataset',
          supported: true,
          shape: [2, 2],
          dtype: 'float64',
        })),
      ];
      const id = `job-${jobs.size + 1}`;
      const failure = options.failStructure && !failed && parameters.operation === 'inspect';
      if (failure) failed = true;
      const result = {
        kind: parameters.operation,
        parameters,
        inputs: [source],
        coverage: { complete: true, strategy: 'selection', visited: 4, total: 4 },
        ...(parameters.operation === 'inspect'
          ? { nodes, capabilities: ['numeric_slice', 'profile'] }
          : {
              values: [
                [1, 2],
                [3, 4],
              ],
              array: { path: parameters.node, shape: [2, 2], unit: 'dimensionless' },
            }),
      };
      jobs.set(id, {
        id,
        state: failure ? 'FAILED' : 'SUCCEEDED',
        operation: parameters.operation,
        request: parameters,
        result: failure ? undefined : result,
        error: failure
          ? { code: 'READ_FAILED', message: 'Synthetic structure read failed.' }
          : undefined,
      });
      await route.fulfill({ status: 202, json: { id } });
      return;
    } else if (endpoint === 'jobs') data = [...jobs.values()];
    else if (endpoint.startsWith('jobs/')) data = jobs.get(endpoint.slice(5));
    else if (endpoint === 'runs') data = runs;
    else if (endpoint === 'runs/compare')
      data = {
        limitation: 'Comparison fixture',
        changes: [],
        a: url.searchParams.get('a'),
        b: url.searchParams.get('b'),
      };
    else if (endpoint.startsWith('runs/')) data = runDetail(endpoint.slice(5));
    else if (endpoint.startsWith('lineage/')) data = graph('Current query');
    else throw new Error(`Unexpected fixture API ${request.method()} ${endpoint}`);
    await route.fulfill({ json: data });
  });
  await page.goto('/');
  await expect(page.getByText('UI regression study', { exact: true })).toBeVisible();
}
async function openFile(page: Page, name = 'one.h5') {
  await page
    .getByRole('complementary', { name: 'Data navigator' })
    .getByRole('button', { name: new RegExp(name.replace('.', '\\.')) })
    .click();
  await expect(page.getByRole('img', { name: /Heatmap/ })).toBeVisible();
}
async function rendered(page: Page, response: Response) {
  await response.finished();
  await page.evaluate(
    () =>
      new Promise<void>((resolve) =>
        requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
      ),
  );
}

test('dataset search finds matches inside collapsed groups and resets between files', async ({
  page,
}) => {
  await fixture(page);
  await openFile(page);
  const navigator = page.getByRole('complementary', { name: 'Data navigator' });
  await navigator
    .locator('.hierarchy')
    .getByRole('button', { name: /measurement.*group/ })
    .click();
  await expect(navigator.locator('.node').filter({ hasText: 'dataset_0' })).toBeHidden();
  await navigator.getByRole('searchbox', { name: 'Find a dataset' }).fill('dataset_0');
  await expect(navigator.locator('.node').filter({ hasText: 'dataset_0' })).toBeVisible();
  await navigator.getByRole('searchbox', { name: 'Find a dataset' }).fill('absent-dataset');
  await expect(navigator.getByText('No matching datasets.', { exact: false })).toBeVisible();
  await navigator.getByRole('button', { name: /two\.h5/ }).click();
  await expect(navigator.locator('.node').filter({ hasText: 'dataset_0' })).toBeVisible();
});

test('failed structure reads offer a direct retry', async ({ page }) => {
  await fixture(page, { failStructure: true });
  await page
    .getByRole('complementary', { name: 'Data navigator' })
    .getByRole('button', { name: /one\.h5/ })
    .click();
  await expect(page.getByRole('alert')).toContainText('READ_FAILED');
  await page.getByRole('button', { name: 'Read structure', exact: true }).click();
  await expect(page.getByRole('img', { name: /Heatmap/ })).toBeVisible();
  await expect(page.getByRole('alert')).toHaveCount(0);
});

test('late readiness and comparison responses cannot attach to another run', async ({ page }) => {
  await fixture(page);
  let release!: () => void;
  let entered!: () => void;
  let gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  let started = new Promise<void>((resolve) => {
    entered = resolve;
  });
  await page.route('**/api/runs/run-one/readiness', async (route) => {
    entered();
    await gate;
    await route.fulfill({
      json: { status: 'OLD READINESS', blockers: [], unknowns: [], limitations: [] },
    });
  });
  await page.getByRole('button', { name: 'Runs', exact: true }).click();
  await expect(page.locator('.run-detail h2')).toHaveText('one.py');
  await page.getByRole('button', { name: 'Assess readiness (dry-run)', exact: true }).click();
  await started;
  await page
    .locator('.run-history')
    .getByRole('button', { name: /two\.py/ })
    .click();
  await expect(page.locator('.run-detail h2')).toHaveText('two.py');
  const late = page.waitForResponse('**/api/runs/run-one/readiness');
  release();
  await rendered(page, await late);
  await expect(page.locator('.readiness')).toHaveCount(0);
  await page
    .locator('.run-history')
    .getByRole('button', { name: /one\.py/ })
    .click();
  await expect(page.locator('.run-detail h2')).toHaveText('one.py');
  gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  started = new Promise<void>((resolve) => {
    entered = resolve;
  });
  await page.route('**/api/runs/compare?*', async (route) => {
    entered();
    await gate;
    await route.fulfill({
      json: { limitation: 'OLD COMPARISON', changes: [{ field: 'old_code' }] },
    });
  });
  await page.getByRole('combobox', { name: 'Reference run', exact: true }).selectOption('run-two');
  await page.getByRole('button', { name: 'Compare recorded evidence', exact: true }).click();
  await started;
  await page
    .locator('.run-history')
    .getByRole('button', { name: /two\.py/ })
    .click();
  await expect(page.locator('.run-detail h2')).toHaveText('two.py');
  const compared = page.waitForResponse('**/api/runs/compare?*');
  release();
  await rendered(page, await compared);
  await expect(page.getByRole('heading', { name: 'Changed evidence', exact: true })).toHaveCount(0);
  await expect(page.getByRole('combobox', { name: 'Reference run', exact: true })).toHaveValue('');
  await expect(
    page.getByRole('button', { name: 'Compare recorded evidence', exact: true }),
  ).toBeDisabled();
});

test('editing lineage scope invalidates an in-flight graph', async ({ page }) => {
  await fixture(page);
  let release!: () => void, entered!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  const started = new Promise<void>((resolve) => {
    entered = resolve;
  });
  let first = true;
  await page.route('**/api/lineage/**', async (route) => {
    if (first) {
      first = false;
      entered();
      await gate;
      await route.fulfill({ json: graph('Old query') });
    } else await route.fulfill({ json: graph('Current query') });
  });
  await page.getByRole('button', { name: 'Lineage', exact: true }).click();
  await page.getByRole('button', { name: 'Load dependencies', exact: true }).click();
  await started;
  await page.getByLabel('Depth', { exact: true }).fill('4');
  const late = page.waitForResponse('**/api/lineage/**');
  release();
  await rendered(page, await late);
  await expect(page.getByRole('button', { name: 'Expand Old query', exact: true })).toHaveCount(0);
  await page.getByRole('button', { name: 'Load dependencies', exact: true }).click();
  await expect(
    page.getByRole('button', { name: 'Expand Current query', exact: true }),
  ).toBeVisible();
});

test('overlay panes respect messages, close with Escape and restore focus', async ({ page }) => {
  await page.setViewportSize({ width: 1000, height: 760 });
  await fixture(page);
  await openFile(page);
  await page.route('**/api/jobs', async (route) => {
    if (route.request().method() === 'POST')
      await route.fulfill({
        status: 400,
        json: { error: { code: 'READ_FAILED', message: 'Synthetic failure for pane review.' } },
      });
    else await route.fallback();
  });
  await page.getByRole('button', { name: 'Update', exact: true }).click();
  await expect(page.getByRole('alert')).toBeVisible();
  const toggle = page.getByRole('button', { name: 'Toggle selection inspector', exact: true });
  await toggle.click();
  const inspector = page.getByRole('complementary', { name: 'Selection inspector' });
  const alert = (await page.getByRole('alert').boundingBox())!;
  expect((await inspector.boundingBox())!.y).toBeGreaterThanOrEqual(alert.y + alert.height);
  await page.screenshot({ path: 'test-results/marsd-inspector-message.png', scale: 'css' });
  await page.keyboard.press('Escape');
  await expect(inspector).toBeHidden();
  await expect(toggle).toBeFocused();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole('button', { name: 'Show navigator', exact: true }).click();
  await toggle.click();
  await expect(page.getByRole('complementary', { name: 'Data navigator' })).toBeHidden();
  await page.screenshot({ path: 'test-results/marsd-inspector-mobile.png', scale: 'css' });
  await page.getByRole('button', { name: 'Hide selection inspector', exact: true }).click();
  await expect(toggle).toBeFocused();
  expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)).toBe(false);
});

test('edited preview controls are distinguished from the applied result', async ({ page }) => {
  await fixture(page);
  await openFile(page);
  await page.getByLabel('Array selection', { exact: true }).fill('0:1,0:1');
  await expect(page.getByText('Options changed', { exact: true })).toBeVisible();
  await page.screenshot({ path: 'test-results/marsd-unapplied-options.png', scale: 'css' });
  await page.getByRole('button', { name: 'Update', exact: true }).click();
  await expect(page.getByText('Options changed', { exact: true })).toHaveCount(0);
  await expect(page.getByRole('img', { name: /Heatmap/ })).toBeVisible();
});

test('closing file tabs keeps keyboard focus in the workbench', async ({ page }) => {
  await fixture(page);
  await openFile(page);
  await openFile(page, 'two.h5');
  await page.getByRole('button', { name: 'Close two.h5', exact: true }).click();
  await expect(page.getByRole('tab', { name: /one\.h5/ })).toBeFocused();
  await page.getByRole('button', { name: 'Close one.h5', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Hide navigator', exact: true })).toBeFocused();
});
