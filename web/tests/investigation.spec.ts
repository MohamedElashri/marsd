import { test, expect } from '@playwright/test';
import fs from 'node:fs/promises';
import path from 'node:path';

test('real Explore, Compare, Validate and report workflow', async ({ page }) => {
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
  await page.getByRole('button', { name: 'Open workspace' }).click();
  await expect(page.getByText('Synthetic spectral study', { exact: true })).toBeVisible();
  const expectedArtifacts = Number(process.env.MARSD_E2E_ARTIFACTS || 7);
  await expect(page.locator('.artifact-list .artifact')).toHaveCount(expectedArtifacts);
  const startupSamples = [(performance.now() - startup) / 1000];
  if (expectedArtifacts === 100) {
    for (let i = 0; i < 3; i++) {
      const start = performance.now();
      await page.reload();
      await expect(page.locator('.artifact-list .artifact')).toHaveCount(100);
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
  await page.getByRole('button', { name: /run_042\.h5/ }).click();
  await expect(page.getByText('Structure loaded. Choose what to read.')).toBeVisible();
  await expect(page.getByLabel('Dataset path', { exact: true })).toHaveValue(
    '/measurement/transmission',
  );
  await page.getByRole('button', { name: 'Preview selection', exact: true }).click();
  await expect(page.getByRole('img', { name: /Heatmap/ })).toBeVisible();
  await expect(page.getByText('Requested scope complete')).toBeVisible();
  await page.screenshot({ path: 'test-results/marsd-explorer.png', fullPage: true });
  await page.getByRole('button', { name: 'Compare', exact: true }).click();
  await page
    .getByRole('combobox', { name: 'Reference B', exact: true })
    .selectOption({ label: 'run_041.h5' });
  await page.getByLabel('Selection', { exact: true }).fill(':,:');
  await page.getByRole('button', { name: 'Compare A with B' }).click();
  await expect(page.locator('.badge.different')).toBeVisible();
  await expect(page.getByText('1', { exact: true }).first()).toBeVisible();
  await page.getByRole('button', { name: /run_042\.parquet/ }).click();
  await expect(page.getByRole('status').filter({ hasText: 'inspect · succeeded' })).toBeVisible();
  await page.getByRole('button', { name: 'Validate', exact: true }).click();
  await page.getByRole('button', { name: 'Validate declared rules' }).click();
  await expect(page.locator('.result-heading .badge.fail')).toBeVisible();
  await expect(page.getByText('calibration', { exact: true })).toBeVisible();
  await expect(
    page.locator('tr').filter({ hasText: 'transmission_unit' }).locator('.badge'),
  ).toHaveText('FAIL');
  await page.getByRole('button', { name: 'Reports', exact: true }).click();
  await expect(page.locator('.report-row')).toHaveCount(2);
  await page.getByRole('button', { name: 'Export HTML', exact: true }).click();
  await expect(page.getByRole('status').filter({ hasText: 'Saved' })).toBeVisible();
  await page.getByRole('button', { name: 'Help', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Investigate with evidence.' })).toBeVisible();
  const project = await page.evaluate(async () => {
    const response = await fetch('/api/project', {
      headers: { Authorization: `Bearer ${sessionStorage.getItem('marsd-session')}` },
    });
    return response.json();
  });
  expect(project.name).toBe('Synthetic spectral study');
  await page.getByRole('button', { name: 'Explore', exact: true }).click();
  await page.getByRole('button', { name: /identifiers\.csv/ }).click();
  await expect(page.getByText('Structure loaded. Choose what to read.')).toBeVisible();
  const inputVersion = await page.evaluate(async () => {
    const response = await fetch('/api/artifacts', {
      headers: { Authorization: `Bearer ${sessionStorage.getItem('marsd-session')}` },
    });
    return (await response.json()).find(
      (artifact: { name: string }) => artifact.name === 'identifiers.csv',
    ).version_id;
  });
  await page.getByRole('button', { name: 'Runs', exact: true }).click();
  await expect(page.locator('.run-history .run-row')).toHaveCount(2);
  await expect(page.locator('.run-detail h2')).toHaveText('FAILED');
  await expect(page.getByText('Possibly partial', { exact: true }).first()).toBeVisible();
  await page.getByRole('button', { name: 'Assess readiness (dry-run)', exact: true }).click();
  await expect(page.locator('.readiness h3')).toHaveText('UNKNOWN');
  await expect(page.getByText('No target code was executed.', { exact: true })).toBeVisible();
  await page
    .getByRole('combobox', { name: 'Reference run', exact: true })
    .selectOption({ index: 1 });
  await page.getByRole('button', { name: 'Compare recorded evidence', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Changed evidence', exact: true })).toBeVisible();
  await page.screenshot({ path: 'test-results/marsd-runs.png', fullPage: true });
  await page.getByRole('button', { name: 'Explore version', exact: true }).first().click();
  await expect(page.locator('.version-label span')).toHaveText(
    `Version ${inputVersion.slice(0, 8)}`,
  );
  await expect(page.getByText('Structure loaded. Choose what to read.')).toBeVisible();
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
  await page
    .getByLabel('Or approved local contract path', { exact: true })
    .fill(path.join(project.path, 'contracts', 'identifiers.yaml'));
  await page.getByRole('button', { name: 'Validate declared rules', exact: true }).click();
  await expect(page.locator('.result-heading .badge.pass')).toBeVisible();
  await expect(page.locator('.version-label span')).toHaveText(
    `Version ${inputVersion.slice(0, 8)}`,
  );
  await page.getByRole('button', { name: 'Runs', exact: true }).click();
  await expect(page.locator('.run-history .run-row')).toHaveCount(2);
  await page.getByRole('button', { name: 'Version lineage', exact: true }).first().click();
  await expect(page.getByLabel('Entity or artifact version ID', { exact: true })).toHaveValue(
    inputVersion,
  );
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
  await page.screenshot({ path: 'test-results/marsd-lineage.png', fullPage: true });
  await page.getByRole('button', { name: 'Explore version', exact: true }).first().click();
  await expect(page.locator('.version-label span')).toHaveText(
    `Version ${inputVersion.slice(0, 8)}`,
  );
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole('button', { name: 'Runs', exact: true }).click();
  await expect(page.locator('.run-detail h2')).toHaveText('FAILED');
  await page.screenshot({ path: 'test-results/marsd-runs-mobile.png', fullPage: true });
  expect(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth)).toBe(
    false,
  );
  await page.getByRole('button', { name: 'Lineage', exact: true }).click();
  await page.getByRole('button', { name: 'Load dependencies', exact: true }).click();
  await expect(page.getByRole('img', { name: /Recorded dependencies/ })).toBeVisible();
  await page.screenshot({ path: 'test-results/marsd-lineage-mobile.png', fullPage: true });
  expect(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth)).toBe(
    false,
  );
  await page.setViewportSize({ width: 1280, height: 900 });
  // Deliver a real metadata completion after the user changes the selected artifact.
  let delayMetadataOnce = true;
  await page.route('**/api/jobs/*', async (route) => {
    if (route.request().method() === 'GET' && delayMetadataOnce) {
      delayMetadataOnce = false;
      await new Promise((resolve) => setTimeout(resolve, 600));
    }
    await route.continue();
  });
  // Negative states use this server's entirely synthetic, disposable project only.
  await page.getByRole('button', { name: 'Explore', exact: true }).click();
  await page.getByRole('button', { name: 'Refresh structure', exact: true }).click();
  await expect(
    page.getByRole('status').filter({ hasText: /inspect · (queued|running)/ }),
  ).toBeVisible();
  await page.getByRole('button', { name: /run_042\.h5/ }).click();
  await expect(page.getByText('Structure loaded. Choose what to read.')).toBeVisible();
  await page.unroute('**/api/jobs/*');
  await page.getByLabel('Dataset path', { exact: true }).fill('/measurement');
  await page.getByRole('button', { name: 'Preview selection', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('UNSUPPORTED_OPERATION');
  await expect(page.getByRole('img', { name: /Heatmap/ })).toHaveCount(0);

  const csv = path.join(project.path, 'data', 'identifiers.csv');
  await fs.appendFile(csv, '0004,3.5,synthetic extra row\n');
  await page.getByRole('button', { name: 'Refresh artifacts', exact: true }).click();
  await expect(page.getByRole('button', { name: /identifiers\.csv/ })).toContainText(
    'Changed or unavailable',
  );
  await fs.unlink(csv);
  await page.getByRole('button', { name: /identifiers\.csv/ }).click();
  await expect(page.getByRole('alert')).toContainText('VERSION_UNAVAILABLE');
  const large = path.join(project.path, 'data', 'cancel.csv');
  await fs.writeFile(large, 'value\n' + '1\n'.repeat(200000));
  await page.getByText('Register a file', { exact: true }).click();
  await page.getByLabel('Local path', { exact: true }).fill(large);
  await page.getByRole('button', { name: 'Register reference', exact: true }).click();
  await page.getByRole('button', { name: /cancel\.csv/ }).click();
  await expect(page.getByText('Structure loaded. Choose what to read.')).toBeVisible();
  await page.getByRole('button', { name: 'Profile within budget', exact: true }).click();
  await page.getByRole('button', { name: 'Cancel job', exact: true }).click();
  await expect(page.getByRole('status').filter({ hasText: 'cancelled' })).toBeVisible();
  expect(errors).toEqual([]);
  expect(externalRequests).toEqual([]);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole('button', { name: 'Explore', exact: true }).click();
  await page.screenshot({ path: 'test-results/marsd-mobile.png', fullPage: true });
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth > window.innerWidth,
  );
  expect(overflow).toBe(false);
});
