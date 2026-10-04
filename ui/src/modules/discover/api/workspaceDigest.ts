/**
 * The Library's "Your workspace" digest — everything the Catalog's docked
 * panel and at-a-glance strip show about the APIs you've imported, joined from
 * real reads only (no field here is inferred or defaulted into existence):
 *
 *   | signal                       | source                                               |
 *   |------------------------------|------------------------------------------------------|
 *   | the API list, title, icon    | `GET /apis` (drained, shared `useAllApis`)           |
 *   | Live vs Draft                | `current_revision_id` (null ⇒ no live revision)       |
 *   | Update available             | `update_available` (Flow-3 upstream-change flag)      |
 *   | catalog ⇄ workspace match    | `catalog_api_id` (slug recorded at catalog import)    |
 *   | needs a credential           | `security_schemes` non-empty AND no ACTIVE credential |
 *   |                              | whose `api` scope covers it (once fully drained)      |
 *   | overlays awaiting review     | `GET /apis/{…}/overlays?status=pending` per API       |
 *   | 7-day calls / failures / line| `GET /monitoring/usage?group_by=api` (org:admin)      |
 *
 * Lives in the discover module (the Library catalog's owner) but reads the
 * registry through SHARED hooks — the workspace module owns the per-API views
 * and the module boundary forbids importing it.
 */
import { useMemo } from 'react';
import { useAllApis, type ApiResponse, type Credential } from '@/shared/credentials/api';
import { apiAccessKey } from '@/shared/credentials/api/apiAccess';
import { isCredentialMissing, useApiHealthIndex } from '@/shared/credentials/api/apiHealth';
import {
	apiUsageKeyFor,
	usePendingOverlayCounts,
	type ApiUsageRow,
	type PendingOverlayCount,
} from '@/shared/hooks';
import { workspaceApiTitle } from '@/shared/lib';
import { ROUTE_PATHS } from '@/shared/app/routes';
import { apiServingState } from '@/shared/ui';

export interface WorkspaceDigestRow {
	/** `vendor/name/version`. */
	key: string;
	ref: { vendor: string; name: string; version: string };
	title: string;
	/** The spec's description (`GET /apis` → `description`) — matched by the list filter. */
	description: string | null;
	host: string | null;
	iconUrl: string | null;
	catalogApiId: string | null;
	/** Null ⇒ no live revision (Draft); read through the shared `apiServingState`. */
	currentRevisionId: string | null;
	updateAvailable: boolean;
	operationCount: number;
	/** The API declares security schemes (so an agent needs a credential to call it). */
	needsAuth: boolean;
	/** Declared security scheme types (`security_schemes`) — the credential form's hint. */
	securitySchemes: string[];
	/**
	 * Active credentials covering this API (the shared `apiAccess` rule); null
	 * until every credential page loaded.
	 */
	credentials: Credential[] | null;
	/** `credentials.length`; null until every credential page loaded. */
	credentialCount: number | null;
	/** Null when the per-API overlay read hasn't answered (or was capped). */
	pendingOverlays: PendingOverlayCount | null;
	/**
	 * 7-day usage row. Null means "no row": combine with
	 * {@link WorkspaceDigest.usageExhaustive} before reading it as zero calls.
	 */
	usage: ApiUsageRow | null;
	createdAt: string;
	/** The API's hub. */
	href: string;
}

export type AttentionId = 'updates' | 'overlays' | 'credentials' | 'failures' | 'drafts';

/**
 * The order "Needs attention" lists its items in — by impact on agents, so
 * the collapsed view's first two are the ones breaking agents today:
 * calls already failing, then APIs agents can't call at all (no credential),
 * then APIs with nothing live yet (draft only). Overlays awaiting review and
 * upstream updates are improvements, not breakage, so they come last.
 */
const ATTENTION_ORDER: readonly AttentionId[] = [
	'failures',
	'credentials',
	'drafts',
	'overlays',
	'updates',
];

export interface AttentionEntry {
	id: AttentionId;
	/** Sentence fragment after the count, e.g. "have an update available". */
	label: string;
	rows: WorkspaceDigestRow[];
	/** Hub tab each row should open on. */
	tab: 'overview' | 'versions';
	/** Count is a floor (a per-API read was capped). */
	atLeast?: boolean;
}

export interface WorkspaceDigest {
	rows: WorkspaceDigestRow[];
	/** Non-empty entries only, in {@link ATTENTION_ORDER}. */
	attention: AttentionEntry[];
	/** Every attention source answered — only then may "All good" be claimed. */
	attentionComplete: boolean;
	/**
	 * Every attention source settled (answered or failed). Settled but not
	 * complete ⇒ some read failed: neither "All good" nor a loading state.
	 */
	attentionSettled: boolean;
	byCatalogApiId: Map<string, WorkspaceDigestRow[]>;
	totals: { apis: number; live: number; draft: number };
	/** 7-day usage is readable for this user (org:admin) and loaded. */
	usageAvailable: boolean;
	/** Every API with traffic is in the usage rows (a missing row ⇒ 0 calls). */
	usageExhaustive: boolean;
	/** The credential list failed — per-row agent figures are unknowable, not loading. */
	credentialsError: boolean;
	isPending: boolean;
	error: Error | null;
	complete: boolean;
	retry: () => void;
}

function titleFor(row: ApiResponse): string {
	return workspaceApiTitle({
		displayName: row.display_name,
		catalogApiId: row.catalog_api_id,
		...row.api,
	});
}

export function useWorkspaceDigest(): WorkspaceDigest {
	const apis = useAllApis();
	const health = useApiHealthIndex();
	const refs = useMemo(
		() =>
			apis.items.map((r) => ({
				vendor: r.api.vendor,
				name: r.api.name,
				version: r.api.version,
			})),
		[apis.items],
	);
	const overlays = usePendingOverlayCounts(refs);

	return useMemo(() => {
		const rows: WorkspaceDigestRow[] = apis.items.map((r) => {
			const ref = { vendor: r.api.vendor, name: r.api.name, version: r.api.version };
			const key = apiAccessKey(ref);
			const { credentials, credentialCount, usage } = health.healthFor(ref);
			return {
				key,
				ref,
				title: titleFor(r),
				description: r.description ?? null,
				host: r.api.host ?? null,
				iconUrl: r.icon_url,
				catalogApiId: r.catalog_api_id,
				currentRevisionId: r.current_revision_id,
				updateAvailable: r.update_available === true,
				operationCount: r.operation_count,
				needsAuth: r.security_schemes.length > 0,
				securitySchemes: r.security_schemes,
				credentials,
				credentialCount,
				pendingOverlays: overlays.byApi.get(key) ?? null,
				usage,
				createdAt: r.created_at,
				href: ROUTE_PATHS.workspaceApiHub(ref),
			};
		});

		const byCatalogApiId = new Map<string, WorkspaceDigestRow[]>();
		for (const row of rows) {
			if (!row.catalogApiId) continue;
			byCatalogApiId.set(row.catalogApiId, [
				...(byCatalogApiId.get(row.catalogApiId) ?? []),
				row,
			]);
		}

		const updates = rows.filter((r) => r.updateAvailable);
		const pending = rows.filter((r) => (r.pendingOverlays?.count ?? 0) > 0);
		const noCredential = rows.filter((r) =>
			isCredentialMissing(r.needsAuth, r.credentialCount),
		);
		// Usage is keyed `vendor/name` (no version), so every version of one API
		// carries the same row — list the API once, not once per version.
		const failingKeys = new Set<string>();
		const failing = rows.filter((r) => {
			if ((r.usage?.failed ?? 0) === 0) return false;
			const usageKey = apiUsageKeyFor(r.ref);
			if (failingKeys.has(usageKey)) return false;
			failingKeys.add(usageKey);
			return true;
		});
		const drafts = rows.filter((r) => apiServingState(r).serving === 'draft');

		const attention: AttentionEntry[] = [
			{ id: 'updates', label: 'upstream update available', rows: updates, tab: 'overview' },
			{
				id: 'overlays',
				label: 'overlay awaiting review',
				rows: pending,
				tab: 'versions',
				// The entry counts APIs, not overlays: one API with a capped overlay
				// read is still exactly one API. Only a capped API list is a floor.
				atLeast: overlays.truncated,
			},
			{
				id: 'failures',
				label: 'failed calls in the last 7 days',
				rows: failing,
				tab: 'overview',
			},
			{
				id: 'credentials',
				label: 'no credential — agents can’t call it',
				rows: noCredential,
				tab: 'overview',
			},
			{ id: 'drafts', label: 'draft only — nothing live yet', rows: drafts, tab: 'versions' },
		];

		return {
			rows,
			attention: attention
				.filter((a) => a.rows.length > 0)
				.sort((x, y) => ATTENTION_ORDER.indexOf(x.id) - ATTENTION_ORDER.indexOf(y.id)),
			attentionComplete: apis.complete && overlays.complete && health.credentialsComplete,
			attentionSettled:
				apis.complete &&
				overlays.settled &&
				(health.credentialsComplete || health.credentialsError),
			byCatalogApiId,
			totals: {
				apis: rows.length,
				live: rows.length - drafts.length,
				draft: drafts.length,
			},
			usageAvailable: health.usageAvailable,
			usageExhaustive: health.usageExhaustive,
			credentialsError: health.credentialsError,
			isPending: apis.isPending,
			error: apis.error,
			complete: apis.complete,
			retry: apis.retry,
		};
		// `useAllApis` returns a fresh wrapper each render — depend on its
		// (individually stable) fields so the digest only rebuilds on real changes.
	}, [apis.items, apis.complete, apis.isPending, apis.error, apis.retry, health, overlays]);
}
