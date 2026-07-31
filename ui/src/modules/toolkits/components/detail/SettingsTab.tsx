import { useState } from 'react';
import { CascadeDeleteDialog, DangerZone, IdentitySettingsCard, toast } from '@/shared/ui';
import type { CascadeDependentGroup, DangerZoneAction } from '@/shared/ui';
import {
	useDeleteToolkit,
	useToolkitAgents,
	useToolkitBindings,
	useToolkitKeys,
	useUpdateToolkit,
} from '@/modules/toolkits/api';
import type { Toolkit } from '@/modules/toolkits/api/types';

interface SettingsTabProps {
	toolkit: Toolkit;
	/** Called after a successful delete (host navigates back to the list). */
	onDeleted: () => void;
}

/**
 * Settings tab — identity editing and the danger zone, both on the shared
 * console cards ({@link IdentitySettingsCard} / {@link DangerZone}) so the
 * toolkit, agent, and service-account Settings tabs read identically.
 * Destructive actions live here rather than in the header chrome so identity
 * actions and irreversible ones stop sharing a row; only the kill switch
 * (reversible, operational) stays pinned up top.
 */
export function SettingsTab({ toolkit, onDeleted }: SettingsTabProps) {
	const toolkitId = toolkit.toolkit_id;
	const updateToolkit = useUpdateToolkit(toolkitId);
	const deleteToolkit = useDeleteToolkit();

	// Blast radius for the cascade dialog. These hooks share query keys with
	// the other tabs, so visiting Settings after Overview/Keys/Access is a
	// cache hit, not a refetch storm.
	const { data: keys = [] } = useToolkitKeys(toolkitId);
	const { data: bindings = [] } = useToolkitBindings(toolkitId);
	const { data: agents = [] } = useToolkitAgents(toolkitId);

	const [deleteOpen, setDeleteOpen] = useState(false);

	const deleteDependents: CascadeDependentGroup[] = [
		{
			label: 'agent grant',
			count: agents.length,
			names: agents.map((a) => a.agent_name),
		},
		{
			label: 'API key',
			count: keys.length,
			names: keys.map((k) => k.label ?? k.key_preview),
		},
		{
			label: 'credential binding',
			count: bindings.length,
			names: bindings.map((b) => b.label ?? b.credential_id),
		},
	].filter((g) => g.count > 0);

	const dangerActions: DangerZoneAction[] = [
		{
			key: 'delete',
			title: 'Delete toolkit',
			description:
				'Deleting cascades: agent grants, API keys, and credential bindings are removed with it. This cannot be undone. To pause access without deleting anything, use the kill switch in the header instead.',
			buttonLabel: 'Delete',
			ariaLabel: `Delete ${toolkit.name}`,
		},
	];

	return (
		<div className="space-y-6">
			<IdentitySettingsCard
				idLabel="Toolkit ID"
				idValue={toolkitId}
				name={toolkit.name}
				description={toolkit.description ?? null}
				saving={updateToolkit.isPending}
				error={updateToolkit.isError ? updateToolkit.error.message : null}
				onSave={async (draft) => {
					await updateToolkit.mutateAsync({
						name: draft.name,
						description: draft.description || null,
					});
					toast({ title: 'Toolkit updated', variant: 'success' });
				}}
			/>

			<DangerZone actions={dangerActions} onAction={() => setDeleteOpen(true)} />

			<CascadeDeleteDialog
				open={deleteOpen}
				entityType="toolkit"
				entityName={toolkit.name}
				dependents={deleteDependents.length > 0 ? deleteDependents : undefined}
				loading={deleteToolkit.isPending}
				error={deleteToolkit.error}
				onClose={() => setDeleteOpen(false)}
				onConfirm={() =>
					deleteToolkit.mutate(toolkitId, {
						onSuccess: () => {
							setDeleteOpen(false);
							onDeleted();
						},
					})
				}
			/>
		</div>
	);
}
