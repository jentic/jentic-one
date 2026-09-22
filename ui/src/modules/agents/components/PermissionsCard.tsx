/**
 * PermissionsCard — view + edit the platform permissions granted to an
 * agent. Part of #615.
 *
 * Reads the actor's current permissions (`GET .../permissions`) and renders them
 * as chips. "Edit permissions" opens the shared {@link ScopePicker} fed by the
 * platform permission catalogue (`GET /permissions`); permissions the caller
 * can't grant (`grantableByCaller === false`) are disabled. Saving does a
 * full-list replace (`PUT .../permissions`) — there is no partial grant/revoke
 * endpoint, so concurrent edits are last-writer-wins (acceptable for v0).
 *
 * A view-tier component: it talks to the backend only through the agents
 * module's hooks (ESLint-enforced), never the facade or generated services.
 */
import { useMemo, useState } from 'react';
import { ShieldCheck } from 'lucide-react';
import {
	Badge,
	Button,
	DetailSection,
	Dialog,
	EmptyRow,
	ErrorAlert,
	LoadingState,
	ScopePicker,
} from '@/shared/ui';
import type { EnhancedScope } from '@/shared/lib';
import { extractResourceFromScope } from '@/shared/lib';
import {
	AgentsApiError,
	usePermissionCatalogue,
	useAgentPermissions,
	useReplaceAgentPermissions,
	type PermissionCatalogEntry,
} from '@/modules/agents/api';
import { ConfirmDialog } from '@/modules/agents/components/confirm/ConfirmDialog';

export interface PermissionsCardProps {
	actorId: string;
	/** Name used in accessible labels / dialog title. */
	actorName: string;
	/**
	 * Whether the operator may edit permissions. When false the card is
	 * read-only (chips, no "Edit permissions"). A 403 from the backend is still
	 * handled defensively even when `canEdit` is true.
	 */
	canEdit?: boolean;
}

/**
 * Permissions whose accidental removal is destructive enough to warrant an
 * explicit confirmation. `org:admin` is the org-wide superuser permission: for
 * an admin operator it is *grantable* (so it behaves like an ordinary,
 * toggleable row, not a preserved one), which means a routine "Deselect all" +
 * Save would silently strip it from an actor that holds it. We can't preserve it
 * blindly (revocation must stay possible), so instead we confirm before a save
 * that removes a previously-held high-privilege permission.
 */
const HIGH_PRIVILEGE_PERMISSIONS = new Set<string>(['org:admin']);

/** Map the platform permission catalogue into the picker's item shape. */
export function catalogueToPickerItems(catalogue: PermissionCatalogEntry[]): EnhancedScope[] {
	return catalogue.map((p) => ({
		scope: p.name,
		description: p.description,
		origin: 'platform' as const,
		// "Recommended" is an OAuth2-shaped heuristic; meaningless for platform
		// permissions, so never pre-recommend (the picker hides the badge too).
		isRecommended: false,
	}));
}

export function PermissionsCard({ actorId, actorName, canEdit = true }: PermissionsCardProps) {
	const permissionsQuery = useAgentPermissions(actorId);
	const replace = useReplaceAgentPermissions();

	const [editing, setEditing] = useState(false);
	const catalogue = usePermissionCatalogue();

	const granted = permissionsQuery.data ?? [];

	return (
		<>
			<DetailSection
				title="Permissions"
				icon={<ShieldCheck className="h-4 w-4" />}
				trailing={
					canEdit && !permissionsQuery.isPending && !permissionsQuery.error ? (
						<Button
							size="sm"
							variant="outline"
							onClick={() => setEditing(true)}
							aria-label={`Edit permissions for ${actorName}`}
						>
							Edit permissions
						</Button>
					) : null
				}
			>
				{permissionsQuery.isPending ? (
					<LoadingState size="sm" />
				) : permissionsQuery.error ? (
					<ErrorAlert message={permissionsQuery.error as Error} />
				) : granted.length === 0 ? (
					<EmptyRow icon={<ShieldCheck />}>
						No permissions granted.
						{canEdit &&
							' This actor can’t perform privileged operations until you grant some.'}
					</EmptyRow>
				) : (
					<ul className="flex flex-wrap gap-2" aria-label="Granted permissions">
						{[...granted]
							.sort((a, b) => a.localeCompare(b))
							.map((permission) => (
								<li key={permission}>
									<Badge variant="default" className="font-mono text-[11px]">
										{permission}
									</Badge>
								</li>
							))}
					</ul>
				)}
			</DetailSection>

			{editing && (
				<EditPermissionsDialog
					actorName={actorName}
					granted={granted}
					catalogue={catalogue.data ?? []}
					catalogueLoading={catalogue.isPending}
					catalogueError={catalogue.error as Error | null}
					saving={replace.isPending}
					onClose={() => setEditing(false)}
					onSave={async (next) => {
						try {
							await replace.mutateAsync({ id: actorId, permissions: next });
							setEditing(false);
							return null;
						} catch (e) {
							// The hook toasts a generic failure; surface a clearer
							// in-dialog message for the common 403 (can't grant).
							if (e instanceof AgentsApiError && e.status === 403) {
								return 'You don’t have permission to grant one or more of these permissions.';
							}
							return e instanceof Error ? e.message : 'Failed to save permissions.';
						}
					}}
				/>
			)}
		</>
	);
}

interface EditPermissionsDialogProps {
	actorName: string;
	granted: string[];
	catalogue: PermissionCatalogEntry[];
	catalogueLoading: boolean;
	catalogueError: Error | null;
	saving: boolean;
	onClose: () => void;
	/** Returns an error message to display, or null on success. */
	onSave: (permissions: string[]) => Promise<string | null>;
}

function EditPermissionsDialog({
	actorName,
	granted,
	catalogue,
	catalogueLoading,
	catalogueError,
	saving,
	onClose,
	onSave,
}: EditPermissionsDialogProps) {
	const [selected, setSelected] = useState<string[]>(granted);
	const [error, setError] = useState<string | null>(null);
	// Permissions pending save that need an explicit confirmation (high-privilege
	// removals). Null when no confirmation is in flight.
	const [confirmRemoval, setConfirmRemoval] = useState<{
		next: string[];
		removed: string[];
	} | null>(null);

	const items = useMemo(() => catalogueToPickerItems(catalogue), [catalogue]);
	const disabledPermissions = useMemo(
		() => catalogue.filter((p) => !p.grantableByCaller).map((p) => p.name),
		[catalogue],
	);
	const disabledSet = useMemo(() => new Set(disabledPermissions), [disabledPermissions]);
	const knownPermissions = useMemo(() => new Set(catalogue.map((p) => p.name)), [catalogue]);

	// A permission already granted but absent from the catalogue (or not
	// grantable by this caller) must survive a save untouched — we can't show it
	// in the picker, but dropping it would silently revoke it. Track it separately.
	const preserved = useMemo(
		() => granted.filter((p) => !knownPermissions.has(p) || disabledSet.has(p)),
		[granted, knownPermissions, disabledSet],
	);

	// The full set that a save would persist (picker selection + preserved
	// non-editable grants). Save is only meaningful when it differs from what
	// the actor already holds, so we disable the button until then.
	const dirty = useMemo(() => {
		const next = new Set([...selected, ...preserved]);
		const current = new Set(granted);
		if (next.size !== current.size) return true;
		for (const p of next) if (!current.has(p)) return true;
		return false;
	}, [selected, preserved, granted]);

	const toggle = (permission: string): void => {
		if (disabledSet.has(permission)) return;
		setSelected((prev) =>
			prev.includes(permission)
				? prev.filter((p) => p !== permission)
				: [...prev, permission],
		);
	};
	const selectAll = (group?: string): void => {
		const pool = group
			? items.filter(
					(i) => !disabledSet.has(i.scope) && extractResourceFromScope(i.scope) === group,
				)
			: items.filter((i) => !disabledSet.has(i.scope));
		setSelected((prev) => Array.from(new Set([...prev, ...pool.map((i) => i.scope)])));
	};
	const deselectAll = (group?: string): void => {
		if (!group) {
			// Keep any disabled-but-granted selections (can't toggle them off here).
			setSelected((prev) => prev.filter((p) => disabledSet.has(p)));
			return;
		}
		setSelected((prev) =>
			prev.filter((p) => extractResourceFromScope(p) !== group || disabledSet.has(p)),
		);
	};

	async function commitSave(next: string[]) {
		setError(null);
		const msg = await onSave(next);
		if (msg) setError(msg);
	}

	async function handleSave() {
		setError(null);
		// Merge picker selection with preserved (non-editable) grants, dedup.
		const next = Array.from(new Set([...selected, ...preserved]));
		// Guard against silently revoking a previously-held high-privilege
		// permission (e.g. an admin "Deselect all"-ing a grantable org:admin the
		// actor holds).
		const nextSet = new Set(next);
		const removed = granted.filter((p) => HIGH_PRIVILEGE_PERMISSIONS.has(p) && !nextSet.has(p));
		if (removed.length > 0) {
			setConfirmRemoval({ next, removed });
			return;
		}
		await commitSave(next);
	}

	return (
		<>
			<Dialog
				open
				onClose={onClose}
				title={`Edit permissions — ${actorName}`}
				subtitle="Grant the platform permissions this actor needs. Saving replaces the full set."
				size="lg"
				footer={
					<>
						<Button variant="ghost" onClick={onClose} disabled={saving}>
							Cancel
						</Button>
						<Button
							onClick={handleSave}
							loading={saving}
							disabled={saving || catalogueLoading || !!catalogueError || !dirty}
						>
							Save permissions
						</Button>
					</>
				}
			>
				{catalogueLoading ? (
					<LoadingState size="sm" message="Loading permissions…" />
				) : catalogueError ? (
					<ErrorAlert message={catalogueError} />
				) : (
					<div className="space-y-3">
						{error && <ErrorAlert message={error} />}
						{preserved.length > 0 && (
							<p className="text-muted-foreground text-xs">
								{preserved.length} existing permission
								{preserved.length === 1 ? '' : 's'} not editable here will be
								preserved.
							</p>
						)}
						<ScopePicker
							vocabulary="permission"
							scopes={items}
							selectedScopes={selected.filter(
								(p) => knownPermissions.has(p) && !disabledSet.has(p),
							)}
							disabledScopes={disabledPermissions}
							showRecommended={false}
							onScopeToggle={toggle}
							onSelectAll={selectAll}
							onDeselectAll={deselectAll}
						/>
					</div>
				)}
			</Dialog>

			<ConfirmDialog
				open={confirmRemoval !== null}
				title="Remove high-privilege permission?"
				body={
					<>
						This will revoke{' '}
						<span className="text-foreground font-mono">
							{confirmRemoval?.removed.join(', ')}
						</span>{' '}
						from {actorName}. This is a powerful permission — make sure you intend to
						remove it.
					</>
				}
				confirmLabel="Remove and save"
				pending={saving}
				onConfirm={async () => {
					const next = confirmRemoval?.next ?? [];
					setConfirmRemoval(null);
					await commitSave(next);
				}}
				onClose={() => setConfirmRemoval(null)}
			/>
		</>
	);
}
