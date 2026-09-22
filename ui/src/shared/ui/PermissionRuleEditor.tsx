import type { ReactNode } from 'react';
import { AlertTriangle, ArrowDown, ArrowUp, Check, Plus, ShieldCheck, Trash2 } from 'lucide-react';
import { Button } from '@/shared/ui/Button';
import { Input } from '@/shared/ui/Input';
import { Select } from '@/shared/ui/Select';
import type { PermissionRuleSchema } from '@/shared/api';

/**
 * Editor for the permission rules on an agent↔credential
 * binding. Each rule is `{ effect, methods?, path?, match_mode?, operations? }`.
 * Rules are evaluated in order, first match wins — so rows are numbered (#1 is
 * evaluated first) and can be reordered, and the numbers are the same ones the
 * rule tester's verdict references.
 *
 * Lives in `shared/ui` (not a feature module) so every surface that authors
 * binding rules can reuse it — the agent console's rule editor and the
 * provisioning-plan fulfilment wizard both compose it.
 *
 * The editor's own verbs (`Add rule`, plus `Allow all operations` while no
 * catch-all grant exists) share ONE row with the host's commit verbs via
 * `actionsSlot`; `beforeActions` sits directly above that row.
 */

/** Write shape for a permission rule (allow/deny + methods/path/operations). */
export type PermissionRuleInput = PermissionRuleSchema;

/**
 * Rule effect values, as plain string literals matching the backend enum
 * (`allow` / `deny`). Defined here so views/editors don't import the generated
 * enum *value* (which the layering ESLint rule forbids outside `api/client.ts`).
 */
export const PERMISSION_EFFECTS = ['allow', 'deny'] as const;
export type PermissionEffect = (typeof PERMISSION_EFFECTS)[number];

/**
 * Path match modes, mirroring the backend enum (`regex` full-match / literal
 * `prefix` / literal `exact`) as plain literals for the same layering reason.
 */
export const PERMISSION_MATCH_MODES = [
	{ value: 'regex', label: 'Regex', placeholder: 'Path regex — empty matches any path' },
	{ value: 'prefix', label: 'Prefix', placeholder: 'Path prefix — e.g. /repos/acme/' },
	{ value: 'exact', label: 'Exact', placeholder: 'Exact path — e.g. /user' },
] as const;

const HTTP_METHODS = ['GET', 'POST', 'PUT', 'PATCH', 'DELETE'] as const;

/**
 * The documented catch-all an `allow` rule must use to grant broad access:
 * `path: ".*"` matches every path explicitly. The backend schema rejects a
 * condition-less `allow` (effect `allow` with no methods/path/operations) with a
 * 422, so the editor never authors one — see `broker-permission-rules.md`.
 */
const ALLOW_ALL_PATH = '.*';

/** `.*` is a catch-all only when the path is read as a regex. A literal cast
 * for the same reason as the rest of this file: the editor works in strings. */
const REGEX_MATCH_MODE = 'regex' as NonNullable<PermissionRuleInput['match_mode']>;

/**
 * True when a rule would be rejected by the backend: an `allow` that constrains
 * nothing (no methods, path, or operations) matches every request, so the API
 * refuses it (422). The editor surfaces this inline and blocks save rather than
 * letting the user submit a guaranteed error.
 */
export function isEmptyAllowRule(rule: PermissionRuleInput): boolean {
	return (
		rule.effect === 'allow' &&
		!(rule.methods?.length || (rule.path && rule.path.trim()) || rule.operations?.length)
	);
}

/**
 * Strip empty conditions from an authored rule so the wire body never carries
 * `methods: []` / `path: ""` noise. The emptiness check trims, but the wire
 * value is the RAW path string — whitespace can be meaningful in a regex, and
 * rewriting it here would silently change what the broker matches. `match_mode`
 * qualifies the path, so it is carried whenever a path is present (and dropped
 * when it is the `regex` backend default) — a prefix/exact rule must never be
 * silently re-stored as regex. One definition for every save/bind path.
 */
export function cleanPermissionRule(rule: PermissionRuleInput): PermissionRuleInput {
	const out: PermissionRuleInput = { effect: rule.effect };
	if (Array.isArray(rule.methods) && rule.methods.length > 0) out.methods = rule.methods;
	if (typeof rule.path === 'string' && rule.path.trim() !== '') {
		out.path = rule.path;
		if (rule.match_mode && String(rule.match_mode) !== 'regex')
			out.match_mode = rule.match_mode;
	}
	if (Array.isArray(rule.operations) && rule.operations.length > 0)
		out.operations = rule.operations;
	return out;
}

/** The catch-all grant, authored in the one shape the backend accepts. */
function allowAllRule(): PermissionRuleInput {
	return {
		effect: 'allow' as PermissionRuleInput['effect'],
		methods: null,
		// A condition-less allow is rejected (422), so grant broadly via `path: ".*"`.
		path: ALLOW_ALL_PATH,
		operations: null,
	};
}

/**
 * True when the draft already grants everything — an unconstrained-method `allow`
 * on the catch-all path, matched as a REGEX. The mode matters: `.*` under `exact`
 * or `prefix` matches a literal two-character path and grants nothing.
 */
function grantsEverything(rules: PermissionRuleInput[]): boolean {
	return rules.some(
		(rule) =>
			rule.effect === 'allow' &&
			!rule.methods?.length &&
			!rule.operations?.length &&
			rule.path?.trim() === ALLOW_ALL_PATH &&
			isRegexMode(rule.match_mode),
	);
}

/** Is this rule's path matched as a regex? `regex` is the backend default. */
function isRegexMode(mode: PermissionRuleInput['match_mode']): boolean {
	return mode == null || String(mode) === 'regex';
}

export interface PermissionRuleEditorProps {
	rules: PermissionRuleInput[];
	onChange: (rules: PermissionRuleInput[]) => void;
	/** The host's own verbs (e.g. Save / Discard), right-aligned on the SAME row as
	 * `Add rule`. Omit and the row holds only the editor's verbs. */
	actionsSlot?: ReactNode;
	/** Full-width content rendered just above the verb row — e.g. a
	 * pending-changes preview of what saving would do. */
	beforeActions?: ReactNode;
}

export function PermissionRuleEditor({
	rules,
	onChange,
	actionsSlot,
	beforeActions,
}: PermissionRuleEditorProps) {
	const update = (index: number, patch: Partial<PermissionRuleInput>) => {
		onChange(rules.map((rule, i) => (i === index ? { ...rule, ...patch } : rule)));
	};
	const remove = (index: number) => onChange(rules.filter((_, i) => i !== index));
	const add = () =>
		onChange([
			...rules,
			{ effect: 'allow' as PermissionRuleInput['effect'], methods: [], path: '' },
		]);
	// Order is semantics here (first match wins), so reordering must be a
	// first-class edit, not a delete-and-retype exercise.
	const move = (index: number, delta: -1 | 1) => {
		const target = index + delta;
		if (target < 0 || target >= rules.length) return;
		const next = [...rules];
		[next[index], next[target]] = [next[target], next[index]];
		onChange(next);
	};

	const toggleMethod = (index: number, method: string) => {
		const current = rules[index].methods ?? [];
		const next = current.includes(method)
			? current.filter((m) => m !== method)
			: [...current, method];
		update(index, { methods: next });
	};

	return (
		<div className="space-y-3">
			{rules.length === 0 && (
				<div className="border-warning/40 bg-warning/5 rounded-lg border p-3">
					<div className="flex items-start gap-2">
						<AlertTriangle className="text-warning mt-0.5 h-4 w-4 shrink-0" />
						<div className="space-y-2">
							<p className="text-foreground text-xs font-medium">
								No rules defined — all operations will be denied by default.
							</p>
							<p className="text-muted-foreground text-xs">
								Add a rule below to grant access, or allow all operations in one
								step.
							</p>
						</div>
					</div>
				</div>
			)}
			{rules.map((rule, index) => {
				const invalid = isEmptyAllowRule(rule);
				const mode = String(rule.match_mode ?? 'regex');
				const placeholder =
					PERMISSION_MATCH_MODES.find((m) => m.value === mode)?.placeholder ??
					PERMISSION_MATCH_MODES[0].placeholder;
				return (
					<div
						key={index}
						data-testid="permission-rule-row"
						className={
							invalid
								? 'border-danger/50 bg-card space-y-2 rounded-lg border p-3'
								: 'border-border bg-card space-y-2 rounded-lg border p-3'
						}
					>
						{/* Line 1 — effect + match mode, path taking the rest. The selects sit in
						    fixed-width wrappers because `Select` renders a `w-full` shell. */}
						<div className="flex flex-wrap items-center gap-2">
							{/* The rule's evaluation position — the SAME number the rule
							    tester's verdict cites, so "#2" always has an anchor. */}
							<span
								className="bg-muted text-muted-foreground inline-flex h-6 min-w-6 shrink-0 items-center justify-center rounded px-1 font-mono text-[11px] font-semibold"
								aria-label={`Rule ${index + 1}`}
							>
								#{index + 1}
							</span>
							<span className="w-[5.5rem] shrink-0">
								<Select
									aria-label="Effect"
									value={rule.effect}
									onChange={(e) =>
										update(index, {
											effect: e.target.value as PermissionRuleInput['effect'],
										})
									}
									className="px-2 py-1.5"
								>
									{PERMISSION_EFFECTS.map((effect: PermissionEffect) => (
										<option key={effect} value={effect}>
											{effect === 'allow' ? 'Allow' : 'Deny'}
										</option>
									))}
								</Select>
							</span>
							<span className="w-[5.5rem] shrink-0">
								<Select
									aria-label="Path match mode"
									value={mode}
									onChange={(e) =>
										update(index, {
											match_mode: e.target
												.value as PermissionRuleInput['match_mode'],
										})
									}
									className="px-2 py-1.5"
								>
									{PERMISSION_MATCH_MODES.map((m) => (
										<option key={m.value} value={m.value}>
											{m.label}
										</option>
									))}
								</Select>
							</span>
							{/* Same wrapper trick: `Input` renders inside a `w-full` shell too. */}
							<span className="min-w-36 flex-1">
								<Input
									aria-label="Path pattern"
									value={rule.path ?? ''}
									onChange={(e) => update(index, { path: e.target.value })}
									placeholder={placeholder}
									className="px-2.5 py-1.5 font-mono"
								/>
							</span>
						</div>
						{/* Line 2 — the conditions and the row verbs: method chips on
						    the left, reorder/delete on the right. */}
						<div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1.5">
							<div
								className="flex min-w-0 flex-wrap items-center gap-1.5"
								role="group"
								aria-label={`Rule ${index + 1} methods — none selected matches any method`}
							>
								{HTTP_METHODS.map((method) => {
									const selected = (rule.methods ?? []).includes(method);
									return (
										<button
											key={method}
											type="button"
											onClick={() => toggleMethod(index, method)}
											aria-pressed={selected}
											className={
												selected
													? 'bg-primary text-background inline-flex items-center gap-1 rounded-md px-2 py-0.5 font-mono text-xs font-semibold'
													: 'border-border bg-background text-muted-foreground hover:border-primary/50 hover:text-foreground inline-flex items-center gap-1 rounded-md border px-2 py-0.5 font-mono text-xs transition-colors'
											}
										>
											{selected && (
												<Check className="h-3 w-3" aria-hidden="true" />
											)}
											{method}
										</button>
									);
								})}
								<span className="text-muted-foreground/60 text-[10px]">
									none = any method
								</span>
							</div>
							<div className="flex shrink-0 items-center">
								<Button
									variant="ghost"
									size="icon"
									aria-label="Move rule up"
									disabled={index === 0}
									onClick={() => move(index, -1)}
								>
									<ArrowUp className="h-4 w-4" />
								</Button>
								<Button
									variant="ghost"
									size="icon"
									aria-label="Move rule down"
									disabled={index === rules.length - 1}
									onClick={() => move(index, 1)}
								>
									<ArrowDown className="h-4 w-4" />
								</Button>
								<Button
									variant="ghost"
									size="icon"
									aria-label="Remove rule"
									onClick={() => remove(index)}
								>
									<Trash2 className="h-4 w-4" />
								</Button>
							</div>
						</div>
						{invalid && (
							<div
								role="alert"
								className="text-danger/90 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs leading-relaxed"
							>
								<span className="flex items-start gap-1.5">
									<AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" />
									<span>
										An Allow rule must constrain at least one method, path, or
										operation.
									</span>
								</span>
								{/* The fix, not the instruction for it. It sets the mode as well as the
								    path: `.*` grants everything only under `regex`. */}
								<Button
									variant="ghost"
									size="sm"
									onClick={() =>
										update(index, {
											path: ALLOW_ALL_PATH,
											match_mode: REGEX_MATCH_MODE,
										})
									}
									className="text-danger hover:text-danger h-auto px-1.5 py-0.5 underline"
								>
									Use <code className="font-mono">.*</code> to allow everything
								</Button>
							</div>
						)}
					</div>
				);
			})}
			{beforeActions}

			{/* One row for every verb: the editor's on the left, the host's
			    commit pair (when it passes any) on the right. */}
			<div className="flex flex-wrap items-center justify-between gap-2">
				<div className="flex flex-wrap items-center gap-2">
					<Button variant="secondary" size="sm" onClick={add}>
						<Plus className="h-4 w-4" /> Add rule
					</Button>
					{/* Reachable with rules already present, not just from the
					    empty state — broadening a narrow grant is a normal edit. */}
					{!grantsEverything(rules) && (
						<Button
							variant="ghost"
							size="sm"
							onClick={() => onChange([...rules, allowAllRule()])}
							className="text-muted-foreground hover:text-foreground"
						>
							<ShieldCheck className="h-4 w-4" /> Allow all operations
						</Button>
					)}
				</div>
				{actionsSlot && (
					<div className="flex flex-wrap items-center gap-2">{actionsSlot}</div>
				)}
			</div>
		</div>
	);
}
