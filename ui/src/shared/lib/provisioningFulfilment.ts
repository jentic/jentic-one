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
import { OpenAPI, apiRequest, ToolkitsService } from '@/shared/api';
import { toRailError } from '@/shared/lib/railEvents';

/** A toolkit created to serve a provisioning plan's API. */
export interface CreatedPlanToolkit {
	toolkitId: string;
	name: string;
}

/**
 * Create a toolkit for the plan. The name must be unique (the backend enforces
 * a unique constraint), so callers derive it from the API slug and may need to
 * disambiguate on a 409.
 */
export async function createPlanToolkit(name: string): Promise<CreatedPlanToolkit> {
	try {
		const res = await ToolkitsService.createToolkit({
			requestBody: { name },
		});
		return { toolkitId: res.toolkit.toolkit_id, name: res.toolkit.name };
	} catch (error) {
		throw toRailError(error, 'Failed to create the toolkit.');
	}
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
