import { useMemo, useState } from 'react';
import { useNavigate } from 'react-router';
import {
	Activity,
	AlertTriangle,
	CheckCircle2,
	Filter,
	Plus,
	Webhook,
	XCircle,
} from 'lucide-react';
import {
	Button,
	DataTable,
	EmptyState,
	ErrorAlert,
	PageHeader,
	PageHelp,
	PageShell,
	SearchInput,
	StatCard,
	type Column,
} from '@/shared/ui';
import { useWebhookEndpoints, type WebhookEndpointEntity } from '@/modules/webhooks/api';
import {
	DestinationBadge,
	EndpointStatusBadge,
	EventTypeChips,
} from '@/modules/webhooks/components/badges';
import { CreateEndpointSheet } from '@/modules/webhooks/components/CreateEndpointSheet';
import { displayUrl, formatRate, timeAgo } from '@/modules/webhooks/lib/format';

function LastDeliveryCell({ endpoint }: { endpoint: WebhookEndpointEntity }) {
	if (!endpoint.lastDeliveryAt) {
		return <span className="text-muted-foreground text-xs">never</span>;
	}
	return (
		<span className="inline-flex items-center gap-1.5 text-xs">
			{endpoint.lastDeliveryOk ? (
				<CheckCircle2 className="text-success h-3.5 w-3.5" aria-hidden="true" />
			) : (
				<XCircle className="text-danger h-3.5 w-3.5" aria-hidden="true" />
			)}
			{timeAgo(endpoint.lastDeliveryAt)}
		</span>
	);
}

export default function WebhooksPage() {
	const navigate = useNavigate();
	const { data: endpoints, isLoading, isError } = useWebhookEndpoints();
	const [query, setQuery] = useState('');
	const [createOpen, setCreateOpen] = useState(false);

	const rows = useMemo(() => {
		const list = endpoints ?? [];
		const q = query.trim().toLowerCase();
		if (!q) return list;
		return list.filter(
			(e) => e.name.toLowerCase().includes(q) || e.url.toLowerCase().includes(q),
		);
	}, [endpoints, query]);

	const stats = useMemo(() => {
		const list = endpoints ?? [];
		const deliveries = list.reduce((sum, e) => sum + e.deliveries24h, 0);
		const rated = list.filter((e) => e.successRate24h != null);
		const weighted = rated.reduce(
			(sum, e) => sum + (e.successRate24h ?? 0) * e.deliveries24h,
			0,
		);
		const ratedVolume = rated.reduce((sum, e) => sum + e.deliveries24h, 0);
		return {
			endpoints: list.length,
			deliveries,
			successRate: ratedVolume > 0 ? weighted / ratedVolume : null,
			failing: list.filter((e) => e.status === 'failing').length,
		};
	}, [endpoints]);

	const columns: Column<WebhookEndpointEntity>[] = [
		{
			key: 'name',
			header: 'Destination',
			render: (e) => (
				<div className="min-w-0">
					<p className="text-foreground truncate text-sm font-medium">{e.name}</p>
					<p className="text-muted-foreground truncate font-mono text-xs">
						{displayUrl(e.url)}
					</p>
				</div>
			),
		},
		{
			key: 'type',
			header: 'Type',
			render: (e) => <DestinationBadge type={e.destinationType} />,
		},
		{ key: 'events', header: 'Events', render: (e) => <EventTypeChips endpoint={e} /> },
		{
			key: 'status',
			header: 'Status',
			render: (e) => <EndpointStatusBadge status={e.status} />,
		},
		{
			key: 'deliveries',
			header: 'Deliveries (24h)',
			render: (e) => (
				<span className="font-mono text-xs tabular-nums">
					{e.deliveries24h}
					<span className="text-muted-foreground">
						{' '}
						· {formatRate(e.successRate24h)} ok
					</span>
				</span>
			),
		},
		{
			key: 'last',
			header: 'Last delivery',
			render: (e) => <LastDeliveryCell endpoint={e} />,
		},
	];

	return (
		<PageShell>
			<PageHeader
				title="Webhooks"
				subtitle="Push platform events to your own systems or straight into Slack — signed, retried, and inspectable."
				actions={
					<>
						<Button onClick={() => setCreateOpen(true)}>
							<Plus className="h-4 w-4" /> New destination
						</Button>
						<PageHelp
							title="About Webhooks"
							intro={
								<p>
									Destinations receive platform events (executions, credentials,
									agent registrations…) without polling. HTTPS endpoints get
									signed JSON for machines; Slack destinations get readable
									channel messages for humans.
								</p>
							}
							sections={[
								{
									heading: 'Verifying deliveries',
									body: (
										<p>
											HTTPS deliveries carry Standard Webhooks headers
											(webhook-id, webhook-timestamp, webhook-signature).
											Verify the HMAC with the endpoint&rsquo;s signing secret
											and treat webhook-id as an idempotency key. Slack
											destinations are unsigned — the incoming webhook URL is
											the credential.
										</p>
									),
								},
								{
									heading: 'Failures and retries',
									body: (
										<p>
											Failed deliveries retry with exponential backoff. An
											endpoint that keeps failing is marked as such and is
											eventually disabled — you can redeliver any event from
											its Deliveries tab once the receiver is fixed.
										</p>
									),
								},
							]}
						/>
					</>
				}
			/>

			<div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
				<StatCard
					label="Endpoints"
					value={stats.endpoints}
					icon={<Webhook className="h-4 w-4" />}
					accent="primary"
					isLoading={isLoading}
				/>
				<StatCard
					label="Deliveries (24h)"
					value={stats.deliveries}
					icon={<Activity className="h-4 w-4" />}
					accent="blue"
					isLoading={isLoading}
				/>
				<StatCard
					label="Success rate (24h)"
					value={formatRate(stats.successRate)}
					icon={<CheckCircle2 className="h-4 w-4" />}
					accent="green"
					isLoading={isLoading}
				/>
				<StatCard
					label="Failing endpoints"
					value={stats.failing}
					icon={<AlertTriangle className="h-4 w-4" />}
					accent={stats.failing > 0 ? 'danger' : 'neutral'}
					valueClassName={stats.failing > 0 ? 'text-danger' : undefined}
					isLoading={isLoading}
				/>
			</div>

			<div className="flex items-center gap-3">
				<SearchInput
					value={query}
					onValueChange={setQuery}
					icon={<Filter className="h-3.5 w-3.5" />}
					placeholder="Filter endpoints by name or URL…"
					aria-label="Filter endpoints"
					className="max-w-sm flex-1"
					disabled={(endpoints ?? []).length === 0}
				/>
			</div>

			{isError && <ErrorAlert message="Failed to load webhook endpoints." />}
			{!isError && !isLoading && (endpoints ?? []).length === 0 ? (
				<EmptyState
					icon={<Webhook className="h-6 w-6" />}
					title="No destinations yet"
					description="Register an HTTPS endpoint or connect a Slack channel to start receiving platform events — agent registrations, execution results, credential expiry warnings, and more."
					action={
						<Button onClick={() => setCreateOpen(true)}>
							<Plus className="h-4 w-4" /> New destination
						</Button>
					}
				/>
			) : (
				!isError && (
					<DataTable
						columns={columns}
						data={rows}
						getRowKey={(e) => e.id}
						isLoading={isLoading}
						emptyMessage="No endpoints match the filter."
						ariaLabel="Webhook endpoints"
						onRowClick={(e) => navigate(`/webhooks/${encodeURIComponent(e.id)}`)}
						getRowLabel={(e) => `View endpoint ${e.name}`}
						renderCard={(e) => (
							<div className="space-y-2">
								<div className="flex items-center justify-between gap-2">
									<p className="text-foreground truncate text-sm font-medium">
										{e.name}
									</p>
									<EndpointStatusBadge status={e.status} />
								</div>
								<p className="text-muted-foreground truncate font-mono text-xs">
									{displayUrl(e.url)}
								</p>
								<div className="flex items-center justify-between gap-2">
									<span className="flex items-center gap-2">
										<DestinationBadge type={e.destinationType} />
										<EventTypeChips endpoint={e} />
									</span>
									<LastDeliveryCell endpoint={e} />
								</div>
							</div>
						)}
					/>
				)
			)}

			<CreateEndpointSheet open={createOpen} onClose={() => setCreateOpen(false)} />
		</PageShell>
	);
}
