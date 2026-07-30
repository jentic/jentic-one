import { useEffect, useRef, useState } from 'react';
import { Fingerprint, SlidersHorizontal, Trash2 } from 'lucide-react';
import {
	Button,
	CascadeDeleteDialog,
	CopyButton,
	ErrorAlert,
	Input,
	Label,
	Textarea,
	toast,
} from '@/shared/ui';
import type { CascadeDependentGroup } from '@/shared/ui';
import {
	useDeleteToolkit,
	useToolkitAgents,
	useToolkitBindings,
	useToolkitKeys,
	useUpdateToolkit,
} from '@/modules/toolkits/api';
import type { Toolkit, ToolkitUpdate } from '@/modules/toolkits/api/types';
import { DetailSection } from '@/modules/toolkits/components/detail/shared';

interface SettingsTabProps {
	toolkit: Toolkit;
	/** Called after a successful delete (host navigates back to the list). */
	onDeleted: () => void;
}

/**
 * Settings tab — identity editing (inline form, replacing the old Edit
 * dialog) and the danger zone. Destructive actions live here rather than in
 * the header chrome so identity actions and irreversible ones stop sharing a
 * row; only the kill switch (reversible, operational) stays pinned up top.
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

	const [editName, setEditName] = useState(toolkit.name);
	const [editDesc, setEditDesc] = useState(toolkit.description ?? '');
	const [deleteOpen, setDeleteOpen] = useState(false);

	// Seed-from-props syncs only when the seed identity changes (dialog-state
	// rule applied to an inline form): re-visiting the tab must not clobber an
	// in-progress draft, but navigating to a different toolkit must reseed.
	const seededIdRef = useRef(toolkitId);
	useEffect(() => {
		if (seededIdRef.current !== toolkitId) {
			seededIdRef.current = toolkitId;
			setEditName(toolkit.name);
			setEditDesc(toolkit.description ?? '');
		}
	}, [toolkitId, toolkit.name, toolkit.description]);

	const dirty = editName !== toolkit.name || editDesc !== (toolkit.description ?? '');

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

	return (
		<div className="space-y-6">
			<DetailSection title="Identity" icon={<SlidersHorizontal className="h-4 w-4" />}>
				{/* The immutable toolkit id — what agents and API calls reference.
				    Lives here (not in the page chrome) so the header stays clean. */}
				<div className="mb-4 flex flex-wrap items-center justify-between gap-2">
					<span className="text-muted-foreground flex items-center gap-1.5 text-xs">
						<Fingerprint className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
						Toolkit ID
					</span>
					<span className="bg-muted text-muted-foreground inline-flex items-center gap-1.5 rounded-md px-2 py-0.5 font-mono text-xs">
						{toolkitId}
						<CopyButton value={toolkitId} size="icon" variant="ghost" />
					</span>
				</div>
				<form
					className="space-y-4"
					onSubmit={(e) => {
						e.preventDefault();
						// Dirty-field-only PATCH: send a field only when it changed.
						// A cleared description wires as an empty STRING (not `null`) —
						// the backend ignores `null` (so the clear would silently
						// revert) but honours `''`, so clearing must persist.
						const patch: ToolkitUpdate = {};
						if (editName !== toolkit.name) patch.name = editName || null;
						if (editDesc !== (toolkit.description ?? '')) patch.description = editDesc;
						updateToolkit.mutate(patch, {
							onSuccess: () =>
								toast({ title: 'Toolkit updated', variant: 'success' }),
						});
					}}
				>
					<div>
						<Label
							htmlFor="tk-settings-name"
							className="text-muted-foreground mb-1 block text-xs"
						>
							Name
						</Label>
						<Input
							id="tk-settings-name"
							type="text"
							value={editName}
							onChange={(e) => setEditName(e.target.value)}
						/>
					</div>
					<div>
						<Label
							htmlFor="tk-settings-description"
							className="text-muted-foreground mb-1 block text-xs"
						>
							Description
						</Label>
						<Textarea
							id="tk-settings-description"
							value={editDesc}
							onChange={(e) => setEditDesc(e.target.value)}
							rows={2}
						/>
					</div>
					{updateToolkit.isError && <ErrorAlert message={updateToolkit.error.message} />}
					<Button
						type="submit"
						size="sm"
						disabled={!dirty || updateToolkit.isPending}
						loading={updateToolkit.isPending}
					>
						{updateToolkit.isPending ? 'Saving…' : 'Save changes'}
					</Button>
				</form>
			</DetailSection>

			<DetailSection title="Danger zone" icon={<Trash2 className="h-4 w-4" />} danger>
				<div className="flex flex-wrap items-center justify-between gap-3 p-1">
					<p className="text-muted-foreground max-w-prose text-sm">
						Deleting this toolkit cascades: agent grants, API keys, and credential
						bindings are removed with it. This cannot be undone. To pause access without
						deleting anything, use the kill switch in the header instead.
					</p>
					<Button
						variant="danger"
						size="sm"
						onClick={() => setDeleteOpen(true)}
						aria-label={`Delete ${toolkit.name}`}
					>
						<Trash2 className="h-4 w-4" /> Delete
					</Button>
				</div>
			</DetailSection>

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
