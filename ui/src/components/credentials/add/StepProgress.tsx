import { cn } from '@/lib/utils';
import type { AddCredentialStep } from '@/hooks/useAddCredentialDialog';

/**
 * Compact step-progress indicator for `<AddCredentialDialog>`.
 *
 * Three dots representing the user-facing journey: "Pick API → Set
 * up → Done". The `existing` and `configure` steps both map to the
 * middle dot — from a user perspective they're a single "set this
 * up" phase even though internally we route through two sub-steps.
 *
 * Pure presentational; no state of its own.
 */
export interface StepProgressProps {
	step: AddCredentialStep;
	/** When true, render only two dots (Pick API + Set up). Used when
	 *  the dialog opens without a selected API. */
	hideConfirmDot?: boolean;
	className?: string;
}

interface DotMeta {
	label: string;
	matchesSteps: AddCredentialStep[];
}

const DOTS: DotMeta[] = [
	{ label: 'Pick API', matchesSteps: ['search'] },
	{ label: 'Set up', matchesSteps: ['existing', 'configure'] },
	{ label: 'Done', matchesSteps: ['confirm'] },
];

function dotIndexForStep(step: AddCredentialStep): number {
	return DOTS.findIndex((d) => d.matchesSteps.includes(step));
}

export function StepProgress({ step, hideConfirmDot, className }: StepProgressProps) {
	const dots = hideConfirmDot ? DOTS.slice(0, 2) : DOTS;
	const activeIndex = dotIndexForStep(step);
	return (
		<ol className={cn('flex items-center gap-2', className)} aria-label="Step progress">
			{dots.map((dot, idx) => {
				const isComplete = activeIndex > idx;
				const isActive = activeIndex === idx;
				return (
					<li
						key={dot.label}
						className="flex items-center gap-2"
						aria-current={isActive ? 'step' : undefined}
					>
						<span
							aria-hidden
							className={cn(
								'h-1.5 rounded-full transition-all',
								isActive ? 'bg-primary w-6' : 'w-1.5',
								isComplete && 'bg-primary/60',
								!isActive && !isComplete && 'bg-muted-foreground/30',
							)}
						/>
						<span
							className={cn(
								'text-xs',
								isActive ? 'text-foreground font-medium' : 'text-muted-foreground',
							)}
						>
							{dot.label}
						</span>
						{idx < dots.length - 1 && (
							<span
								aria-hidden
								className="bg-muted-foreground/30 h-px w-3 shrink-0"
							/>
						)}
					</li>
				);
			})}
		</ol>
	);
}
