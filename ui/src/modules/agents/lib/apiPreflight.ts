/**
 * Add-APIs preflight — the pure data layer behind the tray's tally. There is no
 * `Skip for now`, so every pick must end with a credential; each is classified by
 * what the setup queue will ask for, and the tray tallies the classes before
 * anything is committed. Nothing here settles a credential — the queue does,
 * with the operator.
 */
import type { Credential, SelectedApi } from '@/shared/credentials/api';
import { apiRefKey, apiScopeCovers } from '@/shared/credentials/lib/apiIdentity';
import type { CredentialChoice } from '@/shared/credentials/lib/credentialIdentity';
import type { CredentialBindingEntity, ServedApiEntity } from '@/modules/agents/api/types';

/**
 * What a pick will cost, worst-to-best as work for the operator. The tray only
 * DESCRIBES the outcome; every decision is made in the setup queue.
 *
 * - `attached` — this agent already reaches the API; the tray blocks the pick.
 * - `choose` — one or more org credentials cover it. The queue asks which to use
 *   (or to add a new one) — never bound silently, even when only one covers it:
 *   reuse matches API identity, not account, so a wrong-tenant match must be
 *   the operator's call.
 * - `oauth` — none covers it, and a new one is one sign-in click.
 * - `form` — none covers it, so a new credential is typed in.
 */
export type PreflightOutcome = 'attached' | 'oauth' | 'choose' | 'form';

export interface PreflightItem {
	/** `vendor/name` identity — the picker's selection key for this API. */
	key: string;
	api: SelectedApi;
	outcome: PreflightOutcome;
	/** Every org credential that covers this pick, in list order — the options the
	 * queue offers alongside a new credential. Empty for `oauth` / `form`. */
	covering: Credential[];
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
 * Any match is the `choose` case. Health is NOT a filter — it cannot be
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

	const binding = inputs.bindings.find((b) => b.serves.some((s) => servedCoversPick(s, api)));
	if (binding) {
		return {
			key,
			api,
			outcome: 'attached',
			covering: [],
			attachedVia: binding.name ?? binding.credentialId,
			importsApi: false,
		};
	}

	const covering = inputs.credentials.filter((c) => credentialCoversApi(c, api));
	if (covering.length > 0) return { key, api, outcome: 'choose', covering, importsApi };
	return {
		key,
		api,
		outcome: newCredentialIsOneClick(api, inputs.managedOAuthAvailable) ? 'oauth' : 'form',
		covering,
		importsApi,
	};
}

/** The queue pane's starting selection: the lone covering credential is
 * preselected (still only bound once the operator confirms); several wait for a
 * pick; with none there is nothing to select — the pane goes straight to a new
 * credential. */
export function defaultChoice(item: Pick<PreflightItem, 'covering'>): CredentialChoice | null {
	const [only, ...rest] = item.covering;
	return only && rest.length === 0
		? { kind: 'existing', credentialId: only.credential_id }
		: null;
}

/** Classify a whole selection, preserving pick order. */
export function preflightApis(apis: SelectedApi[], inputs: PreflightInputs): PreflightItem[] {
	return apis.map((api) => preflightApi(api, inputs));
}

export interface PreflightTally {
	attached: number;
	oauth: number;
	choose: number;
	form: number;
	/** Catalog picks that will be imported into the workspace. */
	imports: number;
	/** Picks that can actually be acted on — `attached` excluded. Each one stops
	 *  in the setup queue. */
	actionable: number;
	total: number;
}

export function preflightTally(items: PreflightItem[]): PreflightTally {
	const tally: PreflightTally = {
		attached: 0,
		oauth: 0,
		choose: 0,
		form: 0,
		imports: 0,
		actionable: 0,
		total: items.length,
	};
	for (const item of items) {
		tally[item.outcome] += 1;
		if (item.outcome !== 'attached') tally.actionable += 1;
		if (item.importsApi) tally.imports += 1;
	}
	return tally;
}

/** One-line summary of what the next step will ask for, shown on the pick's row
 * in the tray. Informational only — nothing is chosen in the tray. */
export const PREFLIGHT_LABELS: Record<PreflightOutcome, string> = {
	attached: 'Already added',
	oauth: 'One sign-in click',
	choose: 'Choose a credential in the next step',
	form: 'Needs a new credential',
};

/** The sub-line under a `choose` row: how many credentials the next step offers. */
export function coveringCountLabel(count: number): string {
	return count === 1
		? '1 of your credentials covers this API — use it or add a new one'
		: `${count} of your credentials cover this API — use one or add a new one`;
}

/** The tally lines, in the order the rows' outcomes are worked. Only non-zero
 * lines are rendered. */
export const PREFLIGHT_TALLY_ORDER: readonly PreflightOutcome[] = [
	'choose',
	'oauth',
	'form',
	'attached',
];

/** Plural-aware tally copy — what the next step will ask for, before committing. */
export function preflightTallyLabel(outcome: PreflightOutcome, count: number): string {
	const one = count === 1;
	const subject = `${count} ${one ? 'API' : 'APIs'}`;
	switch (outcome) {
		case 'choose':
			return `${subject}: choose from your existing credentials in the next step`;
		case 'oauth':
			return `${subject} ${one ? 'needs' : 'need'} one sign-in click`;
		case 'form':
			return `${subject} ${one ? 'needs' : 'need'} a new credential`;
		case 'attached':
			return `${subject} ${one ? 'is' : 'are'} already added`;
	}
}
