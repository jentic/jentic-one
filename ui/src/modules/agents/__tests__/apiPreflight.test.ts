/**
 * Unit specs for the Add-APIs preflight — the classification rules the tray's
 * tally rests on. The negative rules carry the most weight: identity matching must
 * not collapse two accounts of one vendor into one another, must never settle a
 * credential on the operator's behalf, must not exclude a
 * credential for looking unhealthy, and must not promise "one click" unprovably.
 */
import { describe, it, expect } from 'vitest';
import {
	credentialCoversApi,
	coveringCountLabel,
	defaultChoice,
	preflightApi,
	preflightApis,
	preflightTally,
	preflightTallyLabel,
	stillOwedItems,
	type PreflightInputs,
} from '@/modules/agents/lib/apiPreflight';
import { apiRefKey } from '@/shared/credentials/lib/apiIdentity';
import type { CredentialBindingEntity } from '@/modules/agents/api';
import { CredentialType, type Credential, type SelectedApi } from '@/shared/credentials/api';

function makeCredential(over: Partial<Credential> = {}): Credential {
	return {
		credential_id: 'cred_1',
		name: 'Stripe — Production',
		type: CredentialType.BEARER_TOKEN,
		provider: 'static',
		api: { vendor: 'stripe.com', name: 'main', version: '1.0.0' },
		active: true,
		details: { hint: '••••' },
		provider_account_ref: null,
		created_at: '2026-01-01T00:00:00Z',
		updated_at: null,
		...over,
	};
}

function makeBinding(over: Partial<CredentialBindingEntity> = {}): CredentialBindingEntity {
	return {
		id: 'acb_1',
		credentialId: 'cred_1',
		name: 'Stripe — Production',
		suspended: false,
		ruleSetId: null,
		boundAt: '2026-01-02T00:00:00Z',
		serves: [{ vendor: 'stripe.com', name: 'main', version: null }],
		...over,
	};
}

function makePick(over: Partial<SelectedApi> = {}): SelectedApi {
	return {
		source: 'local',
		vendor: 'stripe.com',
		name: 'main',
		version: '1.0.0',
		label: 'Stripe',
		...over,
	};
}

function inputs(over: Partial<PreflightInputs> = {}): PreflightInputs {
	return { credentials: [], bindings: [], managedOAuthAvailable: false, ...over };
}

describe('credentialCoversApi', () => {
	it('treats a pinned version as pinned, and an absent one as spanning revisions', () => {
		// The broker resolves a binding with `credential_covers`, where a pinned
		// version is pinned. Offering a cross-revision match here would
		// bind a credential the broker then refuses with an identity mismatch.
		const pinned = makeCredential({
			api: { vendor: 'stripe.com', name: 'main', version: '2.0.0' },
		});
		expect(credentialCoversApi(pinned, makePick({ version: '1.0.0' }))).toBe(false);
		expect(credentialCoversApi(pinned, makePick({ version: '2.0.0' }))).toBe(true);

		// A credential meant to span revisions carries no version. The backend stores
		// NULL and serialises it as `""`, so the empty string must read as "any
		// revision".
		const spanning = makeCredential({
			api: { vendor: 'stripe.com', name: 'main', version: '' },
		});
		expect(credentialCoversApi(spanning, makePick({ version: '1.0.0' }))).toBe(true);
		expect(credentialCoversApi(spanning, makePick({ version: '2.0.0' }))).toBe(true);
	});

	it('does not match on vendor alone', () => {
		const credential = makeCredential({
			api: { vendor: 'stripe.com', name: 'connect', version: '1.0.0' },
		});
		expect(credentialCoversApi(credential, makePick({ name: 'main' }))).toBe(false);
	});

	it('normalises case so a catalog-derived vendor meets a workspace one', () => {
		const credential = makeCredential({
			api: { vendor: 'GitHub.com', name: 'Main', version: '1.0.0' },
		});
		const pick = makePick({ source: 'catalog', vendor: 'github.com', name: 'main' });
		expect(credentialCoversApi(credential, pick)).toBe(true);
	});

	it('prefers catalog identity when both sides recorded one', () => {
		// Same humanised vendor/name, different catalog slugs — different APIs.
		const credential = makeCredential({
			catalog_api_id: 'nytimes.com/books',
			api: { vendor: 'nytimes.com', name: 'main', version: '1.0.0' },
		});
		const pick = makePick({
			apiId: 'nytimes.com/article_search',
			vendor: 'nytimes.com',
			name: 'main',
		});
		expect(credentialCoversApi(credential, pick)).toBe(false);
		expect(credentialCoversApi(credential, { ...pick, apiId: 'nytimes.com/books' })).toBe(true);
	});

	it('falls back to vendor/name when only one side has a slug', () => {
		const credential = makeCredential({ catalog_api_id: 'stripe.com' });
		expect(credentialCoversApi(credential, makePick({ apiId: undefined }))).toBe(true);
	});
});

describe('preflightApi', () => {
	it('one matching credential is still a choice — never reused silently', () => {
		const item = preflightApi(makePick(), inputs({ credentials: [makeCredential()] }));
		expect(item.outcome).toBe('choose');
		expect(item.covering.map((c) => c.credential_id)).toEqual(['cred_1']);
	});

	it('does not filter covering credentials on health', () => {
		// `active: false` is the closest thing to "looks broken" a redacted credential
		// exposes. It must still count: we cannot detect broken, so filtering on it
		// hides a valid option while implying the survivors are fine.
		const item = preflightApi(
			makePick(),
			inputs({ credentials: [makeCredential({ active: false })] }),
		);
		expect(item.outcome).toBe('choose');
		expect(item.covering).toHaveLength(1);
	});

	it('two credentials for the same API must be chosen between, never guessed', () => {
		const production = makeCredential({ credential_id: 'cred_1', name: 'Stripe — Production' });
		const sandbox = makeCredential({ credential_id: 'cred_2', name: 'Stripe — Sandbox' });
		const item = preflightApi(makePick(), inputs({ credentials: [production, sandbox] }));
		expect(item.outcome).toBe('choose');
		expect(item.covering.map((c) => c.credential_id)).toEqual(['cred_1', 'cred_2']);
	});

	it('an unconnected OAuth credential is offered like any other covering one', () => {
		// Its outstanding sign-in is the queue pane's business once it is chosen.
		const credential = makeCredential({
			type: CredentialType.OAUTH2,
			details: { grant_type: 'authorization_code', connected: false },
		});
		const item = preflightApi(makePick(), inputs({ credentials: [credential] }));
		expect(item.outcome).toBe('choose');
		expect(item.covering).toHaveLength(1);
	});

	it('no candidate needs a new credential', () => {
		const item = preflightApi(makePick(), inputs());
		expect(item.outcome).toBe('form');
		expect(item.covering).toEqual([]);
	});

	it('an oauth2-only API is one click only when a managed provider is configured', () => {
		const pick = makePick({ securitySchemeTypes: ['oauth2'] });
		// Direct OAuth2 still needs client id, secret and URLs typed in.
		expect(preflightApi(pick, inputs()).outcome).toBe('form');
		expect(preflightApi(pick, inputs({ managedOAuthAvailable: true })).outcome).toBe('oauth');
	});

	it('a mixed-scheme API is a form even with a managed provider', () => {
		const pick = makePick({ securitySchemeTypes: ['oauth2', 'apiKey'] });
		expect(preflightApi(pick, inputs({ managedOAuthAvailable: true })).outcome).toBe('form');
	});

	it('a catalog pick with no scheme hint is a form, not a promise', () => {
		const pick = makePick({ source: 'catalog', securitySchemeTypes: undefined });
		expect(preflightApi(pick, inputs({ managedOAuthAvailable: true })).outcome).toBe('form');
	});

	it('an API the agent already reaches is attached, and names the credential', () => {
		const item = preflightApi(
			makePick(),
			inputs({ credentials: [makeCredential()], bindings: [makeBinding()] }),
		);
		expect(item.outcome).toBe('attached');
		expect(item.attachedVia).toBe('Stripe — Production');
		expect(item.importsApi).toBe(false);
	});

	it('a vendor-wildcard binding covers every API of that vendor', () => {
		const binding = makeBinding({
			serves: [{ vendor: 'stripe.com', name: null, version: null }],
		});
		const item = preflightApi(makePick({ name: 'connect' }), inputs({ bindings: [binding] }));
		expect(item.outcome).toBe('attached');
	});

	it('a binding for a different API of the same vendor does not count as attached', () => {
		const binding = makeBinding({
			serves: [{ vendor: 'stripe.com', name: 'connect', version: null }],
		});
		expect(
			preflightApi(makePick({ name: 'main' }), inputs({ bindings: [binding] })).outcome,
		).toBe('form');
	});

	it('flags an unregistered catalog pick as an import', () => {
		const fresh = makePick({ source: 'catalog', registered: false, apiId: 'stripe.com' });
		const already = makePick({ source: 'catalog', registered: true, apiId: 'stripe.com' });
		expect(preflightApi(fresh, inputs()).importsApi).toBe(true);
		expect(preflightApi(already, inputs()).importsApi).toBe(false);
		expect(preflightApi(makePick({ source: 'local' }), inputs()).importsApi).toBe(false);
	});

	it('lists every covering credential, whatever the outcome', () => {
		const production = makeCredential({ credential_id: 'cred_1' });
		const sandbox = makeCredential({ credential_id: 'cred_2' });
		const other = makeCredential({
			credential_id: 'cred_3',
			api: { vendor: 'slack.com', name: 'main', version: '1.0.0' },
		});
		const item = preflightApi(
			makePick(),
			inputs({ credentials: [production, sandbox, other] }),
		);
		expect(item.covering.map((c) => c.credential_id)).toEqual(['cred_1', 'cred_2']);
	});

	it('a managed-OAuth API that a credential already covers is a choice, not a click', () => {
		const pick = makePick({ securitySchemeTypes: ['oauth2'] });
		const item = preflightApi(
			pick,
			inputs({ credentials: [makeCredential()], managedOAuthAvailable: true }),
		);
		expect(item.outcome).toBe('choose');
	});

	it('keys each item by its canonical vendor/name identity', () => {
		const pick = makePick({ vendor: 'GitHub.com', name: 'Main' });
		expect(preflightApi(pick, inputs()).key).toBe(apiRefKey(pick));
		// Slug form, so a raw domain and its stored spelling key alike.
		expect(preflightApi(pick, inputs()).key).toBe('github-com/main');
	});
});

describe('defaultChoice', () => {
	it('preselects a lone covering credential, and nothing among several or none', () => {
		const production = makeCredential({ credential_id: 'cred_1' });
		const sandbox = makeCredential({ credential_id: 'cred_2' });
		const one = preflightApi(makePick(), inputs({ credentials: [production] }));
		expect(defaultChoice(one)).toEqual({ kind: 'existing', credentialId: 'cred_1' });

		const several = preflightApi(makePick(), inputs({ credentials: [production, sandbox] }));
		expect(defaultChoice(several)).toBeNull();

		// No covering credential: nothing to choose between.
		expect(defaultChoice(preflightApi(makePick(), inputs()))).toBeNull();
	});
});

describe('preflightTally', () => {
	it('counts each class and derives what the queue has to do', () => {
		const coveringCred = makeCredential();
		const items = preflightApis(
			[
				makePick({ vendor: 'stripe.com', name: 'main' }),
				makePick({ vendor: 'slack.com', name: 'main' }),
				makePick({
					source: 'catalog',
					vendor: 'notion.so',
					name: 'main',
					apiId: 'notion.so',
					registered: false,
				}),
			],
			inputs({ credentials: [coveringCred] }),
		);
		const tally = preflightTally(items);

		expect(tally).toMatchObject({
			choose: 1,
			form: 2,
			oauth: 0,
			attached: 0,
			total: 3,
			imports: 1,
		});
		// Every actionable pick stops in the queue — the covered one included.
		expect(tally.actionable).toBe(3);
	});

	it('already-attached picks are counted but never actionable', () => {
		const items = preflightApis(
			[makePick()],
			inputs({ credentials: [makeCredential()], bindings: [makeBinding()] }),
		);
		const tally = preflightTally(items);
		expect(tally.attached).toBe(1);
		expect(tally.actionable).toBe(0);
	});
});

describe('preflightTallyLabel', () => {
	it('reads correctly for one and for many', () => {
		expect(preflightTallyLabel('choose', 1)).toBe(
			'1 API: choose from your existing credentials in the next step',
		);
		expect(preflightTallyLabel('choose', 3)).toBe(
			'3 APIs: choose from your existing credentials in the next step',
		);
		expect(preflightTallyLabel('form', 1)).toBe('1 API needs a new credential');
		expect(preflightTallyLabel('form', 2)).toBe('2 APIs need a new credential');
		expect(preflightTallyLabel('attached', 1)).toBe('1 API is already added');
		expect(preflightTallyLabel('attached', 2)).toBe('2 APIs are already added');
	});
});

describe('coveringCountLabel', () => {
	it('says how many existing credentials the next step offers', () => {
		expect(coveringCountLabel(1)).toBe(
			'1 of your credentials covers this API — use it or add a new one',
		);
		expect(coveringCountLabel(2)).toBe(
			'2 of your credentials cover this API — use one or add a new one',
		);
	});
});

describe('stillOwedItems', () => {
	const stripe = preflightApi(makePick(), inputs());
	const notion = preflightApi(makePick({ vendor: 'notion.so', label: 'Notion' }), inputs());

	it('drops the items a live binding now serves, keeping the rest in order', () => {
		expect(stillOwedItems([stripe, notion], [makeBinding()])).toEqual([notion]);
	});

	it('hands back the same array when nothing is served, so a caller can compare by reference', () => {
		const batch = [stripe, notion];
		expect(stillOwedItems(batch, [])).toBe(batch);
		// A binding serving nothing (a deleted credential's leftover) reaches nothing.
		expect(stillOwedItems(batch, [makeBinding({ serves: [] })])).toBe(batch);
	});
});
