/**
 * Compact rule-list editor shared between the credentials connect-flow
 * rules page and the Agents page's per-binding editor. Owns the row list
 * (edit-in-place / delete / reorder), the "add rule" affordance, and
 * the shared draft form + validation.
 *
 * Callers own the ``rules`` state and get an ``onChange`` callback
 * — everything below that (rule-form draft, autocomplete dropdown,
 * regex-validity warnings) is internal.
 */

import type { KeyboardEvent as ReactKeyboardEvent, ReactNode } from 'react';
import { useEffect, useId, useMemo, useRef, useState } from 'react';
import {
	AlertTriangle,
	ArrowDown,
	ArrowUp,
	Ban,
	Check,
	ListChecks,
	Pencil,
	Plus,
	Trash2,
} from 'lucide-react';
import { Badge, Button, Input, Label, Select, Tooltip } from '@/shared/ui';
import { cn } from '@/shared/lib/utils';
import {
	examplePath,
	examplePathPrefix,
	nextPathCompletion,
} from '@/shared/credentials/lib/path-completion';
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
	addActions,
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
	 * "No rules yet" panel when ``rules.length === 0``.
	 * Used by the connect-flow rules page to render its greyed-out
	 * ``Allow GET /`` fallback preview.
	 */
	emptyStateContent?: ReactNode;
	/**
	 * The host's own list verbs (e.g. "Allow all operations"), rendered on the
	 * same row as "Add rule" — inside the empty panel while there are no rules.
	 */
	addActions?: ReactNode;
	className?: string;
}) {
	const [editingIndex, setEditingIndex] = useState<number | null>(null);
	const [adding, setAdding] = useState(false);
	// While a form is open, every other row's verbs are disabled: the open edit
	// is addressed by position, so a delete/move underneath it would land its
	// Save on a different rule.
	const formOpen = adding || editingIndex !== null;

	// Focus returns to the verb that opened the form (Add rule / that row's
	// Edit) once it closes — the form's own buttons unmount with it, and focus
	// would otherwise drop to <body>.
	const rootRef = useRef<HTMLDivElement>(null);
	const [returnFocusTo, setReturnFocusTo] = useState<string | null>(null);
	useEffect(() => {
		if (returnFocusTo == null || formOpen) return;
		rootRef.current?.querySelector<HTMLElement>(returnFocusTo)?.focus({ preventScroll: true });
		setReturnFocusTo(null);
	}, [returnFocusTo, formOpen]);
	const closeAdd = (): void => {
		setAdding(false);
		setReturnFocusTo('[data-rule-add]');
	};
	const closeEdit = (i: number): void => {
		setEditingIndex(null);
		setReturnFocusTo(`[data-rule-edit="${i}"]`);
	};

	// Fast index for the "requested by agent" tag. Deep-compare by
	// JSON so rules the user edits in place lose the tag once they
	// diverge from what the agent originally requested.
	const requestedKeys = useMemo(
		() => new Set((requestedRules ?? []).map((r) => JSON.stringify(r))),
		[requestedRules],
	);

	// The list verbs: "Add rule" plus whatever the host contributes. While the
	// list is empty (and the host has no custom empty state) they sit INSIDE
	// the empty panel, so the next step is where the eye already is.
	const actionRow = (
		<div className="flex flex-wrap items-center gap-2">
			<Button
				type="button"
				variant="secondary"
				size="sm"
				onClick={(): void => setAdding(true)}
				disabled={editingIndex !== null}
				data-rule-add=""
			>
				<Plus className="h-4 w-4" aria-hidden="true" />
				Add rule
			</Button>
			{addActions}
		</div>
	);
	const isEmpty = rules.length === 0;
	const defaultEmpty = isEmpty && emptyStateContent == null;

	return (
		<div ref={rootRef} className={cn('edged-controls space-y-2', className)}>
			{isEmpty ? (
				defaultEmpty ? (
					!adding && <EmptyRulesPanel actions={actionRow} />
				) : (
					emptyStateContent
				)
			) : (
				<ol className="space-y-1.5" aria-label="Rules, in evaluation order">
					{rules.map((rule, i) => (
						<li key={i}>
							{editingIndex === i ? (
								<RuleForm
									initial={rule}
									commitLabel="Save"
									onCommit={(updated): void => {
										onChange(rules.map((r, j) => (j === i ? updated : r)));
										closeEdit(i);
									}}
									onCancel={(): void => closeEdit(i)}
									pathSuggestions={pathSuggestions}
								/>
							) : (
								<RulePreviewRow
									index={i}
									rule={rule}
									isRequested={requestedKeys.has(JSON.stringify(rule))}
									opTemplates={opTemplates}
									opsLoaded={opsLoaded}
									locked={formOpen}
									onEdit={(): void => setEditingIndex(i)}
									onDelete={(): void => onChange(rules.filter((_, j) => j !== i))}
									onMoveUp={
										i === 0
											? undefined
											: (): void => onChange(swap(rules, i, i - 1))
									}
									onMoveDown={
										i === rules.length - 1
											? undefined
											: (): void => onChange(swap(rules, i, i + 1))
									}
								/>
							)}
						</li>
					))}
				</ol>
			)}
			{adding ? (
				<RuleForm
					commitLabel="Add"
					onCommit={(rule): void => {
						onChange([...rules, rule]);
						closeAdd();
					}}
					onCancel={closeAdd}
					pathSuggestions={pathSuggestions}
				/>
			) : (
				!defaultEmpty && actionRow
			)}
		</div>
	);
}

/** The no-rules panel: what an empty list MEANS (default deny), plus the verbs. */
function EmptyRulesPanel({ actions }: { actions: ReactNode }) {
	return (
		<div
			data-testid="rules-empty-state"
			className="bg-surface-sheet/60 flex flex-col gap-3 rounded-lg px-4 py-4 sm:flex-row sm:items-center sm:justify-between"
		>
			<div className="flex min-w-0 items-start gap-3">
				<span
					aria-hidden="true"
					className="bg-surface-tonal text-foreground-sub inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-md"
				>
					<ListChecks className="h-4 w-4" />
				</span>
				<div className="min-w-0">
					<p className="text-foreground text-sm font-medium">No rules yet</p>
					<p className="text-foreground-sub text-xs leading-relaxed">
						Anything unmatched is denied — add a rule to grant access.
					</p>
				</div>
			</div>
			<div className="shrink-0">{actions}</div>
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
		<div className="bg-field flex items-center gap-2.5 rounded-md px-2.5 py-1.5 text-xs opacity-70">
			<Badge variant="success">{rule.effect}</Badge>
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
	index,
	rule,
	isRequested,
	opTemplates,
	opsLoaded,
	locked = false,
	onEdit,
	onDelete,
	onMoveUp,
	onMoveDown,
}: {
	index: number;
	rule: PermissionRule;
	isRequested: boolean;
	opTemplates?: readonly string[];
	opsLoaded?: boolean;
	/** Another rule's form is open — this row's verbs are disabled until it closes. */
	locked?: boolean;
	onEdit?: () => void;
	onDelete?: () => void;
	onMoveUp?: () => void;
	onMoveDown?: () => void;
}) {
	const effectVariant = rule.effect === 'allow' ? 'success' : 'danger';
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
			// Borderless: one fill step above the card. "Touches nothing" is a
			// warning, not a disabled row — the inline label carries it, so the
			// rule's text keeps full contrast.
			className="bg-surface-tonal flex flex-col gap-1.5 rounded-md px-2.5 py-2 text-xs"
		>
			<div className="flex flex-wrap items-center gap-x-2.5 gap-y-1.5">
				<div className="flex min-w-[min(100%,15rem)] flex-1 items-center gap-2.5">
					{/* Evaluation position — first match wins, and the rule tester's
					    verdict cites the same number. */}
					<span
						className="bg-surface-sheet text-foreground-sub inline-flex h-5 min-w-6 shrink-0 items-center justify-center rounded-[5px] px-1 font-mono text-[11px] font-semibold"
						title={`Evaluated ${index === 0 ? 'first' : `#${index + 1}`}`}
					>
						#{index + 1}
					</span>
					<Badge variant={effectVariant} className="shrink-0 rounded-[5px] font-semibold">
						{rule.effect}
					</Badge>
					<span className="text-foreground shrink-0 font-mono text-[11px]">
						{methodsLabel}
					</span>
					<span className="text-foreground-sub min-w-0 truncate font-mono text-[11px]">
						{rule.path ?? '/'}
						{rule.match_mode && rule.match_mode !== 'regex'
							? ` (${rule.match_mode})`
							: ''}
					</span>
				</div>
				{warningLabel && (
					<Tooltip content={warningLabel}>
						<span
							className="text-foreground-sub inline-flex items-center gap-1 font-mono text-[10px] uppercase"
							role="status"
							aria-label={warningLabel}
						>
							<AlertTriangle className="text-caution h-3 w-3 shrink-0" />
							never matches
						</span>
					</Tooltip>
				)}
				{affectsNothing && (
					<Tooltip content="This rule doesn't match any imported operation. Adjust the path or method to grant the access you intend.">
						<span
							className="text-foreground-sub inline-flex items-center gap-1 font-mono text-[10px] uppercase"
							role="status"
							aria-label="No operations affected"
						>
							<AlertTriangle className="text-caution h-3 w-3 shrink-0" />
							no ops affected
						</span>
					</Tooltip>
				)}
				{isRequested && (
					<Badge variant="default" className="shrink-0">
						requested by agent
					</Badge>
				)}
				{(onEdit || onMoveUp || onMoveDown || onDelete) && (
					<div className="ml-auto flex shrink-0 items-center gap-0.5">
						{onEdit && (
							<Button
								type="button"
								variant="ghost"
								size="icon-xs"
								className="text-foreground-sub"
								aria-label="Edit rule"
								data-rule-edit={index}
								disabled={locked}
								onClick={onEdit}
							>
								<Pencil className="h-3.5 w-3.5" />
							</Button>
						)}
						{/* Both reorder verbs always render (disabled at the ends) so the
						    row's controls keep one column from row to row. */}
						{(onMoveUp || onMoveDown) && (
							<>
								<Button
									type="button"
									variant="ghost"
									size="icon-xs"
									className="text-foreground-sub"
									aria-label="Move rule up"
									disabled={locked || !onMoveUp}
									onClick={onMoveUp}
								>
									<ArrowUp className="h-3.5 w-3.5" />
								</Button>
								<Button
									type="button"
									variant="ghost"
									size="icon-xs"
									className="text-foreground-sub"
									aria-label="Move rule down"
									disabled={locked || !onMoveDown}
									onClick={onMoveDown}
								>
									<ArrowDown className="h-3.5 w-3.5" />
								</Button>
							</>
						)}
						{onDelete && (
							<Button
								type="button"
								variant="ghost"
								size="icon-xs"
								className="text-foreground-sub hover:bg-danger/10 hover:text-danger"
								aria-label="Delete rule"
								disabled={locked}
								onClick={onDelete}
							>
								<Trash2 className="h-3.5 w-3.5" />
							</Button>
						)}
					</div>
				)}
			</div>
			{/* Operation-id constraints narrow the rule to specific operations —
			    the approver MUST see them (and know they survive edits), so they
			    render as chips under the main row. */}
			{operations.length > 0 && (
				<div className="flex flex-wrap items-center gap-1">
					<span className="text-foreground-sub text-[10px] tracking-wide uppercase">
						operations
					</span>
					{operations.map((op) => (
						<code
							key={op}
							className="bg-surface-sheet text-foreground rounded-sm px-1 py-0.5 font-mono text-[10px]"
						>
							{op}
						</code>
					))}
				</div>
			)}
			{rule.comment && (
				<p className="text-foreground-sub text-[11px] italic">{rule.comment}</p>
			)}
		</div>
	);
}

// Swap two elements in an array immutably — used for move-up / move-down.
function swap<T>(items: readonly T[], i: number, j: number): T[] {
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
		// A stored path with no mode is matched as `regex` by the backend, so the
		// draft must read it the same way — defaulting to `prefix` would re-save
		// `.*` as a literal that matches nothing.
		matchMode: (rule.match_mode ?? (rule.path ? 'regex' : 'prefix')) as RuleDraft['matchMode'],
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
	const ids = useId();
	const toggleMethod = (method: string): void => {
		const next = new Set(draft.methods);
		if (next.has(method)) next.delete(method);
		else next.add(method);
		onChange({ ...draft, methods: next });
	};

	const paths = pathSuggestions ?? EMPTY_PATHS;
	// The example names a route of the API being edited, never another vendor's.
	const pathPlaceholder =
		draft.matchMode === 'prefix' ? examplePathPrefix(paths) : examplePath(paths);

	// Focus the first field when the form opens: the verb that opened it
	// unmounts, and focus must not drop to <body>.
	const effectGroupRef = useRef<HTMLDivElement>(null);
	useEffect(() => {
		effectGroupRef.current
			?.querySelector<HTMLButtonElement>('[aria-pressed="true"]')
			?.focus({ preventScroll: true });
	}, []);

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
		<div className="grid grid-cols-[4.25rem_minmax(0,1fr)] items-center gap-x-3 gap-y-2.5">
			<Label id={`${ids}-effect`} className="text-foreground-sub text-xs">
				Effect
			</Label>
			{/* Segmented allow/deny: the chosen side carries colour AND a glyph AND
			    its word, so the effect never rests on hue alone. */}
			<div
				ref={effectGroupRef}
				role="group"
				aria-labelledby={`${ids}-effect`}
				className="rounded-field inline-flex w-fit gap-0.5 border border-[hsl(var(--control-edge))] p-0.5"
			>
				{(['allow', 'deny'] as const).map((e) => {
					const active = draft.effect === e;
					return (
						<Button
							key={e}
							type="button"
							variant="ghost"
							size="xs"
							onClick={(): void => onChange({ ...draft, effect: e })}
							aria-pressed={active}
							className={cn(
								// Inner radius = the group's 9px − its 3px inset (border + padding).
								'min-w-[4.5rem] rounded-[6px] capitalize',
								active
									? e === 'allow'
										? 'bg-success/15 text-success hover:bg-success/20 hover:text-success'
										: 'bg-danger/15 text-danger hover:bg-danger/20 hover:text-danger'
									: 'text-foreground-sub',
							)}
						>
							{active &&
								(e === 'allow' ? (
									<Check className="h-3.5 w-3.5" aria-hidden="true" />
								) : (
									<Ban className="h-3.5 w-3.5" aria-hidden="true" />
								))}
							{e}
						</Button>
					);
				})}
			</div>

			<Label id={`${ids}-methods`} className="text-foreground-sub self-start pt-1.5 text-xs">
				Methods
			</Label>
			<div className="flex flex-wrap items-center gap-1.5">
				<div
					role="group"
					aria-labelledby={`${ids}-methods`}
					className="flex flex-wrap gap-1"
				>
					{['GET', 'POST', 'PUT', 'PATCH', 'DELETE'].map((m) => {
						const selected = draft.methods.has(m);
						return (
							<Button
								key={m}
								type="button"
								variant="ghost"
								size="xs"
								onClick={(): void => toggleMethod(m)}
								aria-pressed={selected}
								className={cn(
									'px-2 font-mono text-[11px]',
									selected
										? 'bg-primary/15 text-primary hover:bg-primary/20 hover:text-primary shadow-[inset_0_0_0_1px_hsl(var(--primary)/0.55)]'
										: 'text-foreground-sub shadow-[inset_0_0_0_1px_hsl(var(--control-edge))]',
								)}
							>
								{selected && <Check className="h-3 w-3" aria-hidden="true" />}
								{m}
							</Button>
						);
					})}
				</div>
				<span className="text-foreground-sub text-[11px]">none = any method</span>
			</div>

			<Label htmlFor={`${ids}-path`} className="text-foreground-sub text-xs">
				Path
			</Label>
			<div className="flex min-w-0 items-center gap-2">
				<div className="relative min-w-0 flex-1">
					<Input
						id={`${ids}-path`}
						type="text"
						size="sm"
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
						placeholder={pathPlaceholder}
						autoComplete="off"
						aria-label="Path pattern"
						role="combobox"
						aria-expanded={showDropdown}
						aria-autocomplete="list"
						aria-controls={`${ids}-suggestions`}
						aria-invalid={error ? true : undefined}
						aria-describedby={error ? `${ids}-error` : undefined}
						className={cn(
							'px-2.5 py-1.5 font-mono text-xs',
							error && 'border-danger focus:border-danger',
						)}
					/>
					{showDropdown && (
						<ul
							id={`${ids}-suggestions`}
							role="listbox"
							className="bg-surface-sheet shadow-pop absolute top-full right-0 left-0 z-20 mt-1 max-h-56 overflow-y-auto rounded-md py-1"
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
									className={cn(
										'cursor-pointer px-2 py-1 font-mono text-[11px]',
										i === highlightedIndex
											? 'bg-tint-2 text-foreground'
											: 'text-foreground-sub',
									)}
								>
									{p}
								</li>
							))}
						</ul>
					)}
				</div>
				<div className="w-[5.75rem] shrink-0">
					<Select
						aria-label="Path match mode"
						value={draft.matchMode}
						onChange={(e): void =>
							onChange({
								...draft,
								matchMode: e.target.value as RuleDraft['matchMode'],
							})
						}
						className="px-2 py-1.5 font-mono text-xs"
					>
						<option value="prefix">prefix</option>
						<option value="exact">exact</option>
						<option value="regex">regex</option>
					</Select>
				</div>
			</div>

			{/* Operations/comment carried from an agent-requested rule are not
			    editable here, but they stay visible during the edit so the
			    approver knows the constraint survives their changes. */}
			{draft.operations && draft.operations.length > 0 && (
				<>
					<Label className="text-foreground-sub text-xs">Operations</Label>
					<div className="flex flex-wrap items-center gap-1.5">
						{draft.operations.map((op) => (
							<code
								key={op}
								className="bg-surface-chip text-foreground rounded-sm px-1 py-0.5 font-mono text-[10px]"
							>
								{op}
							</code>
						))}
						<span className="text-foreground-sub text-[10px]">
							kept as requested — not editable here
						</span>
					</div>
				</>
			)}
			{draft.comment && (
				<p className="text-foreground-sub col-span-2 text-[11px] italic">{draft.comment}</p>
			)}

			{error && (
				<p
					id={`${ids}-error`}
					role="alert"
					className="text-danger col-span-2 flex items-start gap-1.5 text-xs"
				>
					<AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
					<span>{error}</span>
				</p>
			)}
		</div>
	);
}

/**
 * The open Add / Edit form: a borderless recessed well inside the card (a
 * danger tint once the draft fails validation), its own Cancel + commit pair.
 */
function RuleFormShell({
	children,
	commitLabel,
	onCommit,
	onCancel,
}: {
	children: ReactNode;
	commitLabel: string;
	onCommit: () => void;
	onCancel: () => void;
}) {
	return (
		// A recessed well (the sheet tone) inside the card, borderless; its fields
		// carry the edges, and a failed draft shows on the field (danger edge)
		// plus the inline message.
		<div className="bg-surface-sheet rounded-lg p-3 [--field-bg:var(--surface-field)]">
			{children}
			<div className="flex items-center justify-end gap-2 pt-3">
				<Button type="button" variant="ghost" size="sm" onClick={onCancel}>
					Cancel
				</Button>
				<Button type="button" variant="outline" size="sm" onClick={onCommit}>
					{commitLabel}
				</Button>
			</div>
		</div>
	);
}

/** The open Add / Edit form: a fresh draft (or `initial`'s), validated on commit. */
function RuleForm({
	initial,
	commitLabel,
	onCommit,
	onCancel,
	pathSuggestions,
}: {
	initial?: PermissionRule;
	commitLabel: string;
	onCommit: (rule: PermissionRule) => void;
	onCancel: () => void;
	pathSuggestions?: readonly string[];
}) {
	const [draft, setDraft] = useState<RuleDraft>(() =>
		initial ? ruleDraftFromRule(initial) : EMPTY_RULE_DRAFT,
	);
	const [error, setError] = useState<string | null>(null);

	const handleCommit = (): void => {
		const msg = validateDraft(draft);
		if (msg) {
			setError(msg);
			return;
		}
		onCommit(ruleFromDraft(draft));
	};

	return (
		<RuleFormShell commitLabel={commitLabel} onCommit={handleCommit} onCancel={onCancel}>
			<RuleFormBody
				draft={draft}
				onChange={setDraft}
				error={error}
				pathSuggestions={pathSuggestions}
			/>
		</RuleFormShell>
	);
}
