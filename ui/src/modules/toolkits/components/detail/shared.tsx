import type { PermissionRule as DisplayRule } from '@/shared/lib';
import type { PermissionRule } from '@/modules/toolkits/api/types';

/**
 * Shared scaffolding for the toolkit detail tabs (see `ToolkitDetailBody`).
 * Every tab renders one or more `DetailSection` cards (the shared primitive —
 * `@/shared/ui`); rows animate in/out with the same motion presets the
 * pre-tab layout used, so the split does not change the page's feel.
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
		.map((rule) => {
			const mode = String(rule.match_mode ?? 'regex');
			return {
				// The generated read enum and the display union share the same
				// 'allow'/'deny' strings; String() bridges the nominal enum type.
				effect: String(rule.effect) === 'deny' ? ('deny' as const) : ('allow' as const),
				methods: rule.methods ?? null,
				path: rule.path ?? null,
				// regex is the default; only non-default modes change how the path
				// reads, so they alone survive into the display shape.
				match_mode: mode === 'prefix' || mode === 'exact' ? mode : null,
				operations: rule.operations ?? null,
			};
		});
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
