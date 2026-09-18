/**
 * Add-APIs preflight — the pure data layer behind the tray's tally (plan §4.4).
 *
 * Because there is no `Skip for now` (D13), every API the operator picks must
 * end the flow with a credential. That makes the path longer than a skip-based
 * one, and the only thing that keeps it short is telling the truth up front:
 * each pick is classified by what it will actually cost, and the tray tallies
 * the classes so the real cost is visible BEFORE anything is committed.
 *
 * Everything here is a pure function over lists the surface already fetches
 * (the org credential list, the agent's bindings), so the classification rules
 * are unit-testable without a DOM or MSW.
 */
import type { Credential, SelectedApi } from '@/shared/credentials/api';
import { apiRefKey } from '@/shared/credentials/lib/apiIdentity';
import { credentialAwaitsConsent } from '@/modules/agents/lib/apiTiles';
import type { CredentialBindingEntity, ServedApiEntity } from '@/modules/agents/api/types';

/**
 * What a pick will cost, worst-to-best understood as work for the operator.
 *
 * - `attached` — this agent already reaches the API. Nothing to do, and
 *   re-binding would duplicate; the tray blocks the pick instead of queueing it.
 * - `reuse` — exactly one existing org credential covers the API. Bind it and
 *   move on; no queue step at all. This is the branch the whole flow leans on.
 * - `oauth` — one sign-in click finishes it.
 * - `choose` — several org credentials cover the API, so the queue must ask
 *   which one. Cheap, but not free, and never silently guessed.
 * - `form` — needs a new credential typed in.
 */
export type PreflightOutcome = 'attached' | 'reuse' | 'oauth' | 'choose' | 'form';

export interface PreflightItem {
	/** `vendor/name` identity — the picker's selection key for this API. */
	key: string;
	api: SelectedApi;
	outcome: PreflightOutcome;
	/**
	 * Org credentials whose API identity covers this pick, in list order.
	 * One for `reuse`, several for `choose`, and for `oauth` the single
	 * unconnected credential whose sign-in is the remaining click. Empty when
	 * the pick needs a brand-new credential.
	 */
	candidates: Credential[];
	/** `attached` only: the credential the agent already reaches this API
	 *  through, so the row can say which binding is in the way. */
	attachedVia?: string;
	/** True when accepting this pick imports the API into the workspace (D5). */
	importsApi: boolean;
}

export interface PreflightInputs {
	/** Every org credential — pass a DRAINED list; a first page misclassifies. */
	credentials: Credential[];
	/** The agent's existing bindings, whose `serves` entries say which APIs it
	 *  already reaches (and through which credential). */
	bindings: CredentialBindingEntity[];
	/**
	 * A managed OAuth provider is configured on this server, so a brand-new
	 * oauth2 credential is a sign-in click rather than a client-credentials
	 * form. Read from `GET /credentials/providers`.
	 */
	managedOAuthAvailable: boolean;
}

/**
 * Does a binding's served reference already cover this pick?
 *
 * `served.name == null` is the vendor wildcard (the binding serves every API of
 * that vendor). Version is ignored for the same reason {@link apiRefKey}
 * ignores it — a revision is the same API. Comparison is normalised because a
 * pick's vendor can arrive from a workspace row (the backend's exact casing) or
 * from a catalog slug (lower-cased), and the two must still meet.
 */
function servedCoversPick(served: ServedApiEntity, api: SelectedApi): boolean {
	const norm = (s: string): string => s.trim().toLowerCase();
	if (norm(served.vendor) !== norm(api.vendor)) return false;
	return served.name == null || norm(served.name) === norm(api.name);
}

/**
 * Does an org credential cover this API?
 *
 * Match on API IDENTITY, and never on vendor alone: "Stripe — Production" and
 * "Stripe — Sandbox" are not interchangeable. Both would match here, which is
 * correct — two matches is the `choose` case, and the queue asks.
 *
 * Catalog identity wins when both sides recorded one: two different catalog
 * slugs are two different APIs even if their vendor/name humanise alike. When
 * either side has no slug we fall back to `vendor/name`.
 *
 * Health is NOT a filter (D2b). We cannot reliably detect a broken credential
 * (D2a), so filtering on it would block valid reuse while implying the
 * survivors are healthy.
 */
export function credentialCoversApi(credential: Credential, api: SelectedApi): boolean {
	if (credential.catalog_api_id && api.apiId) {
		return credential.catalog_api_id.trim().toLowerCase() === api.apiId.trim().toLowerCase();
	}
	return apiRefKey(credential.api) === apiRefKey(api);
}

/**
 * Can a brand-new credential for this API be finished with a sign-in click
 * instead of a form?
 *
 * Only when the spec says OAuth 2.0 is the sole scheme AND the server has a
 * managed provider configured (the provider owns the vendor client, so the
 * operator consents and nothing is typed). Direct OAuth2 still needs client id,
 * secret and URLs — that is a form.
 *
 * The check is deliberately conservative: a catalog pick carries no scheme hint
 * until its spec is fetched, so it lands in `form`. Over-stating the cost is
 * safe here; under-stating it is the thing that makes the tally worthless.
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
		// An OAuth credential whose interactive sign-in never completed is the
		// one not-usable state a redacted credential can prove. It is still a
		// legitimate reuse — it just costs the consent click it never got.
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
	/** Catalog picks that will be imported into the workspace (D5). */
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

/**
 * The tally lines, in the order the tray shows them: cheapest first, so the
 * cost of the batch reads as a slope rather than a list. Only non-zero lines
 * are rendered.
 */
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
