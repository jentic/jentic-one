/**
 * API-tile composition — the pure data layer behind the flat Agents surface.
 *
 * The surface renders "the APIs this agent can reach" as a tile grid, but the
 * backend has no such read: a tile is derived client-side from three sources
 * the app already fetches —
 *
 *   agent → its direct bindings (`GET /agents/{id}/credentials`)
 *         → the org credential each binding wraps (`GET /credentials`)
 *         → the workspace APIs each binding's `serves` entries resolve to
 *           (`GET /apis`).
 *
 * Everything here is a pure function over those three lists so the
 * composition, the not-usable predicate, and the stat math are unit-testable
 * without a DOM or MSW.
 */
import { CredentialType, type ApiResponse, type Credential } from '@/shared/credentials/api';
import type { CredentialBindingEntity, ServedApiEntity } from '@/modules/agents/api/types';

/** One tile on the grid: the API is the card, the credential is a line on it. */
export interface ApiTileModel {
	/** Stable render key — binding id + the resolved API identity. */
	key: string;
	/** Display title: the workspace API's display name, else its registry name. */
	title: string;
	/** The domain line under the title (e.g. `github.com`), falling back to the
	 * vendor when the registry knows no host. */
	host: string;
	/** Workspace icon when the API is imported; null falls back to initials. */
	iconUrl: string | null;
	vendor: string;
	/** API version from the workspace registry; null when not imported. */
	version: string | null;
	/** Human auth-type label derived from the credential (`OAuth 2.0`, …). */
	authLabel: string | null;
	/** Operation count from the workspace registry; null when not imported. */
	operationCount: number | null;
	/** The binding behind this tile — the sidebar's join key back to the
	 * agent's binding list (one binding can fan out to several tiles). */
	bindingId: string;
	credentialId: string;
	credentialName: string;
	/** When the credential was created; null when its org row is unreachable. */
	credentialCreatedAt: string | null;
	/** When the credential was last updated (rotation or edit); null when it
	 * never was, or when its org row is unreachable. */
	credentialUpdatedAt: string | null;
	/** When the binding was created (`bound_at`). */
	boundAt: string;
	/** The binding is soft-suspended — excluded by the broker until resumed. */
	suspended: boolean;
	/**
	 * The credential exists but cannot serve traffic yet (OAuth interactive
	 * sign-in not completed). Drives the dashed treatment.
	 */
	awaitingConsent: boolean;
}

/** Human labels for the credential auth types shown on a tile. */
const AUTH_TILE_LABEL: Partial<Record<string, string>> = {
	[CredentialType.BEARER_TOKEN]: 'Bearer token',
	[CredentialType.API_KEY]: 'API key',
	[CredentialType.BASIC]: 'Basic auth',
	[CredentialType.OAUTH2]: 'OAuth 2.0',
	[CredentialType.SIGV4]: 'AWS SigV4',
	[CredentialType.NO_AUTH]: 'No auth',
};

/**
 * True when the credential exists but is not usable yet: an OAuth 2.0
 * authorization-code credential whose interactive sign-in has not completed.
 *
 * This is the only not-usable case the credential's own redacted state can
 * prove: `details.connected` is served for authorization-code grants (false
 * until the consent round-trip lands, true afterwards) and is null/absent for
 * every other grant and type. Revoked-upstream / failing-refresh states never
 * reach the client, so no other case may render as "not usable".
 */
export function credentialAwaitsConsent(credential: Credential | undefined): boolean {
	if (!credential || credential.type !== CredentialType.OAUTH2) return false;
	const details = credential.details;
	if (!details || typeof details !== 'object') return false;
	return details.grant_type === 'authorization_code' && details.connected === false;
}

/** Does a workspace API row satisfy a binding's served-API reference?
 * `name`/`version` null means "covers all names/versions" (wildcard). */
function servedMatchesApi(served: ServedApiEntity, api: ApiResponse): boolean {
	return (
		api.api.vendor === served.vendor &&
		(served.name == null || api.api.name === served.name) &&
		(served.version == null || api.api.version === served.version)
	);
}

/**
 * Every (binding, resolved API) pair the grid draws a tile for, de-duplicated
 * on the tile's render key.
 *
 * One binding can serve several APIs (wildcards): each resolved workspace API
 * is its own identity. A served reference that matches nothing in the registry
 * is still an identity — built from the reference itself — because the binding
 * is real even when the API's metadata isn't imported here.
 *
 * Shared by the tile composition and the strip's per-agent count, so the count
 * on a tab can never disagree with the number of tiles behind it.
 */
function* tileIdentities(
	bindings: CredentialBindingEntity[],
	apis: ApiResponse[],
): Generator<{
	binding: CredentialBindingEntity;
	served: ServedApiEntity;
	api: ApiResponse | null;
	key: string;
}> {
	const seen = new Set<string>();
	for (const binding of bindings) {
		for (const served of binding.serves) {
			const matches = apis.filter((api) => servedMatchesApi(served, api));
			if (matches.length === 0) {
				const key = `${binding.id}:${served.vendor}/${served.name ?? '*'}/${served.version ?? '*'}`;
				if (seen.has(key)) continue;
				seen.add(key);
				yield { binding, served, api: null, key };
				continue;
			}
			for (const api of matches) {
				const key = `${binding.id}:${api.api.vendor}/${api.api.name}/${api.api.version}`;
				if (seen.has(key)) continue;
				seen.add(key);
				yield { binding, served, api, key };
			}
		}
	}
}

/**
 * Compose the tile list for one agent from its bindings, the org credential
 * list, and the workspace API registry.
 */
export function composeApiTiles(
	bindings: CredentialBindingEntity[],
	credentials: Credential[],
	apis: ApiResponse[],
): ApiTileModel[] {
	const credentialsById = new Map(credentials.map((c) => [c.credential_id, c]));
	const tiles: ApiTileModel[] = [];

	for (const { binding, served, api, key } of tileIdentities(bindings, apis)) {
		const credential = credentialsById.get(binding.credentialId);
		const base = {
			key,
			bindingId: binding.id,
			credentialId: binding.credentialId,
			credentialName: binding.name ?? credential?.name ?? binding.credentialId,
			credentialCreatedAt: credential?.created_at ?? null,
			credentialUpdatedAt: credential?.updated_at ?? null,
			boundAt: binding.boundAt,
			suspended: binding.suspended,
			awaitingConsent: credentialAwaitsConsent(credential),
			authLabel: credential ? (AUTH_TILE_LABEL[credential.type] ?? null) : null,
		};

		if (api == null) {
			// Not imported into this workspace — render from the reference.
			tiles.push({
				...base,
				title: served.name ?? served.vendor,
				host: served.vendor,
				iconUrl: null,
				vendor: served.vendor,
				version: null,
				operationCount: null,
			});
			continue;
		}
		// The registry's own identity pair: the API's name is the title and its
		// host is the domain line. Falling back to the vendor for BOTH would
		// print the same string twice.
		tiles.push({
			...base,
			title: api.display_name ?? api.api.name,
			host: api.api.host ?? api.api.vendor,
			iconUrl: api.icon_url ?? null,
			vendor: api.api.vendor,
			version: api.api.version,
			operationCount: api.operation_count,
		});
	}

	return tiles.sort((a, b) => a.title.localeCompare(b.title));
}

/**
 * How many APIs an agent reaches — the figure on its tab in the strip.
 *
 * Counted over the same identities `composeApiTiles` builds tiles from (and
 * needing no credential join), so the tab's count is exactly the number of
 * tiles the grid will show for that agent.
 */
export function agentApiCount(
	bindings: CredentialBindingEntity[] | undefined,
	apis: ApiResponse[],
): number {
	if (!bindings || bindings.length === 0) return 0;
	return [...tileIdentities(bindings, apis)].length;
}

/** Stat-summary math for the grid's side column. */
export interface ApiTileStats {
	/** Tiles whose credential is usable (not awaiting consent). */
	configured: number;
	/** Tiles waiting on an OAuth sign-in. */
	needsSetup: number;
	/** Total operations exposed by usable tiles with known registry metadata. */
	operations: number;
}

export function tileStats(tiles: ApiTileModel[]): ApiTileStats {
	let configured = 0;
	let needsSetup = 0;
	let operations = 0;
	for (const tile of tiles) {
		if (tile.awaitingConsent) {
			needsSetup += 1;
			continue;
		}
		configured += 1;
		if (tile.operationCount != null && !tile.suspended) operations += tile.operationCount;
	}
	return { configured, needsSetup, operations };
}

/**
 * How many of an agent's bound credentials are not usable yet — the strip
 * pill's "N to set up" hint. Counted per credential (a wildcard binding that
 * fans out to several tiles is still one sign-in to finish).
 */
export function agentSetupGapCount(
	bindings: CredentialBindingEntity[] | undefined,
	credentials: Credential[],
): number {
	if (!bindings || bindings.length === 0) return 0;
	const credentialsById = new Map(credentials.map((c) => [c.credential_id, c]));
	const waiting = new Set<string>();
	for (const binding of bindings) {
		if (credentialAwaitsConsent(credentialsById.get(binding.credentialId))) {
			waiting.add(binding.credentialId);
		}
	}
	return waiting.size;
}
