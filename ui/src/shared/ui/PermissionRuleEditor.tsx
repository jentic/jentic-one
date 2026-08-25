import { AlertTriangle, ArrowDown, ArrowUp, Check, Plus, ShieldCheck, Trash2 } from 'lucide-react';
import { Button } from '@/shared/ui/Button';
import { Input } from '@/shared/ui/Input';
import { Select } from '@/shared/ui/Select';
import type { PermissionRuleSchema } from '@/shared/api';

/**
 * Editor for the agent-defined permission rules on a toolkit↔credential
 * binding. Each rule is `{ effect, methods?, path?, match_mode?, operations? }`.
 * Rules are evaluated in order, first match wins — so rows are numbered (#1 is
 * evaluated first) and can be reordered, and the numbers are the same ones the
 * rule tester's verdict references.
 *
 * Lives in `shared/ui` (not a feature module) so every surface that authors
 * binding rules can reuse it — the toolkit detail page and the provisioning-plan
 * fulfilment wizard both compose it.
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

export interface PermissionRuleEditorProps {
	rules: PermissionRuleInput[];
	onChange: (rules: PermissionRuleInput[]) => void;
}

export function PermissionRuleEditor({ rules, onChange }: PermissionRuleEditorProps) {
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
								Add at least one Allow rule to grant access.
							</p>
							<Button
								variant="secondary"
								size="sm"
								onClick={() =>
									onChange([
										{
											effect: 'allow' as PermissionRuleInput['effect'],
											methods: null,
											// Explicit catch-all: a condition-less allow is
											// rejected by the backend (422), so grant broad
											// access via `path: ".*"` instead.
											path: ALLOW_ALL_PATH,
											operations: null,
										},
									])
								}
							>
								<ShieldCheck className="h-4 w-4" /> Allow all operations
							</Button>
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
								? 'border-danger/50 bg-card space-y-2.5 rounded-lg border p-3'
								: 'border-border bg-card space-y-2.5 rounded-lg border p-3'
						}
					>
						<div className="flex flex-wrap items-center gap-2">
							{/* The rule's evaluation position — the SAME number the rule
							    tester's verdict cites, so "#2" always has an anchor. */}
							<span
								className="bg-muted text-muted-foreground inline-flex h-6 min-w-6 shrink-0 items-center justify-center rounded px-1 font-mono text-[11px] font-semibold"
								aria-label={`Rule ${index + 1}`}
							>
								#{index + 1}
							</span>
							<Select
								aria-label="Effect"
								value={rule.effect}
								onChange={(e) =>
									update(index, {
										effect: e.target.value as PermissionRuleInput['effect'],
									})
								}
								className="w-24"
							>
								{PERMISSION_EFFECTS.map((effect: PermissionEffect) => (
									<option key={effect} value={effect}>
										{effect === 'allow' ? 'Allow' : 'Deny'}
									</option>
								))}
							</Select>
							<Select
								aria-label="Path match mode"
								value={mode}
								onChange={(e) =>
									update(index, {
										match_mode: e.target
											.value as PermissionRuleInput['match_mode'],
									})
								}
								className="w-24"
							>
								{PERMISSION_MATCH_MODES.map((m) => (
									<option key={m.value} value={m.value}>
										{m.label}
									</option>
								))}
							</Select>
							<Input
								aria-label="Path pattern"
								value={rule.path ?? ''}
								onChange={(e) => update(index, { path: e.target.value })}
								placeholder={placeholder}
								className="min-w-40 flex-1 font-mono"
							/>
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
						<div>
							<p className="text-muted-foreground mb-1.5 font-mono text-[10px] tracking-wide uppercase">
								Methods
								<span className="text-muted-foreground/60 normal-case">
									{' '}
									· none selected = any method
								</span>
							</p>
							<div className="flex flex-wrap gap-1.5">
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
													? 'bg-primary text-background inline-flex items-center gap-1 rounded-md px-2.5 py-1 font-mono text-xs font-semibold'
													: 'border-border bg-background text-muted-foreground hover:border-primary/50 hover:text-foreground inline-flex items-center gap-1 rounded-md border px-2.5 py-1 font-mono text-xs transition-colors'
											}
										>
											{selected && (
												<Check className="h-3 w-3" aria-hidden="true" />
											)}
											{method}
										</button>
									);
								})}
							</div>
						</div>
						{invalid && (
							<p role="alert" className="text-danger flex items-center gap-1 text-xs">
								<AlertTriangle className="h-3 w-3 shrink-0" />
								An Allow rule must constrain at least one method, path, or operation
								— set the path to <code className="font-mono">.*</code> to allow
								everything.
							</p>
						)}
					</div>
				);
			})}
			<Button variant="secondary" size="sm" onClick={add}>
				<Plus className="h-4 w-4" /> Add rule
			</Button>
		</div>
	);
}
