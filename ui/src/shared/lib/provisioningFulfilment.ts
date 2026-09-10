/**
 * Provisioning-plan fulfilment — server calls the operator wizard drives.
 *
 * These wrap the generated services directly (the sanctioned way for shared
 * code to reach the API — the credentials *module* can't be imported from
 * shared/). Each step maps to one operator action in the wizard:
 *
 *   (credential created by the reused CreateCredentialDialog — Step 1)
 *   createNoAuthCredential — POST /credentials   (auto, for no-auth plans)
 *   amendAccessRequest   — POST .../:amend       (wire the id onto the bind item)
 *   decideAccessRequest  — POST .../:decide      (final approve-all)
 */
import { OpenAPI, apiRequest, CredentialsService, CredentialType } from '@/shared/api';
import { toRailError } from '@/shared/lib/railEvents';

/** The API reference a plan credential is scoped to. */
export interface PlanApiRef {
	vendor: string;
	name?: string;
	version?: string;
}

/**
 * Create a NO_AUTH credential for a no-auth plan. A no-auth API still needs a
 * credential row for the `credential:bind` effect to attach the agent binding
 * + permission rules to (the broker keys rules on `(agent, credential)` and
 * resolves a `no_auth` credential as a no-op auth). So even though there's no
 * secret, the wizard auto-creates this behind the scenes — the operator never
 * sees a credential form for a no-auth plan.
 */
export async function createNoAuthCredential(
	api: PlanApiRef,
	name: string,
): Promise<{ credentialId: string }> {
	try {
		const res = await CredentialsService.createCredential({
			requestBody: {
				type: CredentialType.NO_AUTH,
				provider: 'static',
				name,
				api: {
					vendor: api.vendor,
					name: api.name || undefined,
					version: api.version || undefined,
				},
			},
		});
		return { credentialId: res.credential.credential_id };
	} catch (error) {
		throw toRailError(error, 'Failed to create the no-auth credential.');
	}
}

/**
 * Delete a credential created during an abandoned fulfilment (orphan cleanup).
 * The wizard tracks what it created this session and offers to discard it on
 * cancel.
 */
export async function discardPlanCredential(credentialId: string): Promise<void> {
	try {
		await apiRequest<void>(OpenAPI, {
			method: 'DELETE',
			url: '/credentials/{credential_id}',
			path: { credential_id: credentialId },
		});
	} catch {
		// Best-effort cleanup — a failed discard leaves an orphan the operator
		// can remove from the credentials page, which is acceptable for v1.
	}
}
