import { TriangleAlert } from 'lucide-react';
import { Button } from '@/shared/ui/Button';
import { DetailSection } from '@/shared/ui/DetailSection';

/**
 * DangerZone — the Settings tab's destructive-actions card, shared by the
 * detail consoles (toolkit, agent, service account) so irreversible actions
 * read identically everywhere: the danger-tinted `DetailSection` shell with
 * one row per action.
 *
 * Purely presentational: every button defers to the caller (which owns the
 * confirm dialog and the mutation) — this component never mutates state.
 * Renders nothing when no destructive verb applies.
 */

export interface DangerZoneAction {
	/** Stable key handed back through `onAction`. */
	key: string;
	title: string;
	description: string;
	buttonLabel: string;
	/** Accessible label carrying the entity name (e.g. "Disable support-agent"). */
	ariaLabel: string;
	/**
	 * `solid` for terminal actions (archive, delete); `outline` for reversible
	 * ones (disable).
	 */
	emphasis?: 'solid' | 'outline';
}

export interface DangerZoneProps {
	actions: DangerZoneAction[];
	/** True while any related mutation is in flight (disables every button). */
	pending?: boolean;
	onAction: (key: string) => void;
}

export function DangerZone({ actions, pending = false, onAction }: DangerZoneProps) {
	if (actions.length === 0) return null;
	return (
		<DetailSection
			title="Danger zone"
			icon={<TriangleAlert className="h-4 w-4" />}
			danger
			bodyClassName="divide-border/60 divide-y space-y-0"
		>
			{actions.map((action) => (
				<div
					key={action.key}
					className="flex flex-wrap items-center justify-between gap-3 py-3 first:pt-0 last:pb-0"
				>
					<div className="min-w-0">
						<p className="text-foreground text-sm font-medium">{action.title}</p>
						<p className="text-muted-foreground max-w-prose text-xs">
							{action.description}
						</p>
					</div>
					<Button
						size="sm"
						variant={action.emphasis === 'outline' ? 'outline' : 'danger'}
						className={
							action.emphasis === 'outline'
								? 'border-danger/40 text-danger hover:bg-danger/10 shrink-0'
								: 'shrink-0'
						}
						disabled={pending}
						onClick={() => onAction(action.key)}
						aria-label={action.ariaLabel}
					>
						{action.buttonLabel}
					</Button>
				</div>
			))}
		</DetailSection>
	);
}
