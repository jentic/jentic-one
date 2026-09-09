/**
 * SettingsPage — the flat settings surface (platform page grammar).
 *
 * Flattened per review: the old left sidebar was the only left-sidebar nav in
 * the SPA (every other multi-section surface uses TabNav), had exactly one
 * destination ("Developer Settings"), and triple-nested navigation (sidebar →
 * section header → the section's own TabNav). Now it's a standard page in the
 * AgentsPage shape: PageShell + PageHeader (Add client + PageHelp in the
 * actions slot) + ONE page-level TabNav for the OAuth surface's two tabs.
 *
 * The active tab lives in `?tab=` so the agent rail's "Review" action on an
 * `oauth_client.registered` alert (and the backend approval-pending page) can
 * deep-link straight to the queue via /app/settings?tab=queue. The pending
 * count is fetched here so the queue tab label carries the badge even while
 * the clients tab is active. `OAuthClientsSection` keeps owning data wiring,
 * tables, sheets, and dialogs.
 */
import { useState } from 'react';
import { useSearchParams } from 'react-router';
import { KeyRound, Plus, ShieldQuestion } from 'lucide-react';
import { Button, PageHeader, PageHelp, PageShell, TabNav, type TabNavOption } from '@/shared/ui';
import { useOAuthClientQueue } from '@/modules/settings/api/hooks';
import { isSectionTab, OAuthClientsSection, type SectionTab } from './OAuthClientsSection';

export function SettingsPage() {
	const [searchParams, setSearchParams] = useSearchParams();
	const tabParam = searchParams.get('tab');
	const activeTab: SectionTab = isSectionTab(tabParam) ? tabParam : 'clients';
	// Fetched at page level so the queue tab label can carry the pending
	// count even while the clients tab is active.
	const { data: pendingClients } = useOAuthClientQueue('pending');
	// Lifted so the header's "Add client" opens the section-owned form sheet.
	const [createOpen, setCreateOpen] = useState(false);

	const setTab = (tab: SectionTab): void => {
		setSearchParams(
			(prev) => {
				const next = new URLSearchParams(prev);
				if (tab === 'clients') next.delete('tab');
				else next.set('tab', tab);
				return next;
			},
			{ replace: false },
		);
	};

	const tabOptions: TabNavOption<SectionTab>[] = [
		{ value: 'clients', label: 'Clients', icon: <KeyRound className="h-4 w-4" /> },
		{
			value: 'queue',
			label: 'Approval queue',
			icon: <ShieldQuestion className="h-4 w-4" />,
			count: pendingClients?.length || undefined,
		},
	];

	return (
		<PageShell>
			<PageHeader
				title="Settings"
				subtitle="Manage the OAuth clients that authenticate users via Jentic One."
				actions={
					<div className="flex items-center gap-2">
						<Button onClick={(): void => setCreateOpen(true)}>
							<Plus className="h-4 w-4" />
							Add client
						</Button>
						<PageHelp
							title="About OAuth Clients"
							intro="OAuth clients are third-party applications that use Jentic One for user authentication."
							sections={[
								{
									heading: 'Client ID',
									body: 'The client_id is a public identifier used in OAuth flows. Configure it in the third-party application.',
								},
								{
									heading: 'Client Secret',
									body: 'The client secret is shown once at creation and after rotation. Store it securely — it cannot be retrieved later.',
								},
								{
									heading: 'Redirect URIs',
									body: 'OAuth callbacks are only allowed to URLs in this list. Include all environments (dev, staging, prod).',
								},
								{
									heading: 'Approval queue',
									body: 'Clients that register themselves (DCR) wait in the queue until an admin approves them. Judge a registration by its redirect-URI origins — the name is self-reported.',
								},
							]}
						/>
					</div>
				}
			/>

			<TabNav<SectionTab>
				options={tabOptions}
				value={activeTab}
				onChange={setTab}
				ariaLabel="Settings sections"
			/>

			<OAuthClientsSection
				activeTab={activeTab}
				onTabChange={setTab}
				createOpen={createOpen}
				onCreateOpenChange={setCreateOpen}
			/>
		</PageShell>
	);
}
