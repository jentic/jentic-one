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
import { credentialsBindableBy, type BindViewer } from '@/shared/credentials/lib/bindAuthority';

/**
 * What a pick will cost, worst-to-best as work for the operator. The tray only
 * DESCRIBES the outcome; every decision is made in the setup queue.
 *
 * - `choose` — one or more credentials the viewer may bind cover it. The queue asks which to use
 *   (or to add a new one) — never bound silently, even when only one covers it:
 *   reuse matches API identity, not account, so a wrong-tenant match must be
 *   the operator's call.
 * - `oauth` — none covers it, and a new one is one sign-in click.
 * - `no-auth` — none covers it, and its spec declares no authentication, so the
 *   new credential carries no secret (the queue still says so and lets the
 *   operator set one up for a spec that leaves its auth out).
 * - `form` — none covers it, so a new credential is typed in.
 *
 * An API the agent already reaches is not a dead end: an agent may hold several
 * credentials for one API (one per account), so the pick adds another account
 * and {@link PreflightItem.existing} says which ones it has.
 */
export type PreflightOutcome = 'oauth' | 'choose' | 'no-auth' | 'form';

/** A binding through which the agent already reaches a pick. */
export interface ExistingAccount {
	bindingId: string;
	credentialId: string;
	/** The credential's label, falling back to its id. */
	name: string;
}

export interface PreflightItem {
	/** `vendor/name` identity — the picker's selection key for this API. */
	key: string;
	api: SelectedApi;
	outcome: PreflightOutcome;
	/** Every bindable credential that covers this pick and is not bound to the agent
	 * yet, in list order — the options the queue offers alongside a new credential.
	 * An already-bound credential is left out: binding it again is a 409. Empty for
	 * `oauth` / `form`. */
	covering: Credential[];
	/** The bindings through which the agent already reaches this API, when the pick
	 * adds another account. Empty for an API the agent does not reach yet. The item
	 * is done only once a binding NOT in this list serves the API. */
	existing: ExistingAccount[];
	/** True when accepting this pick imports the API into the workspace. */
	importsApi: boolean;
}

export interface PreflightInputs {
	/** Every org credential — pass a DRAINED list; a first page misclassifies. */
	credentials: Credential[];
	/** The signed-in user. Only credentials they may bind are offered (see
	 * {@link credentialsBindableBy}); `null`/absent = unknown, so nothing is
	 * filtered and the server's 404 stays the backstop. */
	viewer?: BindViewer | null;
	/** The agent's existing bindings, whose `serves` entries say which APIs it
	 *  already reaches (and through which credential). */
	bindings: CredentialBindingEntity[];
	/** A managed OAuth provider is configured, so a new oauth2 credential is a
	 * sign-in click rather than a client-credentials form. */
	managedOAuthAvailable: boolean;
	/** `vendor/name` keys of picks whose read spec declares no authentication.
	 * Absent = not known (yet), which never reads as open. */
	noAuthKeys?: ReadonlySet<string>;
}

/** Does a binding serve this pick? Delegates to {@link apiScopeCovers}, so a
 * vendor-wide binding covers every API of its vendor. */
function bindingServesApi(binding: CredentialBindingEntity, api: SelectedApi): boolean {
	return binding.serves.some((served: ServedApiEntity) => apiScopeCovers(served, api));
}

/** The bindings through which the agent already reaches this API, in list order. */
function accountsServingApi(
	bindings: CredentialBindingEntity[],
	api: SelectedApi,
): ExistingAccount[] {
	return bindings
		.filter((b) => bindingServesApi(b, api))
		.map((b) => ({
			bindingId: b.id,
			credentialId: b.credentialId,
			name: b.name || b.credentialId,
		}));
}

/**
 * The part of an owed setup batch that is still owed. An item is done once a
 * binding it did not start with serves its API (added by the queue, or elsewhere
 * meanwhile) — judged per binding, not per API, so a queued second account is not
 * settled by the first. Done items neither count toward "Finish adding N" nor
 * reopen in the queue. Returns the input array itself when nothing changed, so a
 * caller can compare by reference.
 */
export function stillOwedItems(
	items: PreflightItem[],
	bindings: CredentialBindingEntity[],
): PreflightItem[] {
	const owed = items.filter((item) => {
		const known = new Set(item.existing.map((a) => a.bindingId));
		return !bindings.some((b) => !known.has(b.id) && bindingServesApi(b, item.api));
	});
	return owed.length === items.length ? items : owed;
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
	const existing = accountsServingApi(inputs.bindings, api);

	// Only credentials this viewer may bind: offering another user's credential
	// would end in a 404 on bind and a "Try again" that can never succeed. One
	// already bound to this agent would end in a 409, whatever API it serves.
	const bound = new Set(inputs.bindings.map((b) => b.credentialId));
	const covering = credentialsBindableBy(inputs.credentials, inputs.viewer).filter(
		(c) => !bound.has(c.credential_id) && credentialCoversApi(c, api),
	);
	const outcome: PreflightOutcome =
		covering.length > 0
			? 'choose'
			: newCredentialIsOneClick(api, inputs.managedOAuthAvailable)
				? 'oauth'
				: inputs.noAuthKeys?.has(`${api.vendor}/${api.name}`)
					? 'no-auth'
					: 'form';
	return { key, api, outcome, covering, existing, importsApi };
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
	oauth: number;
	choose: number;
	'no-auth': number;
	form: number;
	/** Picks the agent already reaches — each adds another credential. */
	another: number;
	/** Catalog picks that will be imported into the workspace. */
	imports: number;
	/** Every pick; each one stops in the setup queue. */
	total: number;
}

export function preflightTally(items: PreflightItem[]): PreflightTally {
	const tally: PreflightTally = {
		oauth: 0,
		choose: 0,
		'no-auth': 0,
		form: 0,
		another: 0,
		imports: 0,
		total: items.length,
	};
	for (const item of items) {
		tally[item.outcome] += 1;
		if (item.existing.length > 0) tally.another += 1;
		if (item.importsApi) tally.imports += 1;
	}
	return tally;
}

/** One-line summary of what the next step will ask for, shown on the pick's row
 * in the tray. Informational only — nothing is chosen in the tray. */
export const PREFLIGHT_LABELS: Record<PreflightOutcome, string> = {
	oauth: 'One sign-in click',
	choose: 'Choose a credential in the next step',
	'no-auth': 'No credential needed',
	form: 'Needs a new credential',
};

/** The sub-line under a `choose` row: how many credentials the next step offers. */
export function coveringCountLabel(count: number): string {
	return count === 1
		? '1 of your credentials covers this API — use it or add a new one'
		: `${count} of your credentials cover this API — use one or add a new one`;
}

/** Which accounts the agent already reaches an API through, e.g. `Added via
 * GitHub — personal`. Empty for a pick the agent does not reach yet. */
export function addedViaLabel(existing: ExistingAccount[]): string {
	if (existing.length === 0) return '';
	return `Added via ${existing.map((a) => a.name).join(', ')}`;
}

/** The tally lines, in the order the rows' outcomes are worked. Only non-zero
 * lines are rendered. */
export const PREFLIGHT_TALLY_ORDER: readonly PreflightOutcome[] = [
	'choose',
	'oauth',
	'form',
	'no-auth',
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
		case 'no-auth':
			return `${subject} ${one ? 'declares' : 'declare'} no authentication — no secret to enter`;
	}
}

/**
 * The tally line for picks the agent already reaches. A second credential that
 * ties on scope makes the broker refuse every call that does not name one (409
 * `ambiguous_credential_binding`), so the tally says so before anything is
 * committed.
 */
export function anotherAccountTallyLabel(count: number): string {
	return count === 1
		? '1 API is already added — once another credential is added, calls to it must name one with the Jentic-Credential-Id header, unless one is scoped more narrowly'
		: `${count} APIs are already added — once another credential is added, calls to each must name one with the Jentic-Credential-Id header, unless one is scoped more narrowly`;
}

/** The setup-queue warning for a pick that adds another credential to an API
 * the agent already reaches — see {@link anotherAccountTallyLabel}. */
export function anotherCredentialWarning(apiLabel: string): string {
	return `Once added, calls to ${apiLabel} must name a credential with the Jentic-Credential-Id header, unless one is scoped more narrowly.`;
}
