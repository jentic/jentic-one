import { useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { BackButton, EditNameDescriptionDialog, PageHeader, PageShell } from '@/shared/ui';
import { useToolkit, useUpdateToolkit } from '@/modules/toolkits/api';
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
 *
 * The Edit (rename/re-describe) dialog is owned HERE — the page that renders the
 * `PageHeader` pencil that opens it — mirroring how `AgentDetailPage` owns its
 * dialog. Keeping ownership on the page (rather than straddling a
 * controlled/uncontrolled contract inside the body) removes the inert-button
 * caveat the body used to carry (#8).
 */
export function ToolkitDetailPage() {
	const { toolkitId } = useParams<{ toolkitId: string }>();
	const navigate = useNavigate();
	const { data: toolkit } = useToolkit(toolkitId ?? null);
	const updateToolkit = useUpdateToolkit(toolkitId ?? '');

	// Edit-toolkit dialog: just an open flag now. The shared dialog owns the
	// seeded draft (seeding synchronously once the toolkit resolves, so opening
	// the pencil before the query lands still fills from real data — #3), live
	// validation, the diff-vs-seeded patch, and all hardening.
	const [editOpen, setEditOpen] = useState(false);

	const desc = toolkit?.description?.trim();

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
				subtitle={desc ? desc : undefined}
				onEdit={
					toolkit
						? () => {
								updateToolkit.reset();
								setEditOpen(true);
							}
						: undefined
				}
				editLabel="Rename toolkit"
			/>

			<div className="-mt-2">
				<BackButton to={ROUTES.toolkits} label="All toolkits" />
			</div>

			<ToolkitDetailBody
				toolkitId={toolkitId}
				layout="page"
				onRequestClose={() => navigate(ROUTES.toolkits)}
			/>

			{/* Edit dialog — rename + re-describe the toolkit (#635). The shared
			    dialog owns the seeded draft, live validation (name 1..255,
			    description ≤1024), the diff-vs-seeded patch, and the pending /
			    empty-patch / entity-missing guards; the page wires its mutation
			    + title and passes the current name/description as seeds. */}
			<EditNameDescriptionDialog
				open={editOpen}
				onClose={() => setEditOpen(false)}
				title="Edit toolkit"
				initialName={toolkit?.name ?? ''}
				initialDescription={toolkit?.description ?? null}
				isPending={updateToolkit.isPending}
				error={updateToolkit.isError ? (updateToolkit.error as Error) : null}
				entityMissing={editOpen && !toolkit}
				fieldIdPrefix="tk-settings"
				onSave={(patch) => {
					updateToolkit.mutate(patch, {
						onSuccess: () => {
							setEditOpen(false);
							updateToolkit.reset();
						},
					});
				}}
			/>
		</PageShell>
	);
}
