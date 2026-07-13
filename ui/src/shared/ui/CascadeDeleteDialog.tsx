import { useEffect, useId, useState } from 'react';
import { AlertTriangle, CircleDot, Trash2 } from 'lucide-react';
import { Button } from '@/shared/ui/Button';
import { Dialog } from '@/shared/ui/Dialog';
import { ErrorAlert } from '@/shared/ui/ErrorAlert';
import { Input } from '@/shared/ui/Input';
import { Label } from '@/shared/ui/Label';

/**
 * The entity kinds that expose a hard delete (or a terminal,
 * delete-equivalent action like agent/service-account *archive*).
 */
export type CascadeEntityType = 'credential' | 'api' | 'toolkit' | 'agent' | 'service-account';

/**
 * One group in the blast-radius list. `count` is authoritative (drives the
 * "3 toolkit bindings" headline); `names` is optional detail the dialog lists
 * underneath when present. Shaped to be filled directly from a future backend
 * dependents / `?dry_run=1` response — until that lands, callers omit
 * `dependents` and the dialog falls back to the type-specific generic warning.
 */
export interface CascadeDependentGroup {
	/** Human label for the group, e.g. "toolkit binding" (singular). */
	label: string;
	/** How many of this dependent the cascade removes. */
	count: number;
	/** Optional names to list under the headline. */
	names?: string[];
}

interface CascadeDeleteDialogProps {
	open: boolean;
	onClose: () => void;
	onConfirm: () => void;
	entityType: CascadeEntityType;
	entityName: string;
	/**
	 * The cascade blast radius. When provided, the dialog renders a grouped
	 * list of everything the delete also takes down. When absent (today's
	 * default — the backend dependents endpoint doesn't exist yet), the dialog
	 * renders a strong, type-specific generic warning instead.
	 */
	dependents?: CascadeDependentGroup[];
	loading?: boolean;
	error?: Error | string | null;
}

/** Per-type copy: title, the destructive verb, and what a delete typically takes down. */
const TYPE_COPY: Record<
	CascadeEntityType,
	{ title: string; confirmLabel: string; noun: string; warning: string }
> = {
	credential: {
		title: 'Delete credential',
		confirmLabel: 'Delete credential',
		noun: 'credential',
		warning:
			'Agents and toolkits that authenticate with this credential will stop working until you bind a replacement.',
	},
	api: {
		title: 'Remove API',
		confirmLabel: 'Remove API',
		noun: 'API',
		warning:
			'This API and all of its operations leave your workspace. Toolkits and credentials that reference it will no longer resolve until you re-import it.',
	},
	toolkit: {
		title: 'Delete toolkit',
		confirmLabel: 'Delete toolkit',
		noun: 'toolkit',
		warning:
			'Agents granted this toolkit will fail their next call, and any API keys minted for it stop working immediately.',
	},
	agent: {
		title: 'Archive agent',
		confirmLabel: 'Archive agent',
		noun: 'agent',
		warning:
			'Archiving is permanent — the agent can no longer authenticate or be restored, and its grants and access requests are released.',
	},
	'service-account': {
		title: 'Archive service account',
		confirmLabel: 'Archive service account',
		noun: 'service account',
		warning:
			'Archiving is permanent — the service account can no longer authenticate or be restored, and its grants are released.',
	},
};

/**
 * Pluralise a dependent group's label off its count, e.g.
 * `{ label: 'toolkit binding', count: 3 }` → "3 toolkit bindings".
 *
 * Intentionally naive (`label + 's'`): callers pass singular, space-safe
 * English labels ("agent grant", "API key", "credential binding") where a
 * trailing `s` is correct. If a future label needs irregular pluralisation,
 * pass the plural form explicitly rather than expanding this helper.
 */
function describeGroup(group: CascadeDependentGroup): string {
	const label = group.count === 1 ? group.label : `${group.label}s`;
	return `${group.count} ${label}`;
}

/**
 * Shared, cascade-aware confirmation for destructive deletes across entity
 * types. Two modes:
 *
 *  - **Generic-warning mode** (no `dependents`): a strong, type-specific
 *    warning that names what a delete of THIS type typically takes down —
 *    better than a flat "this can't be undone".
 *  - **Blast-radius mode** (`dependents` provided): a grouped list of exactly
 *    what the cascade removes, with counts and (optionally) names.
 *
 * Both modes gate the destructive action behind a type-to-confirm field: the
 * user must type the entity name before the delete button enables. This is a
 * deliberate friction step for irreversible actions.
 */
export function CascadeDeleteDialog({
	open,
	onClose,
	onConfirm,
	entityType,
	entityName,
	dependents,
	loading = false,
	error,
}: CascadeDeleteDialogProps) {
	const copy = TYPE_COPY[entityType];
	const [typed, setTyped] = useState('');
	// Whether the user has attempted a confirm in *this* dialog session. The
	// mutation `error` lives on the caller's hook and survives close/reopen, so
	// gating on this flag stops a prior session's failure from flashing a stale
	// red alert the moment the dialog reopens.
	const [attempted, setAttempted] = useState(false);
	// One `useId` call, deterministic suffixes. `useId` already returns a
	// document-unique, SSR-stable id — concatenating it into another string is
	// fine for namespacing but should only consume one hook slot.
	const reactId = useId();
	const descriptionId = `${reactId}-desc`;
	const confirmInputId = `${reactId}-confirm`;

	// Type-to-confirm is transient input, not a draft worth preserving — clear
	// it (and the in-session attempt flag) on every (re)open so a fresh dialog
	// never inherits a stale match or a stale error.
	useEffect(() => {
		if (open) {
			setTyped('');
			setAttempted(false);
		}
	}, [open]);

	const confirmed = typed.trim() === entityName.trim();
	const hasDependents = Array.isArray(dependents) && dependents.length > 0;
	// Only surface the error once the user has actually tried this session;
	// hold the narrowed value (not just a boolean) so the JSX renders cleanly.
	const visibleError = attempted && error != null && error !== '' ? error : null;

	const handleClose = (): void => {
		if (!loading) onClose();
	};

	const handleConfirm = (): void => {
		setAttempted(true);
		onConfirm();
	};

	return (
		<Dialog
			open={open}
			onClose={handleClose}
			title={copy.title}
			size="md"
			describedById={descriptionId}
			footer={
				open ? (
					<>
						<Button variant="secondary" onClick={handleClose} disabled={loading}>
							Cancel
						</Button>
						<Button
							variant="danger"
							onClick={handleConfirm}
							loading={loading}
							disabled={!confirmed}
						>
							<Trash2 className="h-4 w-4" />
							{copy.confirmLabel}
						</Button>
					</>
				) : null
			}
		>
			{open ? (
				<div className="space-y-4">
					<p id={descriptionId} className="text-foreground text-sm leading-relaxed">
						<span className="font-semibold">{entityName}</span> will be permanently
						removed from your workspace. This can&apos;t be undone.
					</p>

					{hasDependents ? (
						<BlastRadius noun={copy.noun} dependents={dependents} />
					) : (
						<div className="border-danger/30 bg-danger/5 text-foreground/90 flex gap-2.5 rounded-lg border px-3.5 py-3 text-xs leading-relaxed">
							<AlertTriangle className="text-danger mt-0.5 h-4 w-4 shrink-0" />
							<p>{copy.warning}</p>
						</div>
					)}

					<div className="space-y-1.5">
						<Label htmlFor={confirmInputId}>
							Type <span className="text-foreground font-semibold">{entityName}</span>{' '}
							to confirm
						</Label>
						<Input
							id={confirmInputId}
							value={typed}
							onChange={(e): void => setTyped(e.target.value)}
							disabled={loading}
							autoComplete="off"
							placeholder={entityName}
						/>
					</div>

					{visibleError != null && <ErrorAlert message={visibleError} />}
				</div>
			) : null}
		</Dialog>
	);
}

/**
 * The grouped "this will also remove" list. Rendered only when the caller
 * supplies a non-empty `dependents` array; until the backend dependents
 * endpoint exists, this branch is dormant and the dialog shows the generic
 * warning instead.
 */
function BlastRadius({ noun, dependents }: { noun: string; dependents: CascadeDependentGroup[] }) {
	const total = dependents.reduce((sum, g) => sum + g.count, 0);

	return (
		<div className="border-danger/30 bg-danger/5 space-y-3 rounded-lg border px-3.5 py-3">
			<div className="flex items-center gap-2">
				<AlertTriangle className="text-danger h-4 w-4 shrink-0" />
				<span className="text-foreground text-xs font-medium">
					Deleting this {noun} will also remove {total}{' '}
					{total === 1 ? 'dependent' : 'dependents'}
				</span>
			</div>
			<ul className="space-y-2.5">
				{dependents.map((group) => (
					<li key={group.label}>
						<div className="flex items-center gap-2">
							<CircleDot className="text-danger h-2.5 w-2.5 shrink-0" />
							<span className="text-foreground/90 text-xs font-medium">
								{describeGroup(group)}
							</span>
						</div>
						{group.names && group.names.length > 0 && (
							<ul className="mt-1 space-y-0.5 pl-[18px]">
								{group.names.map((name, i) => (
									<li
										key={`${group.label}-${i}`}
										className="text-muted-foreground truncate text-[11px]"
									>
										{name}
									</li>
								))}
							</ul>
						)}
					</li>
				))}
			</ul>
		</div>
	);
}
