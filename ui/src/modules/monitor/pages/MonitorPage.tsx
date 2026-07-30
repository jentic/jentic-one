/**
 * Monitor module — the observability surface for jentic-one.
 *
 * A single tabbed page with four lenses over platform activity:
 *   - Executions  the execution trace log (+ trace detail)
 *   - Jobs        the async job queue (+ job detail, cancel)
 *   - Events      platform events with a live SSE stream + acknowledge
 *   - Audit       the audit log = actor lens; deep-links into the others
 *
 * The active tab is held in the `?tab=` search param so it's deep-linkable and
 * the browser back button moves between lenses. Tabs themselves are built in
 * the per-tab todos; this page owns the shell + tab switching.
 */
import { useSearchParams } from 'react-router';
import type { ReactNode } from 'react';
import {
	Activity as ActivityIcon,
	BellRing,
	LayoutDashboard,
	ListTodo,
	ScrollText,
} from 'lucide-react';
import { PageShell, PageHeader, PageHelp, TabNav, type TabNavOption } from '@/shared/ui';
import { MONITOR_TABS, type MonitorTab } from '@/modules/monitor/api';
import { OverviewTab } from '@/modules/monitor/components/OverviewTab';
import { ExecutionsTab } from '@/modules/monitor/components/ExecutionsTab';
import { JobsTab } from '@/modules/monitor/components/JobsTab';
import { EventsTab } from '@/modules/monitor/components/EventsTab';
import { AuditTab } from '@/modules/monitor/components/AuditTab';
import { MonitorFilterBar } from '@/modules/monitor/components/MonitorFilterBar';

/**
 * Lens options — same underline TabNav grammar as the detail consoles.
 * Derived from a `Record` keyed by `MonitorTab` so adding a lens to the union
 * without wiring its label/icon fails the type-check instead of silently
 * disappearing from the UI.
 */
const TAB_META: Record<MonitorTab, { label: string; icon: ReactNode }> = {
	overview: { label: 'Overview', icon: <LayoutDashboard className="h-4 w-4" /> },
	executions: { label: 'Executions', icon: <ActivityIcon className="h-4 w-4" /> },
	jobs: { label: 'Jobs', icon: <ListTodo className="h-4 w-4" /> },
	events: { label: 'Events', icon: <BellRing className="h-4 w-4" /> },
	audit: { label: 'Audit', icon: <ScrollText className="h-4 w-4" /> },
};

const TAB_OPTIONS: TabNavOption<MonitorTab>[] = MONITOR_TABS.map((tab) => ({
	value: tab,
	...TAB_META[tab],
}));

const tabId = (tab: string) => `monitor-tab-${tab}`;
const panelId = (tab: string) => `monitor-panel-${tab}`;

function isMonitorTab(value: string | null): value is MonitorTab {
	return value != null && (MONITOR_TABS as string[]).includes(value);
}

export default function MonitorPage() {
	const [searchParams, setSearchParams] = useSearchParams();
	const tabParam = searchParams.get('tab');
	// Overview leads the toggle and is the default landing lens — the headline
	// usage/health view — when no `?tab=` is present. The other lenses are one
	// click (and deep-linkable) away.
	const activeTab: MonitorTab = isMonitorTab(tabParam) ? tabParam : 'overview';

	const setTab = (tab: string) => {
		setSearchParams(
			(prev) => {
				const next = new URLSearchParams(prev);
				next.set('tab', tab);
				// Switching lens drops any detail-sheet / per-tab filter params
				// that only make sense within the previous tab, so a back-and-forth
				// doesn't reopen a stale sheet. The GLOBAL filters (days / actor_id /
				// actor_type) are deliberately preserved across tabs.
				for (const k of [
					'trace_id',
					'execution_id',
					'job_id',
					'status',
					'live',
					'target_id',
					'target_type',
					'cursor',
					// Executions-only scope (deep-linked from toolkit detail);
					// no other lens supports it, so it doesn't survive a switch.
					'toolkit_id',
				]) {
					next.delete(k);
				}
				return next;
			},
			{ replace: false },
		);
	};

	return (
		<PageShell>
			<PageHeader
				title="Monitor"
				subtitle="Execution traces, async jobs, platform events, and the audit log."
				actions={
					<PageHelp
						title="About Monitor"
						intro="Monitor is the observability surface for jentic-one — four lenses over what your agents and the platform are doing."
						sections={[
							{
								heading: 'Executions & Jobs',
								body: 'Executions is the trace log of finished API calls; Jobs is the async work queue. Click any row to open its detail sheet; admins can cancel a non-terminal job from there.',
							},
							{
								heading: 'Events & Audit',
								body: 'Events streams platform events live (toggle Go live) and lets you acknowledge ones that need action. Audit is the org-admin actor log — who did what — and is where execution/job actor attribution lives.',
							},
						]}
					/>
				}
			/>
			<TabNav<MonitorTab>
				ariaLabel="Monitor lenses"
				options={TAB_OPTIONS}
				value={activeTab}
				onChange={(tab) => setTab(tab)}
				getTabId={tabId}
				getControls={panelId}
			/>
			{activeTab !== 'overview' && <MonitorFilterBar tab={activeTab} />}
			<div
				role="tabpanel"
				id={panelId(activeTab)}
				aria-labelledby={tabId(activeTab)}
				tabIndex={0}
				className="focus-visible:outline-none"
			>
				{activeTab === 'overview' && <OverviewTab />}
				{activeTab === 'executions' && <ExecutionsTab />}
				{activeTab === 'jobs' && <JobsTab />}
				{activeTab === 'events' && <EventsTab />}
				{activeTab === 'audit' && <AuditTab />}
			</div>
		</PageShell>
	);
}
