import { test, expect } from '@playwright/test';
import { captureConsoleErrors, fileAccessRequest, uniqueSuffix, authHeaders } from './helpers';
import { provisionAdminOwnedAgent, fileAccessRequestAsAgent } from './agent-flow';

/**
 * Access requests (real backend). Agents file access requests when they need a
 * permission they don't yet hold; a human decides them here. The full queue
 * lives at /app/access-requests (the "View all" target of the Dashboard card),
 * reads GET /access-requests, and each row opens the shared AccessRequestDialog
 * to approve/deny per item via POST /access-requests/{id}:decide.
 *
 * Approval is gated by the backend (_compute_evaluation): the reviewer must NOT
 * be the filer AND must own the filing agent. So the happy-path approve here is
 * filed by a REAL admin-owned agent (register → :approve → own → jwt-bearer mint
 * → file), the only way the admin satisfies `owns_filer`. The negative path
 * (admin files for itself, then tries to approve) exercises the `not_filer`
 * guard and asserts the surfaced "not authorized" error. See agent-flow.ts.
 *
 * On a clean DB the queue is empty, so each spec self-seeds before driving the UI.
 */
test('access-requests queue renders the empty state on a clean backend', async ({ page }) => {
	const errors = captureConsoleErrors(page);

	await page.goto('/app/access-requests');
	await expect(page.getByRole('heading', { name: 'Access requests' })).toBeVisible();

	// Default filter is Pending; a clean DB shows the "No requests" empty state.
	await expect(page.getByText('No requests')).toBeVisible();

	expect(errors, `unexpected console errors:\n${errors.join('\n')}`).toEqual([]);
});

test('a filed access request surfaces in the pending queue', async ({ page, request }) => {
	const reason = `e2e pending ${uniqueSuffix()}`;
	await fileAccessRequest(request, { reason, resourceType: 'credential', action: 'bind' });

	await page.goto('/app/access-requests');
	await expect(page.getByRole('heading', { name: 'Access requests' })).toBeVisible();

	// The request lands as a pending row summarising its first item (resource ·
	// action).
	const row = page.getByRole('button', { name: /credential · bind/i }).first();
	await expect(row).toBeVisible({ timeout: 15_000 });
});

test('approve an admin-owned agent request from the queue', async ({ page, request }) => {
	// Real agent-filed request: only an admin-owned agent's request is approvable
	// by the admin (owns_filer). Walk the full register → approve → own → mint →
	// file chain. We file a scope:grant for an already-grantable scope so the
	// decision applies a real, self-contained effect (no external toolkit needed)
	// and lands on the "Access granted" terminal screen.
	const agent = await provisionAdminOwnedAgent(request, {
		name: `e2e-approve-${uniqueSuffix()}`,
	});
	await fileAccessRequestAsAgent(request, agent, {
		reason: `e2e approve ${uniqueSuffix()}`,
		resourceType: 'scope',
		action: 'grant',
		resourceId: 'capabilities:read',
	});

	await page.goto('/app/access-requests');
	await expect(page.getByRole('heading', { name: 'Access requests' })).toBeVisible();

	// Open this spec's own pending row (targeted by filer) → decision dialog. The
	// queue resolves the agent's id to its directory name via <ActorLabel>, so we
	// match the unique registered name, not the raw agnt_… id.
	await page.getByRole('button', { name: new RegExp(`by ${agent.name}`) }).click();
	const dialog = page.getByRole('dialog', { name: 'Access request' });
	await expect(dialog).toBeVisible();

	// Approve the (single) item on its card, then review & confirm the decision
	// (real :decide call). Because the agent is admin-owned, the backend
	// authorises the admin as reviewer.
	await dialog.getByRole('button', { name: 'Approve', exact: true }).click();
	await dialog.getByRole('button', { name: /Review & submit/i }).click();
	await dialog.getByRole('button', { name: /Confirm decision/ }).click();

	// A success terminal screen confirms the grant; "Done" closes the dialog and
	// invalidates the queue, so the row leaves the default (Pending) filter.
	await expect(dialog.getByText('Access granted')).toBeVisible({ timeout: 15_000 });
	await dialog.getByRole('button', { name: 'Done' }).click();
	await expect(dialog).toBeHidden();
	await page.getByRole('button', { name: 'Approved', exact: true }).click();
	await expect(page.getByRole('button', { name: new RegExp(`by ${agent.name}`) })).toBeVisible({
		timeout: 15_000,
	});
});

test('deny an admin-owned agent request from the queue', async ({ page, request }) => {
	// Target this spec's own request by its filer: queue rows resolve the agent id
	// to its directory name (via <ActorLabel>) and render "by {name}", so we open
	// the row for THIS agent rather than .first() (which is order- and
	// DB-state-dependent).
	const agent = await provisionAdminOwnedAgent(request, { name: `e2e-deny-${uniqueSuffix()}` });
	await fileAccessRequestAsAgent(request, agent, {
		reason: `e2e deny ${uniqueSuffix()}`,
		resourceType: 'credential',
		action: 'bind',
		resourceId: `e2e-deny-${uniqueSuffix()}`,
	});

	await page.goto('/app/access-requests');
	await expect(page.getByRole('heading', { name: 'Access requests' })).toBeVisible();

	await page.getByRole('button', { name: new RegExp(`by ${agent.name}`) }).click();
	const dialog = page.getByRole('dialog', { name: 'Access request' });
	await expect(dialog).toBeVisible();

	// Deny the (single) item inline on its card. A denial REQUIRES a reason
	// (sent back to the agent): clicking the card's deny affordance expands a
	// reason field, and "Confirm deny" stays disabled until it's filled.
	await dialog.getByRole('button', { name: /^Deny / }).click();
	await dialog.getByRole('textbox', { name: /Why deny\?/i }).fill('denied by e2e: not needed');
	await dialog.getByRole('button', { name: 'Confirm deny' }).click();
	await dialog.getByRole('button', { name: /Review & submit/i }).click();

	const confirm = dialog.getByRole('button', { name: /Confirm decision/ });
	await expect(confirm).toBeEnabled();
	await confirm.click();

	// A "declined" terminal screen confirms no access was granted; Done closes it.
	await expect(dialog.getByText('Request declined')).toBeVisible({ timeout: 15_000 });
	await dialog.getByRole('button', { name: 'Done' }).click();
	await expect(dialog).toBeHidden();
	await page.getByRole('button', { name: 'Denied', exact: true }).click();
	await expect(page.getByRole('button', { name: new RegExp(`by ${agent.name}`) })).toBeVisible({
		timeout: 15_000,
	});
});

test('cannot approve a request you filed yourself (not_filer guard)', async ({ page, request }) => {
	// The admin files for ITSELF (created_by == reviewer), so the backend's
	// `not_filer` rule blocks the decision. The dialog should surface the
	// "not authorized to review" error and stay open (no optimistic close).
	const res = await request.post('/access-requests', {
		headers: authHeaders(),
		data: {
			reason: `e2e self-review ${uniqueSuffix()}`,
			items: [
				{
					resource_type: 'credential',
					action: 'bind',
					resource_id: `e2e-self-${uniqueSuffix()}`,
				},
			],
		},
	});
	expect(res.status(), `self-file failed: ${await res.text()}`).toBe(202);
	const requestId = (await res.json()).id as string;

	await page.goto('/app/access-requests');
	await expect(page.getByRole('heading', { name: 'Access requests' })).toBeVisible();

	// Open the admin-filed row. The queue resolves actor ids to display names
	// (via <ActorLabel> / GET /actors, #564), so the admin renders as "Admin
	// User". Multiple admin-filed rows may exist across the run; the request id
	// is not exposed on the row, so match the resolved admin filer name and take
	// the most recent (top) row — the queue is newest-first and we just filed it.
	await page
		.getByRole('button', { name: /by Admin User/ })
		.first()
		.click();
	const dialog = page.getByRole('dialog', { name: 'Access request' });
	await expect(dialog).toBeVisible();

	await dialog.getByRole('button', { name: 'Approve', exact: true }).click();
	await dialog.getByRole('button', { name: /Review & submit/i }).click();
	await dialog.getByRole('button', { name: /Confirm decision/ }).click();

	// Backend rejects with 403 access_request_not_reviewer; the dialog shows an
	// error terminal screen with the surfaced message and stays open (no
	// optimistic close), offering "Try again". (See E2E-FINDINGS F-2: the UI
	// could disable Confirm up front using `evaluation.can_fulfill`.)
	await expect(dialog.getByText(/not authorized to review/i)).toBeVisible({ timeout: 15_000 });
	await expect(dialog.getByRole('button', { name: 'Try again' })).toBeVisible();
	await expect(dialog).toBeVisible();
	expect(requestId).toMatch(/^areq_/);
});
