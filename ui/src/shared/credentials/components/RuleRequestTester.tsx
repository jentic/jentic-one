/**
 * RuleRequestTester — "Test a request": method · path · Test, with an optional
 * operation id behind a disclosure, and the verdict underneath. Nothing is sent
 * upstream; the host decides what answers the question:
 *
 * - the API access sidebar asks the broker's dry run about a binding's SAVED
 *   rules (`AgentBindingRuleTester`);
 * - {@link DraftRuleTester} evaluates rules still being edited, locally, with
 *   the same matcher the broker uses (`rule-matcher.ts`, parity-pinned against
 *   the Python evaluator) — for a binding that doesn't exist yet, or a draft
 *   the backend can't see.
 *
 * Collapsing the operation-id disclosure clears it: a hidden field must never
 * influence a verdict.
 */
import { useMemo, useState, type ReactNode } from 'react';
import { Plus, X } from 'lucide-react';
import { Button, Input, Select } from '@/shared/ui';
import { ruleSummary } from '@/shared/lib';
import type { PermissionRule } from '@/shared/credentials/api/vendors-types';
import { explainRules, type RuleVerdict } from '@/shared/credentials/lib/rule-matcher';
import { examplePath } from '@/shared/credentials/lib/path-completion';

const METHODS = ['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS'] as const;

export interface TesterRequest {
	method: string;
	path: string;
	operation_id?: string;
}

export interface RuleRequestTesterProps {
	/** Asked with a trimmed, non-empty path. */
	onRun: (request: TesterRequest) => void;
	pending?: boolean;
	/** The last run's failure, shown as an alert. */
	error?: string | null;
	/** The answer to show — omitted while there is none (or it went stale). */
	verdict?: ReactNode;
	disabled?: boolean;
	/** The line under the controls: what the verdict is evaluated against. */
	note: ReactNode;
	/** Real paths of the API, for the path placeholder. */
	paths?: readonly string[];
}

/** Shared chip shell so allow and deny read as the same kind of answer. */
export function VerdictChip({ allowed, children }: { allowed: boolean; children: ReactNode }) {
	return (
		<p
			className="bg-surface-sheet flex flex-wrap items-center gap-x-2 gap-y-1 rounded-md px-2.5 py-2 text-xs"
			data-testid="rule-verdict"
		>
			<span
				className={
					allowed
						? 'bg-success/15 text-success rounded-[5px] px-2 py-0.5 text-xs font-semibold'
						: 'bg-danger/15 text-danger rounded-[5px] px-2 py-0.5 text-xs font-semibold'
				}
			>
				{allowed ? 'Allowed' : 'Denied'}
			</span>{' '}
			<span className="text-foreground-sub min-w-0">{children}</span>
		</p>
	);
}

/** "— matched rule #N · summary", or the default-deny line when nothing matched. */
export function MatchedRuleText({
	anchor,
	summary,
	fallback,
}: {
	anchor: string | null;
	summary: string | null;
	/** When the rule can't be numbered. */
	fallback: ReactNode;
}) {
	if (anchor == null) return <>{fallback}</>;
	return (
		<>
			— matched rule <span className="text-foreground font-mono font-semibold">{anchor}</span>
			{summary ? <> · {summary}</> : null}
		</>
	);
}

export function RuleRequestTester({
	onRun,
	pending = false,
	error,
	verdict,
	disabled = false,
	note,
	paths,
}: RuleRequestTesterProps) {
	const pathPlaceholder = useMemo(() => examplePath(paths), [paths]);
	const [method, setMethod] = useState<string>('GET');
	const [path, setPath] = useState('');
	const [operationId, setOperationId] = useState('');
	// The disclosure opens itself whenever it holds a value; closing clears it.
	const [operationOpen, setOperationOpen] = useState(false);

	const run = (): void => {
		if (disabled) return;
		const trimmed = path.trim();
		if (!trimmed) return;
		const op = operationId.trim();
		onRun({ method, path: trimmed, ...(op ? { operation_id: op } : {}) });
	};

	return (
		// Same borderless card as the rule editor beside it; only the controls carry
		// an edge (`.edged-controls`).
		<div className="bg-surface-inset edged-controls space-y-2.5 rounded-lg p-3 sm:p-4">
			<div className="flex items-center gap-2">
				<div className="w-24 shrink-0">
					<Select
						aria-label="HTTP method"
						value={method}
						onChange={(e) => setMethod(e.target.value)}
						className="px-2 py-1.5 text-xs"
						disabled={disabled}
					>
						{METHODS.map((m) => (
							<option key={m} value={m}>
								{m}
							</option>
						))}
					</Select>
				</div>
				<div className="min-w-0 flex-1">
					<Input
						aria-label="Request path"
						value={path}
						onChange={(e) => setPath(e.target.value)}
						placeholder={pathPlaceholder}
						className="px-2.5 py-1.5 font-mono text-xs"
						disabled={disabled}
						onKeyDown={(e) => {
							if (e.key === 'Enter' && !pending) {
								// Inside a host form, Enter tests — it must not submit.
								e.preventDefault();
								run();
							}
						}}
					/>
				</div>
				<Button
					variant="secondary"
					size="sm"
					className="shrink-0"
					onClick={run}
					loading={pending}
					disabled={disabled || !path.trim()}
				>
					Test
				</Button>
			</div>

			{/* Operation-scoped rules only fire when the request carries an operation id,
			    which would otherwise always dry-run to default-deny. */}
			{operationOpen && (
				<div className="flex items-center gap-2">
					<div className="min-w-0 flex-1">
						<Input
							aria-label="Operation ID (optional)"
							value={operationId}
							onChange={(e) => setOperationId(e.target.value)}
							placeholder="operationId"
							className="px-2.5 py-1.5 font-mono text-xs"
							disabled={disabled}
							autoFocus
							onKeyDown={(e) => {
								if (e.key === 'Enter' && !pending) {
									e.preventDefault();
									run();
								}
							}}
						/>
					</div>
					<Button
						variant="ghost"
						size="icon"
						aria-label="Remove operation id"
						disabled={disabled}
						onClick={() => {
							// Clear as well as hide: an invisible value must not change the next verdict.
							setOperationId('');
							setOperationOpen(false);
						}}
					>
						<X className="h-4 w-4" />
					</Button>
				</div>
			)}

			{error && (
				<p className="text-danger text-xs" role="alert">
					{error}
				</p>
			)}
			{!disabled && verdict}

			<div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1">
				{note}
				{!operationOpen && (
					<Button
						variant="tonal"
						size="xs"
						disabled={disabled}
						onClick={() => setOperationOpen(true)}
						className="shrink-0"
					>
						<Plus className="h-3 w-3" /> operation id
					</Button>
				)}
			</div>
		</div>
	);
}

export interface DraftRuleTesterProps {
	/** The ordered rules as they stand in the editor — unsaved. */
	rules: readonly PermissionRule[];
	/**
	 * Names the deciding rule when its editor row number would mean nothing —
	 * e.g. a preset, whose rule the operator never sees as a row. Omitted →
	 * "rule #N · summary", numbered like the editor's rows.
	 */
	describeMatch?: (index: number) => ReactNode;
	paths?: readonly string[];
	disabled?: boolean;
}

/**
 * A dry run against rules still being edited: evaluated locally, first match
 * wins, default deny — the broker's semantics, via its parity-pinned TS port.
 * The last request stays asked, so its verdict follows every edit to the rules.
 */
export function DraftRuleTester({ rules, describeMatch, paths, disabled }: DraftRuleTesterProps) {
	const [request, setRequest] = useState<TesterRequest | null>(null);
	const result = useMemo<RuleVerdict | null>(
		() =>
			request
				? explainRules(rules, {
						method: request.method,
						path: request.path,
						operation_id: request.operation_id ?? null,
					})
				: null,
		[rules, request],
	);

	// A screen-reader user hears a verdict that changes under an edit, not just
	// one that answers a click.
	const spoken =
		result && request
			? `${request.method} ${request.path}: ${result.allowed ? 'allowed' : 'denied'}`
			: '';

	let verdict: ReactNode = null;
	if (result && request) {
		const asked = (
			<span className="text-foreground font-mono">
				{request.method} {request.path}
			</span>
		);
		if (!result.matched || result.ruleIndex == null) {
			verdict = (
				<VerdictChip allowed={false}>{asked} — no rule matched (default deny)</VerdictChip>
			);
		} else {
			const index = result.ruleIndex;
			const rule = rules[index];
			verdict = (
				<VerdictChip allowed={result.allowed}>
					{asked}{' '}
					{describeMatch ? (
						describeMatch(index)
					) : (
						<MatchedRuleText
							anchor={`#${index + 1}`}
							summary={rule ? ruleSummary([rule]).replace(/\.$/, '') : null}
							fallback={null}
						/>
					)}
				</VerdictChip>
			);
		}
	}

	return (
		<>
			<RuleRequestTester
				onRun={setRequest}
				verdict={verdict}
				disabled={disabled}
				paths={paths}
				note={
					<p className="text-foreground-sub text-xs" data-testid="draft-tester-note">
						Checks the rules above as you edit them — before they&apos;re saved. Nothing
						is sent upstream.
					</p>
				}
			/>
			<span className="sr-only" role="status" aria-live="polite">
				{spoken}
			</span>
		</>
	);
}
