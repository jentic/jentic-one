/**
 * DeliveryLogPanel — the delivery history for one endpoint.
 *
 * This is the panel that makes the reliability machinery visible. Deliveries are
 * sent by a background dispatcher, not by the request that queued them, so a row
 * legitimately sits at `pending` for a moment and then moves on its own; the
 * query polls for exactly that reason.
 *
 * The statuses are worth reading literally:
 *
 * - `pending` — queued, not yet attempted (or waiting out a backoff).
 * - `succeeded` — the receiver returned 2xx. Note the name: *not* "delivered".
 * - `failed` — an attempt failed and another is scheduled (`nextAttemptAt`).
 * - `dead` — retries are exhausted. The row is kept, not deleted, so the failure
 *   can be diagnosed afterwards, and "Resend" exists to retry it once the
 *   receiver is fixed.
 */
import { Inbox } from 'lucide-react';
import {
	Badge,
	Button,
	DataTable,
	EmptyState,
	ErrorAlert,
	StatusBadge,
	type Column,
} from '@/shared/ui';
import { useResendDelivery, useWebhookDeliveries } from '@/modules/webhooks/api';
import type { WebhookDeliveryEntity, WebhookDeliveryStatus } from '@/modules/webhooks/api';

interface DeliveryLogPanelProps {
	endpointId: string;
	/** Hides per-row actions when the viewer only holds `webhooks:read`. */
	canWrite: boolean;
}

const STATUS_VARIANT: Record<
	WebhookDeliveryStatus,
	'default' | 'success' | 'warning' | 'danger' | 'pending'
> = {
	pending: 'pending',
	succeeded: 'success',
	failed: 'warning',
	dead: 'danger',
};

const STATUS_LABEL: Record<WebhookDeliveryStatus, string> = {
	pending: 'pending',
	succeeded: 'succeeded',
	failed: 'retrying',
	dead: 'dead-lettered',
};

function formatTime(iso: string | null): string {
	if (!iso) return '—';
	return new Date(iso).toLocaleString();
}

export function DeliveryLogPanel({ endpointId, canWrite }: DeliveryLogPanelProps) {
	const { data, isLoading, error } = useWebhookDeliveries(endpointId);
	const resend = useResendDelivery();

	if (error) {
		return <ErrorAlert message={error instanceof Error ? error : String(error)} />;
	}

	if (!isLoading && (data?.length ?? 0) === 0) {
		return (
			<EmptyState
				icon={<Inbox className="h-6 w-6" />}
				title="No deliveries yet"
				description="Nothing has been sent to this endpoint. Use “Send test” to confirm the wiring without waiting for a real event."
			/>
		);
	}

	const columns: Column<WebhookDeliveryEntity>[] = [
		{
			key: 'status',
			header: 'Status',
			render: (row) => (
				<Badge variant={STATUS_VARIANT[row.status]} dot>
					{STATUS_LABEL[row.status] ?? row.status}
				</Badge>
			),
		},
		{
			key: 'attemptCount',
			header: 'Attempts',
			render: (row) => <span className="font-mono text-xs">{row.attemptCount}</span>,
		},
		{
			key: 'lastStatusCode',
			header: 'Response',
			render: (row) =>
				row.lastStatusCode ? (
					<StatusBadge status={row.lastStatusCode} />
				) : (
					<span className="text-muted-foreground">—</span>
				),
		},
		{
			key: 'lastAttemptAt',
			header: 'Last attempt',
			render: (row) => (
				<span className="text-muted-foreground text-xs">
					{formatTime(row.lastAttemptAt)}
				</span>
			),
		},
		{
			key: 'nextAttemptAt',
			header: 'Next attempt',
			render: (row) => (
				<span className="text-muted-foreground text-xs">
					{/* Only a row awaiting another try has a meaningful next attempt. */}
					{row.status === 'pending' || row.status === 'failed'
						? formatTime(row.nextAttemptAt)
						: '—'}
				</span>
			),
		},
		{
			key: 'lastError',
			header: 'Error',
			render: (row) =>
				row.lastError ? (
					<span className="text-danger max-w-xs truncate text-xs" title={row.lastError}>
						{row.lastError}
					</span>
				) : (
					<span className="text-muted-foreground">—</span>
				),
		},
	];

	if (canWrite) {
		columns.push({
			key: 'actions',
			header: '',
			className: 'text-right',
			render: (row) => (
				<Button
					variant="ghost"
					size="sm"
					onClick={() => resend.mutate({ deliveryId: row.id, endpointId })}
					// A pending row already has an attempt coming; resending it would
					// only duplicate the send.
					disabled={row.status === 'pending' || resend.isPending}
				>
					Resend
				</Button>
			),
		});
	}

	return (
		<DataTable
			columns={columns}
			data={data ?? []}
			getRowKey={(row) => row.id}
			isLoading={isLoading}
			ariaLabel="Webhook delivery log"
		/>
	);
}
