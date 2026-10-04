import { useEffect, useMemo, useRef, useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { ArrowUpDown, Minus, Plus, RotateCcw, Save, ShieldCheck } from 'lucide-react';
import {
	Button,
	allowAllRule,
	cleanPermissionRule,
	grantsEverything,
	isEmptyAllowRule,
} from '@/shared/ui';
import { ruleSummary, type PermissionRule as DisplayRule } from '@/shared/lib';
import {
	useReplaceAgentBindingPermissions,
	type BindingPermissionRule,
	type PermissionRuleInput,
} from '@/modules/agents/api';
import {
	panelMotion,
	toDisplayRule,
	toEditorRule,
} from '@/modules/agents/components/detail/shared';
import { RuleListEditor } from '@/shared/credentials/components/RuleListEditor';
import { useVendorOperations } from '@/shared/credentials/api/vendors-hooks';
import type { OpsApiReference } from '@/shared/credentials/components/OperationImpactPreview';
import type { PermissionRule as EditorRule } from '@/shared/credentials/api/vendors-types';

/**
 * Inline editor for the permission rules on one direct agent↔credential
 * binding (theme 5 phase 5a, transplanted from the toolkit rule editor).
 * System safety rules (`_system: true`) are platform-managed — they are
 * filtered out of the editor so saving never persists them as agent rules.
 *
 * The draft is diffed live against the saved rules into a "Pending changes"
 * panel (− removed / + added, in the same `ruleSummary` voice the platform
 * uses everywhere), so the operator sees exactly which grants a save will
 * revoke or introduce before committing. Because evaluation is
 * first-match-wins, a pure reorder is also a change — dirtiness is
 * order-sensitive and reorders get their own pending-changes line.
 *
 * `onDirtyChange` reports that dirtiness to the host so the dry-run tester, which
 * evaluates SAVED rules, can disable itself while a draft diverges.
 */
export interface AgentBindingPermissionsEditorProps {
	agentId: string;
	credentialId: string;
	credentialLabel: string;
	initialRules: BindingPermissionRule[];
	/** Reports the live draft-vs-saved dirtiness (order-sensitive). */
	onDirtyChange?: (dirty: boolean) => void;
	/**
	 * The API the credential is bound against. Feeds the rule editor's path
	 * autocomplete and "no operations affected" warning from the API's real
	 * operations. Omitted (no concrete version known) → the editor works
	 * without suggestions.
	 */
	apiReference?: OpsApiReference | null;
}

/** Reverse of `toEditorRule` — for saving edits back through the agent-module API. */
function fromEditorRule(rule: EditorRule): PermissionRuleInput {
	return {
		effect: rule.effect as PermissionRuleInput['effect'],
		methods: rule.methods ?? undefined,
		path: rule.path ?? undefined,
		match_mode: (rule.match_mode as PermissionRuleInput['match_mode']) ?? undefined,
		operations: rule.operations ?? undefined,
	};
}

function toInput(rule: BindingPermissionRule): PermissionRuleInput {
	// `effect`/`match_mode` are distinct generated string enums (read vs write
	// schema) with identical values; TS treats string-enum members as assignable
	// across them, so copying directly is type-safe (verified under `strict`).
	return {
		effect: rule.effect,
		methods: rule.methods ?? undefined,
		path: rule.path ?? undefined,
		match_mode: rule.match_mode ?? undefined,
		operations: rule.operations ?? undefined,
	};
}

/** Canonical key for one rule — order-insensitive over its CONDITIONS only. */
function canon(rule: DisplayRule): string {
	return JSON.stringify({
		e: rule.effect,
		m: [...(rule.methods ?? [])].sort(),
		p: rule.path ?? null,
		// regex is the backend default, so normalize it to null for comparison.
		mm: rule.match_mode ?? null,
		o: [...(rule.operations ?? [])].sort(),
	});
}

/** Rules in `a` with no counterpart left in `b` (multiset semantics). */
function diffRules(a: DisplayRule[], b: DisplayRule[]): DisplayRule[] {
	const counts = new Map<string, number>();
	for (const rule of b) {
		const key = canon(rule);
		counts.set(key, (counts.get(key) ?? 0) + 1);
	}
	return a.filter((rule) => {
		const key = canon(rule);
		const left = counts.get(key) ?? 0;
		if (left > 0) {
			counts.set(key, left - 1);
			return false;
		}
		return true;
	});
}

/** One rule in the shared `ruleSummary` voice, without the trailing period. */
function oneLiner(rule: DisplayRule): string {
	return ruleSummary([rule]).replace(/\.$/, '');
}

export function AgentBindingPermissionsEditor({
	agentId,
	credentialId,
	credentialLabel,
	initialRules,
	onDirtyChange,
	apiReference,
}: AgentBindingPermissionsEditorProps) {
	// The saved OPERATOR rules — the draft's starting point, its reset target and
	// the diff's baseline.
	const savedRules = useMemo(
		() => initialRules.filter((r) => !r._system).map(toInput),
		[initialRules],
	);
	const [rules, setRules] = useState<PermissionRuleInput[]>(savedRules);
	const replace = useReplaceAgentBindingPermissions(agentId, credentialId);

	// Feed op paths + templates into the shared editor so autocomplete
	// and the "no ops affected" warning work identically to the
	// connect-flow rules page. Reuses the same query key as the row's
	// ops preview via ``useVendorOperations``.
	const opsQuery = useVendorOperations(apiReference ?? undefined, {
		enabled: !!apiReference,
	});
	const pathSuggestions = useMemo<readonly string[]>(() => {
		const rows = opsQuery.data?.data;
		if (!rows) return [];
		return Array.from(new Set(rows.map((op) => op.path))).sort();
	}, [opsQuery.data]);

	// Drop empty conditions so the wire body (and the diff) never carries noise.
	const clean = rules.map(cleanPermissionRule);
	// A condition-less `allow` is rejected by the backend (422). Block save and
	// rely on the editor's inline warning rather than submitting a known error.
	const hasInvalidRule = clean.some(isEmptyAllowRule);

	// Live draft-vs-saved diff — what a save would revoke (−) and grant (+).
	const savedDisplay = savedRules.map(cleanPermissionRule).map(toDisplayRule);
	const draftDisplay = clean.map(toDisplayRule);
	const added = diffRules(draftDisplay, savedDisplay);
	const removed = diffRules(savedDisplay, draftDisplay);
	// First match wins, so ORDER is part of the grant: a pure permutation of the
	// saved rules must be saveable (and announced), even though the multiset
	// diff is empty.
	const reordered =
		added.length === 0 &&
		removed.length === 0 &&
		draftDisplay.map(canon).join('\u0000') !== savedDisplay.map(canon).join('\u0000');
	const dirty = added.length > 0 || removed.length > 0 || reordered;

	// Lift the dirty flag to the host (tester gating). Effect, not render-time
	// call: the parent may setState in response.
	useEffect(() => {
		onDirtyChange?.(dirty);
	}, [dirty, onDirtyChange]);

	// Retract the report when the editor UNMOUNTS: hosts render it conditionally, and
	// a dirty draft that disappears would otherwise leave the host's flag stuck true,
	// disabling the tester over an editor that no longer exists.
	const onDirtyChangeRef = useRef(onDirtyChange);
	useEffect(() => {
		onDirtyChangeRef.current = onDirtyChange;
	});
	useEffect(
		() => () => {
			onDirtyChangeRef.current?.(false);
		},
		[],
	);

	const save = () => {
		if (hasInvalidRule || !dirty) return;
		replace.mutate(clean);
	};

	const discard = () => {
		setRules(savedRules);
	};

	return (
		// A borderless card (its previous fill) above the sheet; only its form controls
		// carry an edge (`.edged-controls`, ≥3:1 against these close surfaces).
		<div className="bg-surface-inset edged-controls overflow-hidden rounded-lg">
			<div className="px-4 pt-4 sm:px-5">
				<div className="flex flex-wrap items-center gap-x-2.5 gap-y-1">
					<p className="text-foreground text-sm font-semibold">
						Permission rules for {credentialLabel}
					</p>
					{rules.length > 0 && (
						<span className="bg-surface-tonal text-foreground-sub rounded-md px-1.5 py-0.5 font-mono text-[11px]">
							{rules.length} {rules.length === 1 ? 'rule' : 'rules'}
						</span>
					)}
				</div>
				<p className="text-foreground-sub mt-1 text-xs leading-relaxed">
					Rules are evaluated in order — first match wins, anything unmatched is denied.
					System safety rules, when present, are platform-managed and not edited here.
				</p>
			</div>

			<div className="space-y-3 px-4 pt-3 pb-4 sm:px-5">
				<RuleListEditor
					rules={rules.map(toEditorRule)}
					onChange={(next): void => setRules(next.map(fromEditorRule))}
					pathSuggestions={pathSuggestions}
					opTemplates={pathSuggestions}
					opsLoaded={pathSuggestions.length > 0}
					addActions={
						// The catch-all shortcut sits beside "Add rule" — reachable with
						// rules already present, since broadening a narrow grant is a
						// normal edit.
						!grantsEverything(rules) && (
							<Button
								variant="secondary"
								size="sm"
								onClick={() => setRules([...rules, allowAllRule()])}
							>
								<ShieldCheck className="h-4 w-4" /> Allow all operations
							</Button>
						)
					}
				/>

				{/* What this save changes — removals first (the security-critical
			    signal), then additions, each in the platform's rule voice. */}
				<AnimatePresence initial={false}>
					{dirty && (
						<motion.div {...panelMotion} className="overflow-hidden">
							<div
								className="bg-surface-sheet rounded-md p-3"
								data-testid="rules-diff"
							>
								<p className="text-foreground-sub mb-2 font-mono text-[10px] tracking-wide uppercase">
									Pending changes
									<span className="text-foreground-sub normal-case">
										{' '}
										· applied when you save
									</span>
								</p>
								<ul className="space-y-1 text-xs">
									{reordered && (
										<li className="text-foreground flex items-start gap-1.5">
											<ArrowUpDown
												className="mt-0.5 h-3 w-3 shrink-0"
												aria-hidden="true"
											/>
											<span>
												Rules reordered — evaluation is first-match-wins, so
												the new order changes which rule decides a request.
											</span>
										</li>
									)}
									{removed.map((rule, i) => (
										<li
											key={`removed-${i}`}
											className="text-danger flex items-start gap-1.5"
										>
											<Minus
												className="mt-0.5 h-3 w-3 shrink-0"
												aria-hidden="true"
											/>
											<span>
												<span className="sr-only">Removed: </span>
												{oneLiner(rule)}
											</span>
										</li>
									))}
									{added.map((rule, i) => (
										<li
											key={`added-${i}`}
											className="text-success flex items-start gap-1.5"
										>
											<Plus
												className="mt-0.5 h-3 w-3 shrink-0"
												aria-hidden="true"
											/>
											<span>
												<span className="sr-only">Added: </span>
												{oneLiner(rule)}
											</span>
										</li>
									))}
								</ul>
							</div>
						</motion.div>
					)}
				</AnimatePresence>
			</div>

			{/* The commit row, as the card's foot — a darker fill band, no rule: the
			    dirty hint on the left, the commit pair on the right. Disabled
			    "Save rules" sits on the tonal fill with legible text (the
			    `.edged-controls` button rules in index.css). */}
			<div className="bg-surface-sheet/50 flex flex-wrap items-center justify-between gap-2 px-4 py-3 sm:px-5">
				<p className="text-xs" aria-live="polite" data-testid="rules-dirty-hint">
					{dirty ? (
						<span className="text-foreground-lighter inline-flex items-center gap-1.5 font-medium">
							<span
								aria-hidden="true"
								className="bg-caution h-1.5 w-1.5 shrink-0 rounded-full"
							/>
							Unsaved changes
						</span>
					) : (
						<span className="text-foreground-sub">No unsaved changes</span>
					)}
				</p>
				<div className="flex flex-wrap items-center gap-2">
					{dirty && (
						<Button variant="secondary" size="sm" onClick={discard}>
							<RotateCcw className="h-4 w-4" /> Discard changes
						</Button>
					)}
					<Button
						size="sm"
						onClick={save}
						loading={replace.isPending}
						disabled={hasInvalidRule || !dirty}
					>
						<Save className="h-4 w-4" /> {replace.isPending ? 'Saving…' : 'Save rules'}
					</Button>
				</div>
			</div>
		</div>
	);
}
