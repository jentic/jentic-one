/**
 * API-tile composition — the pure data layer behind the flat Agents surface.
 *
 * The backend has no "APIs this agent can reach" read, so a tile is derived from
 * three lists the app already fetches: the agent's bindings, the org credential
 * each wraps, and the workspace APIs its `serves` entries resolve to.
 */
import { apiRefDisplayName } from '@/shared/lib';
import { CredentialType, type ApiResponse, type Credential } from '@/shared/credentials/api';
import { apiScopeCovers } from '@/shared/credentials/lib/apiIdentity';
import { credentialAwaitsConsent } from '@/shared/credentials/lib/credentialIdentity';
import type { CredentialBindingEntity, ServedApiEntity } from '@/modules/agents/api/types';

/** One tile on the grid: the API is the card, the credential is a line on it. */
export interface ApiTileModel {
	/** Stable render key — binding id + the resolved API identity. */
	key: string;
	title: string;
	/** Domain line under the title, falling back to the vendor. */
	host: string;
	iconUrl: string | null;
	vendor: string;
	/** The API's name within the vendor; null for a vendor-wide (wildcard) binding. */
	apiName: string | null;
	/** Null when the API is not imported into the workspace. */
	version: string | null;
	authLabel: string | null;
	/** Null when the API is not imported into the workspace. */
	operationCount: number | null;
	/** One binding can fan out to several tiles. */
	bindingId: string;
	credentialId: string;
	credentialName: string;
	/** Null when the credential's org row is unreachable. */
	credentialCreatedAt: string | null;
	credentialUpdatedAt: string | null;
	boundAt: string;
	/** Soft-suspended — the broker excludes the binding until resumed. */
	suspended: boolean;
	/** OAuth sign-in not completed. Drives the dashed treatment. */
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

/** Does a workspace API row satisfy a binding's served-API reference? The two
 * value spaces differ in casing, so a hand-rolled `===` would disagree. */
function servedMatchesApi(served: ServedApiEntity, api: ApiResponse): boolean {
	return apiScopeCovers(served, api.api);
}

/** Every (binding, resolved API) pair the grid draws a tile for, deduped on the
 * render key. A reference matching nothing in the registry still yields one. */
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

/** Compose the tile list for one agent. */
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
			// `||`, not `??`: an empty name would print a blank line here.
			credentialName: binding.name || credential?.name || binding.credentialId,
			credentialCreatedAt: credential?.created_at ?? null,
			credentialUpdatedAt: credential?.updated_at ?? null,
			boundAt: binding.boundAt,
			suspended: binding.suspended,
			awaitingConsent: credentialAwaitsConsent(credential),
			authLabel: credential ? (AUTH_TILE_LABEL[credential.type] ?? null) : null,
		};

		if (api == null) {
			// Not imported here — render from the reference's machine tuple.
			tiles.push({
				...base,
				title:
					apiRefDisplayName({ vendor: served.vendor, name: served.name }) ||
					served.vendor,
				host: served.vendor,
				iconUrl: null,
				vendor: served.vendor,
				apiName: served.name ?? null,
				version: null,
				operationCount: null,
			});
			continue;
		}
		// Titled by the shared helper, so one API can't read `Ably` in the picker and
		// `ably-io` here.
		tiles.push({
			...base,
			title:
				apiRefDisplayName({
					displayName: api.display_name,
					catalogApiId: api.catalog_api_id,
					vendor: api.api.vendor,
					name: api.api.name,
				}) || api.api.name,
			host: api.api.host ?? api.api.vendor,
			iconUrl: api.icon_url ?? null,
			vendor: api.api.vendor,
			apiName: api.api.name,
			version: api.api.version,
			operationCount: api.operation_count,
		});
	}

	return tiles.sort((a, b) => a.title.localeCompare(b.title));
}

/** How many APIs an agent reaches — the count on its tab in the strip. */
export function agentApiCount(
	bindings: CredentialBindingEntity[] | undefined,
	apis: ApiResponse[],
): number {
	if (!bindings || bindings.length === 0) return 0;
	return [...tileIdentities(bindings, apis)].length;
}

/** Stat-summary math for the grid's side column. */
export interface ApiTileStats {
	configured: number;
	/** Sign-ins owed, per credential — one clears all of its tiles. */
	needsSetup: number;
	/** Null when no tile proves a count and at least one withholds it. */
	operations: number | null;
	/** `operations` is a floor: some tiles withheld their count. Renders as `N+`. */
	operationsAtLeast: boolean;
}

export function tileStats(tiles: ApiTileModel[]): ApiTileStats {
	let configured = 0;
	let operations = 0;
	// Deduped: several tiles can share one sign-in.
	const awaiting = new Set<string>();
	let counted = false;
	let withheld = false;
	for (const tile of tiles) {
		if (tile.awaitingConsent) {
			awaiting.add(tile.credentialId);
			continue;
		}
		configured += 1;
		// A pause is a deliberate exclusion, not a missing fact.
		if (tile.suspended) continue;
		if (tile.operationCount == null) {
			withheld = true;
			continue;
		}
		operations += tile.operationCount;
		counted = true;
	}
	return {
		configured,
		needsSetup: awaiting.size,
		operations: withheld && !counted ? null : operations,
		operationsAtLeast: withheld && counted,
	};
}

/** Bound credentials not usable yet — the strip's "N to set up" hint. */
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
