/**
 * DeliveryLogPanel — the delivery history for one endpoint.
 *
 * This is the panel that makes the reliability machinery visible. Deliveries are
 * sent by a background dispatcher, not by the request that queued them, so a row
 * legitimately sits at `pending` for a moment and then moves on its own; the
 * query polls for exactly that reason (and stops once everything is terminal).
 *
 * The statuses are worth reading literally:
 *
 * - `pending` — queued, not yet attempted (or waiting out a backoff).
 * - `succeeded` — the receiver returned 2xx. Note the name: *not* "delivered".
 * - `failed` — an attempt failed and another is scheduled (`nextAttemptAt`).
 * - `dead` — retries are exhausted. The row is kept, not deleted, so the failure
 *   can be diagnosed afterwards, and "Resend" exists to retry it once the
 *   receiver is fixed.
 *
 * Rows are shown newest-first with a **deterministic** sort (by created time,
 * falling back to id) so the log never reshuffles between polls. A row expands
 * to reveal its full per-attempt history — the parent delivery keeps only the
 * last outcome, so the timeline comes from a separate query.
 */
import { useMemo, useState } from 'react';
import { Inbox } from 'lucide-react';
import {
	Badge,
	Button,
	DataTable,
	EmptyState,
	ErrorAlert,
	LoadingSpinner,
	SegmentedToggle,
	StatusBadge,
	Tooltip,
	type Column,
} from '@/shared/ui';
import {
	useResendDelivery,
	useWebhookDeliveries,
	useWebhookDeliveryAttempts,
} from '@/modules/webhooks/api';
import type {
	WebhookDeliveryAttemptEntity,
	WebhookDeliveryEntity,
	WebhookDeliveryStatus,
} from '@/modules/webhooks/api';
import { describeDeliveryError } from '@/modules/webhooks/lib/deliveryError';

interface DeliveryLogPanelProps {
	endpointId: string;
	/** Hides per-row actions when the viewer only holds `webhooks:read`. */
	canWrite: boolean;
}

/**
 * The Message-Attempts filter lens (the reference's All / Succeeded / Failed /
 * Canceled row). `failed` folds retrying *and* dead-lettered together — both are
 * a delivery the receiver hasn't accepted — because a single "Failed" filter is
 * what an operator reaches for, and splitting them here would add a fourth
 * segment for a distinction the status badge already draws per row.
 */
type DeliveryFilter = 'all' | 'succeeded' | 'failed' | 'pending';

const FILTER_OPTIONS: { value: DeliveryFilter; label: string }[] = [
	{ value: 'all', label: 'All' },
	{ value: 'succeeded', label: 'Succeeded' },
	{ value: 'failed', label: 'Failed' },
	{ value: 'pending', label: 'Pending' },
];

function matchesFilter(status: WebhookDeliveryStatus, filter: DeliveryFilter): boolean {
	switch (filter) {
		case 'all':
			return true;
		case 'succeeded':
			return status === 'succeeded';
		case 'failed':
			// Both a retrying and a dead-lettered row are "not delivered yet".
			return status === 'failed' || status === 'dead';
		case 'pending':
			return status === 'pending';
	}
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

function formatMs(ms: number | null): string {
	if (ms == null) return '—';
	if (ms < 1000) return `${Math.round(ms)} ms`;
	return `${(ms / 1000).toFixed(2)} s`;
}

/**
 * Newest-first, deterministically. `createdAt` is the primary key; ties (or a
 * null timestamp) fall back to the KSUID id, which is itself time-ordered — so
 * the order is stable across polls even when two rows share a millisecond.
 */
function sortDeliveriesDesc(rows: WebhookDeliveryEntity[]): WebhookDeliveryEntity[] {
	return [...rows].sort((a, b) => {
		const at = a.createdAt ? Date.parse(a.createdAt) : 0;
		const bt = b.createdAt ? Date.parse(b.createdAt) : 0;
		if (at !== bt) return bt - at;
		return b.id.localeCompare(a.id);
	});
}

/** The expandable per-attempt timeline for one delivery. */
function AttemptHistory({ deliveryId }: { deliveryId: string }) {
	const { data, isLoading, error } = useWebhookDeliveryAttempts(deliveryId);

	if (isLoading) {
		return (
			<div className="text-muted-foreground flex items-center gap-2 py-2 text-xs">
				<LoadingSpinner size="sm" />
				Loading attempt history…
			</div>
		);
	}
	if (error) {
		return <ErrorAlert message={error instanceof Error ? error : String(error)} />;
	}
	const attempts: WebhookDeliveryAttemptEntity[] = data ?? [];
	if (attempts.length === 0) {
		return <p className="text-muted-foreground py-2 text-xs">No recorded attempts yet.</p>;
	}

	return (
		<ol className="space-y-1.5">
			{attempts.map((a) => (
				<li key={a.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
					<span className="text-muted-foreground font-mono">#{a.attemptNumber}</span>
					{a.statusCode != null ? (
						<StatusBadge status={a.statusCode} />
					) : (
						<Badge variant="danger">no response</Badge>
					)}
					<span className="text-muted-foreground">{formatMs(a.durationMs)}</span>
					<span className="text-muted-foreground">{formatTime(a.createdAt)}</span>
					{a.error && <span className="text-danger">{a.error}</span>}
				</li>
			))}
		</ol>
	);
}

export function DeliveryLogPanel({ endpointId, canWrite }: DeliveryLogPanelProps) {
	const { data, isLoading, error } = useWebhookDeliveries(endpointId);
	const resend = useResendDelivery();
	const [expandedId, setExpandedId] = useState<string | null>(null);
	const [filter, setFilter] = useState<DeliveryFilter>('all');

	const allRows = useMemo(() => sortDeliveriesDesc(data ?? []), [data]);
	const rows = useMemo(
		() => allRows.filter((row) => matchesFilter(row.status, filter)),
		[allRows, filter],
	);

	if (error) {
		return <ErrorAlert message={error instanceof Error ? error : String(error)} />;
	}

	// No deliveries at all (as opposed to none matching the current filter) is
	// the true empty state — the endpoint has simply never fired.
	if (!isLoading && allRows.length === 0) {
		return (
			<EmptyState
				icon={<Inbox className="h-6 w-6" />}
				title="No deliveries yet"
				description="Nothing has been sent to this endpoint. Use “Send test” to confirm the wiring without waiting for a real event."
			/>
		);
	}

	function toggleExpanded(id: string) {
		setExpandedId((cur) => (cur === id ? null : id));
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
			render: (row) => {
				const isOpen = expandedId === row.id;
				return (
					<button
						type="button"
						onClick={() => toggleExpanded(row.id)}
						aria-expanded={isOpen}
						aria-controls={`wh-attempts-${row.id}`}
						aria-label={`${isOpen ? 'Hide' : 'Show'} attempt history for delivery ${row.eventId}`}
						className="text-foreground hover:text-primary font-mono text-xs underline-offset-2 hover:underline"
					>
						{row.attemptCount}
					</button>
				);
			},
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
			key: 'durationMs',
			header: 'Duration',
			render: (row) => (
				<span className="text-muted-foreground font-mono text-xs">
					{formatMs(row.durationMs)}
				</span>
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
			render: (row) => {
				const described = describeDeliveryError(row.lastError);
				if (!described) {
					return <span className="text-muted-foreground">—</span>;
				}
				// Focusable tooltip: the explanation + remediation hint is
				// available to keyboard and screen-reader users (via the
				// aria-label on the cell), not just on hover of a truncated cell.
				return (
					<Tooltip content={described.description} interactiveChild>
						<button
							type="button"
							aria-label={`${described.label}: ${described.description}`}
							className="text-danger focus-visible:ring-ring max-w-xs truncate rounded text-left text-xs focus-visible:ring-2 focus-visible:outline-none"
						>
							{described.label}
						</button>
					</Tooltip>
				);
			},
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

	// Mobile card renderer — the table's columns don't fit a phone, so stack the
	// key facts. The attempt-history toggle rides along here too.
	function renderCard(row: WebhookDeliveryEntity) {
		const isOpen = expandedId === row.id;
		const describedError = describeDeliveryError(row.lastError);
		return (
			<div className="space-y-2">
				<div className="flex items-center justify-between gap-2">
					<Badge variant={STATUS_VARIANT[row.status]} dot>
						{STATUS_LABEL[row.status] ?? row.status}
					</Badge>
					{row.lastStatusCode ? <StatusBadge status={row.lastStatusCode} /> : null}
				</div>
				<dl className="grid grid-cols-2 gap-x-3 gap-y-1 text-xs">
					<dt className="text-muted-foreground">Attempts</dt>
					<dd className="text-right font-mono">{row.attemptCount}</dd>
					<dt className="text-muted-foreground">Duration</dt>
					<dd className="text-right font-mono">{formatMs(row.durationMs)}</dd>
					<dt className="text-muted-foreground">Last attempt</dt>
					<dd className="text-right">{formatTime(row.lastAttemptAt)}</dd>
				</dl>
				{describedError && (
					<Tooltip content={describedError.description} interactiveChild>
						<button
							type="button"
							aria-label={`${describedError.label}: ${describedError.description}`}
							className="text-danger focus-visible:ring-ring rounded text-left text-xs focus-visible:ring-2 focus-visible:outline-none"
						>
							{describedError.label}
						</button>
					</Tooltip>
				)}
				<div className="flex items-center justify-between gap-2">
					<button
						type="button"
						onClick={() => toggleExpanded(row.id)}
						aria-expanded={isOpen}
						aria-controls={`wh-attempts-${row.id}`}
						className="text-primary text-xs underline-offset-2 hover:underline"
					>
						{isOpen ? 'Hide' : 'Show'} attempt history
					</button>
					{canWrite && (
						<Button
							variant="ghost"
							size="sm"
							onClick={() => resend.mutate({ deliveryId: row.id, endpointId })}
							disabled={row.status === 'pending' || resend.isPending}
						>
							Resend
						</Button>
					)}
				</div>
				{isOpen && (
					<div id={`wh-attempts-${row.id}`} className="border-border border-t pt-2">
						<AttemptHistory deliveryId={row.id} />
					</div>
				)}
			</div>
		);
	}

	return (
		<div className="space-y-3">
			<div className="flex flex-wrap items-center justify-between gap-2">
				<h4 className="text-foreground text-sm font-semibold">Message attempts</h4>
				<SegmentedToggle
					options={FILTER_OPTIONS}
					value={filter}
					onChange={setFilter}
					ariaLabel="Filter deliveries by status"
				/>
			</div>

			{!isLoading && rows.length === 0 ? (
				<p className="text-muted-foreground border-border rounded-lg border border-dashed px-4 py-8 text-center text-sm">
					No {filter} deliveries.
				</p>
			) : (
				<DataTable
					columns={columns}
					data={rows}
					getRowKey={(row) => row.id}
					isLoading={isLoading}
					ariaLabel="Webhook delivery log"
					renderCard={renderCard}
				/>
			)}
			{/* Desktop attempt-history detail for the expanded row. On mobile the
			    card renderer hosts its own, so this is hidden below `sm`. */}
			{expandedId && (
				<div
					id={`wh-attempts-${expandedId}`}
					className="border-border bg-muted/30 hidden rounded-lg border p-3 sm:block"
				>
					<h4 className="text-muted-foreground mb-2 text-xs font-semibold tracking-wider uppercase">
						Attempt history
					</h4>
					<AttemptHistory deliveryId={expandedId} />
				</div>
			)}
		</div>
	);
}
