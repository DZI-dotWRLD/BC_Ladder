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
const browser = await chromium.launch(process.env.PLAYWRIGHT_CHANNEL ? { channel: process.env.PLAYWRIGHT_CHANNEL } : {});
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
}
async function layout(label, width) {
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), `${label}: page overflow at ${width}`);
    assert.deepEqual(await page.locator('[aria-describedby]').evaluateAll(elements => elements.flatMap(el =>
        el.getAttribute('aria-describedby').split(/\s+/).filter(id => !document.getElementById(id))
    )), [], `${label}: missing field descriptions`);
    assert.equal(await page.locator('h1').count(), 1, `${label}: exactly one h1 at ${width}`);
    await page.screenshot({ path: `${output}/${label}-${width}.png`, fullPage: true });
}
async function playTab(name, path) {
    await page.getByRole('navigation', { name: 'Play sections' }).getByRole('link', { name, exact: true }).click();
    await page.waitForURL(`${base}${path}`);
    assert.equal(await page.getByRole('navigation', { name: 'Play sections' }).locator('[aria-current="page"]').innerText(), name);
}
try {
    for (const width of [320, 390, 768, 1024, 1440]) {
        await page.setViewportSize({ width, height: 900 });
        for (const [label, path] of [['login', '/accounts/login/'], ['register', '/accounts/register/'], ['reset', '/accounts/password-reset/']]) {
            await visit(path);
            await layout(label, width);
        }
        await visit('/accounts/login/');
        assert.equal(await page.locator('header').getByRole('link', { name: /Create/ }).count(), 0, 'No duplicate sign-up button in the header');
        await page.getByRole('link', { name: 'Create an account', exact: true }).click();
        await page.waitForURL(`${base}/accounts/register/`);
        assert.equal(await page.locator('[name=password2]').count(), 1);
    }
    // Field guidance stays hidden until the field is focused, but is always connected for assistive tech.
    await visit('/accounts/register/');
    const hint = page.locator('#id_username_helptext');
    await page.locator('[name=email]').focus();
    assert.equal(await hint.isVisible(), false);
    await page.locator('[name=username]').focus();
    await hint.waitFor({ state: 'visible' });

    await visit('/accounts/login/');
    await page.locator('[name=username]').fill('demo-mens-1');
    await page.locator('[name=password]').fill('DemoPass123!');
    await page.locator('form').filter({ has: page.locator('[name=password]') }).locator('button[type=submit]').click();
    await page.waitForURL(`${base}/`);
    for (const width of [320, 390, 768, 1024, 1440]) {
        await page.setViewportSize({ width, height: 900 });
        await visit('/');
        await layout('home', width);
        assert.equal(await page.getByText('Start here').count(), 0, 'Home has no setup checklist');
        const board = page.locator('.match-card .versus-board');
        const offset = await board.evaluate(el => {
            const r = el.getBoundingClientRect(), v = el.querySelector('.versus').getBoundingClientRect();
            return Math.abs(r.x + r.width / 2 - v.x - v.width / 2);
        });
        assert.ok(offset < 2, `VS off centre at ${width}`);
        await page.locator('.match-card').getByRole('link', { name: 'View match' }).click();
        await page.locator('.scorecard-page').waitFor();
        await layout('scorecard', width);
        assert.equal(await page.locator('#score-submission form input[name=csrfmiddlewaretoken]').count(), 1);

        // Play is three real routes behind one tab control; nothing is composed client-side.
        await page.getByRole('navigation', { name: 'Main navigation' }).getByRole('link', { name: 'Play', exact: true }).click();
        await page.waitForURL(`${base}/availability/`);
        assert.equal(await page.locator('.availability-form').count(), 1);
        await layout('availability', width);
        await playTab('Find a match', '/suggestions/');
        assert.equal(await page.locator('.availability-form').count(), 0, 'Suggestions page does not repeat availability');
        await layout('suggestions', width);
        await page.getByRole('link', { name: 'Find compatible opponents', exact: true }).click();
        await page.waitForURL(`${base}/suggestions/?discover=1`);
        await layout('discover', width);
        await playTab('Matches', '/matches/');
        assert.equal(await page.locator('#submit-score').count(), 1);
        await layout('matches', width);
        await page.getByRole('link', { name: 'Enter score', exact: true }).first().click();
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
        // The account menu holds team and logout; it opens and closes without leaving the page.
        await page.locator('summary[aria-label="Account menu"]').click();
        await page.getByRole('button', { name: 'Log out' }).waitFor();
        await page.keyboard.press('Escape');
        assert.equal(await page.locator('details.account-menu[open]').count(), 0);
        await page.getByRole('navigation', { name: 'Main navigation' }).getByRole('link', { name: 'Play', exact: true }).click();
        await page.locator('.availability-form').waitFor();
    }
    // Help is hidden until asked for.
    const help = page.locator('details.help').first();
    assert.equal(await help.locator('.help-panel').isVisible(), false);
    await help.locator('summary').click();
    await help.locator('.help-panel').waitFor();
    await page.locator('h1').click();
    assert.equal(await help.evaluate(el => el.open), false);
    // The day/time picker writes the real form fields and saves through Django CSRF/session middleware.
    assert.equal(await page.locator('[data-native-fields]').isVisible(), false, 'Picker replaces raw datetime fields');
    const windowsBefore = await page.locator('.slot').count();
    await page.locator('.day-chip').nth(13).click();
    await page.getByRole('button', { name: /^Morning/ }).click();
    const [startValue, endValue] = [await page.locator('[name=starts_at]').inputValue(), await page.locator('[name=ends_at]').inputValue()];
    assert.match(startValue, /T08:00$/);
    assert.equal(endValue, startValue.replace('T08:00', 'T10:00'));
    assert.ok(await page.locator('#picker-to option').evaluateAll(options => options.every(option => Number(option.value) > 8 * 60)), 'Finish options follow the start');
    await page.getByRole('button', { name: 'Save this time', exact: true }).click();
    await page.getByRole('status').filter({ hasText: 'Availability saved.' }).waitFor();
    assert.equal(await page.locator('.slot').count(), windowsBefore + 1);
    // Without JavaScript every page, tab and form still works, and the server still rejects invalid times.
    const noJS = await browser.newContext({ javaScriptEnabled: false, reducedMotion: 'reduce', storageState: await context.storageState() });
    const fallback = await noJS.newPage();
    await fallback.goto(`${base}/availability/`);
    assert.equal(await fallback.locator('.availability-form input[name=csrfmiddlewaretoken]').count(), 1);
    await fallback.locator('[name=starts_at]').fill('2030-01-02T18:00');
    await fallback.locator('[name=ends_at]').fill('2030-01-02T17:00');
    await fallback.getByRole('button', { name: 'Save this time', exact: true }).click();
    await fallback.getByRole('alert').filter({ hasText: 'Availability start must be before end.' }).waitFor();
    assert.equal(await fallback.getByRole('navigation', { name: 'Play sections' }).locator('a[href="/suggestions/"]').count(), 1);
    await fallback.goto(`${base}/matches/`);
    assert.equal(await fallback.locator('#submit-score').count(), 1);
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
    const opponentUsername = (await page.locator('.versus-board .side').nth(1).innerText()).match(/demo-mens-[3-8]/)?.[0];
    assert.ok(opponentUsername, 'Selected opponent recorded on scorecard');
    for (const [name, value] of Object.entries({ set1_team_a: '6', set1_team_b: '4', set2_team_a: '6', set2_team_b: '3' })) {
        await page.locator(`[name=${name}]`).fill(value);
    }
    assert.equal(await page.locator('[data-score-form]').getAttribute('data-tiebreak'), 'off', 'Straight sets need no tie-break');
    await page.getByRole('button', { name: 'Submit score', exact: true }).click();
    await page.getByText('Your team submitted its score. Waiting for the opponent.', { exact: true }).waitFor();
    assert.equal(await page.locator('#score-submission').count(), 0);
    const opponentContext = await browser.newContext({ reducedMotion: 'reduce' });
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
    console.log('Frontend checks passed: five viewport sizes, auth and member routes, Play tabs, menus, help, CSRF, invalid form, no-JS and score flow.');
} catch (error) {
    await page.screenshot({ path: `${output}/failure.png`, fullPage: true }).catch(() => {});
    throw error;
} finally {
    await browser.close();
}
