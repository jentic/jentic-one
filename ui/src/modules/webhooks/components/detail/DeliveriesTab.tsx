import { useMemo, useState } from 'react';
import { Inbox } from 'lucide-react';
import {
	DataTable,
	EmptyState,
	ErrorAlert,
	SegmentedToggle,
	Select,
	StatusBadge,
	type Column,
} from '@/shared/ui';
import {
	useWebhookDeliveries,
	type WebhookDeliveryEntity,
	type WebhookEndpointEntity,
} from '@/modules/webhooks/api';
import { DeliveryStatusBadge } from '@/modules/webhooks/components/badges';
import { DeliveryDetailSheet } from '@/modules/webhooks/components/detail/DeliveryDetailSheet';
import { timeAgo } from '@/modules/webhooks/lib/format';

const STATUS_OPTIONS = [
	{ value: 'all', label: 'All' },
	{ value: 'delivered', label: 'Delivered' },
	{ value: 'pending', label: 'Pending' },
	{ value: 'failed', label: 'Failed' },
] as const;

type StatusFilter = (typeof STATUS_OPTIONS)[number]['value'];

/**
 * Deliveries tab — the endpoint's delivery log with status / event-type
 * filters; a row opens the inspector sheet (attempt timeline, payload,
 * response, redeliver).
 */
export function DeliveriesTab({ endpoint }: { endpoint: WebhookEndpointEntity }) {
	const [status, setStatus] = useState<StatusFilter>('all');
	const [eventType, setEventType] = useState('all');
	const [selectedId, setSelectedId] = useState<string | null>(null);

	// `failed` view includes exhausted deliveries — same operator question
	// ("what didn't arrive?"), so they share a lens rather than a 5th segment.
	const wireStatus = status === 'failed' ? null : status === 'all' ? null : status;
	const {
		data: deliveries,
		isLoading,
		isError,
	} = useWebhookDeliveries(endpoint.id, {
		status: wireStatus,
		eventType: eventType === 'all' ? null : eventType,
	});

	const rows = useMemo(() => {
		const list = deliveries ?? [];
		if (status === 'failed') {
			return list.filter((d) => d.status === 'failed' || d.status === 'exhausted');
		}
		return list;
	}, [deliveries, status]);

	const eventTypeOptions = useMemo(() => {
		const seen = new Set((deliveries ?? []).map((d) => d.eventType));
		return ['all', ...[...seen].sort()];
	}, [deliveries]);

	const selected = rows.find((d) => d.id === selectedId) ?? null;

	const columns: Column<WebhookDeliveryEntity>[] = [
		{
			key: 'event',
			header: 'Event',
			render: (d) => (
				<div className="min-w-0">
					<p className="text-foreground truncate font-mono text-xs">{d.eventType}</p>
					<p className="text-muted-foreground truncate font-mono text-[11px]">
						{d.eventId}
						{d.isTest ? ' · test' : ''}
						{d.isRedelivery ? ' · redelivery' : ''}
					</p>
				</div>
			),
		},
		{
			key: 'status',
			header: 'Status',
			render: (d) => <DeliveryStatusBadge status={d.status} />,
		},
		{
			key: 'http',
			header: 'HTTP',
			render: (d) => <StatusBadge status={d.lastHttpStatus} />,
		},
		{
			key: 'attempts',
			header: 'Attempts',
			render: (d) => (
				<span className="font-mono text-xs tabular-nums">{d.attempts.length}</span>
			),
		},
		{
			key: 'latency',
			header: 'Latency',
			render: (d) => (
				<span className="font-mono text-xs tabular-nums">
					{d.lastLatencyMs != null ? `${d.lastLatencyMs} ms` : '—'}
				</span>
			),
		},
		{
			key: 'when',
			header: 'When',
			render: (d) => (
				<span className="text-muted-foreground text-xs">{timeAgo(d.createdAt)}</span>
			),
		},
	];

	return (
		<div className="space-y-4">
			<div className="flex flex-wrap items-center gap-3">
				<SegmentedToggle
					options={[...STATUS_OPTIONS]}
					value={status}
					onChange={setStatus}
					ariaLabel="Filter deliveries by status"
				/>
				<Select
					value={eventType}
					onChange={(e) => setEventType(e.target.value)}
					aria-label="Filter deliveries by event type"
					className="max-w-60"
				>
					{eventTypeOptions.map((t) => (
						<option key={t} value={t}>
							{t === 'all' ? 'All event types' : t}
						</option>
					))}
				</Select>
			</div>

			{isError && <ErrorAlert message="Failed to load deliveries." />}
			{!isError && !isLoading && rows.length === 0 ? (
				<EmptyState
					icon={<Inbox className="h-6 w-6" />}
					title="No deliveries"
					description={
						status === 'all' && eventType === 'all'
							? 'Nothing has been delivered to this endpoint yet. Send a test event from the Overview tab to try it out.'
							: 'No deliveries match the current filters.'
					}
				/>
			) : (
				!isError && (
					<DataTable
						columns={columns}
						data={rows}
						getRowKey={(d) => d.id}
						isLoading={isLoading}
						ariaLabel="Webhook deliveries"
						onRowClick={(d) => setSelectedId(d.id)}
						getRowLabel={(d) => `Inspect delivery ${d.id}`}
					/>
				)
			)}

			<DeliveryDetailSheet
				endpointId={endpoint.id}
				delivery={selected}
				onClose={() => setSelectedId(null)}
			/>
		</div>
	);
}
