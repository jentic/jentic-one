/**
 * CardStatusPill — a catalog entry's relation to your workspace: "In your
 * workspace" vs "Available".
 *
 * A small, self-contained presentational pill (emerald filled for imported,
 * neutral outline for available). Keyed off the catalog entry's `registered`
 * flag — the single source of truth under D-005a. An upstream update is a
 * serving state, rendered beside it with the shared `ApiStateBadge`. An import
 * in flight is shown on the card's primary button and the docked panel's
 * "Adding" section, not here.
 */
import { CheckCircle2, Globe } from 'lucide-react';

interface CardStatusPillProps {
	registered: boolean;
	className?: string;
}

const SPEC = {
	imported: {
		label: 'In your workspace',
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
} as const;

export function CardStatusPill({ registered, className }: CardStatusPillProps) {
	const spec = registered ? SPEC.imported : SPEC.available;
	const Icon = spec.icon;
	return (
		<span
			data-testid={spec.testId}
			className={`inline-flex shrink-0 items-center gap-1 rounded-full px-2 py-0 text-[11px] font-medium whitespace-nowrap ring-1 ${spec.cls} ${className ?? ''}`}
		>
			<Icon size={11} aria-hidden="true" />
			{spec.label}
		</span>
	);
}
