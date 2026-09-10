import { test, expect } from '@playwright/test';
import {
	authHeaders,
	bindToolkitToAgent,
	createApiKeyCredential,
	createToolkit,
	importInlineApi,
	replaceAgentScopes,
	uniqueSuffix,
} from './helpers';
import { provisionAdminOwnedAgent, fileAccessRequestAsAgent } from './agent-flow';

/**
 * Full platform loop (real backend).
 *
 * The end-to-end "agent gets to actually call an upstream" journey is:
 *
 *   import API → create toolkit → create credential → bind toolkit to agent
 *     → grant capabilities:execute → file access request (as agent)
 *     → admin approves → mint agent token → broker GET → read execution back
 *
 * The PREFIX of that journey (everything up to and including admin approval)
 * runs against the combined `make start-app` boot and is asserted here for
 * real. The BROKER TAIL (token → /execute → executions) cannot run in this
 * suite and is captured as `test.fixme` with the issues that block it:
 *
 *   - The combined app boot does not serve the broker surface as a callable
 *     /execute endpoint in this harness; the broker runs as its own process.
 *   - #526 (F-9): a standalone broker never installs the registry resolver, so
 *     every /execute 500s until that boot-ordering bug is fixed.
 *   - #527 (F-10): a vendor-only credential persists api_name='' (not NULL), so
 *     the broker's IS NULL wildcard never matches → no_toolkit_binding. The
 *     credential here pins the exact API identity to sidestep it, but the fix
 *     belongs upstream.
 *   - #539 (F-11): a successful broker execution did not surface in
 *     admin.execution_records / GET /executions (needs single-DB verification).
 *
 * When #526/#527/#539 land, the fixme tail can be un-fixme'd and asserted live.
 */

test('full loop prefix: import → toolkit → credential → bind → grant → file → approve', async ({
	page,
	request,
}) => {
	// A cold worker on a fresh DB can take ~25s for the first import (F-6, a
	// known test-infra warmup cost, not a product bug) — widen the timeout.
	test.slow();

	const sfx = uniqueSuffix();
	const vendor = 'httpbin.org';
	const apiName = `httpbin-${sfx}`;

	// 1. Import an API into the local registry (async job; helper polls it).
	await importInlineApi(request, { vendor, apiName, title: `e2e full-loop ${sfx}` });

	// 2. Create a toolkit the agent will be bound to.
	const toolkitId = await createToolkit(request, `e2e-loop-tk-${sfx}`);

	// 3. Create a credential. Pin the EXACT resolved API identity (not a bare
	//    vendor) to sidestep #527 (F-10) so this stays a faithful happy-path.
	const credentialId = await createApiKeyCredential(request, {
		name: `e2e-loop-cred-${sfx}`,
		vendor,
		apiName,
		apiVersion: '1.0.0',
	});

	// 4. Provision an admin-owned agent (register → approve → own).
	const agent = await provisionAdminOwnedAgent(request, { name: `e2e-loop-agent-${sfx}` });

	// 5. Bind the toolkit + grant capabilities:execute via the PUBLIC API
	//    (the scope endpoints that landed with #517 / closed F-7).
	await bindToolkitToAgent(request, agent.clientId, toolkitId);
	const scopes = await replaceAgentScopes(request, agent.clientId, ['capabilities:execute']);
	expect(scopes).toContain('capabilities:execute');

	// 6. File an access request AS the agent and approve it from the UI — the
	//    real human-in-the-loop gate. Because the agent is admin-owned, the
	//    admin satisfies owns_filer and the decision is authorised. We file a
	//    credential:bind for the REAL credential created above (by its id) so
	//    the approved decision applies a genuine, resolvable effect (filing
	//    stamps a read-only default rule set — theme-5 phase 3). The row is
	//    matched in the queue by filer name (the queue resolves the agent id to
	//    its directory name via <ActorLabel>).
	await fileAccessRequestAsAgent(request, agent, {
		reason: `e2e full-loop ${sfx}`,
		resourceType: 'credential',
		action: 'bind',
		resourceId: credentialId,
	});

	await page.goto('/app/access-requests');
	await expect(page.getByRole('heading', { name: 'Access requests' })).toBeVisible();
	await page.getByRole('button', { name: new RegExp(`by ${agent.name}`) }).click();
	const dialog = page.getByRole('dialog', { name: 'Access request' });
	await expect(dialog).toBeVisible();
	await dialog.getByRole('button', { name: 'Approve', exact: true }).click();
	await dialog.getByRole('button', { name: /Review & submit/i }).click();
	await dialog.getByRole('button', { name: /Confirm decision/i }).click();
	await dialog.getByRole('button', { name: 'Done' }).click();
	await expect(dialog).toBeHidden();

	// Settled into Approved — the request left the default Pending filter.
	await page.getByRole('button', { name: 'Approved', exact: true }).click();
	await expect(page.getByRole('button', { name: new RegExp(`by ${agent.name}`) })).toBeVisible({
		timeout: 15_000,
	});

	// Sanity: the agent now carries the execute scope on a freshly minted token.
	// (We re-read via the API rather than asserting on the broker, which is the
	// fixme tail below.)
	const after = await request.get(`/agents/${agent.clientId}/scopes`, { headers: authHeaders() });
	expect(after.ok(), `read agent scopes failed: ${after.status()}`).toBeTruthy();
	expect((await after.json()).scopes).toContain('capabilities:execute');
});

// The broker tail. Blocked by #526 / #527 / #539 and by the broker not being a
// callable surface in the combined harness. Documented end-to-end so the
// intended assertions are explicit and ready to enable once those land.
test.fixme('full loop tail: agent token → broker GET → execution recorded (blocked by #526/#527/#539)', async () => {
	// 1. Mint an agent token carrying capabilities:execute (jwt-bearer).
	// 2. POST {broker}/execute (or the bound GET) with that token.
	// 3. Expect 200 + a Jentic-Execution-Id header from the real upstream.
	// 4. GET /executions (control) → the execution surfaces with that id.
	//
	// Today (1) works, but (2) requires a standalone broker that installs the
	// registry resolver (#526), (3) requires the vendor-wildcard credential
	// fix (#527) unless the API identity is pinned, and (4) requires the
	// execution-record persistence fix (#539). Enable when those are fixed.
});
