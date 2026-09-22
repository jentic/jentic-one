/**
 * Add-APIs preflight — the pure data layer behind the tray's tally. There is no
 * `Skip for now`, so every pick must end with a credential; each is classified by
 * what it will cost and the tray tallies the classes before anything is committed.
 */
import type { Credential, SelectedApi } from '@/shared/credentials/api';
import { apiRefKey, apiScopeCovers } from '@/shared/credentials/lib/apiIdentity';
import { credentialAwaitsConsent } from '@/modules/agents/lib/apiTiles';
import type { CredentialBindingEntity, ServedApiEntity } from '@/modules/agents/api/types';

/**
 * What a pick will cost, worst-to-best as work for the operator.
 *
 * - `attached` — this agent already reaches the API; the tray blocks the pick.
 * - `reuse` — exactly one org credential covers it. Bind and move on.
 * - `oauth` — one sign-in click finishes it.
 * - `choose` — several org credentials cover it, so the queue asks which.
 * - `form` — needs a new credential typed in.
 */
export type PreflightOutcome = 'attached' | 'reuse' | 'oauth' | 'choose' | 'form';

export interface PreflightItem {
	/** `vendor/name` identity — the picker's selection key for this API. */
	key: string;
	api: SelectedApi;
	outcome: PreflightOutcome;
	/** Org credentials whose identity covers this pick, in list order: one for
	 * `reuse`, several for `choose`, the unconnected one for `oauth`. */
	candidates: Credential[];
	/** `attached` only: the credential the agent already reaches this API
	 *  through, so the row can say which binding is in the way. */
	attachedVia?: string;
	/** True when accepting this pick imports the API into the workspace. */
	importsApi: boolean;
}

export interface PreflightInputs {
	/** Every org credential — pass a DRAINED list; a first page misclassifies. */
	credentials: Credential[];
	/** The agent's existing bindings, whose `serves` entries say which APIs it
	 *  already reaches (and through which credential). */
	bindings: CredentialBindingEntity[];
	/** A managed OAuth provider is configured, so a new oauth2 credential is a
	 * sign-in click rather than a client-credentials form. */
	managedOAuthAvailable: boolean;
}

/** Does a binding's served reference already cover this pick? Delegates to
 * {@link apiScopeCovers} — two comparators is how a pick reads `Already added`
 * while the grid draws no tile for it. */
function servedCoversPick(served: ServedApiEntity, api: SelectedApi): boolean {
	return apiScopeCovers(served, api);
}

/**
 * Does an org credential cover this API? Match on API IDENTITY, never vendor
 * alone: "Stripe — Production" and "Stripe — Sandbox" are not interchangeable.
 * Two matches is the `choose` case. Health is NOT a filter — it cannot be
 * reliably detected, and filtering on it would imply the survivors are healthy.
 */
export function credentialCoversApi(credential: Credential, api: SelectedApi): boolean {
	if (credential.catalog_api_id && api.apiId) {
		return credential.catalog_api_id.trim().toLowerCase() === api.apiId.trim().toLowerCase();
	}
	return apiScopeCovers(credential.api, api);
}

/**
 * Can a brand-new credential for this API be finished with a sign-in click
 * instead of a form? Only when the spec says OAuth 2.0 is the sole scheme AND a
 * managed provider is configured. A catalog pick carries no scheme hint until its
 * spec is fetched, so it lands in `form` — under-stating the cost is worse.
 */
function newCredentialIsOneClick(api: SelectedApi, managedOAuthAvailable: boolean): boolean {
	if (!managedOAuthAvailable) return false;
	const types = (api.securitySchemeTypes ?? []).map((t) => t.trim().toLowerCase());
	return types.length > 0 && types.every((t) => t === 'oauth2');
}

/** Classify one pick. */
export function preflightApi(api: SelectedApi, inputs: PreflightInputs): PreflightItem {
	const key = apiRefKey(api);
	const importsApi = api.source === 'catalog' && !api.registered;

	const covering = inputs.bindings.find((b) => b.serves.some((s) => servedCoversPick(s, api)));
	if (covering) {
		return {
			key,
			api,
			outcome: 'attached',
			candidates: [],
			attachedVia: covering.name ?? covering.credentialId,
			importsApi: false,
		};
	}

	const candidates = inputs.credentials.filter((c) => credentialCoversApi(c, api));

	if (candidates.length > 1) {
		return { key, api, outcome: 'choose', candidates, importsApi };
	}
	if (candidates.length === 1) {
		// An OAuth credential whose sign-in never completed is still legitimate reuse —
		// it just costs the consent click it never got.
		const outcome = credentialAwaitsConsent(candidates[0]) ? 'oauth' : 'reuse';
		return { key, api, outcome, candidates, importsApi };
	}

	return {
		key,
		api,
		outcome: newCredentialIsOneClick(api, inputs.managedOAuthAvailable) ? 'oauth' : 'form',
		candidates: [],
		importsApi,
	};
}

/** Classify a whole selection, preserving pick order. */
export function preflightApis(apis: SelectedApi[], inputs: PreflightInputs): PreflightItem[] {
	return apis.map((api) => preflightApi(api, inputs));
}

export interface PreflightTally {
	attached: number;
	reuse: number;
	oauth: number;
	choose: number;
	form: number;
	/** Picks that need a stop in the setup queue — everything except `reuse`
	 *  (bound straight through) and `attached` (already there). */
	queued: number;
	/** Catalog picks that will be imported into the workspace. */
	imports: number;
	/** Picks that can actually be acted on — `attached` excluded. */
	actionable: number;
	total: number;
}

export function preflightTally(items: PreflightItem[]): PreflightTally {
	const tally: PreflightTally = {
		attached: 0,
		reuse: 0,
		oauth: 0,
		choose: 0,
		form: 0,
		queued: 0,
		imports: 0,
		actionable: 0,
		total: items.length,
	};
	for (const item of items) {
		tally[item.outcome] += 1;
		if (item.outcome !== 'attached') tally.actionable += 1;
		if (item.outcome !== 'attached' && item.outcome !== 'reuse') tally.queued += 1;
		if (item.importsApi) tally.imports += 1;
	}
	return tally;
}

/** One-line summary of what a pick costs, shown on its row in the tray. */
export const PREFLIGHT_LABELS: Record<PreflightOutcome, string> = {
	attached: 'Already added',
	reuse: 'Reuses a credential you have',
	oauth: 'One sign-in click',
	choose: 'Pick which credential',
	form: 'Needs a new credential',
};

/** The tally lines, cheapest first, so the cost of the batch reads as a slope.
 * Only non-zero lines are rendered. */
export const PREFLIGHT_TALLY_ORDER: readonly PreflightOutcome[] = [
	'reuse',
	'oauth',
	'choose',
	'form',
	'attached',
];

/** Plural-aware tally copy — the tray's "real cost before committing" line. */
export function preflightTallyLabel(outcome: PreflightOutcome, count: number): string {
	const one = count === 1;
	const subject = `${count} ${one ? 'API' : 'APIs'}`;
	switch (outcome) {
		case 'reuse':
			return `${subject} ${one ? 'reuses' : 'reuse'} a credential you already have`;
		case 'oauth':
			return `${subject} ${one ? 'needs' : 'need'} one sign-in click`;
		case 'choose':
			return `${subject} ${one ? 'needs' : 'need'} you to pick which credential to use`;
		case 'form':
			return one ? '1 API needs a new credential' : `${subject} need new credentials`;
		case 'attached':
			return `${subject} ${one ? 'is' : 'are'} already added`;
	}
}
