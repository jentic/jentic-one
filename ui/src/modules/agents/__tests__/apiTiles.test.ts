/**
 * Unit specs for the flat surface's pure composition layer: tile derivation
 * (agent → bindings → credential → workspace APIs), the not-usable predicate,
 * and the stat/gap math. No DOM, no MSW — plain functions.
 */
import { describe, it, expect } from 'vitest';
import {
	agentApiCount,
	agentSetupGapCount,
	composeApiTiles,
	isOrphanBinding,
	partitionBindings,
	provenOrphanCredentialIds,
	tileStats,
} from '@/modules/agents/lib/apiTiles';
import { credentialAwaitsConsent } from '@/shared/credentials/lib/credentialIdentity';
import type { CredentialBindingEntity } from '@/modules/agents/api';
import { CredentialType, type ApiResponse, type Credential } from '@/shared/credentials/api';

function makeCredential(over: Partial<Credential> = {}): Credential {
	return {
		credential_id: 'cred_1',
		name: 'Test credential',
		type: CredentialType.BEARER_TOKEN,
		provider: 'manual',
		api: { vendor: 'slack.com', name: 'default', version: '1.0.0' },
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
		name: 'Test credential',
		suspended: false,
		ruleSetId: null,
		boundAt: '2026-01-02T00:00:00Z',
		serves: [{ vendor: 'slack.com', name: null, version: null }],
		...over,
	};
}

function makeApi(over: {
	vendor: string;
	name?: string;
	version?: string;
	host?: string | null;
	display_name?: string | null;
	operation_count?: number;
	icon_url?: string | null;
}): ApiResponse {
	return {
		_links: { self: '/apis/x', openapi: '/apis/x/openapi' },
		api: {
			vendor: over.vendor,
			name: over.name ?? 'default',
			version: over.version ?? '1.0.0',
			host: over.host ?? null,
		},
		catalog_api_id: null,
		created_at: '2026-01-01T00:00:00Z',
		current_revision_id: null,
		description: null,
		display_name: over.display_name ?? null,
		icon_url: over.icon_url ?? null,
		operation_count: over.operation_count ?? 0,
		revision_count: 1,
		security_schemes: [],
		updated_at: '2026-01-01T00:00:00Z',
	} as unknown as ApiResponse;
}

describe('credentialAwaitsConsent', () => {
	it('is true only for an authorization-code OAuth credential not yet connected', () => {
		const waiting = makeCredential({
			type: CredentialType.OAUTH2,
			details: { grant_type: 'authorization_code', connected: false },
		});
		expect(credentialAwaitsConsent(waiting)).toBe(true);
	});

	it('is false once the sign-in completed', () => {
		const connected = makeCredential({
			type: CredentialType.OAUTH2,
			details: { grant_type: 'authorization_code', connected: true },
		});
		expect(credentialAwaitsConsent(connected)).toBe(false);
	});

	it('is false for client-credentials OAuth (no interactive consent exists)', () => {
		const machine = makeCredential({
			type: CredentialType.OAUTH2,
			details: { grant_type: 'client_credentials', connected: null },
		});
		expect(credentialAwaitsConsent(machine)).toBe(false);
	});

	it('is false for non-OAuth types and unknown credentials', () => {
		expect(credentialAwaitsConsent(makeCredential())).toBe(false);
		expect(credentialAwaitsConsent(undefined)).toBe(false);
		// Missing details prove nothing → render normally, never invent health.
		expect(
			credentialAwaitsConsent(makeCredential({ type: CredentialType.OAUTH2, details: null })),
		).toBe(false);
	});
});

describe('composeApiTiles', () => {
	it('resolves a wildcard serves entry to every matching workspace API', () => {
		const apis = [
			makeApi({ vendor: 'slack.com', display_name: 'Slack', operation_count: 181 }),
			makeApi({
				vendor: 'slack.com',
				name: 'admin',
				display_name: 'Slack Admin',
				operation_count: 40,
			}),
			makeApi({ vendor: 'github.com', display_name: 'GitHub', operation_count: 900 }),
		];
		const tiles = composeApiTiles([makeBinding()], [makeCredential()], apis);
		expect(tiles.map((t) => t.title)).toEqual(['Slack', 'Slack Admin']);
		expect(tiles[0]).toMatchObject({
			host: 'slack.com',
			authLabel: 'Bearer token',
			operationCount: 181,
			credentialName: 'Test credential',
			awaitingConsent: false,
		});
	});

	it('respects a pinned name/version in the serves entry', () => {
		const apis = [
			makeApi({ vendor: 'slack.com', display_name: 'Slack' }),
			makeApi({ vendor: 'slack.com', name: 'admin', display_name: 'Slack Admin' }),
		];
		const binding = makeBinding({
			serves: [{ vendor: 'slack.com', name: 'admin', version: null }],
		});
		const tiles = composeApiTiles([binding], [makeCredential()], apis);
		expect(tiles.map((t) => t.title)).toEqual(['Slack Admin']);
	});

	it('titles a tile with the shared humaniser and puts the domain beside it', () => {
		// The title is the SAME friendly name the credential picker and the
		// workspace cards show for this API — never the raw registry slug — and
		// the real domain is the line underneath it.
		const named = makeApi({
			vendor: 'github-com',
			name: 'github-com-issues-api',
			version: '1.1.4',
			host: 'github.com',
		});
		const binding = makeBinding({
			serves: [{ vendor: 'github-com', name: null, version: null }],
		});
		const [tile] = composeApiTiles([binding], [makeCredential()], [named]);
		expect(tile).toMatchObject({ title: 'Issues Api', host: 'github.com', version: '1.1.4' });

		// A user-set label wins over the humanised tuple, and with no host the
		// vendor is the honest domain the registry can prove.
		const labelled = makeApi({
			vendor: 'github-com',
			name: 'main',
			host: null,
			display_name: 'GitHub',
		});
		const [fallback] = composeApiTiles([binding], [makeCredential()], [labelled]);
		expect(fallback).toMatchObject({ title: 'GitHub', host: 'github-com' });
	});

	it('still renders a tile when the served API is not in the workspace registry', () => {
		const tiles = composeApiTiles([makeBinding()], [makeCredential()], []);
		expect(tiles).toHaveLength(1);
		// Only the machine tuple is known here, so the humaniser titles it and
		// the vendor stands in for the domain line.
		expect(tiles[0]).toMatchObject({
			title: 'Slack.Com',
			host: 'slack.com',
			operationCount: null,
		});
	});

	it('marks tiles of an OAuth credential awaiting consent and keeps others solid', () => {
		const credentials = [
			makeCredential({ credential_id: 'cred_ok' }),
			makeCredential({
				credential_id: 'cred_wait',
				type: CredentialType.OAUTH2,
				details: { grant_type: 'authorization_code', connected: false },
			}),
		];
		const bindings = [
			makeBinding({ id: 'acb_ok', credentialId: 'cred_ok' }),
			makeBinding({
				id: 'acb_wait',
				credentialId: 'cred_wait',
				serves: [{ vendor: 'github.com', name: null, version: null }],
			}),
		];
		const tiles = composeApiTiles(bindings, credentials, []);
		const byVendor = new Map(tiles.map((t) => [t.vendor, t]));
		expect(byVendor.get('slack.com')?.awaitingConsent).toBe(false);
		expect(byVendor.get('github.com')?.awaitingConsent).toBe(true);
	});

	it('falls back to the binding name, then the credential id, for the credential line', () => {
		const anonymous = makeBinding({ name: null, credentialId: 'cred_missing' });
		const tiles = composeApiTiles([anonymous], [], []);
		expect(tiles[0]?.credentialName).toBe('cred_missing');
	});
});

describe('tileStats / agentSetupGapCount', () => {
	it('splits configured vs needs-setup and sums usable operations', () => {
		const apis = [
			makeApi({ vendor: 'slack.com', display_name: 'Slack', operation_count: 100 }),
			makeApi({ vendor: 'github.com', display_name: 'GitHub', operation_count: 50 }),
		];
		const credentials = [
			makeCredential({ credential_id: 'cred_ok' }),
			makeCredential({
				credential_id: 'cred_wait',
				type: CredentialType.OAUTH2,
				details: { grant_type: 'authorization_code', connected: false },
			}),
		];
		const bindings = [
			makeBinding({ id: 'acb_ok', credentialId: 'cred_ok' }),
			makeBinding({
				id: 'acb_wait',
				credentialId: 'cred_wait',
				serves: [{ vendor: 'github.com', name: null, version: null }],
			}),
		];
		const stats = tileStats(composeApiTiles(bindings, credentials, apis));
		// The awaiting-consent GitHub tile contributes to needsSetup, not ops.
		expect(stats).toEqual({
			configured: 1,
			needsSetup: 1,
			operations: 100,
			operationsAtLeast: false,
		});
		expect(agentSetupGapCount(bindings, credentials)).toBe(1);
	});

	it('excludes suspended bindings from the operations sum but keeps them configured', () => {
		const apis = [
			makeApi({ vendor: 'slack.com', display_name: 'Slack', operation_count: 100 }),
		];
		const suspended = makeBinding({ suspended: true });
		const stats = tileStats(composeApiTiles([suspended], [makeCredential()], apis));
		expect(stats).toEqual({
			configured: 1,
			needsSetup: 0,
			operations: 0,
			operationsAtLeast: false,
		});
	});

	it('withholds the operations figure when no usable tile proves a count', () => {
		// A binding whose API the registry does not describe: the tile renders,
		// but nothing states how many operations it exposes. A `0` would claim
		// the agent can call nothing, so the figure is withheld instead.
		const stats = tileStats(composeApiTiles([makeBinding()], [makeCredential()], []));
		expect(stats).toEqual({
			configured: 1,
			needsSetup: 0,
			operations: null,
			operationsAtLeast: false,
		});
	});

	it('sums the counts it has when only some tiles withhold theirs', () => {
		const apis = [
			makeApi({ vendor: 'slack.com', display_name: 'Slack', operation_count: 100 }),
		];
		const bindings = [
			makeBinding({ id: 'acb_known' }),
			makeBinding({
				id: 'acb_unknown',
				serves: [{ vendor: 'unlisted.example', name: null, version: null }],
			}),
		];
		const stats = tileStats(composeApiTiles(bindings, [makeCredential()], apis));
		// …and flags the sum as a floor, so the line can print `100+` rather than
		// offer a partial total as the whole truth.
		expect(stats).toEqual({
			configured: 2,
			needsSetup: 0,
			operations: 100,
			operationsAtLeast: true,
		});
	});

	it('counts a multi-API credential as one setup gap', () => {
		const credentials = [
			makeCredential({
				credential_id: 'cred_wait',
				type: CredentialType.OAUTH2,
				details: { grant_type: 'authorization_code', connected: false },
			}),
		];
		const binding = makeBinding({
			credentialId: 'cred_wait',
			serves: [
				{ vendor: 'slack.com', name: null, version: null },
				{ vendor: 'slack.com', name: 'admin', version: null },
			],
		});
		expect(agentSetupGapCount([binding], credentials)).toBe(1);
		expect(agentSetupGapCount(undefined, credentials)).toBe(0);
	});

	it('agrees with the strip hint when one credential fans out to several tiles', () => {
		// The two figures render at once — the tab's "· N to set up" and the stat
		// line's "N to set up" — so counting tiles in one and credentials in the
		// other puts two different numbers under the same words on one screen.
		const apis = [
			makeApi({ vendor: 'slack.com', display_name: 'Slack' }),
			makeApi({ vendor: 'slack.com', name: 'admin', display_name: 'Slack Admin' }),
		];
		const credentials = [
			makeCredential({
				credential_id: 'cred_wait',
				type: CredentialType.OAUTH2,
				details: { grant_type: 'authorization_code', connected: false },
			}),
		];
		// One wildcard binding, two tiles, one sign-in to finish.
		const bindings = [
			makeBinding({
				credentialId: 'cred_wait',
				serves: [{ vendor: 'slack.com', name: null, version: null }],
			}),
		];
		const tiles = composeApiTiles(bindings, credentials, apis);
		expect(tiles).toHaveLength(2);
		expect(tileStats(tiles).needsSetup).toBe(agentSetupGapCount(bindings, credentials));
		expect(tileStats(tiles).needsSetup).toBe(1);
	});
});

describe('agentApiCount', () => {
	const apis = [
		makeApi({ vendor: 'slack.com', display_name: 'Slack' }),
		makeApi({ vendor: 'slack.com', name: 'admin', display_name: 'Slack Admin' }),
	];

	it('counts exactly what the grid renders, without the credential join', () => {
		// The band's count and the grid must never disagree, so the count runs
		// the same identity/dedup pass as the tiles — it just needs no
		// credential to do it.
		const bindings = [makeBinding()];
		expect(agentApiCount(bindings, apis)).toBe(
			composeApiTiles(bindings, [makeCredential()], apis).length,
		);
	});

	it('counts one API twice when two credentials serve it, and an unknown API once', () => {
		// A tile is an API *through a credential* — two credentials serving the
		// same API are two tiles with their own rules, so two is the count the
		// grid shows. Within one binding a repeated serves entry still collapses.
		const shared = [
			makeBinding({ serves: [{ vendor: 'slack.com', name: 'admin', version: null }] }),
			makeBinding({
				id: 'acb_2',
				credentialId: 'cred_2',
				serves: [{ vendor: 'slack.com', name: 'admin', version: null }],
			}),
		];
		expect(agentApiCount(shared, apis)).toBe(2);
		expect(agentApiCount(shared, apis)).toBe(
			composeApiTiles(shared, [makeCredential()], apis).length,
		);

		const duplicated = makeBinding({
			serves: [
				{ vendor: 'slack.com', name: 'admin', version: null },
				{ vendor: 'slack.com', name: 'admin', version: '1.0.0' },
			],
		});
		expect(agentApiCount([duplicated], apis)).toBe(1);

		// Not in the registry — still one API this agent can reach.
		expect(agentApiCount([makeBinding()], [])).toBe(1);
	});

	it('is zero for an agent with no bindings', () => {
		expect(agentApiCount([], apis)).toBe(0);
		expect(agentApiCount(undefined, apis)).toBe(0);
	});
});

describe('orphan bindings (credential deleted, #1426)', () => {
	const orphan = makeBinding({
		id: 'acb_dead',
		credentialId: 'cred_gone',
		name: null,
		serves: [],
	});

	it('flags the backend shape — no name, serving nothing — even before the credentials drain', () => {
		expect(isOrphanBinding(orphan)).toBe(true);
	});

	it('flags a named binding serving nothing once a complete list lacks its credential', () => {
		const named = makeBinding({ credentialId: 'cred_gone', name: 'Old key', serves: [] });
		expect(isOrphanBinding(named, new Map(), false)).toBe(false);
		expect(isOrphanBinding(named, new Map(), true)).toBe(true);
	});

	it('never flags a binding that still serves an API, even against a stale list', () => {
		const fresh = makeBinding({ credentialId: 'cred_new' });
		expect(isOrphanBinding(fresh, new Map(), true)).toBe(false);
	});

	it('partitions live bindings from orphans and draws no tile for an orphan', () => {
		const live = makeBinding();
		const { live: kept, orphans } = partitionBindings([live, orphan], [makeCredential()], true);
		expect(kept).toEqual([live]);
		expect(orphans).toEqual([orphan]);
		expect(composeApiTiles([orphan], [], [])).toEqual([]);
	});

	it('proves an orphan for purging only against a complete credentials list', () => {
		const named = makeBinding({ credentialId: 'cred_gone', name: 'Old key', serves: [] });
		// A null name hides a binding, but only a complete list may delete it.
		expect(provenOrphanCredentialIds([orphan], [], false)).toEqual([]);
		expect(provenOrphanCredentialIds([orphan, named], [], true)).toEqual([
			'cred_gone',
			'cred_gone',
		]);
		// A credential the complete list still has is not gone, whatever the name says.
		const listed = makeCredential({ credential_id: 'cred_gone' });
		expect(provenOrphanCredentialIds([orphan], [listed], true)).toEqual([]);
	});
});
