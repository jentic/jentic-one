/**
 * ServingStateStrip — one-line "what is serving right now" summary shown above
 * the Revisions/Overlays sections, e.g.:
 *
 *   Serving revision dc9bcdeb (imported) · 2 overlays (0 active) · last
 *   change: rolled back Aug 7
 *
 * Reads the same revision/overlay queries the sections below use (shared
 * TanStack cache — no extra requests) and derives the line with the pure
 * `describeServingState`. Renders nothing while either list is still loading
 * or errored: the sections below own those states.
 */
import { Activity } from 'lucide-react';
import { useApiRevisions, useOverlays, describeServingState } from '@/modules/workspace/api';
import type { ApiKey } from '@/modules/workspace/api';

export function ServingStateStrip({
	apiKey,
	currentRevisionId,
}: {
	apiKey: ApiKey;
	currentRevisionId: string | null;
}) {
	const revisionsQuery = useApiRevisions(apiKey);
	const overlaysQuery = useOverlays(apiKey);

	if (!revisionsQuery.data || !overlaysQuery.data) return null;

	const line = describeServingState(
		revisionsQuery.data.items,
		overlaysQuery.data.items,
		currentRevisionId,
	);

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
