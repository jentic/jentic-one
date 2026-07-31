/**
 * CardStatusPill — "Imported" vs "Available" badge (+ optional "Update available").
 *
 * A small, self-contained presentational pill. Mirrors jentic-mini's
 * CardStatusPill visual language (emerald filled for imported, neutral outline
 * for available) so the Discover grid reads identically. Keyed off the catalog
 * entry's `registered` flag — the single source of truth under D-005a. A
 * registered entry with an upstream spec update also gets a warning-styled
 * "Update available" badge, matching the Workspace ApiCard signal.
 */
import { CheckCircle2, Globe, Loader2, RefreshCw } from 'lucide-react';

interface CardStatusPillProps {
	registered: boolean;
	/** Import job in flight — overrides the Available state with a spinner pill. */
	pending?: boolean;
	/**
	 * Registered entry whose upstream spec has an update the local revision
	 * hasn't adopted. Renders an extra "Update available" badge beside the
	 * Imported pill, mirroring the Workspace ApiCard signal.
	 */
	updateAvailable?: boolean;
	className?: string;
}

const SPEC = {
	imported: {
		label: 'Imported',
		icon: CheckCircle2,
		cls: 'bg-emerald-500/15 text-emerald-300 ring-emerald-500/30',
		testId: 'card-status-imported',
	},
	available: {
		label: 'Available',
		icon: Globe,
		cls: 'border-border/70 bg-transparent text-muted-foreground ring-border/60',
		testId: 'card-status-available',
	},
	pending: {
		label: 'Importing…',
		icon: Loader2,
		cls: 'bg-primary/10 text-primary ring-primary/30',
		testId: 'card-status-pending',
	},
	updateAvailable: {
		label: 'Update available',
		icon: RefreshCw,
		cls: 'bg-warning/15 text-warning ring-warning/30',
		testId: 'card-status-update-available',
	},
} as const;

export function CardStatusPill({
	registered,
	pending,
	updateAvailable,
	className,
}: CardStatusPillProps) {
	const spec = pending ? SPEC.pending : registered ? SPEC.imported : SPEC.available;
	const Icon = spec.icon;
	const UpdateIcon = SPEC.updateAvailable.icon;
	// Only surface the update badge on a settled (non-pending) imported entry —
	// mid-import the pending spinner is the honest state.
	const showUpdate = updateAvailable && registered && !pending;
	return (
		<>
			<span
				data-testid={spec.testId}
				className={`inline-flex shrink-0 items-center gap-1 rounded-full px-2 py-0 text-[11px] font-medium whitespace-nowrap ring-1 ${spec.cls} ${className ?? ''}`}
			>
				<Icon
					size={11}
					aria-hidden="true"
					className={pending ? 'animate-spin' : undefined}
				/>
				{spec.label}
			</span>
			{showUpdate && (
				<span
					data-testid={SPEC.updateAvailable.testId}
					className={`inline-flex shrink-0 items-center gap-1 rounded-full px-2 py-0 text-[11px] font-medium whitespace-nowrap ring-1 ${SPEC.updateAvailable.cls}`}
				>
					<UpdateIcon size={11} aria-hidden="true" />
					{SPEC.updateAvailable.label}
				</span>
			)}
		</>
	);
}
