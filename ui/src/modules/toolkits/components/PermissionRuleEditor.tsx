import { AlertTriangle, Plus, ShieldCheck, Trash2 } from 'lucide-react';
import { Button, Input, Select } from '@/shared/ui';
import {
	PERMISSION_EFFECTS,
	type PermissionEffect,
	type PermissionRuleInput,
} from '@/modules/toolkits/api/types';

/**
 * Editor for the agent-defined permission rules on a toolkit↔credential
 * binding. Each rule is `{ effect, methods?, path?, operations? }`. Rules are
 * evaluated in order, first match wins. System safety rules are appended by the
 * backend and are NOT edited here (the caller filters `_system` rules out
 * before passing them in).
 *
 * In-module replacement for mini's shared `PermissionRuleEditor`, retyped
 * against the real `PermissionRuleSchema` contract.
 */
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
				return (
					<div
						key={index}
						className={
							invalid
								? 'border-danger/50 bg-card space-y-2 rounded-lg border p-3'
								: 'border-border bg-card space-y-2 rounded-lg border p-3'
						}
					>
						<div className="flex items-center gap-2">
							<Select
								aria-label="Effect"
								value={rule.effect}
								onChange={(e) =>
									update(index, {
										effect: e.target.value as PermissionRuleInput['effect'],
									})
								}
								className="w-28"
							>
								{PERMISSION_EFFECTS.map((effect: PermissionEffect) => (
									<option key={effect} value={effect}>
										{effect}
									</option>
								))}
							</Select>
							<Input
								aria-label="Path regex"
								value={rule.path ?? ''}
								onChange={(e) => update(index, { path: e.target.value })}
								placeholder="Path regex (optional)"
								className="flex-1"
							/>
							<Button
								variant="ghost"
								size="icon"
								aria-label="Remove rule"
								onClick={() => remove(index)}
							>
								<Trash2 className="h-4 w-4" />
							</Button>
						</div>
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
												? 'bg-primary text-background rounded-md px-2 py-0.5 font-mono text-xs'
												: 'bg-muted text-muted-foreground hover:bg-muted/60 rounded-md px-2 py-0.5 font-mono text-xs'
										}
									>
										{method}
									</button>
								);
							})}
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
