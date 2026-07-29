import { useSearchParams } from 'react-router';
import { AnimatePresence, motion } from 'framer-motion';
import { ShieldOff } from 'lucide-react';
import { Button, CopyButton, SegmentedToggle } from '@/shared/ui';
import { useToolkit } from '@/modules/toolkits/api';
import { ToolkitKillSwitch } from '@/modules/toolkits/components/ToolkitKillSwitch';
import { OverviewTab } from '@/modules/toolkits/components/detail/OverviewTab';
import { ActivityTab } from '@/modules/toolkits/components/detail/ActivityTab';
import { AccessTab } from '@/modules/toolkits/components/detail/AccessTab';
import { KeysTab } from '@/modules/toolkits/components/detail/KeysTab';
import { SettingsTab } from '@/modules/toolkits/components/detail/SettingsTab';
import { UsageStrip } from '@/modules/toolkits/components/detail/UsageStrip';
import { RowSkeleton } from '@/modules/toolkits/components/detail/shared';

/**
 * Toolkit detail — the tabbed shell.
 *
 * The always-visible header zone carries the operational chrome: the mono id
 * chip and the kill switch (suspension is the page's safety superpower, so it
 * never hides behind a tab), plus the suspended banner. Everything else lives
 * in four tabs, one component each:
 *
 *   - Overview  bound agents (safety-first ordering, #636) + audit slice
 *   - Activity  7d usage chart + recent executions (admin-gated, Monitor link)
 *   - Access    credential bindings + per-binding permission rules
 *   - Keys      static API keys (create / one-time reveal / revoke)
 *   - Settings  identity editing + danger zone (cascade delete)
 *
 * The active tab is held in the `?tab=` search param (same pattern as the
 * Monitor page) so tabs are deep-linkable and the back button moves between
 * them. Each tab owns its queries/mutations; keys are shared through
 * `toolkitKeys`, so tab switches hit the cache rather than refetching.
 */

const TOOLKIT_TABS = ['overview', 'activity', 'access', 'keys', 'settings'] as const;
type ToolkitTab = (typeof TOOLKIT_TABS)[number];

const TAB_LABELS: Record<ToolkitTab, string> = {
	overview: 'Overview',
	activity: 'Activity',
	access: 'Access',
	keys: 'Keys',
	settings: 'Settings',
};

const TAB_OPTIONS = TOOLKIT_TABS.map((id) => ({ value: id, label: TAB_LABELS[id] }));

const tabId = (tab: string) => `toolkit-tab-${tab}`;
const panelId = (tab: string) => `toolkit-panel-${tab}`;

function isToolkitTab(value: string | null): value is ToolkitTab {
	return value != null && (TOOLKIT_TABS as readonly string[]).includes(value);
}

const panelMotion = {
	initial: { opacity: 0, height: 0 },
	animate: { opacity: 1, height: 'auto' as const },
	exit: { opacity: 0, height: 0 },
	transition: { duration: 0.2, ease: 'easeOut' as const },
};

export interface ToolkitDetailBodyProps {
	toolkitId: string;
	/** Host close callback (not-found "Back", post-delete). Page → navigate. */
	onRequestClose: () => void;
}

export function ToolkitDetailBody({ toolkitId, onRequestClose }: ToolkitDetailBodyProps) {
	const { data: toolkit, isLoading } = useToolkit(toolkitId);

	const [searchParams, setSearchParams] = useSearchParams();
	const tabParam = searchParams.get('tab');
	const activeTab: ToolkitTab = isToolkitTab(tabParam) ? tabParam : 'overview';

	const setTab = (tab: string) => {
		setSearchParams(
			(prev) => {
				const next = new URLSearchParams(prev);
				next.set('tab', tab);
				return next;
			},
			{ replace: false },
		);
	};

	if (isLoading)
		return (
			<div className="space-y-6" data-testid="toolkit-loading">
				<div className="flex items-center justify-between gap-3">
					<div className="bg-muted h-6 w-40 animate-pulse rounded-md" />
					<div className="bg-muted h-8 w-28 animate-pulse rounded" />
				</div>
				<div className="border-border bg-card space-y-3 rounded-xl border p-5">
					<div className="bg-muted h-5 w-32 animate-pulse rounded" />
					<RowSkeleton />
					<RowSkeleton />
				</div>
			</div>
		);

	if (!toolkit)
		return (
			<div className="flex flex-col items-center justify-center gap-3 px-5 py-12 text-center">
				<span className="text-2xl">🔍</span>
				<p className="text-foreground font-medium">Toolkit not found</p>
				<Button variant="secondary" onClick={onRequestClose}>
					Back
				</Button>
			</div>
		);

	const suspended = !toolkit.active;

	return (
		<div className="space-y-6">
			{/* Operational chrome — never hidden behind a tab. */}
			<div className="flex flex-wrap items-center justify-between gap-3">
				<span className="bg-muted text-muted-foreground inline-flex items-center gap-1.5 rounded-md px-2 py-0.5 font-mono text-xs">
					{toolkit.toolkit_id}
					<CopyButton value={toolkit.toolkit_id} size="icon" variant="ghost" />
				</span>
				<ToolkitKillSwitch toolkitId={toolkitId} active={toolkit.active} />
			</div>

			<AnimatePresence initial={false}>
				{suspended && (
					<motion.div key="suspended-banner" {...panelMotion} className="overflow-hidden">
						<div
							className="border-danger/40 bg-danger/5 flex items-start gap-3 rounded-xl border p-4"
							role="alert"
						>
							<div className="bg-danger/15 text-danger flex h-9 w-9 shrink-0 items-center justify-center rounded-lg">
								<ShieldOff className="h-5 w-5" />
							</div>
							<div className="min-w-0 flex-1">
								<p className="text-danger font-heading text-sm font-semibold">
									Toolkit suspended — all access blocked
								</p>
								<p className="text-muted-foreground mt-0.5 text-sm">
									Every call is rejected with{' '}
									<code className="bg-danger/10 text-danger rounded px-1 font-mono text-xs">
										403 toolkit_suspended
									</code>{' '}
									— this applies to both toolkit API keys and agent-identity
									callers. Restore access with the kill switch above.
								</p>
							</div>
						</div>
					</motion.div>
				)}
			</AnimatePresence>

			<UsageStrip toolkit={toolkit} />

			<SegmentedToggle
				as="tabs"
				ariaLabel="Toolkit sections"
				options={TAB_OPTIONS}
				value={activeTab}
				onChange={setTab}
				getTabId={tabId}
				getControls={panelId}
			/>

			<div
				role="tabpanel"
				id={panelId(activeTab)}
				aria-labelledby={tabId(activeTab)}
				tabIndex={0}
				className="focus-visible:outline-none"
			>
				{activeTab === 'overview' && <OverviewTab toolkitId={toolkitId} />}
				{activeTab === 'activity' && <ActivityTab toolkitId={toolkitId} />}
				{activeTab === 'access' && <AccessTab toolkitId={toolkitId} />}
				{activeTab === 'keys' && <KeysTab toolkitId={toolkitId} suspended={suspended} />}
				{activeTab === 'settings' && (
					<SettingsTab toolkit={toolkit} onDeleted={onRequestClose} />
				)}
			</div>
		</div>
	);
}
