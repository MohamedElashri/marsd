import { test, expect } from '@playwright/test';

test('native launcher opens with one folder choice, reports errors and closes cleanly', async ({
  page,
}) => {
  await page.addInitScript(() => {
    const state = window as unknown as {
      isTauri: boolean;
      __TAURI_INTERNALS__: unknown;
      calls: { command: string; args: Record<string, unknown> }[];
      picks: (string | null)[];
      fail: boolean;
    };
    state.isTauri = true;
    state.calls = [];
    state.picks = [null, '/tmp/synthetic study', '/tmp/synthetic study'];
    state.fail = true;
    state.__TAURI_INTERNALS__ = {
      invoke: async (command: string, args: Record<string, unknown> = {}) => {
        state.calls.push({ command, args });
        if (command === 'desktop_status') return { running: false };
        if (command === 'pick_path') return state.picks.shift();
        if (command === 'open_project') {
          if (state.fail) {
            state.fail = false;
            throw 'PROJECT_BUSY: Close the existing project writer.';
          }
          return;
        }
        if (command === 'close_project') return true;
        if (command === 'backend_request')
          return {
            status: 200,
            data:
              args.path === 'project'
                ? {
                    name: 'Native fixture',
                    path: '/tmp/synthetic study',
                    source_roots: ['/tmp/synthetic study'],
                    outputs: '/tmp/synthetic study/.marsd/reports',
                  }
                : [],
          };
        throw new Error(`Unexpected command ${command}`);
      },
    };
  });
  await page.goto('/');
  await expect(
    page.getByRole('heading', { name: 'Open a local project', exact: true }),
  ).toBeVisible();
  await page.screenshot({ path: 'test-results/marsd-launcher.png', fullPage: true, scale: 'css' });
  await page.getByRole('button', { name: 'Open project…', exact: true }).click();
  await expect(
    page.getByRole('heading', { name: 'Open a local project', exact: true }),
  ).toBeVisible();
  await page.getByRole('button', { name: 'Open project…', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('PROJECT_BUSY');
  await page.getByRole('button', { name: 'Open project…', exact: true }).click();
  await expect(page.getByText('Native fixture', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Project', exact: true }).click();
  await page.getByRole('button', { name: 'Close project', exact: true }).click();
  await expect(
    page.getByRole('heading', { name: 'Open a local project', exact: true }),
  ).toBeVisible();
  const calls = await page.evaluate(
    () =>
      (window as unknown as { calls: { command: string; args: Record<string, unknown> }[] }).calls,
  );
  expect(calls.filter((call) => call.command === 'open_project')).toEqual([
    {
      command: 'open_project',
      args: { project: '/tmp/synthetic study', create: false, roots: [] },
    },
    {
      command: 'open_project',
      args: { project: '/tmp/synthetic study', create: false, roots: [] },
    },
  ]);
  expect(calls.some((call) => call.command === 'close_project')).toBe(true);
});

test('native project creation approves only the selected source folders', async ({ page }) => {
  await page.addInitScript(() => {
    const state = window as unknown as {
      isTauri: boolean;
      __TAURI_INTERNALS__: unknown;
      calls: { command: string; args: Record<string, unknown> }[];
      picks: string[];
    };
    state.isTauri = true;
    state.calls = [];
    state.picks = ['/tmp/workspace مرصد', '/tmp/source Δ', '/tmp/source Δ'];
    state.__TAURI_INTERNALS__ = {
      invoke: async (command: string, args: Record<string, unknown> = {}) => {
        state.calls.push({ command, args });
        if (command === 'desktop_status') return { running: false };
        if (command === 'pick_path') return state.picks.shift();
        if (command === 'open_project') return;
        if (command === 'backend_request')
          return {
            status: 200,
            data:
              args.path === 'project'
                ? {
                    name: 'Created fixture',
                    path: '/tmp/workspace مرصد',
                    source_roots: ['/tmp/workspace مرصد', '/tmp/source Δ'],
                    outputs: '/tmp/workspace مرصد/.marsd/reports',
                  }
                : [],
          };
        throw new Error(`Unexpected command ${command}`);
      },
    };
  });
  await page.goto('/');
  await page.getByRole('button', { name: 'Create project…', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Create project', exact: true })).toBeDisabled();
  await page.getByRole('button', { name: 'Choose folder', exact: true }).click();
  await page.locator('.native-roots summary').click();
  await page.getByRole('button', { name: 'Add source folder', exact: true }).click();
  await page.getByRole('button', { name: 'Add source folder', exact: true }).click();
  await expect(page.locator('.native-roots li')).toHaveCount(1);
  await page.getByRole('button', { name: 'Create project', exact: true }).click();
  await expect(page.getByText('Created fixture', { exact: true })).toBeVisible();
  const calls = await page.evaluate(
    () =>
      (window as unknown as { calls: { command: string; args: Record<string, unknown> }[] }).calls,
  );
  expect(calls.filter((call) => call.command === 'open_project')).toEqual([
    {
      command: 'open_project',
      args: {
        project: '/tmp/workspace مرصد',
        create: true,
        roots: ['/tmp/workspace مرصد', '/tmp/source Δ'],
      },
    },
  ]);
});
