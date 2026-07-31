import { test, expect, type APIRequestContext } from '@playwright/test';
import { authHeaders } from './helpers';

/**
 * Flow-3 catalog update-notify loop (real backend, API-driven).
 *
 * The one full-stack assertion of the catalog-update feature: it drives the
 * genuine loop through the live combined app + Postgres over HTTP (no UI), using
 * a controllable fixture upstream (scripts/flow3_upstream_fixture.py) that the
 * ui-e2e-realbackend CI job launches on 127.0.0.1:8099 before the app.
 *
 *   refresh manifest → import the catalog API → assert not-outdated
 *     → bump the upstream spec → POST /catalog:refresh (fires the sweep)
 *     → assert GET /apis update_available:true, GET /catalog?outdated_only outdated_count:1,
 *       and an actionable catalog.update_available event
 *     → re-import (adopts the upstream) → assert update_available:false, outdated_count:0,
 *       AND the event is acknowledged (the settle path — regressed by the SQLite
 *       nested-transaction self-deadlock the manual E2E caught).
 *
 * The fixture upstream is only present in CI (and local runs that start it), so
 * the whole spec self-skips when 127.0.0.1:8099 is unreachable — it must never
 * fail a plain `npm run e2e:docker` that didn't launch the fixture.
 */

const UPSTREAM = 'http://127.0.0.1:8099';
const CATALOG_API_ID = 'flow3-e2e.test';
const IMPORT_PATH = `/catalog/${CATALOG_API_ID}:import`;

/** True when the fixture upstream is up (CI launches it; local runs may not). */
async function upstreamReachable(request: APIRequestContext): Promise<boolean> {
	try {
		const res = await request.get(`${UPSTREAM}/healthz`, { timeout: 2000 });
		return res.ok();
	} catch {
		return false;
	}
}

/** Poll an import job (queued → terminal). The real backend imports async. */
async function waitForJob(request: APIRequestContext, jobId: string): Promise<void> {
	await expect
		.poll(
			async () => {
				const j = await request.get(`/jobs/${jobId}`, { headers: authHeaders() });
				return j.ok() ? ((await j.json()).status as string) : 'unknown';
			},
			{
				message: `import job ${jobId} never completed`,
				timeout: 60_000,
				intervals: [250, 500, 1000, 2000],
			},
		)
		.toMatch(/succeeded|completed|done/);
}

/** The single registered API's row from GET /apis (the fixture seeds exactly one). */
async function fixtureApi(request: APIRequestContext): Promise<Record<string, unknown>> {
	const res = await request.get('/apis', { headers: authHeaders() });
	expect(res.ok(), `GET /apis failed: ${res.status()} ${await res.text()}`).toBeTruthy();
	const rows = (await res.json()).data as Array<Record<string, unknown>>;
	const row = rows.find((r) => r.origin === 'catalog');
	expect(row, 'no catalog-origin API found after import').toBeTruthy();
	return row as Record<string, unknown>;
}

test('flow-3 loop: import → upstream change → update_available → re-import clears + acks', async ({
	request,
}) => {
	// A cold worker on a fresh DB can take ~25s for the first import — widen the timeout.
	test.slow();

	test.skip(
		!(await upstreamReachable(request)),
		'flow3 fixture upstream not running on 127.0.0.1:8099 (CI-only; launched by ui-e2e-realbackend)',
	);

	// 1. Load the fixture manifest so `flow3-e2e.test` becomes an importable catalog entry.
	const refresh1 = await request.post('/catalog:refresh', { headers: authHeaders() });
	expect(
		refresh1.ok(),
		`catalog:refresh failed: ${refresh1.status()} ${await refresh1.text()}`,
	).toBeTruthy();

	// 2. Import the catalog API (async job).
	const imp1 = await request.post(IMPORT_PATH, { headers: authHeaders() });
	expect(imp1.status(), `import failed: ${await imp1.text()}`).toBe(202);
	await waitForJob(request, (await imp1.json()).job_id as string);

	// Freshly imported → the served digest equals the upstream, so not outdated.
	expect(
		(await fixtureApi(request)).update_available,
		'fresh import should not be outdated',
	).toBe(false);

	// 3. Simulate an upstream spec change (new bytes → new digest).
	const bump = await request.post(`${UPSTREAM}/control/bump`);
	expect(bump.ok(), 'fixture bump failed').toBeTruthy();

	// 4. Trigger the update-notify sweep (also refreshes the manifest).
	const refresh2 = await request.post('/catalog:refresh', { headers: authHeaders() });
	expect(refresh2.ok(), `catalog:refresh (sweep) failed: ${await refresh2.text()}`).toBeTruthy();

	// 5. The sweep must now flag the API across the per-API and catalog surfaces.
	await expect
		.poll(async () => (await fixtureApi(request)).update_available, {
			message: 'update_available never flipped true after upstream change',
			timeout: 20_000,
			intervals: [500, 1000, 2000],
		})
		.toBe(true);

	const outdated = await request.get('/catalog?outdated_only=true', { headers: authHeaders() });
	expect(outdated.ok()).toBeTruthy();
	const outdatedBody = await outdated.json();
	expect(outdatedBody.outdated_count, 'catalog outdated_count should be 1').toBe(1);
	expect((outdatedBody.data as unknown[]).length, 'one outdated catalog row expected').toBe(1);

	// An actionable catalog.update_available event must have been emitted.
	const events1 = await request.get('/events?type=catalog.update_available', {
		headers: authHeaders(),
	});
	expect(events1.ok()).toBeTruthy();
	const evt = ((await events1.json()).data as Array<Record<string, unknown>>).find(
		(e) => e.type === 'catalog.update_available',
	);
	expect(evt, 'no catalog.update_available event emitted').toBeTruthy();
	expect(evt!.requires_action, 'event should be actionable').toBe(true);
	expect(evt!.acknowledged, 'event should not be acked before re-import').toBe(false);

	// 6. Re-import adopts the upstream: clears the flag AND settles the event.
	const imp2 = await request.post(IMPORT_PATH, { headers: authHeaders() });
	expect(imp2.status(), `re-import failed: ${await imp2.text()}`).toBe(202);
	await waitForJob(request, (await imp2.json()).job_id as string);

	await expect
		.poll(async () => (await fixtureApi(request)).update_available, {
			message: 'update_available never cleared after re-import',
			timeout: 20_000,
			intervals: [500, 1000, 2000],
		})
		.toBe(false);

	const catalogAfter = await request.get('/catalog', { headers: authHeaders() });
	expect(catalogAfter.ok()).toBeTruthy();
	expect((await catalogAfter.json()).outdated_count, 'outdated_count should clear to 0').toBe(0);

	// The settle path must have acknowledged the actionable event (the regression
	// the manual E2E surfaced: on SQLite it self-deadlocked and never acked).
	await expect
		.poll(
			async () => {
				const res = await request.get('/events?type=catalog.update_available', {
					headers: authHeaders(),
				});
				if (!res.ok()) return null;
				const e = ((await res.json()).data as Array<Record<string, unknown>>).find(
					(x) => x.type === 'catalog.update_available',
				);
				return e ? (e.acknowledged as boolean) : null;
			},
			{
				message: 'catalog.update_available event was never acknowledged by the settle path',
				timeout: 20_000,
				intervals: [500, 1000, 2000],
			},
		)
		.toBe(true);
});
