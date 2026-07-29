import { useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { ChevronDown, FlaskConical, Minus, Plus, Save } from 'lucide-react';
import { Button, PermissionRuleEditor, isEmptyAllowRule } from '@/shared/ui';
import { ruleSummary, type PermissionRule as DisplayRule } from '@/shared/lib';
import { useReplacePermissions } from '@/modules/toolkits/api';
import { RuleTester } from '@/modules/toolkits/components/detail/RuleTester';
import { panelMotion } from '@/modules/toolkits/components/detail/shared';
import type { PermissionRule, PermissionRuleInput } from '@/modules/toolkits/api/types';

/**
 * Inline editor for the agent permission rules on one toolkit↔credential
 * binding. System safety rules (`_system: true`, appended by the backend on
 * every save) are filtered out of the editor so saving never persists them as
 * agent rules — the backend re-appends a fresh copy each time.
 *
 * The draft is diffed live against the saved rules into a "Pending changes"
 * panel (− removed / + added, in the same `ruleSummary` voice the platform
 * uses everywhere), so the operator sees exactly which grants a save will
 * revoke or introduce before committing. The broker dry-run tester is tucked
 * behind a disclosure — present when needed, not competing with the editor.
 */
export interface CredentialPermissionEditorProps {
	toolkitId: string;
	credentialId: string;
	credentialLabel: string;
	initialRules: PermissionRule[];
	onClose: () => void;
}

function toInput(rule: PermissionRule): PermissionRuleInput {
	// `effect` is two distinct generated string enums (read vs write schema) with
	// identical 'allow'/'deny' values; TS treats string-enum members as assignable
	// across them, so copying directly is type-safe (verified under `strict`).
	return {
		effect: rule.effect,
		methods: rule.methods ?? undefined,
		path: rule.path ?? undefined,
		operations: rule.operations ?? undefined,
	};
}

/** Drop empty conditions so the wire body (and the diff) never carries noise. */
function cleanRule(rule: PermissionRuleInput): PermissionRuleInput {
	const out: PermissionRuleInput = { effect: rule.effect };
	if (Array.isArray(rule.methods) && rule.methods.length > 0) out.methods = rule.methods;
	if (typeof rule.path === 'string' && rule.path.trim() !== '') out.path = rule.path.trim();
	if (Array.isArray(rule.operations) && rule.operations.length > 0)
		out.operations = rule.operations;
	return out;
}

function toDisplay(rule: PermissionRuleInput): DisplayRule {
	return {
		effect: String(rule.effect) === 'deny' ? 'deny' : 'allow',
		methods: rule.methods ?? null,
		path: rule.path ?? null,
		operations: rule.operations ?? null,
	};
}

/** Order-insensitive canonical key for one rule (for the multiset diff). */
function canon(rule: DisplayRule): string {
	return JSON.stringify({
		e: rule.effect,
		m: [...(rule.methods ?? [])].sort(),
		p: rule.path ?? null,
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

export function CredentialPermissionEditor({
	toolkitId,
	credentialId,
	credentialLabel,
	initialRules,
	onClose,
}: CredentialPermissionEditorProps) {
	const [rules, setRules] = useState<PermissionRuleInput[]>(() =>
		initialRules.filter((r) => !r._system).map(toInput),
	);
	const [testerOpen, setTesterOpen] = useState(false);
	const replace = useReplacePermissions(toolkitId, credentialId);

	const clean = rules.map(cleanRule);
	// A condition-less `allow` is rejected by the backend (422). Block save and
	// rely on the editor's inline warning rather than submitting a known error.
	const hasInvalidRule = clean.some(isEmptyAllowRule);

	// Live draft-vs-saved diff — what a save would revoke (−) and grant (+).
	const savedDisplay = initialRules
		.filter((r) => !r._system)
		.map(toInput)
		.map(cleanRule)
		.map(toDisplay);
	const draftDisplay = clean.map(toDisplay);
	const added = diffRules(draftDisplay, savedDisplay);
	const removed = diffRules(savedDisplay, draftDisplay);
	const dirty = added.length > 0 || removed.length > 0;

	const save = () => {
		if (hasInvalidRule || !dirty) return;
		replace.mutate(clean, { onSuccess: () => onClose() });
	};

	return (
		<div className="border-border bg-muted/20 space-y-4 border-t p-4 sm:p-5">
			<div>
				<p className="text-foreground text-sm font-semibold">
					Permission rules for {credentialLabel}
				</p>
				<p className="text-muted-foreground mt-0.5 text-xs">
					Rules are evaluated in order — first match wins, anything unmatched is denied.
					System safety rules are always appended.
				</p>
			</div>

			<PermissionRuleEditor rules={rules} onChange={setRules} />

			{/* What this save changes — removals first (the security-critical
			    signal), then additions, each in the platform's rule voice. */}
			<AnimatePresence initial={false}>
				{dirty && (
					<motion.div {...panelMotion} className="overflow-hidden">
						<div
							className="border-border/60 bg-card rounded-lg border p-3"
							data-testid="rules-diff"
						>
							<p className="text-muted-foreground mb-2 font-mono text-[10px] tracking-wide uppercase">
								Pending changes
								<span className="text-muted-foreground/60 normal-case">
									{' '}
									· applied when you save
								</span>
							</p>
							<ul className="space-y-1 text-xs">
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

			<div className="flex gap-2">
				<Button
					onClick={save}
					loading={replace.isPending}
					disabled={hasInvalidRule || !dirty}
				>
					<Save className="h-4 w-4" /> {replace.isPending ? 'Saving…' : 'Save rules'}
				</Button>
				<Button variant="secondary" onClick={onClose}>
					Cancel
				</Button>
			</div>

			{/* Broker dry-run, behind a disclosure so it never competes with the
			    editor for attention. */}
			<div className="border-border/60 border-t pt-3">
				<button
					type="button"
					onClick={() => setTesterOpen((prev) => !prev)}
					aria-expanded={testerOpen}
					className="text-muted-foreground hover:text-foreground flex items-center gap-1.5 text-xs font-medium transition-colors"
				>
					<FlaskConical className="h-3.5 w-3.5" aria-hidden="true" />
					Test a request
					<motion.span
						animate={{ rotate: testerOpen ? 180 : 0 }}
						transition={{ duration: 0.18 }}
						className="flex"
					>
						<ChevronDown className="h-3.5 w-3.5" aria-hidden="true" />
					</motion.span>
				</button>
				<AnimatePresence initial={false}>
					{testerOpen && (
						<motion.div {...panelMotion} className="overflow-hidden">
							<div className="pt-3">
								<RuleTester toolkitId={toolkitId} credentialId={credentialId} />
							</div>
						</motion.div>
					)}
				</AnimatePresence>
			</div>
		</div>
	);
}
