import { useNavigate, useParams } from 'react-router-dom';
import { BackButton, PageHeader, PageShell } from '@/shared/ui';
import { useToolkit } from '@/modules/toolkits/api';
import { ToolkitDetailBody } from '@/modules/toolkits/components/ToolkitDetailBody';
import { ROUTES } from '@/shared/app/routes';

/**
 * `/toolkits/:toolkitId` (→ `/app/toolkits/:toolkitId`) — full-page host for the
 * toolkit detail.
 *
 * Mirrors the `/agents/:agentId` layout: a shared `PageHeader` band (the
 * toolkit name as title + its own description as subtitle) sits at the top, a
 * `BackButton` row sits just beneath it, and the operational chrome + content
 * (id, kill switch, keys, credentials, agents) lives in the shared
 * `ToolkitDetailBody` (which owns all the queries/mutations). The header is read
 * from the same cached `useToolkit` query the body uses, so there is no extra
 * fetch.
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
			/>

			<div className="-mt-2">
				<BackButton to={ROUTES.toolkits} label="All toolkits" />
			</div>

			<ToolkitDetailBody
				toolkitId={toolkitId}
				layout="page"
				onRequestClose={() => navigate(ROUTES.toolkits)}
			/>
		</PageShell>
	);
}
