/**
 * Compact rule-list editor shared between the credentials connect-flow
 * rules page and the agent-detail per-binding editor. Owns the row list
 * (edit-in-place / delete / reorder), the "add rule" affordance, and
 * the shared draft form + validation.
 *
 * Callers own the ``rules`` state and get an ``onChange`` callback
 * — everything below that (rule-form draft, autocomplete dropdown,
 * regex-validity warnings) is internal.
 */

import type { KeyboardEvent as ReactKeyboardEvent, ReactNode } from 'react';
import { useEffect, useMemo, useState } from 'react';
import { AlertTriangle, ArrowDown, ArrowUp, Pencil, Plus, X } from 'lucide-react';
import { Badge, Button, Label, Tooltip } from '@/shared/ui';
import { nextPathCompletion } from '@/shared/credentials/lib/path-completion';
import { ruleValidityIssue } from '@/shared/credentials/lib/rule-matcher';
import { ruleAppliesToTemplate } from '@/shared/credentials/lib/template-matcher';
import type { PermissionRule } from '@/shared/credentials/api/vendors-types';

// Stable empty reference so ``useMemo`` deps don't invalidate on every
// render when a caller omits ``pathSuggestions``.
const EMPTY_PATHS: readonly string[] = [];

// Max suggestions rendered under the path input at once. Deliberately
// small — a longer list dominates the dialog and forces the user to
// scan rather than skim.
const MAX_PATH_SUGGESTIONS = 5;

/**
 * Top-level list editor. Renders:
 *
 * * Each rule as a read-only row (verdict pill, methods, path) with
 *   Edit / Delete / Move-up / Move-down controls.
 * * A row swaps to an inline editor when the user clicks Edit.
 * * A trailing "Add rule" button expands into the same compact form.
 *
 * Warning states surfaced per row: malformed / empty regex ("never
 * matches") and "no ops affected" (when ``opTemplates`` is provided
 * and ``opsLoaded`` is true) — silent-fail cases the user would
 * otherwise miss.
 */
export function RuleListEditor({
	rules,
	onChange,
	pathSuggestions,
	opTemplates,
	opsLoaded,
	requestedRules,
	emptyStateContent,
	className,
}: {
	rules: readonly PermissionRule[];
	onChange: (next: PermissionRule[]) => void;
	/** Real op paths from the vendor's OpenAPI — feeds autocomplete + Tab-completion. */
	pathSuggestions?: readonly string[];
	/** Op templates from the vendor's OpenAPI — feeds the "no ops affected" warning. */
	opTemplates?: readonly string[];
	/** Set once the ops query has resolved so warnings don't flash during import. */
	opsLoaded?: boolean;
	/** Rules the initiating agent supplied on ``:connect`` — carry a "requested by agent" pill. */
	requestedRules?: readonly PermissionRule[];
	/**
	 * Custom empty-state content — rendered in place of the default
	 * "No rules yet — add one below." copy when ``rules.length === 0``.
	 * Used by the connect-flow rules page to render its greyed-out
	 * ``Allow GET /`` fallback preview.
	 */
	emptyStateContent?: ReactNode;
	className?: string;
}) {
	const [editingIndex, setEditingIndex] = useState<number | null>(null);

	// Fast index for the "requested by agent" tag. Deep-compare by
	// JSON so rules the user edits in place lose the tag once they
	// diverge from what the agent originally requested.
	const requestedKeys = useMemo(
		() => new Set((requestedRules ?? []).map((r) => JSON.stringify(r))),
		[requestedRules],
	);

	return (
		<div className={className}>
			<div className="border-border bg-muted/20 space-y-1.5 rounded-lg border p-2">
				{rules.length === 0
					? (emptyStateContent ?? (
							<p className="text-muted-foreground px-1 py-1 text-xs">
								No rules yet — add one below.
							</p>
						))
					: rules.map((rule, i) =>
							editingIndex === i ? (
								<InlineEditRuleForm
									key={i}
									initial={rule}
									onSave={(updated): void => {
										onChange(rules.map((r, j) => (j === i ? updated : r)));
										setEditingIndex(null);
									}}
									onCancel={(): void => setEditingIndex(null)}
									pathSuggestions={pathSuggestions}
								/>
							) : (
								<RulePreviewRow
									key={i}
									rule={rule}
									isRequested={requestedKeys.has(JSON.stringify(rule))}
									opTemplates={opTemplates}
									opsLoaded={opsLoaded}
									onEdit={(): void => setEditingIndex(i)}
									onDelete={(): void => onChange(rules.filter((_, j) => j !== i))}
									onMoveUp={
										i === 0
											? undefined
											: (): void => onChange(swap([...rules], i, i - 1))
									}
									onMoveDown={
										i === rules.length - 1
											? undefined
											: (): void => onChange(swap([...rules], i, i + 1))
									}
								/>
							),
						)}
			</div>
			<AddRuleForm
				onAdd={(rule): void => onChange([...rules, rule])}
				pathSuggestions={pathSuggestions}
			/>
		</div>
	);
}

/**
 * Read-only rendering of a rule row without any controls — the connect
 * flow's empty state renders this for its ``Allow GET /`` fallback so
 * users see the default that will be injected on Continue-click. The
 * row is greyed and carries a "default" tag so it can't be mistaken
 * for an authored rule.
 */
export function DefaultRulePreviewRow({ rule }: { rule: PermissionRule }) {
	const methodsLabel =
		rule.methods && rule.methods.length > 0 ? rule.methods.join(', ') : 'any method';
	return (
		<div className="bg-background border-border flex items-center gap-2.5 rounded-md border px-2.5 py-1.5 text-xs opacity-70">
			<span className="bg-success/10 text-success border-success/40 rounded-md border px-1.5 py-0.5 font-mono text-[10px] tracking-wide uppercase">
				{rule.effect}
			</span>
			<span className="text-foreground font-mono text-[11px]">{methodsLabel}</span>
			<span className="text-muted-foreground truncate font-mono text-[11px]">
				{rule.path ?? '/'}
				{rule.match_mode && rule.match_mode !== 'regex' ? ` (${rule.match_mode})` : ''}
			</span>
			<span className="text-muted-foreground ml-auto text-[10px] italic">default</span>
		</div>
	);
}

// ---------------------------------------------------------------------------
// Row + inline edit (private)
// ---------------------------------------------------------------------------

/**
 * One row in the rules editor. Read-only view of the rule + edit +
 * delete + up/down controls. When the rule's regex is malformed (or
 * empty), a warning badge is shown inline — otherwise the rule fails
 * silently closed at broker time and the user has no way to tell why
 * the ops-preview grid stays red.
 */
function RulePreviewRow({
	rule,
	isRequested,
	opTemplates,
	opsLoaded,
	onEdit,
	onDelete,
	onMoveUp,
	onMoveDown,
}: {
	rule: PermissionRule;
	isRequested: boolean;
	opTemplates?: readonly string[];
	opsLoaded?: boolean;
	onEdit?: () => void;
	onDelete?: () => void;
	onMoveUp?: () => void;
	onMoveDown?: () => void;
}) {
	const effectClass =
		rule.effect === 'allow'
			? 'bg-success/10 text-success border-success/40'
			: 'bg-danger/10 text-danger border-danger/40';
	const methodsLabel =
		rule.methods && rule.methods.length > 0 ? rule.methods.join(', ') : 'any method';
	const validityIssue = ruleValidityIssue(rule);
	const warningLabel =
		validityIssue === 'invalid-regex'
			? "This regex pattern isn't valid — the rule will never match. Edit the path or delete the rule."
			: validityIssue === 'empty-regex'
				? 'Empty regex — the rule will never match. Add a pattern (e.g. `.*` for match-any) or delete the rule.'
				: null;
	// Only report once ops have finished loading — otherwise we'd flash
	// "no operations affected" while the import is still pending.
	// Suppressed when a stronger validity warning is already active.
	const affectsNothing = Boolean(
		opsLoaded &&
		!warningLabel &&
		opTemplates &&
		opTemplates.length > 0 &&
		!opTemplates.some((tpl) => ruleAppliesToTemplate(rule, tpl)),
	);
	const operations = rule.operations ?? [];
	return (
		<div
			className={`bg-background border-border flex flex-col gap-1 rounded-md border px-2.5 py-1.5 text-xs ${
				affectsNothing ? 'opacity-60' : ''
			}`}
		>
			<div className="flex items-center gap-2.5">
				<span
					className={`rounded-md border px-1.5 py-0.5 font-mono text-[10px] tracking-wide uppercase ${effectClass}`}
				>
					{rule.effect}
				</span>
				<span className="text-foreground font-mono text-[11px]">{methodsLabel}</span>
				<span className="text-muted-foreground truncate font-mono text-[11px]">
					{rule.path ?? '/'}
					{rule.match_mode && rule.match_mode !== 'regex' ? ` (${rule.match_mode})` : ''}
				</span>
				{warningLabel && (
					<Tooltip content={warningLabel}>
						<span
							className="text-warning inline-flex items-center gap-1 font-mono text-[10px] uppercase"
							role="status"
							aria-label={warningLabel}
						>
							<AlertTriangle className="h-3 w-3 shrink-0" />
							never matches
						</span>
					</Tooltip>
				)}
				{affectsNothing && (
					<Tooltip content="This rule doesn't match any imported operation. Adjust the path or method to grant the access you intend.">
						<span
							className="text-warning inline-flex items-center gap-1 font-mono text-[10px] uppercase"
							role="status"
							aria-label="No operations affected"
						>
							<AlertTriangle className="h-3 w-3 shrink-0" />
							no ops affected
						</span>
					</Tooltip>
				)}
				{isRequested && (
					<Badge variant="default" className="ml-auto text-[10px]">
						requested by agent
					</Badge>
				)}
				{(onEdit || onMoveUp || onMoveDown || onDelete) && (
					<div className="ml-auto flex items-center gap-0.5">
						{onEdit && (
							<button
								type="button"
								className="text-muted-foreground hover:text-foreground p-0.5"
								aria-label="Edit rule"
								onClick={onEdit}
							>
								<Pencil className="h-3.5 w-3.5" />
							</button>
						)}
						{onMoveUp && (
							<button
								type="button"
								className="text-muted-foreground hover:text-foreground p-0.5"
								aria-label="Move rule up"
								onClick={onMoveUp}
							>
								<ArrowUp className="h-3.5 w-3.5" />
							</button>
						)}
						{onMoveDown && (
							<button
								type="button"
								className="text-muted-foreground hover:text-foreground p-0.5"
								aria-label="Move rule down"
								onClick={onMoveDown}
							>
								<ArrowDown className="h-3.5 w-3.5" />
							</button>
						)}
						{onDelete && (
							<button
								type="button"
								className="text-muted-foreground hover:text-danger p-0.5"
								aria-label="Delete rule"
								onClick={onDelete}
							>
								<X className="h-3.5 w-3.5" />
							</button>
						)}
					</div>
				)}
			</div>
			{/* Operation-id constraints narrow the rule to specific operations —
			    the approver MUST see them (and know they survive edits), so they
			    render as chips under the main row. */}
			{operations.length > 0 && (
				<div className="flex flex-wrap items-center gap-1">
					<span className="text-muted-foreground text-[10px] tracking-wide uppercase">
						operations
					</span>
					{operations.map((op) => (
						<code
							key={op}
							className="border-border bg-muted/40 text-foreground rounded border px-1 py-0.5 font-mono text-[10px]"
						>
							{op}
						</code>
					))}
				</div>
			)}
			{rule.comment && (
				<p className="text-muted-foreground text-[11px] italic">{rule.comment}</p>
			)}
		</div>
	);
}

// Swap two elements in an array immutably — used for move-up / move-down.
function swap<T>(items: T[], i: number, j: number): T[] {
	const next = [...items];
	[next[i], next[j]] = [next[j], next[i]];
	return next;
}

// ---------------------------------------------------------------------------
// Rule form — shared draft + body for the Add and Edit paths
// ---------------------------------------------------------------------------

/**
 * Local editing shape for a ``PermissionRule``. Kept separate from the
 * wire type so the form can track a ``Set`` of methods and a raw ``path``
 * string without threading nullable list/string juggling through every
 * field. Converted at save time via {@link ruleFromDraft}.
 *
 * ``operations`` and ``comment`` are NOT editable in this form, but they
 * MUST survive the edit round-trip verbatim: dropping an agent-requested
 * rule's ``operations`` constraint on save would silently WIDEN the
 * grant (a rule constrained to specific operation ids becomes one that
 * matches every operation on the path). They're carried on the draft
 * exactly as found on the source rule and re-emitted by
 * {@link ruleFromDraft}.
 */
interface RuleDraft {
	effect: 'allow' | 'deny';
	methods: Set<string>;
	path: string;
	matchMode: 'regex' | 'prefix' | 'exact';
	/** Carried verbatim from the source rule — never edited here. */
	operations?: string[] | null;
	/** Carried verbatim from the source rule — never edited here. */
	comment?: string | null;
}

const EMPTY_RULE_DRAFT: RuleDraft = {
	effect: 'allow',
	methods: new Set(),
	path: '',
	matchMode: 'prefix',
};

function ruleDraftFromRule(rule: PermissionRule): RuleDraft {
	return {
		effect: rule.effect,
		methods: new Set(rule.methods ?? []),
		path: rule.path ?? '',
		matchMode: (rule.match_mode ?? 'prefix') as RuleDraft['matchMode'],
		operations: rule.operations,
		comment: rule.comment,
	};
}

function ruleFromDraft(draft: RuleDraft): PermissionRule {
	const hasMethods = draft.methods.size > 0;
	const hasPath = draft.path.trim().length > 0;
	const rule: PermissionRule = {
		effect: draft.effect,
		methods: hasMethods ? Array.from(draft.methods) : null,
		path: hasPath ? draft.path.trim() : null,
		match_mode: draft.matchMode,
	};
	// Re-emit only fields the source rule actually carried, so a rule the
	// user authored here (no operations/comment) keeps its original wire
	// shape and the "requested by agent" JSON-identity check stays exact.
	if (draft.operations !== undefined) rule.operations = draft.operations;
	if (draft.comment !== undefined) rule.comment = draft.comment;
	return rule;
}

/**
 * Client-side mirror of ``PermissionRuleSchema._reject_condition_less_allow``
 * so the user gets an inline error instead of a 422 from the server on
 * save. Returns the error string (or ``null`` when the draft is valid).
 */
function validateDraft(draft: RuleDraft): string | null {
	const hasMethods = draft.methods.size > 0;
	const hasPath = draft.path.trim().length > 0;
	const hasOperations = (draft.operations?.length ?? 0) > 0;
	if (draft.effect === 'allow' && !hasMethods && !hasPath && !hasOperations) {
		return 'An "allow" rule must constrain at least one of methods or path.';
	}
	return null;
}

/**
 * Fully-controlled form fields. Consumers own the draft state and the
 * validation lifecycle so this component is trivially reusable between the
 * inline Add form and the inline Edit form.
 */
function RuleFormBody({
	draft,
	onChange,
	error,
	pathSuggestions,
}: {
	draft: RuleDraft;
	onChange: (next: RuleDraft) => void;
	error: string | null;
	pathSuggestions?: readonly string[];
}) {
	const toggleMethod = (method: string): void => {
		const next = new Set(draft.methods);
		if (next.has(method)) next.delete(method);
		else next.add(method);
		onChange({ ...draft, methods: next });
	};

	const paths = pathSuggestions ?? EMPTY_PATHS;
	// Prefix-match filter, capped so the dropdown never dominates the
	// dialog. ``startsWith`` matches on the raw input string — with no
	// path typed we still surface the first ``MAX_PATH_SUGGESTIONS``
	// so the dropdown is useful from the first focus.
	const filteredSuggestions = useMemo(() => {
		if (paths.length === 0) return EMPTY_PATHS;
		const filtered = draft.path ? paths.filter((p) => p.startsWith(draft.path)) : paths;
		return filtered.slice(0, MAX_PATH_SUGGESTIONS);
	}, [paths, draft.path]);

	const [suggestOpen, setSuggestOpen] = useState(false);
	const [highlightedIndex, setHighlightedIndex] = useState(0);
	useEffect(() => {
		setHighlightedIndex(0);
	}, [filteredSuggestions]);

	const commitSuggestion = (path: string): void => {
		onChange({ ...draft, path });
		setSuggestOpen(false);
	};

	const showDropdown = suggestOpen && filteredSuggestions.length > 0;

	const handlePathKeyDown = (e: ReactKeyboardEvent<HTMLInputElement>): void => {
		if (paths.length === 0) return;
		if (e.key === 'Escape') {
			if (suggestOpen) {
				e.preventDefault();
				setSuggestOpen(false);
			}
			return;
		}
		if (e.key === 'ArrowDown' && showDropdown) {
			e.preventDefault();
			setHighlightedIndex((i) => (i + 1) % filteredSuggestions.length);
			return;
		}
		if (e.key === 'ArrowUp' && showDropdown) {
			e.preventDefault();
			setHighlightedIndex(
				(i) => (i - 1 + filteredSuggestions.length) % filteredSuggestions.length,
			);
			return;
		}
		if (e.key === 'Enter' && showDropdown) {
			const pick = filteredSuggestions[highlightedIndex];
			if (pick != null) {
				e.preventDefault();
				commitSuggestion(pick);
			}
			return;
		}
		if (e.key === 'Tab' && !e.shiftKey) {
			const extended = nextPathCompletion(draft.path, paths);
			if (extended == null) return;
			e.preventDefault();
			onChange({ ...draft, path: extended });
		}
	};

	return (
		<div className="space-y-2">
			<div className="flex items-center gap-2">
				<Label className="text-[11px]">Effect</Label>
				<div className="flex gap-1">
					{(['allow', 'deny'] as const).map((e) => (
						<button
							key={e}
							type="button"
							onClick={(): void => onChange({ ...draft, effect: e })}
							className={`rounded-md border px-2 py-0.5 font-mono text-[10px] uppercase ${
								draft.effect === e
									? e === 'allow'
										? 'bg-success/15 text-success border-success/50'
										: 'bg-danger/15 text-danger border-danger/50'
									: 'text-muted-foreground border-border'
							}`}
						>
							{e}
						</button>
					))}
				</div>
			</div>

			<div className="flex flex-wrap items-center gap-2">
				<Label className="text-[11px]">Methods</Label>
				<div className="flex flex-wrap gap-1">
					{['GET', 'POST', 'PUT', 'PATCH', 'DELETE'].map((m) => (
						<button
							key={m}
							type="button"
							onClick={(): void => toggleMethod(m)}
							className={`rounded-md border px-1.5 py-0.5 font-mono text-[10px] ${
								draft.methods.has(m)
									? 'bg-primary/15 text-primary border-primary/40'
									: 'text-muted-foreground border-border'
							}`}
						>
							{m}
						</button>
					))}
				</div>
			</div>

			<div className="flex items-center gap-2">
				<Label className="text-[11px]">Path</Label>
				<div className="relative flex-1">
					<input
						type="text"
						value={draft.path}
						onChange={(e): void => {
							onChange({ ...draft, path: e.target.value });
							setSuggestOpen(true);
						}}
						onKeyDown={handlePathKeyDown}
						onFocus={(): void => setSuggestOpen(true)}
						onBlur={(): void => {
							setTimeout(() => setSuggestOpen(false), 100);
						}}
						placeholder="/repos"
						autoComplete="off"
						aria-label="Path pattern"
						role="combobox"
						aria-expanded={showDropdown}
						aria-autocomplete="list"
						aria-controls="rule-path-suggestions"
						className="border-border bg-background text-foreground placeholder:text-input-placeholder w-full rounded-md border px-2 py-1 font-mono text-[11px]"
					/>
					{showDropdown && (
						<ul
							id="rule-path-suggestions"
							role="listbox"
							className="border-border bg-card shadow-pop absolute top-full right-0 left-0 z-20 mt-1 max-h-56 overflow-y-auto rounded-md border py-1"
						>
							{filteredSuggestions.map((p, i) => (
								<li
									key={p}
									role="option"
									aria-selected={i === highlightedIndex}
									onMouseDown={(e): void => {
										e.preventDefault();
										commitSuggestion(p);
									}}
									onMouseEnter={(): void => setHighlightedIndex(i)}
									className={`cursor-pointer px-2 py-1 font-mono text-[11px] ${
										i === highlightedIndex
											? 'bg-muted text-foreground'
											: 'text-muted-foreground'
									}`}
								>
									{p}
								</li>
							))}
						</ul>
					)}
				</div>
				<select
					aria-label="Path match mode"
					value={draft.matchMode}
					onChange={(e): void =>
						onChange({
							...draft,
							matchMode: e.target.value as RuleDraft['matchMode'],
						})
					}
					className="border-border bg-background text-foreground rounded-md border px-2 py-1 font-mono text-[10px]"
				>
					<option value="prefix">prefix</option>
					<option value="exact">exact</option>
					<option value="regex">regex</option>
				</select>
			</div>

			{/* Operations/comment carried from an agent-requested rule are not
			    editable here, but they stay visible during the edit so the
			    approver knows the constraint survives their changes. */}
			{draft.operations && draft.operations.length > 0 && (
				<div className="flex flex-wrap items-center gap-1.5">
					<Label className="text-[11px]">Operations</Label>
					{draft.operations.map((op) => (
						<code
							key={op}
							className="border-border bg-muted/40 text-foreground rounded border px-1 py-0.5 font-mono text-[10px]"
						>
							{op}
						</code>
					))}
					<span className="text-muted-foreground text-[10px]">
						kept as requested — not editable here
					</span>
				</div>
			)}
			{draft.comment && (
				<p className="text-muted-foreground text-[11px] italic">{draft.comment}</p>
			)}

			{error && <p className="text-danger text-[11px]">{error}</p>}
		</div>
	);
}

function AddRuleForm({
	onAdd,
	pathSuggestions,
}: {
	onAdd: (rule: PermissionRule) => void;
	pathSuggestions?: readonly string[];
}) {
	const [open, setOpen] = useState(false);
	const [draft, setDraft] = useState<RuleDraft>(EMPTY_RULE_DRAFT);
	const [error, setError] = useState<string | null>(null);

	const reset = (): void => {
		setDraft(EMPTY_RULE_DRAFT);
		setError(null);
	};

	const handleSave = (): void => {
		const msg = validateDraft(draft);
		if (msg) {
			setError(msg);
			return;
		}
		onAdd(ruleFromDraft(draft));
		setOpen(false);
		reset();
	};

	if (!open) {
		return (
			<Button
				type="button"
				variant="ghost"
				size="sm"
				className="w-full justify-start"
				onClick={(): void => setOpen(true)}
			>
				<Plus className="h-3.5 w-3.5" />
				Add rule
			</Button>
		);
	}

	return (
		<div className="border-border bg-background rounded-lg border p-3">
			<RuleFormBody
				draft={draft}
				onChange={setDraft}
				error={error}
				pathSuggestions={pathSuggestions}
			/>
			<div className="flex items-center justify-end gap-2 pt-2">
				<Button
					type="button"
					variant="ghost"
					size="sm"
					onClick={(): void => {
						setOpen(false);
						reset();
					}}
				>
					Cancel
				</Button>
				<Button type="button" variant="primary" size="sm" onClick={handleSave}>
					Add
				</Button>
			</div>
		</div>
	);
}

function InlineEditRuleForm({
	initial,
	onSave,
	onCancel,
	pathSuggestions,
}: {
	initial: PermissionRule;
	onSave: (rule: PermissionRule) => void;
	onCancel: () => void;
	pathSuggestions?: readonly string[];
}) {
	const [draft, setDraft] = useState<RuleDraft>(() => ruleDraftFromRule(initial));
	const [error, setError] = useState<string | null>(null);

	const handleSave = (): void => {
		const msg = validateDraft(draft);
		if (msg) {
			setError(msg);
			return;
		}
		onSave(ruleFromDraft(draft));
	};

	return (
		<div className="border-border bg-background rounded-lg border p-3">
			<RuleFormBody
				draft={draft}
				onChange={setDraft}
				error={error}
				pathSuggestions={pathSuggestions}
			/>
			<div className="flex items-center justify-end gap-2 pt-2">
				<Button type="button" variant="ghost" size="sm" onClick={onCancel}>
					Cancel
				</Button>
				<Button type="button" variant="primary" size="sm" onClick={handleSave}>
					Save
				</Button>
			</div>
		</div>
	);
}
