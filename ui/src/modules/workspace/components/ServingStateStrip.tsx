/**
 * ServingStateStrip — one-line "what is serving right now" summary shown above
 * the Revisions/Overlays sections, e.g.:
 *
 *   Serving revision dc9bcdeb (imported) · 2 overlays (0 active) · last
 *   change: rolled back Aug 7
 *
 * Reads the same revision/overlay queries the sections below use (shared
 * TanStack cache — no extra requests) and derives the line with the pure
 * `describeServingState`, whose "current" comes from the revisions list alone
 * (single source of truth — no separate `current_revision_id` prop that could
 * disagree mid-refetch). Renders nothing until BOTH background page walks
 * finish: a partially-loaded list would present undercounts as fact.
 */
import { Activity } from 'lucide-react';
import { useApiRevisions, useOverlays, describeServingState } from '@/modules/workspace/api';
import type { ApiKey } from '@/modules/workspace/api';

export function ServingStateStrip({ apiKey }: { apiKey: ApiKey }) {
	const revisions = useApiRevisions(apiKey);
	const overlays = useOverlays(apiKey);

	const ready =
		!revisions.isLoading &&
		!revisions.isLoadingAll &&
		!revisions.isError &&
		!overlays.isLoading &&
		!overlays.isLoadingAll &&
		!overlays.isError;
	if (!ready) return null;

	const line = describeServingState(revisions.items, overlays.items);

	return (
		<p
			className="text-muted-foreground flex items-center gap-2 text-sm"
			data-testid="serving-state-strip"
		>
			<Activity size={14} aria-hidden="true" className="shrink-0" />
			<span>{line}</span>
		</p>
	);
}
