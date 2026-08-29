import { test, expect } from '@playwright/test';
import { createServer } from 'node:http';
import { existsSync, readFileSync } from 'node:fs';
import { readFile, stat } from 'node:fs/promises';
import { extname, join, normalize, resolve } from 'node:path';
import pixelmatch from 'pixelmatch';
import { PNG } from 'pngjs';
import { fixtureFor, LONG_LABEL, routeContracts } from './fixtures.mjs';

const currentRoot = resolve(process.cwd());
const baselineRoot = resolve(currentRoot, '.visual-baseline');
const currentOrigin = 'http://127.0.0.1:4173';
const baselineOrigin = 'http://127.0.0.1:4174';
const defaultVisualChangeRatio = 0.005;
const maximumApprovedChangeRatio = 0.15;
const visualBaselineSha = process.env.VISUAL_BASELINE_SHA || '';
const visualChangePolicy = JSON.parse(readFileSync(
  join(currentRoot, 'visual-tests', 'approved-visual-changes.json'),
  'utf8'
));

const viewports = [
  { name: 'desktop', width: 1440, height: 1000 },
  { name: 'mobile', width: 390, height: 844 }
];

const mimeTypes = {
  '.css': 'text/css; charset=utf-8',
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.png': 'image/png',
  '.svg': 'image/svg+xml'
};

function createStaticServer(repositoryRoot, port) {
  const webRoot = resolve(repositoryRoot, 'webapp');
  const server = createServer(async (request, response) => {
    try {
      const requestUrl = new URL(request.url || '/', `http://${request.headers.host}`);
      const rawPath = decodeURIComponent(requestUrl.pathname);
      const relativePath = rawPath.startsWith('/static/')
        ? rawPath.slice('/static/'.length)
        : rawPath.replace(/^\/+/, '');
      const candidate = resolve(webRoot, normalize(relativePath));
      let filePath = candidate.startsWith(`${webRoot}/`) ? candidate : join(webRoot, 'index.html');
      const fileStat = await stat(filePath).catch(() => null);
      if (!fileStat?.isFile()) filePath = join(webRoot, 'index.html');
      const body = await readFile(filePath);
      response.writeHead(200, {
        'Cache-Control': 'no-store',
        'Content-Type': mimeTypes[extname(filePath)] || 'application/octet-stream'
      });
      response.end(body);
    } catch (error) {
      response.writeHead(500, { 'Content-Type': 'text/plain; charset=utf-8' });
      response.end(`Visual fixture server error: ${error.message}`);
    }
  });
  return new Promise((resolveServer, reject) => {
    server.once('error', reject);
    server.listen(port, '127.0.0.1', () => resolveServer(server));
  });
}

function primaryEndpoint(tab, pathname) {
  const matchers = {
    home: ['/api/meta/connections', '/api/health/overview', '/api/audit-events'],
    fb_accounts: ['/api/meta/connections', '/api/accounts'],
    accounts: ['/api/accounts'],
    rules: ['/api/presets', '/api/rule-groups'],
    summary: ['/api/summary'],
    logs: ['/api/audit-events'],
    settings: ['/api/settings']
  };
  return (matchers[tab] || []).some((prefix) => pathname.startsWith(prefix));
}

function shouldFail(tab, scenario, pathname) {
  if (scenario === 'partial') {
    if (tab === 'home') return pathname === '/api/health/overview';
    if (tab === 'fb_accounts') return pathname === '/api/meta/connections';
    return false;
  }
  if (scenario !== 'error') return false;
  return primaryEndpoint(tab, pathname);
}

function validateVisualChangePolicy() {
  if (visualChangePolicy.version !== 1 || !Array.isArray(visualChangePolicy.changes)) {
    throw new Error('Visual change policy must use version 1 and a changes array');
  }
  const allowedRoutes = new Set(routeContracts.map((contract) => contract.tab));
  const allowedViewports = new Set(viewports.map((viewport) => viewport.name));
  const keys = new Set();
  for (const approval of visualChangePolicy.changes) {
    const key = `${approval.baselineSha}/${approval.route}/${approval.viewport}`;
    if (
      !/^[0-9a-f]{40}$/.test(String(approval.baselineSha || ''))
      || !allowedRoutes.has(approval.route)
      || !allowedViewports.has(approval.viewport)
      || !/^BL-\d+$/.test(String(approval.clickupId || ''))
      || String(approval.rationale || '').trim().length < 20
      || !Number.isFinite(approval.maxChangedRatio)
      || approval.maxChangedRatio <= defaultVisualChangeRatio
      || approval.maxChangedRatio > maximumApprovedChangeRatio
      || keys.has(key)
    ) {
      throw new Error(`Invalid visual change approval: ${key}`);
    }
    keys.add(key);
  }
}

function approvedVisualChangeRatio(tab, viewport) {
  const approval = (visualChangePolicy.changes || []).find((item) => (
    item.baselineSha === visualBaselineSha
    && item.route === tab
    && item.viewport === viewport
  ));
  if (!approval) return defaultVisualChangeRatio;
  return approval.maxChangedRatio;
}

function capturePageErrors(page) {
  const errors = [];
  page.on('pageerror', (error) => errors.push(error.message));
  return errors;
}

async function installDeterministicEnvironment(page, tab, scenario) {
  await page.addInitScript(() => {
    const fixedTime = new Date('2026-08-29T12:00:00Z').valueOf();
    const NativeDate = Date;
    class FixedDate extends NativeDate {
      constructor(...args) {
        super(...(args.length ? args : [fixedTime]));
      }
      static now() { return fixedTime; }
    }
    Object.setPrototypeOf(FixedDate, NativeDate);
    window.Date = FixedDate;
  });

  await page.route('https://telegram.org/**', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'text/javascript',
      body: 'window.Telegram={WebApp:{initData:"",ready(){},expand(){},HapticFeedback:{impactOccurred(){},notificationOccurred(){},selectionChanged(){}}}};'
    });
  });

  await page.route('https://fonts.googleapis.com/**', async (route) => {
    await route.fulfill({ status: 200, contentType: 'text/css', body: '' });
  });
  await page.route('https://fonts.gstatic.com/**', async (route) => route.abort());

  await page.route('**/api/**', async (route) => {
    const url = new URL(route.request().url());
    const pathname = url.pathname;
    if (scenario === 'loading' && primaryEndpoint(tab, pathname)) {
      await new Promise((resolveDelay) => setTimeout(resolveDelay, 2_000));
    }
    if (shouldFail(tab, scenario, pathname)) {
      await route.fulfill({
        status: 503,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'Visual QA simulated upstream failure' })
      });
      return;
    }
    const fixtureScenario = ['empty', 'long', 'partial'].includes(scenario) ? scenario : 'populated';
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(fixtureFor(pathname, fixtureScenario))
    });
  });
}

async function openRoute(page, origin, contract, scenario, options = {}) {
  await installDeterministicEnvironment(page, contract.tab, scenario);
  const qaSearch = options.includeQaState === false ? '' : `?qa-state=${scenario}`;
  await page.goto(`${origin}${contract.path}${qaSearch}`, { waitUntil: 'domcontentloaded' });
  await expect(page.locator('#app')).toBeVisible();
  const section = page.locator(`#tab-${contract.tab}`);
  await expect(section).toHaveClass(/\bactive\b/);
  if (scenario !== 'loading') {
    const busy = await section.getAttribute('aria-busy');
    if (busy !== null) await expect(section).toHaveAttribute('aria-busy', 'false');
  }
  return section;
}

async function assertStateEvidence(page, contract, scenario) {
  const section = page.locator(`#tab-${contract.tab}`);
  if (scenario === 'loading') {
    await expect(section).toHaveAttribute('aria-busy', 'true');
    return;
  }
  if (scenario === 'error') {
    await expect(section.locator(':scope > .ui-route-status.is-error')).toBeVisible();
    return;
  }
  if (scenario === 'partial') {
    const selectors = {
      home: '#todayWorkspaceStatus[data-state="partial"]',
      fb_accounts: ':scope > .ui-route-status.is-warning',
      summary: '#summaryQualityBanner:not(.hidden)'
    };
    await expect(section.locator(selectors[contract.tab])).toBeVisible();
    return;
  }
  if (scenario === 'empty') {
    const selectors = {
      fb_accounts: '#fbAccountsEmptyState:not(.hidden)',
      accounts: '#accountsEmptyState:not(.hidden)',
      rules: '#rulesEmptyState:not(.hidden)',
      summary: '#summaryMobileCards .empty-state',
      logs: '#logsEmptyState:not(.hidden)'
    };
    await expect(section.locator(selectors[contract.tab])).toHaveCount(1);
    return;
  }
  if (scenario === 'long') {
    await expect(section).toContainText(LONG_LABEL.slice(0, 48));
  }
}

async function assertQualityContract(page, contract) {
  const result = await page.evaluate((tab) => {
    const root = document.documentElement;
    const section = document.getElementById(`tab-${tab}`);
    const duplicateIds = [...document.querySelectorAll('[id]')]
      .map((element) => element.id)
      .filter((id, index, ids) => ids.indexOf(id) !== index);
    const brokenImages = [...document.images]
      .filter((image) => image.offsetParent !== null && image.complete && image.naturalWidth === 0)
      .map((image) => image.currentSrc || image.src || image.alt);
    const visibleControls = [...section.querySelectorAll('button, a[href], input, select, textarea, [role="button"]')]
      .filter((element) => {
        const style = getComputedStyle(element);
        const rect = element.getBoundingClientRect();
        return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
      });
    const missingNames = visibleControls.filter((element) => {
      const label = element.labels?.[0]?.textContent || '';
      return ![
        element.getAttribute('aria-label'),
        element.getAttribute('aria-labelledby'),
        element.textContent,
        element.getAttribute('title'),
        element.getAttribute('placeholder'),
        label
      ].some((value) => String(value || '').trim());
    }).map((element) => `${element.tagName.toLowerCase()}#${element.id}.${element.className}`);
    return {
      duplicateIds: [...new Set(duplicateIds)],
      brokenImages,
      missingNames,
      mainCount: document.querySelectorAll('main').length,
      visibleHeadings: [...section.querySelectorAll('h1')].filter((heading) => heading.offsetParent !== null).length,
      overflow: root.scrollWidth - root.clientWidth
    };
  }, contract.tab);

  expect(result.duplicateIds, 'IDs must remain unique').toEqual([]);
  expect(result.brokenImages, 'Visible images must load').toEqual([]);
  expect(result.missingNames, 'Visible controls need accessible names').toEqual([]);
  expect(result.mainCount, 'The app needs one main landmark').toBe(1);
  expect(result.visibleHeadings, 'The active route needs a visible h1').toBeGreaterThanOrEqual(1);
  expect(result.overflow, 'Document-level horizontal overflow is forbidden').toBeLessThanOrEqual(1);
}

async function waitForPopulatedRoute(page, contract) {
  const evidence = {
    home: { selector: '#todayWorkspaceStatus[data-state="healthy"]', text: 'под контролем' },
    fb_accounts: { selector: '#fbAccountsTableBody tr', text: 'Meta Operations Profile' },
    accounts: { selector: '#accountsList .attio-row', text: 'Nordic Growth' },
    rules: { selector: '#ruleGroupsContainer .rule-card', text: 'Stop high CPL' },
    summary: { selector: '#summaryTableBody tr', text: 'Nordic Growth' },
    logs: { selector: '#logsTableBody tr', text: 'Nordic Growth' },
    settings: { selector: '#settingsDisplayName', text: 'Visual QA' }
  };
  const target = page.locator(`#tab-${contract.tab} ${evidence[contract.tab].selector}`).first();
  await expect(target).toBeVisible();
  await expect(target).toContainText(evidence[contract.tab].text);
}

async function compareScreenshots(currentPage, baselinePage, contract, viewport, testInfo) {
  const currentErrors = capturePageErrors(currentPage);
  const baselineErrors = capturePageErrors(baselinePage);
  await Promise.all([
    openRoute(currentPage, currentOrigin, contract, 'populated', { includeQaState: false }),
    openRoute(baselinePage, baselineOrigin, contract, 'populated', { includeQaState: false })
  ]);
  await Promise.all([
    waitForPopulatedRoute(currentPage, contract),
    waitForPopulatedRoute(baselinePage, contract)
  ]);
  expect(currentErrors, 'Current route emitted uncaught browser errors').toEqual([]);
  expect(baselineErrors, 'Baseline route emitted uncaught browser errors').toEqual([]);
  await Promise.all([
    currentPage.addStyleTag({ content: '*,*::before,*::after{animation:none!important;transition:none!important;caret-color:transparent!important}' }),
    baselinePage.addStyleTag({ content: '*,*::before,*::after{animation:none!important;transition:none!important;caret-color:transparent!important}' })
  ]);

  const [currentBuffer, baselineBuffer] = await Promise.all([
    currentPage.locator(`#tab-${contract.tab}`).screenshot(),
    baselinePage.locator(`#tab-${contract.tab}`).screenshot()
  ]);
  const currentImage = PNG.sync.read(currentBuffer);
  const baselineImage = PNG.sync.read(baselineBuffer);
  if (currentImage.width !== baselineImage.width || currentImage.height !== baselineImage.height) {
    await testInfo.attach(`${contract.tab}-${viewport.name}-current`, { body: currentBuffer, contentType: 'image/png' });
    await testInfo.attach(`${contract.tab}-${viewport.name}-baseline`, { body: baselineBuffer, contentType: 'image/png' });
  }
  expect(
    { width: currentImage.width, height: currentImage.height },
    'The route geometry changed; review the attached screenshots'
  ).toEqual({ width: baselineImage.width, height: baselineImage.height });

  const diffImage = new PNG({ width: currentImage.width, height: currentImage.height });
  const changedPixels = pixelmatch(
    currentImage.data,
    baselineImage.data,
    diffImage.data,
    currentImage.width,
    currentImage.height,
    { threshold: 0.12, includeAA: false }
  );
  const ratio = changedPixels / (currentImage.width * currentImage.height);
  const approvedRatio = approvedVisualChangeRatio(contract.tab, viewport.name);
  if (ratio > defaultVisualChangeRatio) {
    await testInfo.attach(`${contract.tab}-${viewport.name}-current`, { body: currentBuffer, contentType: 'image/png' });
    await testInfo.attach(`${contract.tab}-${viewport.name}-baseline`, { body: baselineBuffer, contentType: 'image/png' });
    await testInfo.attach(`${contract.tab}-${viewport.name}-diff`, { body: PNG.sync.write(diffImage), contentType: 'image/png' });
  }
  expect(
    ratio,
    `Unexpected visual change exceeds the reviewed ${(approvedRatio * 100).toFixed(1)}% budget; inspect attached current/baseline/diff images`
  ).toBeLessThanOrEqual(approvedRatio);
}

async function attachFallbackScreenshot(page, name, testInfo) {
  if (page.isClosed()) return;
  const body = await page.screenshot({ fullPage: true });
  await testInfo.attach(name, { body, contentType: 'image/png' });
}

let currentServer;
let baselineServer;

test.beforeAll(async () => {
  validateVisualChangePolicy();
  currentServer = await createStaticServer(currentRoot, 4173);
  if (existsSync(join(baselineRoot, 'webapp', 'index.html'))) {
    baselineServer = await createStaticServer(baselineRoot, 4174);
  }
});

test.afterAll(async () => {
  await Promise.all([
    currentServer ? new Promise((resolveClose) => currentServer.close(resolveClose)) : Promise.resolve(),
    baselineServer ? new Promise((resolveClose) => baselineServer.close(resolveClose)) : Promise.resolve()
  ]);
});

for (const viewport of viewports) {
  for (const contract of routeContracts) {
    for (const scenario of contract.states) {
      test(`${viewport.name} · ${contract.tab} · ${scenario}`, async ({ page }) => {
        await page.setViewportSize(viewport);
        const pageErrors = capturePageErrors(page);
        await openRoute(page, currentOrigin, contract, scenario);
        await assertStateEvidence(page, contract, scenario);
        await assertQualityContract(page, contract);
        expect(pageErrors, 'Route emitted uncaught browser errors').toEqual([]);
      });
    }
  }
}

for (const viewport of viewports) {
  test(`keyboard navigation exposes focus · ${viewport.name}`, async ({ page }) => {
    await page.setViewportSize(viewport);
    const contract = routeContracts[0];
    await openRoute(page, currentOrigin, contract, 'populated');
    const target = viewport.name === 'mobile'
      ? page.locator('.mobile-nav-item[data-tab="home"]')
      : page.locator('#navDashboard');
    await target.focus();
    await expect(target).toBeFocused();
    const focusStyle = await target.evaluate((element) => {
      const style = getComputedStyle(element);
      return { outline: style.outlineStyle, boxShadow: style.boxShadow };
    });
    expect(focusStyle.outline !== 'none' || focusStyle.boxShadow !== 'none').toBeTruthy();
    await page.keyboard.press('Enter');
    await expect(page.locator('#tab-home')).toHaveClass(/\bactive\b/);
  });
}

for (const viewport of viewports) {
  for (const contract of routeContracts) {
    test(`visual regression · ${viewport.name} · ${contract.tab}`, async ({ page, browser }, testInfo) => {
      test.skip(!baselineServer, 'Baseline checkout is available in pull-request CI');
      await page.setViewportSize(viewport);
      const baselineContext = await browser.newContext({ viewport, locale: 'ru-RU', colorScheme: 'light', reducedMotion: 'reduce' });
      const baselinePage = await baselineContext.newPage();
      try {
        await compareScreenshots(
          page,
          baselinePage,
          contract,
          viewport,
          testInfo
        );
      } catch (error) {
        await Promise.allSettled([
          attachFallbackScreenshot(page, `${contract.tab}-${viewport.name}-current-failure`, testInfo),
          attachFallbackScreenshot(baselinePage, `${contract.tab}-${viewport.name}-baseline-failure`, testInfo)
        ]);
        throw error;
      } finally {
        await baselineContext.close();
      }
    });
  }
}
