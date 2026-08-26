/**
 * WebhookEndpointList — the list surface's data container: overview strip + rows.
 *
 * Owns the one place the list fans out per-endpoint `/stats` (via
 * {@link useWebhookEndpointStatsList}) and derives from it both the workspace
 * summary the {@link WebhooksOverviewStrip} shows and the per-endpoint health map
 * the {@link WebhookEndpointTable} rows read. Keeping the fan-out here (rather
 * than in the page) means the queries mount only once endpoints have loaded and
 * unmount with the list, and the page stays focused on the create / reveal /
 * relay-guide flows.
 *
 * The strip is hidden entirely when there are no endpoints — the table renders
 * its call-to-action empty state instead, so a brand-new workspace sees a single
 * clear "create your first endpoint" prompt, not a row of zeroes.
 */
import { useMemo } from 'react';
import {
	useWebhookEndpointStatsList,
	type WebhookEndpointEntity,
	type WebhookEndpointStats,
} from '@/modules/webhooks/api';
import { summariseEndpoints } from '@/modules/webhooks/lib/health';
import { WebhookEndpointTable } from '@/modules/webhooks/components/WebhookEndpointTable';
import { WebhooksOverviewStrip } from '@/modules/webhooks/components/WebhooksOverviewStrip';

interface WebhookEndpointListProps {
	endpoints: WebhookEndpointEntity[];
	canWrite: boolean;
	onCreate: () => void;
}

export function WebhookEndpointList({ endpoints, canWrite, onCreate }: WebhookEndpointListProps) {
	const endpointIds = useMemo(() => endpoints.map((e) => e.id), [endpoints]);
	const { byId, isLoading, isError } = useWebhookEndpointStatsList(endpointIds);

	// A plain id → stats map for the row/summary helpers (drops the per-query
	// loading flags they don't need).
	const statsById = useMemo(() => {
		const map = new Map<string, WebhookEndpointStats | undefined>();
		for (const [id, result] of byId) map.set(id, result.data);
		return map;
	}, [byId]);

	const summary = useMemo(() => summariseEndpoints(endpoints, statsById), [endpoints, statsById]);

	return (
		<div className="space-y-4">
			{endpoints.length > 0 && (
				<WebhooksOverviewStrip summary={summary} isLoading={isLoading} isError={isError} />
			)}
			<WebhookEndpointTable
				endpoints={endpoints}
				statsById={statsById}
				canWrite={canWrite}
				onCreate={onCreate}
			/>
		</div>
	);
}
