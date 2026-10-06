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
import { credentialAwaitsConsent, idTail } from '@/shared/credentials/lib/credentialIdentity';
import type { BindingRuleSummary } from '@/modules/agents/api';
import type { CredentialBindingEntity, ServedApiEntity } from '@/modules/agents/api/types';
import { rulesBlock } from '@/modules/agents/lib/tileStatus';

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
	/** Who created the credential (`null` when it records no owner). Absent when
	 * the credential's org row is unreachable — its owner is then unknown. */
	credentialCreatedBy?: string | null;
	boundAt: string;
	/** Soft-suspended — the broker excludes the binding until resumed. */
	suspended: boolean;
	/** Why it is suspended (`api_deleted` = its API was deleted). */
	suspendedReason: string | null;
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
	/** The API alone, as {@link tileApiKey} keys it: shared by its credentials. */
	apiKey: string;
}> {
	const seen = new Set<string>();
	for (const binding of bindings) {
		for (const served of binding.serves) {
			const matches = apis.filter((api) => servedMatchesApi(served, api));
			if (matches.length === 0) {
				const key = `${binding.id}:${served.vendor}/${served.name ?? '*'}/${served.version ?? '*'}`;
				// As its tile keys it: an unimported API's tile carries no version.
				const apiKey = tileApiKey({
					vendor: served.vendor,
					apiName: served.name ?? null,
					version: null,
				});
				if (seen.has(key)) continue;
				seen.add(key);
				yield { binding, served, api: null, key, apiKey };
				continue;
			}
			for (const api of matches) {
				const apiKey = tileApiKey({
					vendor: api.api.vendor,
					apiName: api.api.name,
					version: api.api.version,
				});
				const key = `${binding.id}:${apiKey}`;
				if (seen.has(key)) continue;
				seen.add(key);
				yield { binding, served, api, key, apiKey };
			}
		}
	}
}

/**
 * What an orphan verdict may rest on. A credential missing from a list proves it
 * was deleted only when the list is AUTHORITATIVE: read by an `org:admin` (the
 * only viewer whose `GET /credentials` is the whole org — anyone else's holds just
 * their own credentials) and drained completely, without error.
 */
export interface OrphanProof {
	viewerIsAdmin: boolean;
	/** Every page of the credentials list loaded, and none failed. */
	credentialsComplete: boolean;
}

/**
 * A binding whose credential is gone: deleting a credential leaves its bindings
 * behind (#1426), and they unlock nothing. The grid hides such a binding entirely
 * (no tile, no figure, no per-binding read — those 404 on a missing credential)
 * and purges it quietly — so the verdict must be proven, never guessed:
 *
 * - Only absence from an authoritative list counts (see {@link OrphanProof}).
 *   A non-admin, or a viewer not known yet, never gets a verdict: their bindings
 *   stay live and render like any other.
 * - `name == null && serves == []` is NOT proof on its own. The bindings read
 *   returns exactly that shape for EVERY binding when its surface can't reach the
 *   control DB and skips enrichment (a split deploy), so trusting it would hide
 *   every binding there.
 * - A binding that serves an API is always live: enrichment found its
 *   credential, even when a list fetched a moment earlier doesn't have it yet.
 */
export function isOrphanBinding(
	binding: CredentialBindingEntity,
	credentialsById: ReadonlyMap<string, Credential>,
	proof: OrphanProof,
): boolean {
	if (!proof.viewerIsAdmin || !proof.credentialsComplete) return false;
	if (binding.serves.length > 0) return false;
	return !credentialsById.has(binding.credentialId);
}

/** Split an agent's bindings into the live ones and the proven orphans — the
 * ones safe to hide and purge. */
export function partitionBindings(
	bindings: CredentialBindingEntity[],
	credentials: Credential[],
	proof: OrphanProof,
): { live: CredentialBindingEntity[]; orphans: CredentialBindingEntity[] } {
	const credentialsById = new Map(credentials.map((c) => [c.credential_id, c]));
	const live: CredentialBindingEntity[] = [];
	const orphans: CredentialBindingEntity[] = [];
	for (const binding of bindings) {
		(isOrphanBinding(binding, credentialsById, proof) ? orphans : live).push(binding);
	}
	return { live, orphans };
}

/** Compose the tile list for one agent. A binding that serves nothing — an
 * orphan (see {@link isOrphanBinding}), or any binding the backend could not
 * enrich — yields no tile. */
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
			...(credential && { credentialCreatedBy: credential.created_by ?? null }),
			boundAt: binding.boundAt,
			suspended: binding.suspended,
			suspendedReason: binding.suspendedReason,
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

/** One API the agent reaches through several bindings (one per account). */
export interface MultiAccountApi {
	title: string;
	/** Distinct bindings serving it — always 2 or more. */
	count: number;
}

/** The API identity a tile draws, without the binding — tiles sharing it are
 * accounts of one API. */
export function tileApiKey(tile: Pick<ApiTileModel, 'vendor' | 'apiName' | 'version'>): string {
	return `${tile.vendor}/${tile.apiName ?? '*'}/${tile.version ?? '*'}`;
}

/** The APIs the grid draws more than one binding for, keyed by {@link tileApiKey},
 * in grid order. With several accounts the broker needs the call to name one. */
export function multiAccountApis(tiles: ApiTileModel[]): Map<string, MultiAccountApi> {
	const bindingsByApi = new Map<string, { title: string; bindings: Set<string> }>();
	for (const tile of tiles) {
		const key = tileApiKey(tile);
		const entry = bindingsByApi.get(key) ?? { title: tile.title, bindings: new Set() };
		entry.bindings.add(tile.bindingId);
		bindingsByApi.set(key, entry);
	}
	const multi = new Map<string, MultiAccountApi>();
	for (const [key, { title, bindings }] of bindingsByApi) {
		if (bindings.size > 1) multi.set(key, { title, count: bindings.size });
	}
	return multi;
}

/** The label each multi-account tile prints so its account is told apart, keyed
 * by tile key: the credential's name, with its id tail when two accounts of one
 * API share a name. Tiles of a single-account API are absent. */
export function accountLabels(tiles: ApiTileModel[]): Map<string, string> {
	const multi = multiAccountApis(tiles);
	const labels = new Map<string, string>();
	for (const tile of tiles) {
		const apiKey = tileApiKey(tile);
		if (!multi.has(apiKey)) continue;
		const twin = tiles.some(
			(t) =>
				t.bindingId !== tile.bindingId &&
				tileApiKey(t) === apiKey &&
				t.credentialName === tile.credentialName,
		);
		labels.set(
			tile.key,
			twin ? `${tile.credentialName} · …${idTail(tile.credentialId)}` : tile.credentialName,
		);
	}
	return labels;
}

/** How many APIs an agent reaches — the count on its tab in the strip. An API
 * reached through several credentials counts once. */
export function agentApiCount(
	bindings: CredentialBindingEntity[] | undefined,
	apis: ApiResponse[],
): number {
	if (!bindings || bindings.length === 0) return 0;
	return new Set([...tileIdentities(bindings, apis)].map((t) => t.apiKey)).size;
}

/** How many APIs the grid's tiles cover — the number beside its heading. An API
 * drawn once per credential counts once. */
export function distinctApiCount(tiles: ApiTileModel[]): number {
	return new Set(tiles.map(tileApiKey)).size;
}

/** Stat-summary math for the grid's side column. Counts are per API — one
 * drawn once per credential counts once — except `needsSetup`. */
export interface ApiTileStats {
	/** APIs with at least one credential past its sign-in. */
	configured: number;
	/** Sign-ins owed, per credential — one clears all of its tiles. */
	needsSetup: number;
	/** Null when no tile proves a count and at least one withholds it. */
	operations: number | null;
	/** `operations` is a floor: some tiles withheld their count. Renders as `N+`. */
	operationsAtLeast: boolean;
	/** Tiles whose rules let no call through (`Blocked`) — 0 when rules are unknown. */
	blocked: number;
}

export function tileStats(
	tiles: ApiTileModel[],
	/** Rule breakdown per tile, when loaded: a Blocked tile reaches nothing, so
	 * its operations stay out of "reachable" (see `deriveTileStatus`). */
	rulesFor?: (tile: ApiTileModel) => BindingRuleSummary | undefined,
): ApiTileStats {
	const configured = new Set<string>();
	// Per API: the largest count its reachable tiles prove (its credentials reach
	// the same operations), or null while none of them proves one.
	const operationsByApi = new Map<string, number | null>();
	let blocked = 0;
	// Deduped: several tiles can share one sign-in.
	const awaiting = new Set<string>();
	for (const tile of tiles) {
		if (tile.awaitingConsent) {
			awaiting.add(tile.credentialId);
			continue;
		}
		const apiKey = tileApiKey(tile);
		configured.add(apiKey);
		// A pause is a deliberate exclusion, not a missing fact.
		if (tile.suspended) continue;
		if (rulesBlock(rulesFor?.(tile))) {
			blocked += 1;
			continue;
		}
		const known = operationsByApi.get(apiKey) ?? null;
		operationsByApi.set(
			apiKey,
			tile.operationCount == null ? known : Math.max(known ?? 0, tile.operationCount),
		);
	}
	let operations = 0;
	let counted = false;
	let withheld = false;
	for (const count of operationsByApi.values()) {
		if (count == null) {
			withheld = true;
			continue;
		}
		operations += count;
		counted = true;
	}
	return {
		configured: configured.size,
		needsSetup: awaiting.size,
		operations: withheld && !counted ? null : operations,
		operationsAtLeast: withheld && counted,
		blocked,
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
