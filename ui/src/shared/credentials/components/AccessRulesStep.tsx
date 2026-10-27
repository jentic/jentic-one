/**
 * AccessRulesStep — "What can this agent call?": the access presets (Read only ·
 * Read only, ask before anything else · Full access · Custom rules), the coverage
 * caveat for a credential that reaches beyond one pinned API, and under Custom
 * rules two editors of one saved list:
 *
 * - By action: each action Allow, Ask or Deny (or unset). Choices are staged
 *   until Apply turns them into at most one Deny, one Ask and one Allow rule.
 * - By path: the whole list in evaluation order — the action rules first
 *   (fixed: a choice on a specific action always wins), then the path rules,
 *   which cover everything not set by action and can be reordered.
 *
 * Below sit "Effective access" (every call and its outcome) and an inline
 * "Try a request" dry run with the broker's semantics.
 *
 * Controlled: the host owns the preset and the custom rules, and turns them
 * into the rules to save with `rulesForPreset`. The workspace bind dialog, the
 * Agents tab's Add-APIs queue and Manage access all render it, so the choice
 * reads the same wherever a binding is made or changed.
 */
import {
	useEffect,
	useId,
	useMemo,
	useRef,
	useState,
	type MutableRefObject,
	type ReactNode,
} from 'react';
import { ChevronRight, FlaskConical, ListChecks, Loader2, ScanEye, Trash2 } from 'lucide-react';
import {
	Button,
	DetailsLine,
	ExpandReveal,
	InlineAction,
	RadioCardGroup,
	SegmentedToggle,
} from '@/shared/ui';
import { cn } from '@/shared/lib/utils';
import { useVendorOperationsProgress } from '@/shared/credentials/api/vendors-hooks';
import type { PermissionRule } from '@/shared/credentials/api/vendors-types';
import { RuleListEditor } from '@/shared/credentials/components/RuleListEditor';
import { ActionList } from '@/shared/credentials/components/ActionList';
import {
	ActionChecklist,
	changedChoices,
	choiceCounts,
} from '@/shared/credentials/components/ActionChecklist';
import {
	UnappliedChoicesDialog,
	type UnappliedChoices,
} from '@/shared/credentials/components/UnappliedChoicesDialog';
import { EFFECT_WORD, EffectBadge } from '@/shared/credentials/components/EffectBadge';
import { AccessRulesHelp } from '@/shared/credentials/components/AccessRulesHelp';
import {
	useActionCatalog,
	type SiblingApiReference,
} from '@/shared/credentials/components/useActionCatalog';
import { DraftRuleTester } from '@/shared/credentials/components/RuleRequestTester';
import { ruleSummary, type PermissionRule as DisplayRule } from '@/shared/lib';
import { OperationImpactPreview } from '@/shared/credentials/components/OperationImpactPreview';
import {
	describeActions,
	siblingActions,
	siblingActionsLine,
	type ActionCatalog,
	type SiblingActions,
} from '@/shared/credentials/lib/actionRules';
import {
	actionRuleCount,
	coverageNote,
	draftRulesForPreset,
	joinCustomRules,
	presetOptions,
	splitCustomRules,
	type ActionGroups,
	type RulesPreset,
	type ScopeReach,
} from '@/shared/credentials/lib/accessPresets';

export interface AccessRulesStepProps {
	reach: ScopeReach;
	preset: RulesPreset | null;
	onPresetChange: (preset: RulesPreset) => void;
	customRules: PermissionRule[];
	onCustomRulesChange: (rules: PermissionRule[]) => void;
	/**
	 * The API (with a concrete version) whose operations seed the editor's path
	 * suggestions, the tester's placeholder, the actions checklist and the
	 * effective access. Omitted → each works without them, or isn't offered.
	 */
	apiReference?: { vendor: string; name: string; version: string } | null;
	/** The operations are coming but `apiReference` can't read them yet (API still importing). */
	operationsPending?: boolean;
	/** Fetch the API's operations only while the host is showing this step. */
	active?: boolean;
	disabled?: boolean;
	/** The question above the presets. */
	heading?: ReactNode;
	/** Offer the inline "Try a request" dry run against the rules being edited. */
	tester?: boolean;
	/** A host-owned last row for the tools list (e.g. "Add without rules…"). */
	extraTools?: ReactNode;
	/**
	 * The other APIs these rules govern (one rule list shared across them). Their
	 * actions are named, never listed for editing: By action shows this API's
	 * actions only, and keeps the others' exactly as they are.
	 */
	siblings?: readonly SiblingApiReference[];
	/** Open another API's access panel, from the line that names its actions. */
	onOpenSibling?: (key: string) => void;
	/** The Custom rules editor to open on. Default: By path. */
	initialBuild?: 'action' | 'path';
	/** Leave out the coverage caveat (the host names the other APIs itself). */
	hideCoverageNote?: boolean;
	/**
	 * By action's choices are staged until Apply. The host reads this ref to
	 * settle them (apply or discard) before it saves or closes.
	 */
	stagedRef?: MutableRefObject<UnappliedChoices | null>;
	/** How many By action choices are staged, as it changes. */
	onStagedChange?: (count: number) => void;
	/**
	 * Offer the Effective access tool. Off where the host shows the preview
	 * itself (Manage access, always visible below the editor).
	 */
	effectiveTool?: boolean;
	/** Prefix for the step's `data-testid`s (`<prefix>-rules`, `<prefix>-rules-preset`,
	 * `<prefix>-coverage-note`, `<prefix>-tester`, `<prefix>-advanced`,
	 * `<prefix>-effective`, `<prefix>-actions`, `<prefix>-rules-help`). */
	testIdPrefix?: string;
}

/** How the inline tester names the rule that decided, for a preset — whose
 * rules are never shown as numbered editor rows. */
const PRESET_MATCH: Record<Exclude<RulesPreset, 'custom'>, (index: number) => string> = {
	read: () => '— allowed by “Read only”',
	// Rule 0 is its GET allow; rule 1 is the catch-all Ask.
	'read-ask': (index) =>
		index === 0
			? '— allowed by “Read only, ask before anything else”'
			: '— held for approval by “Read only, ask before anything else”',
	all: () => '— allowed by “Full access”',
};

export function AccessRulesStep({
	reach,
	preset,
	onPresetChange,
	customRules,
	onCustomRulesChange,
	apiReference,
	operationsPending = false,
	active = true,
	disabled = false,
	heading = 'What can this agent call?',
	tester = false,
	extraTools,
	siblings,
	onOpenSibling,
	initialBuild = 'path',
	hideCoverageNote = false,
	effectiveTool = true,
	stagedRef,
	onStagedChange,
	testIdPrefix: p = 'access',
}: AccessRulesStepProps) {
	const headingId = useId();
	const [open, setOpen] = useState<'tester' | 'effective' | null>(null);
	const toggle = (key: NonNullable<typeof open>) => setOpen((o) => (o === key ? null : key));
	// Operations as far as read (usable from the first page); fetched only once
	// Custom rules or a tool is shown.
	const wantsOps = active && !!apiReference && (preset === 'custom' || open !== null);
	const opsLoad = useVendorOperationsProgress(apiReference ?? undefined, {
		enabled: wantsOps,
	});
	const opsQuery = opsLoad.query;
	const ops = opsLoad.operations;
	const opsComplete = opsLoad.complete;
	// The same read, as the catalog that names the action rule's ids.
	const actionCatalog = useActionCatalog(apiReference, {
		enabled: wantsOps,
		pending: operationsPending,
		siblings,
	});
	const pathSuggestions = useMemo<readonly string[]>(
		() => Array.from(new Set(ops.map((op) => op.path))).sort(),
		[ops],
	);
	const options = useMemo(() => presetOptions(reach), [reach]);
	const note = hideCoverageNote ? null : coverageNote(reach);

	const draftRules = useMemo(
		() => draftRulesForPreset(preset, customRules),
		[preset, customRules],
	);
	const { pathRules, groups } = useMemo(() => splitCustomRules(customRules), [customRules]);
	// In Custom rules the dry run names a rule as By path numbers it: path
	// rule N among the path rules, or the By action choice that decided.
	const customActionRules = preset === 'custom' ? actionRuleCount(groups) : 0;
	const describeMatch =
		preset && preset !== 'custom'
			? PRESET_MATCH[preset]
			: preset === 'custom'
				? (index: number): ReactNode => {
						const rule = draftRules[index];
						if (index < customActionRules && rule) {
							return `— set by action (${EFFECT_RULE_NAME[rule.effect] ?? 'Allow'})`;
						}
						const summary = rule
							? ruleSummary([rule as DisplayRule]).replace(/\.$/, '')
							: null;
						return (
							<>
								— matched path rule{' '}
								<span className="text-foreground font-mono font-semibold">
									#{index - customActionRules + 1}
								</span>
								{summary ? <> · {summary}</> : null}
							</>
						);
					}
				: undefined;

	// The toggle picks the editor; both halves of the one saved list stay
	// reachable. It appears once the first page of operations lands.
	const actionsAvailable = ops.length > 0;
	const opsLoading = opsQuery.isLoading || operationsPending;
	const actionsPending = !actionsAvailable && opsLoading;
	const [build, setBuild] = useState<'action' | 'path'>(initialBuild);
	const buildMode = actionsAvailable ? build : 'path';
	const setPathRules = (next: PermissionRule[]): void =>
		onCustomRulesChange(joinCustomRules(next, groups));

	// By action is STAGED: choices land in `staged` and become rules on Apply.
	const [staged, setStaged] = useState<ActionGroups | null>(null);
	const shownGroups = staged ?? groups;
	const unapplied = staged ? changedChoices(staged, groups) : 0;
	// Applied choices show as rule cards; the operations list then folds behind
	// a line that says how many are still unset, until Edit or the line opens it.
	const hasActionRules = (['deny', 'ask', 'allow'] as const).some((g) => groups[g].length > 0);
	const [listOpen, setListOpen] = useState(false);
	const listShown = !hasActionRules || listOpen;
	const unsetCount = useMemo(
		() =>
			choiceCounts(
				groups,
				ops.map((op) => op.operation_id),
			).unset,
		[groups, ops],
	);
	/** Editing one action rule: By action filtered to its ids. */
	const [editing, setEditing] = useState<keyof ActionGroups | null>(null);
	const editingIds = useMemo(
		() => (editing ? new Set(groups[editing]) : null),
		[editing, groups],
	);
	const apply = (): PermissionRule[] => {
		const next = staged ? joinCustomRules(pathRules, staged) : customRules;
		if (staged) onCustomRulesChange(next);
		setStaged(null);
		setListOpen(false);
		return next;
	};
	const discard = (): void => setStaged(null);
	// The host settles staged choices before it moves on (adding the API,
	// closing the panel) — never silently dropping them.
	const unappliedState = useMemo<UnappliedChoices | null>(
		() => (unapplied > 0 ? { count: unapplied, apply, discard } : null),
		// `apply` closes over the current draft, captured with `staged`.
		// eslint-disable-next-line react-hooks/exhaustive-deps
		[unapplied, staged, customRules],
	);
	if (stagedRef) stagedRef.current = unappliedState;
	const onStagedChangeRef = useRef(onStagedChange);
	useEffect(() => {
		onStagedChangeRef.current = onStagedChange;
	});
	useEffect(() => {
		onStagedChangeRef.current?.(unapplied);
	}, [unapplied]);
	// Unmounted, nothing is staged any more: the host must not settle choices
	// that left with this step.
	useEffect(
		() => () => {
			if (stagedRef) stagedRef.current = null;
			onStagedChangeRef.current?.(0);
		},
		[stagedRef],
	);
	const [guard, setGuard] = useState<null | { then: () => void; context: string }>(null);
	/** Leave By action (or this rule's edit): settle staged choices first. */
	const leaveActions = (then: () => void, context: string): void => {
		if (unapplied > 0) setGuard({ then, context });
		else {
			setStaged(null);
			then();
		}
	};
	const switchBuild = (to: 'action' | 'path'): void => {
		if (to === buildMode) return;
		if (to === 'path')
			leaveActions(() => (setEditing(null), setBuild('path')), 'before switching to By path');
		else setBuild('action');
	};
	const editActionRule = (g: keyof ActionGroups): void => {
		setEditing(g);
		setListOpen(true);
		setBuild('action');
	};
	/** Fold the operations list, settling staged choices first. */
	const closeList = (context: string): void =>
		leaveActions(() => (setEditing(null), setListOpen(false)), context);

	// Action choices are checked first, so a path rule whose every match (on
	// any API the list governs) is one of them decides nothing.
	const decided = useMemo(
		() => new Set([...groups.allow, ...groups.ask, ...groups.deny]),
		[groups],
	);

	// Only judged once this API's list is whole: until then an id of its own
	// could still read as another API's.
	const siblingRows = useMemo(
		() => (actionCatalog.status === 'ready' ? siblingActions(groups, actionCatalog) : []),
		[groups, actionCatalog],
	);
	const actionRows = (['deny', 'ask', 'allow'] as const)
		.filter((g) => groups[g].length > 0)
		.map((g) => (
			<ActionRuleRow
				key={g}
				operations={groups[g]}
				effect={GROUP_EFFECT[g]}
				catalog={actionCatalog}
				disabled={disabled}
				onEdit={actionsAvailable ? (): void => editActionRule(g) : undefined}
				onRemove={(): void =>
					onCustomRulesChange(joinCustomRules(pathRules, { ...groups, [g]: [] }))
				}
				testId={`${p}-action-rule${g === 'allow' ? '' : `-${g}`}`}
			/>
		));
	const editingHeader =
		editing && editingIds ? (
			<div
				className="flex flex-wrap items-center gap-2 text-xs"
				data-testid={`${p}-editing-rule`}
			>
				<span className="text-foreground font-medium">
					Editing the {EFFECT_RULE_NAME[GROUP_EFFECT[editing]]} actions ·{' '}
					{editingIds.size} {editingIds.size === 1 ? 'action' : 'actions'}
				</span>
				<Button
					variant="secondary"
					size="xs"
					className="ml-auto"
					onClick={(): void => closeList('before closing the list')}
				>
					Done
				</Button>
			</div>
		) : null;

	return (
		<div className="space-y-2" data-testid={`${p}-rules`} data-preset={preset ?? undefined}>
			<p id={headingId} className="text-foreground text-sm font-medium">
				{heading}
			</p>
			<RadioCardGroup<RulesPreset>
				options={options}
				value={preset}
				onChange={onPresetChange}
				ariaLabelledBy={headingId}
				disabled={disabled}
				data-testid={`${p}-rules-preset`}
				className={cn(
					'divide-y-0 bg-transparent',
					'[&_[role=radio]]:min-h-9 [&_[role=radio]]:gap-3 [&_[role=radio]]:px-3 [&_[role=radio]]:py-2',
					'[&_[role=radio][aria-checked=true]]:bg-surface-selected',
					'[&_[role=radio]]:rounded-lg',
					'[&_[role=radio]>span:first-child]:flex [&_[role=radio]>span:first-child]:flex-wrap [&_[role=radio]>span:first-child]:items-baseline [&_[role=radio]>span:first-child]:gap-x-2 [&_[role=radio]>span:first-child]:leading-5',
					'[&_[role=radio]>span:first-child>span:first-child]:text-foreground-name',
					'[&_[role=radio]>span:first-child>span:last-child]:text-foreground-sub [&_[role=radio]>span:first-child>span:last-child]:mt-0',
				)}
			/>
			{note && (
				<DetailsLine
					data-testid={`${p}-coverage-note`}
					summary={
						reach === 'vendor-wide'
							? 'Covers every API of this vendor'
							: 'Covers every version of this API'
					}
				>
					{note}
				</DetailsLine>
			)}
			{/* `!mt-0` + inner `pt-2`: the gap grows with the height, not before it. */}
			<ExpandReveal open={preset === 'custom'} className="!mt-0">
				<div className="pt-2">
					<div
						className="bg-surface-inset space-y-3 rounded-lg p-3"
						data-testid={`${p}-custom-editor`}
						data-build={buildMode}
					>
						{(actionsPending || actionsAvailable) && (
							// The help line sits under the loading line too, so the
							// toggle replaces only that line and nothing below moves.
							<div className="space-y-1.5">
								{actionsPending ? (
									// Holds the toggle's exact row height while operations load.
									<p
										role="status"
										className="text-muted-foreground flex min-h-[30px] items-center gap-1.5 text-xs"
										data-testid={`${p}-actions-pending`}
									>
										<Loader2
											aria-hidden="true"
											className="h-3.5 w-3.5 animate-spin motion-reduce:animate-none"
										/>
										{"Reading this API's actions…"}
									</p>
								) : (
									<SegmentedToggle
										tone="inset"
										ariaLabel="Build rules"
										className="w-fit"
										value={buildMode}
										onChange={switchBuild}
										disabled={disabled}
										options={[
											{ value: 'action', label: 'By action' },
											{ value: 'path', label: 'By path' },
										]}
									/>
								)}
								<AccessRulesHelp testId={`${p}-rules-help`} />
							</div>
						)}
						{buildMode === 'path' ? (
							<div className="space-y-3" data-testid={`${p}-ordered-rules`}>
								<section aria-label="Path rules" className="space-y-1.5">
									{actionRows.length > 0 && (
										<p className="text-foreground-sub text-[11px] font-medium">
											Path rules
										</p>
									)}
									<RuleListEditor
										rules={pathRules}
										onChange={setPathRules}
										pathSuggestions={pathSuggestions}
										opTemplates={opsComplete ? pathSuggestions : undefined}
										opsLoaded={opsComplete && pathSuggestions.length > 0}
										// Open on the form only when there are no rules at all.
										openWhenEmpty={customRules.length === 0}
										addLabel="Add path rule"
										addFirst
										decided={decided}
										actionCatalog={actionCatalog}
									/>
								</section>
								{actionRows.length > 0 && (
									<section
										aria-label="Set by action"
										className="space-y-1.5"
										data-testid={`${p}-action-rules`}
									>
										<p className="text-foreground-sub text-[11px] font-medium">
											Set by action
										</p>
										{actionRows}
									</section>
								)}
							</div>
						) : (
							<>
								{hasActionRules && (
									<Disclosure
										open={listShown}
										onToggle={(): void => {
											if (listShown) closeList('before closing the list');
											else setListOpen(true);
										}}
										icon={<ListChecks className="h-3.5 w-3.5" />}
										label={
											<>
												{listShown
													? 'Choose actions'
													: 'Choose more actions'}
												<span className="text-foreground-sub font-normal">
													{' '}
													· {unsetCount.toLocaleString()}
													{opsComplete ? '' : '+'} not set
												</span>
											</>
										}
										controls={`${p}-actions-list`}
									/>
								)}
								<ExpandReveal open={listShown} id={`${p}-actions-list`}>
									<div className="space-y-2">
										{/* No line about the path rules here: what decides an
										    action left unset is in the Details under the toggle. */}
										<ActionChecklist
											ops={ops}
											complete={opsComplete}
											failed={!opsComplete && opsQuery.isError}
											value={shownGroups}
											applied={groups}
											onChange={setStaged}
											disabled={disabled}
											testId={`${p}-actions`}
											only={editingIds}
											header={editingHeader}
										/>
										<div
											className="flex flex-wrap items-center gap-2"
											data-testid={`${p}-apply-row`}
										>
											<p
												className="text-foreground-sub min-w-0 flex-1 text-xs"
												aria-live="polite"
												data-testid={`${p}-apply-status`}
											>
												{unapplied > 0 && (
													<span className="text-foreground inline-flex items-center gap-1.5 font-medium">
														<span
															aria-hidden="true"
															className="bg-caution h-1.5 w-1.5 rounded-full"
														/>
														{unapplied.toLocaleString()}{' '}
														{unapplied === 1 ? 'change' : 'changes'} not
														applied
													</span>
												)}
											</p>
											<Button
												variant="ghost"
												size="sm"
												disabled={disabled || unapplied === 0}
												onClick={discard}
											>
												Reset
											</Button>
											<Button
												variant="secondary"
												size="sm"
												disabled={disabled || unapplied === 0}
												onClick={(): void => void apply()}
												data-testid={`${p}-apply`}
											>
												Apply
											</Button>
										</div>
									</div>
								</ExpandReveal>
								{hasActionRules && (
									<section
										aria-label="Set by action"
										className="space-y-1.5"
										data-testid={`${p}-action-rules`}
									>
										<p className="text-foreground-sub text-[11px] font-medium">
											Set by action
										</p>
										{actionRows}
									</section>
								)}
								{pathRules.length > 0 && (
									<section
										aria-label="Path rules"
										className="space-y-1.5"
										data-testid={`${p}-path-rules`}
									>
										<div className="flex items-center gap-2">
											<p className="text-foreground-sub text-[11px] font-medium">
												Path rules
											</p>
											<Button
												variant="ghost"
												size="xs"
												className="text-foreground-sub ml-auto"
												disabled={disabled}
												onClick={(): void => switchBuild('path')}
											>
												Edit
											</Button>
										</div>
										<RuleListEditor
											rules={pathRules}
											onChange={setPathRules}
											opTemplates={opsComplete ? pathSuggestions : undefined}
											opsLoaded={opsComplete && pathSuggestions.length > 0}
											decided={decided}
											actionCatalog={actionCatalog}
											readOnly
										/>
									</section>
								)}
								<SiblingActionsNote
									rows={siblingRows}
									onOpen={onOpenSibling}
									testId={`${p}-sibling-actions`}
								/>
							</>
						)}
					</div>
				</div>
			</ExpandReveal>

			{(tester || (apiReference && effectiveTool) || extraTools) && (
				// Fixed order: Effective access, Try a request, extraTools.
				<ul className="-ml-2 space-y-0.5 pt-1" data-testid={`${p}-tools`}>
					{apiReference && effectiveTool && (
						<li>
							<Disclosure
								open={open === 'effective'}
								onToggle={() => toggle('effective')}
								icon={<ScanEye className="h-3.5 w-3.5" />}
								// The counts live in the preview it opens, stated once.
								label="Effective access"
								controls={`${p}-effective`}
							/>
							<ExpandReveal
								open={open === 'effective'}
								id={`${p}-effective`}
								data-testid={`${p}-effective`}
							>
								<div className="pt-1 pb-2 pl-2">
									<OperationImpactPreview
										api={apiReference}
										rules={draftRules}
										label="Allowed and denied, by path"
									/>
								</div>
							</ExpandReveal>
						</li>
					)}
					{tester && (
						<li>
							<Disclosure
								open={open === 'tester'}
								onToggle={() => toggle('tester')}
								icon={<FlaskConical className="h-3.5 w-3.5" />}
								label="Try a request"
								controls={`${p}-tester`}
							/>
							<ExpandReveal
								open={open === 'tester'}
								id={`${p}-tester`}
								data-testid={`${p}-tester`}
							>
								<div className="pt-1 pb-2 pl-2">
									<DraftRuleTester
										rules={draftRules}
										describeMatch={describeMatch}
										paths={pathSuggestions}
										disabled={disabled}
									/>
								</div>
							</ExpandReveal>
						</li>
					)}
					{extraTools && <li>{extraTools}</li>}
				</ul>
			)}
			<UnappliedChoicesDialog
				open={guard != null}
				count={unapplied}
				context={guard?.context}
				onApply={(): void => {
					const then = guard?.then;
					setGuard(null);
					apply();
					then?.();
				}}
				onDiscard={(): void => {
					const then = guard?.then;
					setGuard(null);
					discard();
					then?.();
				}}
				onClose={(): void => setGuard(null)}
			/>
		</div>
	);
}

function Disclosure({
	open,
	onToggle,
	icon,
	label,
	controls,
}: {
	open: boolean;
	onToggle: () => void;
	icon: ReactNode;
	label: ReactNode;
	controls: string;
}) {
	return (
		<Button
			variant="ghost"
			size="xs"
			aria-expanded={open}
			// Only reference the panel while it is open.
			aria-controls={open ? controls : undefined}
			onClick={onToggle}
			className={cn(
				'text-muted-foreground hover:text-foreground hover:bg-tint-2 gap-1.5 px-2 text-xs font-medium',
				open && 'text-foreground',
			)}
		>
			<ChevronRight
				aria-hidden="true"
				className={cn(
					'h-3 w-3 transition-transform duration-200 motion-reduce:transition-none',
					open && 'rotate-90',
				)}
			/>
			<span aria-hidden="true" className="contents">
				{icon}
			</span>
			{label}
		</Button>
	);
}

/** An action rule's effect by name, for its row's verbs. */
const EFFECT_RULE_NAME: Record<string, string> = {
	allow: 'Allow',
	'require-approval': 'Ask',
	deny: 'Deny',
};

/** The rule effect each By action choice saves as. */
const GROUP_EFFECT: Record<keyof ActionGroups, PermissionRule['effect']> = {
	allow: 'allow',
	ask: 'require-approval',
	deny: 'deny',
};

/**
 * The chosen actions that belong to other APIs these rules govern: one quiet,
 * read-only line per API, with a way to that API's own access panel. They are
 * kept exactly as they are on save.
 */
function SiblingActionsNote({
	rows,
	onOpen,
	testId,
}: {
	rows: readonly SiblingActions[];
	onOpen?: (key: string) => void;
	testId: string;
}) {
	if (rows.length === 0) return null;
	return (
		<ul className="space-y-1" data-testid={testId}>
			{rows.map((row) => (
				<li
					key={row.key}
					className="text-foreground-sub flex flex-wrap items-center gap-x-1.5 px-1 text-xs"
				>
					<span>
						{siblingActionsLine(row)} — edit{' '}
						{row.allow + row.ask + row.deny === 1 ? 'it' : 'them'} from {row.title}
						&apos;s access.
					</span>
					{onOpen && (
						<InlineAction
							className="text-foreground px-1.5 underline underline-offset-2"
							onClick={() => onOpen(row.key)}
						>
							Open {row.title}
						</InlineAction>
					)}
				</li>
			))}
		</ul>
	);
}

/**
 * An action rule as a read-only row, unnumbered (it is checked before every
 * path rule). "Edit" opens By action filtered to its ids.
 */
function ActionRuleRow({
	operations,
	effect,
	catalog,
	disabled,
	onEdit,
	onRemove,
	testId,
}: {
	operations: readonly string[];
	effect: PermissionRule['effect'];
	/** The API's operation list, to name each action. */
	catalog: ActionCatalog;
	disabled: boolean;
	onEdit?: () => void;
	onRemove: () => void;
	testId: string;
}) {
	const description = useMemo(() => describeActions(operations, catalog), [operations, catalog]);
	return (
		<div
			className="bg-surface-tonal flex flex-col gap-1.5 rounded-md px-2.5 py-2 text-xs"
			data-testid={testId}
		>
			<div className="flex flex-wrap items-center gap-x-2.5 gap-y-1.5">
				<EffectBadge effect={effect} className="shrink-0 rounded-[5px] font-semibold">
					{EFFECT_WORD[effect] ?? String(effect)}
				</EffectBadge>
				<span className="text-foreground inline-flex items-center gap-1.5">
					<ListChecks className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
					{operations.length} {operations.length === 1 ? 'action' : 'actions'}
				</span>
				<div className="ml-auto flex shrink-0 items-center gap-0.5">
					{onEdit && (
						<Button
							variant="ghost"
							size="xs"
							disabled={disabled}
							onClick={onEdit}
							className="text-foreground-sub"
						>
							Edit
						</Button>
					)}
					<Button
						variant="ghost"
						size="icon-xs"
						aria-label={`Remove the ${EFFECT_RULE_NAME[effect] ?? 'Allow'} actions rule`}
						disabled={disabled}
						onClick={onRemove}
						className="text-foreground-sub hover:bg-danger/10 hover:text-danger"
					>
						<Trash2 className="h-3.5 w-3.5" />
					</Button>
				</div>
			</div>
			{/* What is granted, in the By action checklist's own row: method,
			    path, summary. The ids stay reachable as each row's title. */}
			<ActionList
				description={description}
				className="-mx-1"
				data-testid={`${testId}-actions`}
			/>
		</div>
	);
}
