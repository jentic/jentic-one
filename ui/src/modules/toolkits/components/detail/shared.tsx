import type { ReactNode } from 'react';
import { Button } from '@/shared/ui';
import type { PermissionRule as DisplayRule } from '@/shared/lib';
import type { PermissionRule } from '@/modules/toolkits/api/types';

/**
 * Shared scaffolding for the toolkit detail tabs (see `ToolkitDetailBody`).
 * Every tab renders one or more `DetailSection` cards; rows animate in/out
 * with the same motion presets the pre-tab layout used, so the split does not
 * change the page's feel.
 */

/**
 * Project a binding's stored rules into the shared display shape consumed by
 * `OperationsSummary`/`OperationsDialog` — the SAME preview the access-request
 * review cards render, so "what can this credential do" reads identically at
 * review time and on the live binding. System safety rules are dropped: they
 * are backend-owned plumbing, not part of the operator's grant.
 */
export function toDisplayRules(rules: PermissionRule[] | null | undefined): DisplayRule[] {
	return (rules ?? [])
		.filter((rule) => !rule._system)
		.map((rule) => ({
			// The generated read enum and the display union share the same
			// 'allow'/'deny' strings; String() bridges the nominal enum type.
			effect: String(rule.effect) === 'deny' ? ('deny' as const) : ('allow' as const),
			methods: rule.methods ?? null,
			path: rule.path ?? null,
			operations: rule.operations ?? null,
		}));
}

export const rowMotion = {
	initial: { opacity: 0, y: -4, height: 0 },
	animate: { opacity: 1, y: 0, height: 'auto' as const },
	exit: { opacity: 0, y: -4, height: 0 },
	transition: { duration: 0.18, ease: 'easeOut' as const },
};

export const panelMotion = {
	initial: { opacity: 0, height: 0 },
	animate: { opacity: 1, height: 'auto' as const },
	exit: { opacity: 0, height: 0 },
	transition: { duration: 0.2, ease: 'easeOut' as const },
};

export function RowSkeleton() {
	return (
		<div className="bg-muted/30 border-border/60 flex items-center gap-3 rounded-lg border p-3">
			<div className="bg-muted h-8 w-8 shrink-0 animate-pulse rounded-lg" />
			<div className="min-w-0 flex-1 space-y-1.5">
				<div className="bg-muted h-3.5 w-1/3 animate-pulse rounded" />
				<div className="bg-muted h-3 w-1/2 animate-pulse rounded" />
			</div>
		</div>
	);
}

export interface SectionActionProps {
	label: ReactNode;
	onClick: () => void;
	variant?: 'primary' | 'secondary';
}

interface DetailSectionProps {
	/** Section heading (sentence-case, `font-heading font-semibold` ladder). */
	title: ReactNode;
	/**
	 * Leading glyph for the heading (`h-4 w-4`), rendered in the same muted
	 * icon medallion the dashboard sections use — one grammar everywhere.
	 */
	icon?: ReactNode;
	/** Extra inline content next to the title (e.g. a "Keys blocked" pill). */
	titleExtra?: ReactNode;
	/** Right-aligned header action button. */
	action?: SectionActionProps;
	/** Danger-tinted borders (suspended keys section). */
	danger?: boolean;
	/** Extra classes on the card shell (e.g. `h-full` in equal-height grids). */
	className?: string;
	/** Right-aligned header content when `action` (a button) doesn't fit — e.g. a link. */
	trailing?: ReactNode;
	children: ReactNode;
}

/** The card shell every toolkit-detail section renders inside. */
export function DetailSection({
	title,
	icon,
	titleExtra,
	action,
	danger,
	className,
	trailing,
	children,
}: DetailSectionProps) {
	return (
		<div
			className={`flex flex-col overflow-hidden rounded-xl border ${danger ? 'border-danger/50' : 'border-border'} bg-card ${className ?? ''}`}
		>
			<div
				className={`flex flex-wrap items-center justify-between gap-2 px-4 py-3.5 sm:px-5 sm:py-4 ${
					danger ? 'border-danger/30 bg-danger/5 border-b' : 'border-border border-b'
				}`}
			>
				<div className="flex items-center gap-2.5">
					{icon && (
						<span
							aria-hidden="true"
							className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-lg ring-1 ${
								danger
									? 'bg-danger/10 text-danger ring-danger/25'
									: 'bg-muted text-muted-foreground ring-border'
							}`}
						>
							{icon}
						</span>
					)}
					<h3 className="font-heading text-foreground font-semibold">{title}</h3>
					{titleExtra}
				</div>
				{action && (
					<Button
						variant={action.variant ?? 'secondary'}
						size="sm"
						onClick={action.onClick}
					>
						{action.label}
					</Button>
				)}
				{trailing}
			</div>
			<div className="flex-1 space-y-2 px-4 py-3.5 sm:px-5 sm:py-4">{children}</div>
		</div>
	);
}

interface EmptyRowProps {
	icon: ReactNode;
	children: ReactNode;
}

/** Dashed empty-state panel used inside a `DetailSection`. */
export function EmptyRow({ icon, children }: EmptyRowProps) {
	return (
		<div className="border-border/50 rounded-lg border border-dashed px-5 py-6 text-center">
			<span className="text-muted-foreground/50 mx-auto block h-6 w-6 [&>svg]:h-6 [&>svg]:w-6">
				{icon}
			</span>
			<p className="text-muted-foreground mt-2 text-sm">{children}</p>
		</div>
	);
}
