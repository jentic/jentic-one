/**
 * Shared scaffolding for the agent panels the Agents page hosts (dock sheets,
 * the API access sidebar) — the pieces they would otherwise copy. The card
 * shells themselves (`DetailSection`, `DangerZone`, `IdentitySettingsCard`,
 * `AuditTrailCard`) are shared product-wide from `@/shared/ui`.
 */
import type { ReactNode } from 'react';
import type { PermissionRule as DisplayRule } from '@/shared/lib';
import type { PermissionRule as EditorRule } from '@/shared/credentials/api/vendors-types';
import type { BindingPermissionRule } from '@/modules/agents/api';

/** A compact label/value pair used in the attribution / key meta grids. */
export function MetaItem({ label, value }: { label: string; value: ReactNode }) {
	return (
		<div className="min-w-0">
			<dt className="text-muted-foreground text-[10px] tracking-wider uppercase">{label}</dt>
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
// Direct-binding surfaces — the rule projections (display and editor shapes)
// and the motion preset shared by the API access sheet and its rule editor and
// tester.
// ---------------------------------------------------------------------------

/** The condition fields every rule shape here carries — the stored read rule and
 * the write input alike (their `effect`/`match_mode` are distinct generated
 * string enums with identical values). */
interface RuleConditions {
	effect: string;
	methods?: string[] | null;
	path?: string | null;
	match_mode?: string | null;
	operations?: string[] | null;
}

/** One rule in the shared display shape `ruleSummary` reads.
 * Regex is the default; only non-default modes change how the path reads, so
 * they alone survive into the display shape. */
export function toDisplayRule(rule: RuleConditions): DisplayRule {
	const mode = String(rule.match_mode ?? 'regex');
	return {
		effect: String(rule.effect) === 'deny' ? 'deny' : 'allow',
		methods: rule.methods ?? null,
		path: rule.path ?? null,
		match_mode: mode === 'prefix' || mode === 'exact' ? mode : null,
		operations: rule.operations ?? null,
	};
}

/**
 * Project a binding's stored rules into the shared display shape consumed by
 * `ruleSummary` — so "what can this credential do" reads identically wherever
 * the grant is shown. System safety rules are dropped: they are backend-owned
 * plumbing, not part of the operator's grant.
 */
export function toDisplayRules(rules: BindingPermissionRule[] | null | undefined): DisplayRule[] {
	return (rules ?? []).filter((rule) => !rule._system).map(toDisplayRule);
}

/** One rule in the credentials kit's shape (`RuleListEditor`,
 * `OperationImpactPreview`). */
export function toEditorRule(rule: RuleConditions): EditorRule {
	const mode = rule.match_mode;
	return {
		effect: String(rule.effect) === 'deny' ? 'deny' : 'allow',
		methods: rule.methods ?? null,
		path: rule.path ?? null,
		match_mode: mode === 'prefix' || mode === 'exact' || mode === 'regex' ? mode : undefined,
		operations: rule.operations ?? null,
	};
}

/** Expand/collapse motion for inline panels (the rule editor's pending-changes diff). */
export const panelMotion = {
	initial: { opacity: 0, height: 0 },
	animate: { opacity: 1, height: 'auto' as const },
	exit: { opacity: 0, height: 0 },
	transition: { duration: 0.2, ease: 'easeOut' as const },
};
