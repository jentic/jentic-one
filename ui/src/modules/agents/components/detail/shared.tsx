/**
 * Shared scaffolding for the agent / service-account detail consoles — the
 * pieces both pages (and their tab panels) would otherwise copy. The card
 * shells themselves (`DetailSection`, `DangerZone`, `IdentitySettingsCard`,
 * `AuditTrailCard`) are shared product-wide from `@/shared/ui`.
 */
import type { ReactNode } from 'react';
import type { PermissionRule as DisplayRule } from '@/shared/lib';
import type { BindingPermissionRule } from '@/modules/agents/api';

/** A compact label/value pair used in the attribution / key meta grids. */
export function MetaItem({ label, value }: { label: string; value: ReactNode }) {
	return (
		<div className="min-w-0">
			<dt className="text-muted-foreground/70 text-[10px] tracking-wider uppercase">
				{label}
			</dt>
			<dd className="text-foreground/90 mt-0.5 truncate text-xs">{value}</dd>
		</div>
	);
}

/** "99.2%" success share, or an em-dash when there's no traffic to judge. */
export function successShare(success: number, total: number): string {
	if (total === 0) return '—';
	return `${((success / total) * 100).toFixed(1).replace(/\.0$/, '')}%`;
}

// ---------------------------------------------------------------------------
// Direct-binding surfaces (theme 5 phase 5a) — motion presets and the rule
// display projection the "Bound credentials" card and its rule editor share.
// ---------------------------------------------------------------------------

/**
 * Project a binding's stored rules into the shared display shape consumed by
 * `OperationsSummary`/`OperationsDialog` — the SAME preview the access-request
 * review cards render, so "what can this credential do" reads identically at
 * review time and on the live binding. System safety rules are dropped: they
 * are backend-owned plumbing, not part of the operator's grant.
 */
export function toDisplayRules(rules: BindingPermissionRule[] | null | undefined): DisplayRule[] {
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

/** Row enter/exit motion for binding rows (matches the toolkit console feel). */
export const rowMotion = {
	initial: { opacity: 0, y: -4, height: 0 },
	animate: { opacity: 1, y: 0, height: 'auto' as const },
	exit: { opacity: 0, y: -4, height: 0 },
	transition: { duration: 0.18, ease: 'easeOut' as const },
};

/** Expand/collapse motion for inline panels (rule editor, tester disclosure). */
export const panelMotion = {
	initial: { opacity: 0, height: 0 },
	animate: { opacity: 1, height: 'auto' as const },
	exit: { opacity: 0, height: 0 },
	transition: { duration: 0.2, ease: 'easeOut' as const },
};
