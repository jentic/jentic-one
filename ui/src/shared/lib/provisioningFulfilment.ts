/**
 * Provisioning-plan fulfilment — server calls the operator wizard drives.
 *
 * These wrap the generated services directly (the sanctioned way for shared
 * code to reach the API — the credentials/toolkits *modules* can't be imported
 * from shared/). Each step maps to one operator action in the wizard:
 *
 *   createPlanToolkit    — POST /toolkits           (Step 1)
 *   (credential created by the reused CreateCredentialDialog — Step 2)
 *   amendAccessRequest   — POST .../:amend          (wire ids onto the bind item)
 *   bindToolkitToAgentIfNeeded — POST /agents/{id}/toolkits (Step 5 pre-wire)
 *   decideAccessRequest  — POST .../:decide         (final approve-all)
 */
import {
	OpenAPI,
	apiRequest,
	ToolkitsService,
	CredentialsService,
	CredentialType,
} from '@/shared/api';
import { toRailError } from '@/shared/lib/railEvents';

/** A toolkit created to serve a provisioning plan's API. */
export interface CreatedPlanToolkit {
	toolkitId: string;
	name: string;
}

/** The API reference a plan credential/toolkit is scoped to. */
export interface PlanApiRef {
	vendor: string;
	name?: string;
	version?: string;
}

/**
 * Create a NO_AUTH credential for a no-auth plan. A no-auth API still needs a
 * credential row for the `credential:bind` effect to attach the toolkit binding
 * + permission rules to (the broker keys rules on `(toolkit, credential)` and
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
 * Create a toolkit for the plan. Toolkit names are globally unique (the backend
 * maps a duplicate to 409), so on a collision — e.g. two plans for the same API,
 * or reopening a plan after a "finish later" that left an earlier toolkit — we
 * transparently disambiguate the name with a numeric suffix (`…-2`, `…-3`, …)
 * and retry. The name is cosmetic (all downstream wiring resolves the toolkit by
 * id), so a suffixed name changes nothing functional. Gives up after a bounded
 * number of attempts to avoid an unbounded retry loop.
 */
const _MAX_NAME_ATTEMPTS = 20;

export async function createPlanToolkit(name: string): Promise<CreatedPlanToolkit> {
	for (let attempt = 1; attempt <= _MAX_NAME_ATTEMPTS; attempt++) {
		const candidate = attempt === 1 ? name : `${name}-${attempt}`;
		try {
			const res = await ToolkitsService.createToolkit({
				requestBody: { name: candidate },
			});
			return { toolkitId: res.toolkit.toolkit_id, name: res.toolkit.name };
		} catch (error) {
			const railError = toRailError(error, 'Failed to create the toolkit.');
			// Only a name collision (409) is retryable with a new name; anything
			// else (auth, validation, server error) is surfaced immediately.
			if (railError.status !== 409 || attempt === _MAX_NAME_ATTEMPTS) {
				throw railError;
			}
		}
	}
	// Unreachable — the loop either returns or throws — but satisfies the type.
	throw toRailError(new Error('exhausted name attempts'), 'Failed to create the toolkit.');
}

/**
 * Suggest a toolkit name from the plan's API reference — the vendor/name slug,
 * which reads naturally (e.g. `posthog-com/posthog-api`) and is stable per API.
 */
export function suggestToolkitName(vendor: string, name?: string): string {
	return [vendor, name].filter(Boolean).join('/');
}

/**
 * Delete a toolkit created during an abandoned fulfilment (orphan cleanup). The
 * wizard tracks what it created this session and offers to discard it on cancel.
 */
export async function discardPlanToolkit(toolkitId: string): Promise<void> {
	try {
		await apiRequest<void>(OpenAPI, {
			method: 'DELETE',
			url: '/toolkits/{toolkit_id}',
			path: { toolkit_id: toolkitId },
		});
	} catch {
		// Best-effort cleanup — a failed discard leaves an orphan the operator can
		// remove from the toolkits page, which is acceptable for v1.
	}
}

/** Delete a credential created during an abandoned fulfilment (orphan cleanup). */
export async function discardPlanCredential(credentialId: string): Promise<void> {
	try {
		await apiRequest<void>(OpenAPI, {
			method: 'DELETE',
			url: '/credentials/{credential_id}',
			path: { credential_id: credentialId },
		});
	} catch {
		// Best-effort — see discardPlanToolkit.
	}
}
