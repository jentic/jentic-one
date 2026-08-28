import { useState } from 'react';
import { useParams } from 'react-router';
import { AlertTriangle, Inbox, LayoutDashboard, Settings, Webhook } from 'lucide-react';
import {
	BackButton,
	EmptyState,
	ErrorAlert,
	LoadingState,
	PageHeader,
	PageShell,
	TabNav,
	type TabNavOption,
} from '@/shared/ui';
import { useWebhookEndpoint } from '@/modules/webhooks/api';
import { DestinationBadge, EndpointStatusBadge } from '@/modules/webhooks/components/badges';
import { DeliveriesTab } from '@/modules/webhooks/components/detail/DeliveriesTab';
import { OverviewTab } from '@/modules/webhooks/components/detail/OverviewTab';
import { SettingsTab } from '@/modules/webhooks/components/detail/SettingsTab';
import { displayUrl } from '@/modules/webhooks/lib/format';

type Tab = 'overview' | 'deliveries' | 'settings';

const TABS: TabNavOption<Tab>[] = [
	{ value: 'overview', label: 'Overview', icon: <LayoutDashboard className="h-4 w-4" /> },
	{ value: 'deliveries', label: 'Deliveries', icon: <Inbox className="h-4 w-4" /> },
	{ value: 'settings', label: 'Settings', icon: <Settings className="h-4 w-4" /> },
];

export default function WebhookDetailPage() {
	const { webhookId } = useParams<{ webhookId: string }>();
	const { data: endpoint, isLoading, isError } = useWebhookEndpoint(webhookId ?? null);
	const [tab, setTab] = useState<Tab>('overview');

	if (isLoading) {
		return (
			<PageShell>
				<LoadingState />
			</PageShell>
		);
	}

	if (isError || !endpoint) {
		return (
			<PageShell>
				<BackButton to="/webhooks" label="Webhooks" />
				{isError ? (
					<ErrorAlert message="Failed to load this webhook endpoint." />
				) : (
					<EmptyState
						icon={<Webhook className="h-6 w-6" />}
						title="Endpoint not found"
						description="It may have been deleted."
					/>
				)}
			</PageShell>
		);
	}

	return (
		<PageShell>
			<BackButton to="/webhooks" label="Webhooks" />
			<PageHeader
				title={endpoint.name}
				subtitle={displayUrl(endpoint.url)}
				actions={
					<span className="flex items-center gap-3">
						<DestinationBadge type={endpoint.destinationType} />
						<EndpointStatusBadge status={endpoint.status} />
					</span>
				}
			/>

			{endpoint.status === 'failing' && (
				<div
					role="status"
					className="border-danger/40 bg-danger/5 flex items-start gap-3 rounded-lg border p-3"
				>
					<AlertTriangle
						className="text-danger mt-0.5 h-4 w-4 shrink-0"
						aria-hidden="true"
					/>
					<div className="min-w-0 text-sm">
						<p className="text-foreground font-medium">
							{endpoint.consecutiveFailures} consecutive delivery failures
						</p>
						<p className="text-muted-foreground text-xs">
							Deliveries keep failing and this endpoint will be disabled automatically
							if that continues. Inspect the latest attempts in the Deliveries tab,
							fix the receiver, then redeliver.
						</p>
					</div>
				</div>
			)}

			<TabNav
				options={TABS}
				value={tab}
				onChange={setTab}
				ariaLabel="Endpoint sections"
				getTabId={(v) => `webhook-tab-${v}`}
				getControls={(v) => `webhook-panel-${v}`}
			/>

			<div id={`webhook-panel-${tab}`} role="tabpanel" aria-labelledby={`webhook-tab-${tab}`}>
				{tab === 'overview' && <OverviewTab endpoint={endpoint} />}
				{tab === 'deliveries' && <DeliveriesTab endpoint={endpoint} />}
				{tab === 'settings' && <SettingsTab endpoint={endpoint} />}
			</div>
		</PageShell>
	);
}
