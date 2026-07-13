/**
 * ScopesCard — view + edit the platform permission scopes granted to an actor
 * (agent or service account). Part of #615.
 *
 * Reads the actor's current scopes (`GET .../scopes`) and renders them as
 * chips. "Edit scopes" opens the shared {@link ScopePicker} fed by the platform
 * permission catalogue (`GET /permissions`); scopes the caller can't grant
 * (`grantableByCaller === false`) are disabled. Saving does a full-list replace
 * (`PUT .../scopes`) — there is no partial grant/revoke endpoint, so concurrent
 * edits are last-writer-wins (acceptable for v0).
 *
 * A view-tier component: it talks to the backend only through the agents
 * module's hooks (ESLint-enforced), never the facade or generated services.
 */
import { useMemo, useState } from 'react';
import { ShieldCheck } from 'lucide-react';
import {
	Badge,
	Button,
	Card,
	CardBody,
	CardHeader,
	CardTitle,
	Dialog,
	ErrorAlert,
	LoadingState,
	ScopePicker,
} from '@/shared/ui';
import type { EnhancedScope } from '@/shared/lib';
import { extractResourceFromScope } from '@/shared/lib';
import {
	AgentsApiError,
	usePermissionCatalogue,
	useAgentScopes,
	useReplaceAgentScopes,
	useServiceAccountScopes,
	useReplaceServiceAccountScopes,
	type PermissionCatalogEntry,
} from '@/modules/agents/api';
import { ConfirmDialog } from '@/modules/agents/components/confirm/ConfirmDialog';

export interface ScopesCardProps {
	actorKind: 'agent' | 'service-account';
	actorId: string;
	/** Name used in accessible labels / dialog title. */
	actorName: string;
	/**
	 * Whether the operator may edit scopes. When false the card is read-only
	 * (chips, no "Edit scopes"). A 403 from the backend is still handled
	 * defensively even when `canEdit` is true.
	 */
	canEdit?: boolean;
}

/**
 * Scopes whose accidental removal is destructive enough to warrant an explicit
 * confirmation. `org:admin` is the org-wide superuser permission: for an admin
 * operator it is *grantable* (so it behaves like an ordinary, toggleable row,
 * not a preserved one), which means a routine "Deselect all" + Save would
 * silently strip it from an actor that holds it. We can't preserve it blindly
 * (revocation must stay possible), so instead we confirm before a save that
 * removes a previously-held high-privilege scope.
 */
const HIGH_PRIVILEGE_SCOPES = new Set<string>(['org:admin']);

/** Map the platform permission catalogue into the picker's scope shape. */
function catalogueToScopes(catalogue: PermissionCatalogEntry[]): EnhancedScope[] {
	return catalogue.map((p) => ({
		scope: p.name,
		description: p.description,
		origin: 'platform' as const,
		// "Recommended" is an OAuth2-shaped heuristic; meaningless for platform
		// permissions, so never pre-recommend (the picker hides the badge too).
		isRecommended: false,
	}));
}

export function ScopesCard({ actorKind, actorId, actorName, canEdit = true }: ScopesCardProps) {
	const isAgent = actorKind === 'agent';

	// Only the relevant pair of hooks is enabled (the other is passed `null`).
	const agentScopes = useAgentScopes(isAgent ? actorId : null);
	const saScopes = useServiceAccountScopes(isAgent ? null : actorId);
	const scopesQuery = isAgent ? agentScopes : saScopes;

	const replaceAgent = useReplaceAgentScopes();
	const replaceSa = useReplaceServiceAccountScopes();
	const replace = isAgent ? replaceAgent : replaceSa;

	const [editing, setEditing] = useState(false);
	const catalogue = usePermissionCatalogue();

	const granted = scopesQuery.data ?? [];

	return (
		<Card>
			<CardHeader className="flex flex-row items-center justify-between gap-2">
				<div className="flex items-center gap-2">
					<ShieldCheck className="text-primary h-4 w-4" />
					<CardTitle>Scopes</CardTitle>
				</div>
				{canEdit && !scopesQuery.isPending && !scopesQuery.error && (
					<Button
						size="sm"
						variant="outline"
						onClick={() => setEditing(true)}
						aria-label={`Edit scopes for ${actorName}`}
					>
						Edit scopes
					</Button>
				)}
			</CardHeader>
			<CardBody className="space-y-2">
				{scopesQuery.isPending ? (
					<LoadingState size="sm" />
				) : scopesQuery.error ? (
					<ErrorAlert message={scopesQuery.error as Error} />
				) : granted.length === 0 ? (
					<div className="text-muted-foreground border-border/60 rounded-lg border border-dashed p-4 text-center text-sm">
						No scopes granted.
						{canEdit &&
							' This actor can’t perform privileged operations until you grant some.'}
					</div>
				) : (
					<ul className="flex flex-wrap gap-2" aria-label="Granted scopes">
						{[...granted]
							.sort((a, b) => a.localeCompare(b))
							.map((scope) => (
								<li key={scope}>
									<Badge variant="default" className="font-mono text-[11px]">
										{scope}
									</Badge>
								</li>
							))}
					</ul>
				)}
			</CardBody>

			{editing && (
				<EditScopesDialog
					actorName={actorName}
					granted={granted}
					catalogue={catalogue.data ?? []}
					catalogueLoading={catalogue.isPending}
					catalogueError={catalogue.error as Error | null}
					saving={replace.isPending}
					onClose={() => setEditing(false)}
					onSave={async (next) => {
						try {
							await replace.mutateAsync({ id: actorId, scopes: next });
							setEditing(false);
							return null;
						} catch (e) {
							// The hook toasts a generic failure; surface a clearer
							// in-dialog message for the common 403 (can't grant).
							if (e instanceof AgentsApiError && e.status === 403) {
								return 'You don’t have permission to grant one or more of these scopes.';
							}
							return e instanceof Error ? e.message : 'Failed to save scopes.';
						}
					}}
				/>
			)}
		</Card>
	);
}

interface EditScopesDialogProps {
	actorName: string;
	granted: string[];
	catalogue: PermissionCatalogEntry[];
	catalogueLoading: boolean;
	catalogueError: Error | null;
	saving: boolean;
	onClose: () => void;
	/** Returns an error message to display, or null on success. */
	onSave: (scopes: string[]) => Promise<string | null>;
}

function EditScopesDialog({
	actorName,
	granted,
	catalogue,
	catalogueLoading,
	catalogueError,
	saving,
	onClose,
	onSave,
}: EditScopesDialogProps) {
	const [selected, setSelected] = useState<string[]>(granted);
	const [error, setError] = useState<string | null>(null);
	// Scopes pending save that need an explicit confirmation (high-privilege
	// removals). Null when no confirmation is in flight.
	const [confirmRemoval, setConfirmRemoval] = useState<{
		next: string[];
		removed: string[];
	} | null>(null);

	const scopes = useMemo(() => catalogueToScopes(catalogue), [catalogue]);
	const disabledScopes = useMemo(
		() => catalogue.filter((p) => !p.grantableByCaller).map((p) => p.name),
		[catalogue],
	);
	const disabledSet = useMemo(() => new Set(disabledScopes), [disabledScopes]);
	const knownScopes = useMemo(() => new Set(catalogue.map((p) => p.name)), [catalogue]);

	// A scope already granted but absent from the catalogue (or not grantable by
	// this caller) must survive a save untouched — we can't show it in the
	// picker, but dropping it would silently revoke it. Track it separately.
	const preserved = useMemo(
		() => granted.filter((s) => !knownScopes.has(s) || disabledSet.has(s)),
		[granted, knownScopes, disabledSet],
	);

	// The full set that a save would persist (picker selection + preserved
	// non-editable grants). Save is only meaningful when it differs from what
	// the actor already holds, so we disable the button until then.
	const dirty = useMemo(() => {
		const next = new Set([...selected, ...preserved]);
		const current = new Set(granted);
		if (next.size !== current.size) return true;
		for (const s of next) if (!current.has(s)) return true;
		return false;
	}, [selected, preserved, granted]);

	const toggle = (scope: string): void => {
		if (disabledSet.has(scope)) return;
		setSelected((prev) =>
			prev.includes(scope) ? prev.filter((s) => s !== scope) : [...prev, scope],
		);
	};
	const selectAll = (group?: string): void => {
		const pool = group
			? scopes.filter(
					(s) => !disabledSet.has(s.scope) && extractResourceFromScope(s.scope) === group,
				)
			: scopes.filter((s) => !disabledSet.has(s.scope));
		setSelected((prev) => Array.from(new Set([...prev, ...pool.map((s) => s.scope)])));
	};
	const deselectAll = (group?: string): void => {
		if (!group) {
			// Keep any disabled-but-granted selections (can't toggle them off here).
			setSelected((prev) => prev.filter((s) => disabledSet.has(s)));
			return;
		}
		setSelected((prev) =>
			prev.filter((s) => extractResourceFromScope(s) !== group || disabledSet.has(s)),
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
		// Guard against silently revoking a previously-held high-privilege scope
		// (e.g. an admin "Deselect all"-ing a grantable org:admin the actor holds).
		const nextSet = new Set(next);
		const removed = granted.filter((s) => HIGH_PRIVILEGE_SCOPES.has(s) && !nextSet.has(s));
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
				title={`Edit scopes — ${actorName}`}
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
							Save scopes
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
								{preserved.length} existing scope
								{preserved.length === 1 ? '' : 's'} not editable here will be
								preserved.
							</p>
						)}
						<ScopePicker
							scopes={scopes}
							selectedScopes={selected.filter(
								(s) => knownScopes.has(s) && !disabledSet.has(s),
							)}
							disabledScopes={disabledScopes}
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
				title="Remove high-privilege scope?"
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
