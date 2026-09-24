import { test, expect } from '@playwright/test';
import {
	SETUP_ADMIN,
	getHealth,
	submitSetup,
	saveBootstrapState,
	loadBootstrapState,
} from './helpers';

/**
 * Bootstrap setup project — runs once before auth.setup.ts.
 *
 * Clears the first-run gate by creating the first admin through the /setup UI,
 * then records the chosen password for auth.setup.ts (cross-project handoff).
 *
 * Idempotent and retry-safe (the config gives this project retries:0, but the
 * logic below does not depend on that): creating the admin is a NON-idempotent
 * mutation, so we decide what to do from the *backend's* gate state, not a local
 * file —
 *   - gate UP  (setup_required=true)  -> create the admin via the UI, then persist.
 *   - gate DOWN (setup_required=false) -> already created. Reuse the recorded
 *     password if we have it; otherwise the DB was provisioned out-of-band and we
 *     cannot know the password, so fail loudly with a clear remediation.
 * This decouples "is the gate cleared?" (backend truth) from "do we have the
 * password?" (local file) — ANDing the two would produce a
 * misleading hard-fail when the local file is missing against a provisioned DB.
 */
test('bootstrap: ensure the first-run gate is cleared and the admin password is known', async ({
	page,
	request,
}) => {
	const health = await getHealth(request);

	if (!health.setup_required) {
		// Gate already cleared (persistent DB, or a prior run). We can only proceed
		// if we recorded the password we created the admin with.
		const existing = loadBootstrapState();
		expect(
			existing?.adminPassword,
			'The first-run gate is already cleared (setup_required=false) but no admin ' +
				'password was recorded under playwright/.auth/. The DB was provisioned out-of-band ' +
				'and the e2e suite cannot recover the password. Reset with ' +
				'`make destroy-fixtures && make start-fixtures` to restore the first-run gate.',
		).toBeTruthy();
		return;
	}

	// Gate is up — this must be a clean DB. Create the first admin via the UI.
	expect(health.next_step).toBe('create_admin');

	await page.goto('/app/setup');
	await submitSetup(page, SETUP_ADMIN.email, SETUP_ADMIN.password);

	// The create has now mutated the DB. Persist the password IMMEDIATELY —
	// before any post-condition assertion — so a later flake can't leave the DB
	// provisioned with no record of the password. This is safe because
	// submitSetup() only returns after the create-admin handler resolves (the UI
	// adopts the returned token and navigates to /app), so the mutation committed.
	saveBootstrapState({ adminPassword: SETUP_ADMIN.password });

	// Post-conditions: the UI lands authenticated and the gate is cleared.
	await expect(page).toHaveURL(/\/app/);
	await expect(page.getByRole('heading', { name: 'Dashboard' })).toBeVisible();

	const after = await getHealth(request);
	expect(after.setup_required).toBe(false);
	expect(after.next_step).toBeNull();
});
