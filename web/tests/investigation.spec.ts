import { test, expect } from '@playwright/test';
import fs from 'node:fs/promises';
import path from 'node:path';

test('dense workbench preserves scientific context across files and evidence views', async ({
  page,
}) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  const errors: string[] = [];
  const externalRequests: string[] = [];
  await page.route('**/*', (route) => {
    const url = new URL(route.request().url());
    if (['http:', 'https:'].includes(url.protocol) && url.hostname !== '127.0.0.1') {
      externalRequests.push(url.origin);
      return route.abort();
    }
    return route.continue();
  });
  page.on('pageerror', (error) => errors.push(error.message));
  await page.goto('/');
  await page.getByLabel('Launch code', { exact: true }).fill('automated-test-session');
  const startup = performance.now();
  await page.getByRole('button', { name: 'Open workspace', exact: true }).click();
  await expect(page.getByText('Synthetic spectral study', { exact: true })).toBeVisible();
  const expectedArtifacts = Number(process.env.MARSD_E2E_ARTIFACTS || 7);
  await expect(page.locator('.file-list .file-row')).toHaveCount(expectedArtifacts);
  const startupSamples = [(performance.now() - startup) / 1000];
  if (expectedArtifacts === 100) {
    for (let i = 0; i < 3; i++) {
      const start = performance.now();
      await page.reload();
      await expect(page.locator('.file-list .file-row')).toHaveCount(100);
      startupSamples.push((performance.now() - start) / 1000);
    }
    const median = [...startupSamples.slice(1)].sort((a, b) => a - b)[1];
    console.log(
      JSON.stringify({
        benchmark: '100-artifact UI startup',
        seconds: startupSamples,
        median_seconds: median,
      }),
    );
    if (process.env.MARSD_ENFORCE_BUDGETS === '1') expect(median).toBeLessThanOrEqual(3);
  }
  const files = page.getByRole('complementary', { name: 'Data navigator' });
  await files.getByRole('searchbox', { name: 'Find files', exact: true }).fill('no-such-file');
  await expect(files.locator('.file-row')).toHaveCount(0);
  await expect(files.getByText('No matching files.', { exact: true })).toBeVisible();
  await files.getByRole('searchbox', { name: 'Find files', exact: true }).fill('');
  await files.getByRole('combobox', { name: 'File format', exact: true }).selectOption('HDF5');
  await expect(files.locator('.file-row')).toHaveCount(2);
  await files.getByRole('combobox', { name: 'File format', exact: true }).selectOption('');
  await files.getByRole('button', { name: /run_042\.h5/ }).click();
  await expect(page.getByRole('img', { name: /Heatmap/ })).toBeVisible();
  await expect(page.getByText('Requested scope complete')).toBeVisible();
  const heatmapCell = await page.locator('.heatmap-cells rect').first().boundingBox();
  expect(heatmapCell).not.toBeNull();
  expect(heatmapCell!.width).toBeCloseTo(heatmapCell!.height, 1);
  await page.getByRole('button', { name: 'Values', exact: true }).click();
  await expect(page.locator('.results table')).toBeVisible();
  await page.getByRole('button', { name: 'Plot', exact: true }).click();
  await page.getByRole('button', { name: 'Statistics', exact: true }).click();
  await expect(page.locator('.profile-statistics')).toContainText('4096');
  await page.getByRole('button', { name: 'Statistics', exact: true }).focus();
  const profileRefresh = page.waitForRequest(
    (request) =>
      request.method() === 'POST' &&
      request.url().endsWith('/api/jobs') &&
      request.postDataJSON().operation === 'profile',
  );
  await page.keyboard.press('ControlOrMeta+Enter');
  await profileRefresh;
  await expect(
    page.getByRole('status').filter({ hasText: 'Statistics · succeeded' }),
  ).toBeVisible();

  await files.getByRole('button', { name: /run_041\.h5/ }).click();
  await expect(page.getByRole('img', { name: /Heatmap/ })).toBeVisible();
  await page.getByRole('tab', { name: /run_042\.h5/ }).click();
  await expect(page.locator('.profile-statistics')).toContainText('4096');

  await page.getByRole('button', { name: 'Preview', exact: true }).click();
  await files
    .locator('.hierarchy')
    .getByRole('button', { name: /large_integer/ })
    .click();
  await expect(
    page.getByText(
      'Switch to Values to inspect large integers exactly. This selection is not plotted.',
    ),
  ).toBeVisible();
  await page.getByRole('button', { name: 'Values', exact: true }).click();
  await expect(page.getByRole('cell', { name: '9007199254740993', exact: true })).toBeVisible();
  await files
    .locator('.hierarchy')
    .getByRole('button', { name: /transmission/ })
    .click();
  await expect(page.getByRole('img', { name: /Heatmap/ })).toBeVisible();

  const divider = page.getByRole('separator', { name: 'Resize data navigator', exact: true });
  const initialWidth = Number(await divider.getAttribute('aria-valuenow'));
  await divider.focus();
  await page.keyboard.press('ArrowRight');
  expect(Number(await divider.getAttribute('aria-valuenow'))).toBe(initialWidth + 16);
  await expect
    .poll(async () => Math.round((await files.boundingBox())!.width))
    .toBe(initialWidth + 16);
  await page.keyboard.press('ArrowLeft');
  await files.getByRole('button', { name: /run_041\.h5/ }).click();
  await expect(page.getByRole('tab', { name: /run_041\.h5/ })).toHaveAttribute(
    'aria-selected',
    'true',
  );
  await expect(page.getByRole('img', { name: /Heatmap/ })).toBeVisible();
  await page.keyboard.press('Alt+Digit1');
  await expect(page.getByRole('tab', { name: /run_042\.h5/ })).toHaveAttribute(
    'aria-selected',
    'true',
  );
  await expect(page.getByRole('img', { name: /Heatmap/ })).toBeVisible();
  // Hold a real completed preview, change dataset, then release the old response.
  let releasePreview!: () => void, previewCompleted!: () => void;
  const previewGate = new Promise<void>((resolve) => {
    releasePreview = resolve;
  });
  const completedPreview = new Promise<void>((resolve) => {
    previewCompleted = resolve;
  });
  let held = false;
  await page.route('**/api/jobs/*', async (route) => {
    const response = await route.fetch();
    const job = await response.json();
    if (!held && job.state === 'SUCCEEDED' && job.result?.kind === 'preview') {
      held = true;
      previewCompleted();
      await previewGate;
    }
    await route.fulfill({ response });
  });
  await page.getByRole('button', { name: 'Update', exact: true }).click();
  await completedPreview;
  await files
    .locator('.hierarchy')
    .getByRole('button', { name: /wavelength/ })
    .click();
  releasePreview();
  await expect(page.getByRole('img', { name: /Line plot/ })).toBeVisible();
  await expect(page.getByRole('img', { name: /Heatmap/ })).toHaveCount(0);
  await page.unroute('**/api/jobs/*');
  await files
    .locator('.hierarchy')
    .getByRole('button', { name: /transmission/ })
    .click();
  await expect(page.getByRole('img', { name: /Heatmap/ })).toBeVisible();
  await page.screenshot({ path: 'test-results/marsd-explorer.png', fullPage: true, scale: 'css' });
  await page.getByRole('button', { name: 'Compare', exact: true }).click();
  await page
    .getByRole('combobox', { name: 'Reference B', exact: true })
    .selectOption({ label: 'run_041.h5' });
  await page.getByLabel('Comparison selection', { exact: true }).fill(':,:');
  await page.getByRole('button', { name: 'Run comparison', exact: true }).click();
  await expect(page.locator('.results .badge.different')).toBeVisible();
  await page.screenshot({ path: 'test-results/marsd-compare.png', fullPage: true, scale: 'css' });
  await files.getByRole('button', { name: /run_042\.parquet/ }).click();
  await expect(page.locator('.results tbody tr')).toHaveCount(8);
  await page.getByRole('button', { name: 'Compare', exact: true }).click();
  await page
    .getByRole('combobox', { name: 'Reference B', exact: true })
    .selectOption({ label: 'run_041.parquet' });
  await expect(page.getByRole('button', { name: 'Run comparison', exact: true })).toBeDisabled();
  await page.locator('.key-picker').getByRole('button', { name: 'device_id', exact: true }).click();
  await page
    .locator('.key-picker')
    .getByRole('button', { name: 'wavelength_nm', exact: true })
    .click();
  await page.getByRole('button', { name: 'Run comparison', exact: true }).click();
  await expect(page.locator('.results .result-heading .badge.different')).toBeVisible();

  await page.getByRole('button', { name: 'Validate', exact: true }).click();
  await page.getByRole('button', { name: 'Run validation', exact: true }).click();
  await expect(page.locator('.results .badge.fail').first()).toBeVisible();
  await expect(
    page.locator('tr').filter({ hasText: 'transmission_unit' }).locator('.badge'),
  ).toHaveText('FAIL');
  await page.screenshot({
    path: 'test-results/marsd-validation.png',
    fullPage: true,
    scale: 'css',
  });
  await page.getByRole('tab', { name: /run_042\.h5/ }).click();
  await expect(page.getByRole('combobox', { name: 'Reference B', exact: true })).toHaveValue(
    /run_041\.h5$/,
  );
  await expect(page.locator('.results .badge.different')).toBeVisible();
  await page.getByRole('button', { name: 'Reports', exact: true }).click();
  await expect(page.locator('.report-row')).toHaveCount(5);
  await page.locator('.report-row').first().click();
  await expect(page.getByRole('button', { name: 'Export HTML', exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Export HTML', exact: true }).click();
  await expect(page.getByRole('status').filter({ hasText: 'Saved' })).toBeVisible();
  const project = await page.evaluate(async () =>
    (
      await fetch('/api/project', {
        headers: { Authorization: `Bearer ${sessionStorage.getItem('marsd-session')}` },
      })
    ).json(),
  );
  await page.getByRole('button', { name: 'Workbench', exact: true }).click();
  await files.getByRole('button', { name: /identifiers\.csv/ }).click();
  await expect(page.locator('.results tbody tr')).toHaveCount(3);
  const version = await page.evaluate(
    async () =>
      (
        await (
          await fetch('/api/artifacts', {
            headers: { Authorization: `Bearer ${sessionStorage.getItem('marsd-session')}` },
          })
        ).json()
      ).find((file: { name: string }) => file.name === 'identifiers.csv').version_id,
  );
  await page.getByRole('button', { name: 'Related runs', exact: true }).click();
  await expect(page.locator('.run-history .run-row')).toHaveCount(2);
  await expect(page.locator('.run-detail .section-heading .badge')).toHaveText('FAILED');
  await expect(page.getByText('Possibly partial', { exact: true }).first()).toBeVisible();
  await page.getByRole('button', { name: 'Assess readiness (dry-run)', exact: true }).click();
  await expect(page.locator('.readiness h3')).toHaveText('UNKNOWN');
  await page
    .getByRole('combobox', { name: 'Reference run', exact: true })
    .selectOption({ index: 1 });
  await page.getByRole('button', { name: 'Compare recorded evidence', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Changed evidence', exact: true })).toBeVisible();
  await page.screenshot({ path: 'test-results/marsd-runs.png', fullPage: true, scale: 'css' });
  await page.getByRole('button', { name: 'Explore version', exact: true }).first().click();
  await expect(page.getByRole('tab', { name: /identifiers\.csv/ })).toHaveAttribute(
    'aria-selected',
    'true',
  );
  await expect(page.locator('.results tbody tr')).toHaveCount(3);
  await fs.writeFile(
    path.join(project.path, 'contracts', 'identifiers.yaml'),
    JSON.stringify({
      schema_version: 1,
      name: 'Synthetic identifiers',
      revision: '1',
      rules: [{ id: 'identifier_present', kind: 'required', field: 'sample_id' }],
    }),
  );
  await page.getByRole('button', { name: 'Validate', exact: true }).click();
  await page.getByRole('button', { name: 'Use a local contract file', exact: true }).click();
  await page
    .getByLabel('Local contract path', { exact: true })
    .fill(path.join(project.path, 'contracts', 'identifiers.yaml'));
  await page.getByRole('button', { name: 'Run validation', exact: true }).click();
  await expect(page.locator('.results .result-heading .badge.pass')).toBeVisible();
  await page.getByRole('button', { name: 'Lineage', exact: true }).click();
  await page.getByRole('combobox', { name: 'Direction', exact: true }).selectOption('downstream');
  await page.getByLabel('Depth', { exact: true }).fill('3');
  await page.getByRole('button', { name: 'Load dependencies', exact: true }).click();
  await expect(page.getByRole('img', { name: /Recorded dependencies/ })).toBeVisible();
  await expect(page.getByRole('cell', { name: 'generated', exact: true }).first()).toBeVisible();
  await page
    .getByLabel('Entity or artifact version ID', { exact: true })
    .fill('synthetic:calibration:01');
  await page.getByLabel('Depth', { exact: true }).fill('4');
  await page.getByRole('button', { name: 'Load dependencies', exact: true }).click();
  await expect(page.getByRole('cell', { name: 'references', exact: true })).toBeVisible();
  expect(await page.locator('.dependency-node').count()).toBeLessThanOrEqual(200);
  await page.screenshot({ path: 'test-results/marsd-lineage.png', fullPage: true, scale: 'css' });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({
    path: 'test-results/marsd-lineage-mobile.png',
    fullPage: true,
    scale: 'css',
  });
  expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)).toBe(false);
  await page.getByRole('button', { name: 'Workbench', exact: true }).click();
  await expect(files).toBeHidden();
  await expect(page.getByRole('tab', { selected: true })).toBeInViewport({ ratio: 0.5 });
  await page.getByRole('button', { name: 'Show navigator', exact: true }).click();
  await expect(files).toBeVisible();
  await page.getByRole('button', { name: 'Hide navigator', exact: true }).click();
  await page.getByRole('button', { name: 'Data', exact: true }).click();
  await expect(page.locator('.results tbody tr')).toHaveCount(3);
  await page.screenshot({ path: 'test-results/marsd-mobile.png', fullPage: true, scale: 'css' });
  expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)).toBe(false);
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.getByRole('button', { name: 'Show navigator', exact: true }).click();
  await files.getByRole('button', { name: /run_042\.h5/ }).click();
  await page.getByRole('button', { name: 'Data', exact: true }).click();
  await expect(page.getByRole('img', { name: /Heatmap/ })).toBeVisible();
  await page.getByRole('button', { name: 'Read options', exact: true }).click();
  await page.getByLabel('Dataset path', { exact: true }).fill('/measurement');
  await page.getByRole('button', { name: 'Update', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('UNSUPPORTED_OPERATION');
  await expect(page.getByRole('img', { name: /Heatmap/ })).toHaveCount(0);
  const csv = path.join(project.path, 'data', 'identifiers.csv');
  await fs.appendFile(csv, '0004,3.5,synthetic extra row\n');
  // Register a new observation without refreshing first; the old open version must stay distinct.
  await files.getByRole('button', { name: 'Add file', exact: true }).click();
  await page.getByLabel('Local file path', { exact: true }).fill(csv);
  await page.getByRole('dialog').getByRole('button', { name: 'Add file', exact: true }).click();
  await expect(page.getByRole('tab', { name: /identifiers\.csv/ })).toHaveCount(2);
  await expect(page.locator('.results tbody tr')).toHaveCount(4);
  await page.locator(`[id="file-tab-${version}"]`).click();
  await expect(page.getByText(/This source changed or is unavailable/)).toBeVisible();
  await fs.appendFile(csv, '0005,4.5,another synthetic row\n');

  await files.getByRole('button', { name: 'Refresh files', exact: true }).click();
  await expect(
    files.locator('.file-row').filter({ hasText: 'identifiers.csv' }).locator('.changed-marker'),
  ).toBeVisible();
  await fs.unlink(csv);
  await files.getByRole('button', { name: /identifiers\.csv/ }).click();
  await expect(page.getByText(/This source changed or is unavailable/)).toBeVisible();
  const large = path.join(project.path, 'data', 'cancel.csv');
  await fs.writeFile(large, 'value\n' + '1\n'.repeat(200000));
  await files.getByRole('button', { name: 'Add file', exact: true }).click();
  await page.getByLabel('Local file path', { exact: true }).fill(large);
  await page.getByRole('dialog').getByRole('button', { name: 'Add file', exact: true }).click();
  await expect(page.getByRole('tab', { name: /cancel\.csv/ })).toHaveAttribute(
    'aria-selected',
    'true',
  );
  await expect(page.locator('.results tbody tr')).toHaveCount(64);
  await page.getByRole('button', { name: 'Next rows', exact: true }).click();
  await expect(page.locator('.results tbody tr').first().locator('.row-number')).toHaveText('64');
  await page.getByRole('button', { name: 'Previous rows', exact: true }).click();
  await expect(page.locator('.results tbody tr').first().locator('.row-number')).toHaveText('0');

  await page.getByRole('button', { name: 'Statistics', exact: true }).click();
  await page.getByRole('button', { name: /Cancel job/ }).click();
  await expect(page.getByRole('status').filter({ hasText: 'cancelled' })).toBeVisible();
  await page.getByRole('button', { name: 'Help and keyboard shortcuts', exact: true }).click();
  await expect(page.getByRole('dialog', { name: 'Workbench reference' })).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await page.getByRole('button', { name: 'Close cancel.csv', exact: true }).click();
  await page.getByRole('button', { name: 'Project', exact: true }).click();
  await page.getByRole('button', { name: 'Lock browser session', exact: true }).click();
  await expect(
    page.getByRole('heading', { name: 'Connect to your local workspace', exact: true }),
  ).toBeVisible();
  expect(await page.evaluate(() => sessionStorage.getItem('marsd-session'))).toBeNull();

  expect(errors).toEqual([]);
  expect(externalRequests).toEqual([]);
  expect(version).toBeTruthy();
});
