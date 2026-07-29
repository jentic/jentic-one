import { useNavigate, useParams } from 'react-router';
import { BackButton, PageHeader, PageShell } from '@/shared/ui';
import { useToolkit } from '@/modules/toolkits/api';
import { ToolkitDetailBody } from '@/modules/toolkits/components/ToolkitDetailBody';
import { ToolkitKillSwitch } from '@/modules/toolkits/components/ToolkitKillSwitch';
import { ROUTES } from '@/shared/app/routes';

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
					<BackButton to={ROUTES.toolkits} label="All toolkits" />
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

			<div className="-mt-2">
				<BackButton to={ROUTES.toolkits} label="All toolkits" />
			</div>

			<ToolkitDetailBody
				toolkitId={toolkitId}
				onRequestClose={() => navigate(ROUTES.toolkits)}
			/>
		</PageShell>
	);
}
