import assert from 'node:assert/strict';
import { mkdir } from 'node:fs/promises';
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright');

// Run only against an isolated database populated by the existing seed_demo command.
const base = process.env.FRONTEND_BASE_URL || 'http://127.0.0.1:8001';
const output = 'output/playwright/ci';
await mkdir(output, { recursive: true });
for (let attempt = 0; ; attempt++) {
    try {
        assert.equal((await fetch(`${base}/health/`)).status, 200);
        break;
    } catch (error) {
        if (attempt >= 30) throw error;
        await new Promise(resolve => setTimeout(resolve, 500));
    }
}
const browser = await chromium.launch();
const context = await browser.newContext({ reducedMotion: 'reduce' });
const page = await context.newPage();
const errors = [];
page.on('pageerror', error => errors.push(error.message));
page.on('response', response => {
    if (response.url().startsWith(base) && response.status() >= 400) {
        errors.push(`HTTP ${response.status()}: ${new URL(response.url()).pathname}`);
    }
});
async function visit(path) {
    const response = await page.goto(base + path);
    assert.equal(response.status(), 200, path);
    await page.waitForFunction(() => !document.querySelector('[data-compose-url][aria-busy="true"]'));
}
async function layout(label, width) {
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), `${label}: page overflow at ${width}`);
    assert.deepEqual(await page.locator('[aria-describedby]').evaluateAll(elements => elements.flatMap(el =>
        el.getAttribute('aria-describedby').split(/\s+/).filter(id => !document.getElementById(id))
    )), [], `${label}: missing field descriptions`);
    await page.screenshot({ path: `${output}/${label}-${width}.png`, fullPage: true });
}
try {
    for (const width of [320, 390, 768, 1024, 1440]) {
        await page.setViewportSize({ width, height: 900 });
        for (const [label, path] of [['login', '/accounts/login/'], ['register', '/accounts/register/'], ['reset', '/accounts/password-reset/']]) {
            await visit(path);
            await layout(label, width);
        }
        await visit('/accounts/login/');
        await page.locator('.nav-disclosure').evaluate(el => el.open = true);
        await page.getByRole('link', { name: 'Create account', exact: true }).first().click();
        await page.waitForURL(`${base}/accounts/register/`);
        assert.equal(await page.locator('[name=password2]').count(), 1);
    }
    await visit('/accounts/login/');
    await page.locator('[name=username]').fill('demo-mens-1');
    await page.locator('[name=password]').fill('DemoPass123!');
    await page.locator('form').filter({ has: page.locator('[name=password]') }).locator('button[type=submit]').click();
    await page.waitForURL(`${base}/`);
    for (const width of [320, 390, 768, 1024, 1440]) {
        await page.setViewportSize({ width, height: 900 });
        await visit('/');
        await page.locator('#team').scrollIntoViewIfNeeded();
        await page.locator('#team [data-fragment="team"]').waitFor();
        await layout('dashboard', width);
        const card = page.locator('.dashboard-match-board');
        const offset = await card.locator('.match-lineups').evaluate(el => {
            const r = el.getBoundingClientRect(), v = el.querySelector('.versus').getBoundingClientRect();
            return Math.abs(r.x + r.width / 2 - v.x - v.width / 2);
        });
        assert.ok(offset < 2, `VS off centre at ${width}`);
        await card.getByRole('link', { name: 'View match', exact: true }).click();
        await page.locator('.scorecard-page').waitFor();
        await layout('scorecard', width);
        assert.equal(await page.locator('#score-submission form input[name=csrfmiddlewaretoken]').count(), 1);
        for (const path of ['/availability/', '/suggestions/', '/matches/']) {
            await visit(path);
            for (const fragment of ['availability', 'suggestions', 'matches']) {
                await page.locator(`#${fragment}`).scrollIntoViewIfNeeded();
                await page.locator(`[data-fragment="${fragment}"]`).waitFor();
            }
            assert.equal(await page.locator('.availability-form').count(), 1);
            assert.equal(await page.locator('#submit-score').count(), 1);
            await layout(path.split('/')[1], width);
        }
        await page.locator('#submit-score').getByRole('link', { name: 'Enter score', exact: true }).first().click();
        await page.locator('#score-submission').waitFor();
        await visit('/ladders/mens/');
        for (const division of ['mens', 'womens']) {
            if (division === 'womens') {
                await page.getByRole('navigation', { name: 'Ladder division' }).getByRole('link', { name: 'Women’s', exact: true }).click();
                await page.waitForURL(`${base}/ladders/womens/`);
            }
            assert.equal(await page.locator('table').count(), 1);
            await layout(division, width);
        }
        await page.locator('.nav-disclosure').evaluate(el => el.open = true);
        await page.getByRole('navigation', { name: 'Main navigation' }).getByRole('link', { name: 'Play', exact: true }).click();
        await page.locator('.availability-form').waitFor();
    }
    // Real form POST through Django CSRF/session middleware; invalid dates cannot book anything.
    await page.locator('[name=starts_at]').fill('2030-01-02T18:00');
    await page.locator('[name=ends_at]').fill('2030-01-02T17:00');
    await page.getByRole('button', { name: 'Save this time', exact: true }).click();
    await page.getByRole('alert').filter({ hasText: 'Availability start must be before end.' }).waitFor();
    // Progressive enhancement must retain useful fallback links when composition fails.
    await page.route('**/suggestions/', route => route.abort());
    await visit('/availability/');
    await page.locator('#suggestions').scrollIntoViewIfNeeded();
    await page.locator('#suggestions [data-compose-status]').filter({ hasText: 'could not load' }).waitFor();
    assert.equal(await page.locator('#suggestions a[href="/suggestions/"]').count(), 1);
    await page.unroute('**/suggestions/');
    const noJS = await browser.newContext({ javaScriptEnabled: false, storageState: await context.storageState() });
    const fallback = await noJS.newPage();
    await fallback.goto(`${base}/availability/`);
    assert.equal(await fallback.locator('.availability-form input[name=csrfmiddlewaretoken]').count(), 1);
    assert.equal(await fallback.locator('#suggestions a[href="/suggestions/"]').count(), 1);
    await noJS.close();
    // Native keyboard navigation reaches the skip link and content landmark.
    await visit('/');
    await page.keyboard.press('Tab');
    assert.match(await page.locator(':focus').innerText(), /Skip to/);
    await page.keyboard.press('Enter');
    assert.equal(await page.locator(':focus').getAttribute('id'), 'main-content');
    // Exercise a selected participant's real score POST and waiting state.
    await visit('/matches/');
    await page.getByRole('link', { name: 'Enter score', exact: true }).first().click();
    const scorecardURL = page.url();
    const opponentUsername = (await page.locator('.panel').first().innerText()).match(/demo-mens-[3-8]/)?.[0];
    assert.ok(opponentUsername, 'Selected opponent recorded on scorecard');
    for (const [name, value] of Object.entries({ set1_team_a: '6', set1_team_b: '4', set2_team_a: '6', set2_team_b: '3' })) {
        await page.locator(`[name=${name}]`).fill(value);
    }
    await page.getByRole('button', { name: 'Submit score', exact: true }).click();
    await page.getByText('Your team submitted its score. Waiting for the opponent.', { exact: true }).waitFor();
    assert.equal(await page.locator('#score-submission').count(), 0);
    const opponentContext = await browser.newContext();
    const opponentPage = await opponentContext.newPage();
    await opponentPage.goto(`${base}/accounts/login/`);
    await opponentPage.locator('[name=username]').fill(opponentUsername);
    await opponentPage.locator('[name=password]').fill('DemoPass123!');
    await opponentPage.locator('form').filter({ has: opponentPage.locator('[name=password]') }).locator('button[type=submit]').click();
    await opponentPage.waitForURL(`${base}/`);
    await opponentPage.goto(scorecardURL);
    await opponentPage.getByText('Opponent score received. Your team needs to submit its score.', { exact: true }).waitFor();
    for (const [name, value] of Object.entries({ set1_team_a: '6', set1_team_b: '4', set2_team_a: '6', set2_team_b: '3' })) {
        await opponentPage.locator(`[name=${name}]`).fill(value);
    }
    await opponentPage.getByRole('button', { name: 'Submit score', exact: true }).click();
    await opponentPage.getByRole('heading', { name: 'Official score', exact: true }).waitFor();
    await opponentContext.close();
    assert.deepEqual(errors, [], 'Browser or HTTP errors');
    console.log('Frontend checks passed: five viewport sizes, public/private routes, composition, links, CSRF and invalid form submission.');
} catch (error) {
    await page.screenshot({ path: `${output}/failure.png`, fullPage: true }).catch(() => {});
    throw error;
} finally {
    await browser.close();
}
