import { useNavigate, useParams } from 'react-router';
import { Activity } from 'lucide-react';
import { AppLink, BackButton, PageHeader, PageShell } from '@/shared/ui';
import { useToolkit } from '@/modules/toolkits/api';
import { ToolkitDetailBody } from '@/modules/toolkits/components/ToolkitDetailBody';
import { ToolkitKillSwitch } from '@/modules/toolkits/components/ToolkitKillSwitch';
import { ROUTES, ROUTE_PATHS } from '@/shared/app/routes';

/**
 * `/toolkits/:toolkitId` (→ `/app/toolkits/:toolkitId`) — full-page host for the
 * toolkit detail.
 *
 * Mirrors the `/agents/:agentId` layout: a shared `PageHeader` band (the
 * toolkit name as title + its own description as subtitle, with the kill
 * switch as the header action so suspension is always one click away) sits at
 * the top, a `BackButton` row sits just beneath it, and the KPI strip + tabbed
 * content (Overview/Activity/Access/Keys/Settings) lives in
 * `ToolkitDetailBody`, whose tabs each own their queries/mutations. The header
 * is read from the same cached `useToolkit` query the body uses, so there is
 * no extra fetch.
 */
export function ToolkitDetailPage() {
	const { toolkitId } = useParams<{ toolkitId: string }>();
	const navigate = useNavigate();
	const { data: toolkit } = useToolkit(toolkitId ?? null);

	if (!toolkitId) {
		return (
			<PageShell width="wide">
				<PageHeader title="Toolkit" subtitle="No toolkit selected." />
				<div className="-mt-2">
					<BackButton to={ROUTES.toolkits} label="All toolkits" useHistory={false} />
				</div>
			</PageShell>
		);
	}

	return (
		<PageShell width="wide">
			<PageHeader
				title={toolkit?.name ?? 'Toolkit'}
				subtitle={toolkit?.description ?? undefined}
				actions={
					toolkit ? (
						<ToolkitKillSwitch toolkitId={toolkitId} active={toolkit.active} />
					) : undefined
				}
			/>

			<div className="-mt-2 flex items-center justify-between">
				{/* Static link (not history-back): tab switches push history entries,
				    so popping would step through tabs instead of leaving the page. */}
				<BackButton to={ROUTES.toolkits} label="All toolkits" useHistory={false} />
				{/* Same pre-filtered Monitor deep-link the agent console carries. */}
				<AppLink
					href={ROUTE_PATHS.monitorExecutions({ toolkitId })}
					className="text-muted-foreground hover:text-foreground inline-flex items-center gap-1 text-xs font-medium transition-colors"
				>
					<Activity className="h-3.5 w-3.5" /> Open Monitor
				</AppLink>
			</div>

			<ToolkitDetailBody
				toolkitId={toolkitId}
				onRequestClose={() => navigate(ROUTES.toolkits)}
			/>
		</PageShell>
	);
}
