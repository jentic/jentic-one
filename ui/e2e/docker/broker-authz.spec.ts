import { test, expect } from '@playwright/test';
import { authHeaders, replaceAgentPermissions, uniqueSuffix } from './helpers';
import { provisionAdminOwnedAgent } from './agent-flow';

/**
 * Broker authorization (real backend).
 *
 * The broker's /execute path requires the caller's token to carry
 * `capabilities:execute` (BROKER_EXECUTE_PERMISSION). The AUTHORIZATION SOURCE
 * OF TRUTH is the agent's permission grants (actor_permission_grants), read at
 * token-mint time. That source is fully testable here via the public permission
 * API (the endpoints that landed with #517): an agent granted the permission has
 * it; an agent not granted it does not. These are the assertions that decide
 * whether a broker call would be authorised.
 *
 * The actual broker round-trip (200 for granted, 403 insufficient_scope — the
 * RFC 6750 error code — for
 * ungranted) is `test.fixme` — the broker is a separate process, not a callable
 * surface in the combined `make start-app` harness, and is further blocked by
 * #526 (resolver boot). Enable the round-trip assertions when the broker is
 * reachable in CI.
 */

test('a newly created agent receives default permissions including capabilities:execute', async ({
	request,
}) => {
	const agent = await provisionAdminOwnedAgent(request, {
		name: `e2e-authz-defaults-${uniqueSuffix()}`,
	});

	const res = await request.get(`/agents/${agent.clientId}/permissions`, {
		headers: authHeaders(),
	});
	expect(res.ok(), `read permissions failed: ${res.status()}`).toBeTruthy();
	const permissions = (await res.json()).permissions;
	expect(permissions).toContain('capabilities:execute');
	expect(permissions).toContain('capabilities:read');
	expect(permissions).toContain('owner:credentials:read');
});

test('a granted agent carries capabilities:execute in its permission grants', async ({
	request,
}) => {
	const agent = await provisionAdminOwnedAgent(request, {
		name: `e2e-authz-grant-${uniqueSuffix()}`,
	});
	await replaceAgentPermissions(request, agent.clientId, ['capabilities:execute']);

	const res = await request.get(`/agents/${agent.clientId}/permissions`, {
		headers: authHeaders(),
	});
	expect(res.ok(), `read permissions failed: ${res.status()}`).toBeTruthy();
	expect((await res.json()).permissions).toContain('capabilities:execute');
});

test('an ungranted agent lacks capabilities:execute in its permission grants', async ({
	request,
}) => {
	const agent = await provisionAdminOwnedAgent(request, {
		name: `e2e-authz-deny-${uniqueSuffix()}`,
	});
	// Agents receive default permissions on creation; explicitly remove them to
	// test the ungranted state.
	await replaceAgentPermissions(request, agent.clientId, []);

	const res = await request.get(`/agents/${agent.clientId}/permissions`, {
		headers: authHeaders(),
	});
	expect(res.ok(), `read permissions failed: ${res.status()}`).toBeTruthy();
	expect((await res.json()).permissions).not.toContain('capabilities:execute');
});

test('replacing permissions is a full replace, not a merge (revokes execute)', async ({
	request,
}) => {
	const agent = await provisionAdminOwnedAgent(request, {
		name: `e2e-authz-replace-${uniqueSuffix()}`,
	});

	await replaceAgentPermissions(request, agent.clientId, ['capabilities:execute']);
	// Replace with a different set — execute must be gone (PUT is a bulk replace).
	const after = await replaceAgentPermissions(request, agent.clientId, []);
	expect(after).not.toContain('capabilities:execute');
});

// The live broker round-trip. Blocked by #526 and by the broker not being a
// callable surface in the combined harness.
test.fixme('granted agent token → broker 200; ungranted token → broker 403 (blocked by #526)', async () => {
	// granted:   mint token with capabilities:execute → {broker}/execute → 200
	// ungranted: mint token without it                → {broker}/execute → 403
	//            insufficient_scope (require_execute_permission in broker/web/deps.py)
});
