import { test, expect } from '@playwright/test';
import {
	SETUP_ADMIN,
	STORAGE_STATE_PATH,
	TOKEN_STORAGE_KEY,
	ensureAuthDir,
	loadBootstrapState,
	submitLogin,
} from './helpers';

/**
 * Auth setup project — runs after bootstrap.setup.ts, before the e2e project.
 *
 * Logs the first admin into the authenticated shell with the password the
 * bootstrap project created it with, and saves the browser `storageState` (the
 * JWT lives in localStorage under `jentic-one.access_token`) to
 * playwright/.auth/admin.json. The `e2e` project sets `use.storageState` to that
 * file, so every downstream spec starts already authenticated — no per-spec login.
 */
test('auth: log in and persist storageState for the e2e project', async ({ page }) => {
	const bootstrap = loadBootstrapState();
	expect(
		bootstrap?.adminPassword,
		'bootstrap.setup.ts must run first and record the admin password',
	).toBeTruthy();

	await page.goto('/app/login');
	await submitLogin(page, SETUP_ADMIN.email, bootstrap!.adminPassword);

	// Land on the authenticated shell — confirms the JWT is in place before we
	// snapshot storage.
	await expect(page).toHaveURL(/\/app/);
	await expect(page.getByRole('heading', { name: 'Agents', exact: true })).toBeVisible();

	ensureAuthDir();
	const state = await page.context().storageState({ path: STORAGE_STATE_PATH });

	// Assert the JWT actually made it into the snapshot. storageState matches
	// localStorage by exact origin, so if setup and the e2e specs ever run under
	// mismatched origins (localhost vs 127.0.0.1 via E2E_BASE_URL), the token
	// would be silently absent and every e2e spec would start UNauthenticated.
	// Fail loudly here instead.
	const tokenEntry = state.origins
		.flatMap((o) => o.localStorage)
		.find((kv) => kv.name === TOKEN_STORAGE_KEY);
	expect(
		tokenEntry?.value,
		`storageState is missing ${TOKEN_STORAGE_KEY} — check that setup and e2e specs share the same origin`,
	).toBeTruthy();
});
