import { useMemo } from 'react';
import { useQueries } from '@tanstack/react-query';
import { OverlaysService } from '@/shared/api';

/**
 * Overlays awaiting review, per API — `GET /apis/{v}/{n}/{ver}/overlays
 * ?status=pending` (apis:read). The backend has no cross-API overlay list, so
 * this fans out one read per API; it's bounded by {@link MAX_APIS} and by the
 * first page (50) of each, reported as a floor via `atLeast`.
 *
 * Shared because the Library catalog's docked workspace panel (discover) needs
 * it and may not import the workspace module that owns the per-API overlay
 * views. The workspace module's overlay actions invalidate
 * {@link pendingOverlaysRoot} so a confirm drops the count at once.
 */
export const pendingOverlaysRoot = ['pending-overlays'] as const;

const pendingOverlaysKey = (ref: { vendor: string; name: string; version: string }) =>
	[...pendingOverlaysRoot, ref.vendor, ref.name, ref.version] as const;

const MAX_APIS = 50;
const FAN_OUT_STALE_MS = 5 * 60_000;
const PAGE = 50;

export interface PendingOverlayCount {
	count: number;
	/** More pending overlays exist beyond the first page. */
	atLeast: boolean;
}

export interface PendingOverlayCounts {
	/** Keyed `vendor/name/version`. Only APIs whose read succeeded are present. */
	byApi: Map<string, PendingOverlayCount>;
	/** Every API (within the cap) answered — only then is a total exhaustive. */
	complete: boolean;
	/** Every read settled, answered or failed (a failure never completes). */
	settled: boolean;
	/** APIs beyond the fan-out cap weren't checked. */
	truncated: boolean;
}

export function usePendingOverlayCounts(
	refs: ReadonlyArray<{ vendor: string; name: string; version: string }>,
): PendingOverlayCounts {
	const checked = refs.slice(0, MAX_APIS);
	const results = useQueries({
		queries: checked.map((ref) => ({
			queryKey: pendingOverlaysKey(ref),
			queryFn: () =>
				OverlaysService.listOverlays({
					vendor: ref.vendor,
					name: ref.name,
					version: ref.version,
					status: 'pending',
					limit: PAGE,
				}),
			retry: false,
			// One request per API: hold answers for minutes and skip focus
			// refetches. Overlay actions invalidate `pendingOverlaysRoot`, so a
			// confirm/deprecate still drops the count at once.
			staleTime: FAN_OUT_STALE_MS,
			refetchOnWindowFocus: false,
		})),
	});

	// Fold the ref identity into the stamp alongside query state so a same-length
	// ref swap (with otherwise-identical status/timestamps) still recomputes and
	// drops keys for APIs no longer in `checked`.
	const stamp = checked
		.map(
			(ref, i) =>
				`${ref.vendor}/${ref.name}/${ref.version}#${results[i]?.status}:${results[i]?.dataUpdatedAt}`,
		)
		.join(',');
	return useMemo(() => {
		const byApi = new Map<string, PendingOverlayCount>();
		checked.forEach((ref, i) => {
			const data = results[i]?.data;
			if (!data) return;
			byApi.set(`${ref.vendor}/${ref.name}/${ref.version}`, {
				count: data.data.length,
				atLeast: data.has_more ?? false,
			});
		});
		return {
			byApi,
			complete: results.every((r) => r.isSuccess),
			settled: results.every((r) => !r.isPending),
			truncated: refs.length > MAX_APIS,
		};
		// eslint-disable-next-line react-hooks/exhaustive-deps -- `stamp` tracks `checked` refs + `results`
	}, [stamp, refs.length]);
}
