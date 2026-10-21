/**
 * AccessRulesStep — "What can this agent call?": the access presets (Read only ·
 * Read only, ask before anything else · Full access · Custom rules), the coverage
 * caveat for a credential that reaches beyond one pinned API, and under Custom
 * rules a "By action" list (each action Allow, Ask or Off; saved as at most one
 * operation-id Ask rule and one Allow rule) and a "By path" rule editor. Every rule that will
 * be saved stays on screen. Below sit "Effective access" and an inline "Try a
 * request" dry run with the broker's semantics.
 *
 * Controlled: the host owns the preset and the custom rules, and turns them
 * into the rules to save with `rulesForPreset`. The workspace bind dialog and
 * the Agents tab's Add-APIs queue both render it, so the choice reads the same
 * wherever a binding is made.
 */
import { useId, useMemo, useState, type ReactNode } from 'react';
import {
	AlertTriangle,
	ChevronRight,
	FlaskConical,
	ListChecks,
	Loader2,
	ScanEye,
	Trash2,
} from 'lucide-react';
import { Button, DetailsLine, ExpandReveal, RadioCardGroup, SegmentedToggle } from '@/shared/ui';
import { cn } from '@/shared/lib/utils';
import { useVendorOperationsProgress } from '@/shared/credentials/api/vendors-hooks';
import type { PermissionRule } from '@/shared/credentials/api/vendors-types';
import { RuleListEditor } from '@/shared/credentials/components/RuleListEditor';
import { ActionList } from '@/shared/credentials/components/ActionList';
import { ActionChecklist } from '@/shared/credentials/components/ActionChecklist';
import { EFFECT_WORD, EffectBadge } from '@/shared/credentials/components/EffectBadge';
import { useActionCatalog } from '@/shared/credentials/components/useActionCatalog';
import { DraftRuleTester } from '@/shared/credentials/components/RuleRequestTester';
import {
	OperationImpactPreview,
	operationCountsText,
	useAllowedOperationCount,
} from '@/shared/credentials/components/OperationImpactPreview';
import {
	actionsDecidedByPathRules,
	describeActions,
	type ActionCatalog,
} from '@/shared/credentials/lib/actionRules';
import {
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
	/** Prefix for the step's `data-testid`s (`<prefix>-rules`, `<prefix>-rules-preset`,
	 * `<prefix>-coverage-note`, `<prefix>-tester`, `<prefix>-advanced`,
	 * `<prefix>-effective`, `<prefix>-actions`). */
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
	});
	const pathSuggestions = useMemo<readonly string[]>(
		() => Array.from(new Set(ops.map((op) => op.path))).sort(),
		[ops],
	);
	const options = useMemo(() => presetOptions(reach), [reach]);
	const note = coverageNote(reach);

	const draftRules = useMemo(
		() => draftRulesForPreset(preset, customRules),
		[preset, customRules],
	);
	const describeMatch = preset && preset !== 'custom' ? PRESET_MATCH[preset] : undefined;
	const counted = useAllowedOperationCount(apiReference ?? null, draftRules, {
		enabled: wantsOps,
	});

	// The toggle picks the editor, not a filter: both halves of the one saved list
	// stay on screen. It appears once the first page of operations lands.
	const actionsAvailable = ops.length > 0;
	const opsLoading = opsQuery.isLoading || operationsPending;
	const actionsPending = !actionsAvailable && opsLoading;
	const [build, setBuild] = useState<'action' | 'path'>('path');
	const buildMode = actionsAvailable ? build : 'path';
	const pathEditorOpen = buildMode === 'path';
	const { pathRules, groups, denyMovesAhead } = useMemo(
		() => splitCustomRules(customRules),
		[customRules],
	);
	const setGroups = (next: ActionGroups): void =>
		onCustomRulesChange(joinCustomRules(pathRules, next));
	const setPathRules = (next: PermissionRule[]): void =>
		onCustomRulesChange(joinCustomRules(next, groups));
	// The saved positions of the action rules: Deny ahead of the path rules,
	// Ask and Allow after them (see `joinCustomRules`).
	const hasDeny = groups.deny.length > 0;
	const pathOffset = hasDeny ? 1 : 0;
	// An Ask action an earlier path rule already allows runs without asking.
	const askNeverFires = useMemo(
		() => actionsDecidedByPathRules(groups.ask, pathRules, actionCatalog, 'allow'),
		[groups.ask, pathRules, actionCatalog],
	);
	// An Allow or Ask action an earlier path deny blocks never runs.
	const blockedByPathDeny = useMemo(
		() =>
			actionsDecidedByPathRules(
				[...groups.ask, ...groups.allow],
				pathRules,
				actionCatalog,
				'deny',
			),
		[groups.ask, groups.allow, pathRules, actionCatalog],
	);
	const actionRow = (g: keyof ActionGroups, index: number) => (
		<ActionRuleRow
			key={g}
			operations={groups[g]}
			effect={GROUP_EFFECT[g]}
			catalog={actionCatalog}
			index={index}
			disabled={disabled}
			onEdit={actionsAvailable ? (): void => setBuild('action') : undefined}
			onRemove={(): void => setGroups({ ...groups, [g]: [] })}
			testId={`${p}-action-rule${g === 'allow' ? '' : `-${g}`}`}
		/>
	);

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
							actionsAvailable && (
								<SegmentedToggle
									tone="inset"
									ariaLabel="Build rules"
									className="w-fit"
									value={buildMode}
									onChange={setBuild}
									disabled={disabled}
									options={[
										{ value: 'action', label: 'By action' },
										{ value: 'path', label: 'By path' },
									]}
								/>
							)
						)}
						{denyMovesAhead && (
							<p
								role="status"
								className="text-foreground-sub flex items-start gap-1.5 text-xs"
								data-testid={`${p}-deny-moves-ahead`}
							>
								<AlertTriangle className="text-caution mt-0.5 h-3.5 w-3.5 shrink-0" />
								A Deny actions rule sits after the path rules. Saving moves it ahead
								of them, so those actions are blocked even where a path rule allows
								them.
							</p>
						)}
						{/* In broker evaluation order: the Deny action rule, the path rules,
						    then the Ask and Allow action rules. */}
						{buildMode === 'path' && hasDeny && actionRow('deny', 0)}
						{/* Kept at the same tree position so a half-typed rule survives the
						    loading → toggle swap. */}
						{(pathEditorOpen || pathRules.length > 0) && (
							<RuleListEditor
								indexOffset={pathOffset}
								rules={pathRules}
								onChange={setPathRules}
								pathSuggestions={pathSuggestions}
								opTemplates={opsComplete ? pathSuggestions : undefined}
								opsLoaded={opsComplete && pathSuggestions.length > 0}
								openWhenEmpty={pathEditorOpen}
								hideAdd={!pathEditorOpen}
								actionCatalog={actionCatalog}
							/>
						)}
						{buildMode === 'action' ? (
							<ActionChecklist
								ops={ops}
								complete={opsComplete}
								failed={!opsComplete && opsQuery.isError}
								groups={groups}
								onChange={setGroups}
								disabled={disabled}
								testId={`${p}-actions`}
							/>
						) : (
							// After the path rules, in saved order: Ask, then Allow.
							(['ask', 'allow'] as const)
								.filter((g) => groups[g].length > 0)
								.map((g, i) => actionRow(g, pathOffset + pathRules.length + i))
						)}
						{blockedByPathDeny > 0 && (
							<p
								role="status"
								className="text-foreground-sub flex items-center gap-1.5 text-xs"
								data-testid={`${p}-actions-shadowed`}
							>
								<AlertTriangle className="text-caution h-3.5 w-3.5 shrink-0" />
								{blockedByPathDeny === 1
									? 'Path rules are evaluated first: a deny there blocks one Allow or Ask action.'
									: `Path rules are evaluated first: a deny there blocks ${blockedByPathDeny.toLocaleString()} Allow or Ask actions.`}
							</p>
						)}
						{askNeverFires > 0 && (
							<p
								role="status"
								className="text-foreground-sub flex items-center gap-1.5 text-xs"
								data-testid={`${p}-ask-never-fires`}
							>
								<AlertTriangle className="text-caution h-3.5 w-3.5 shrink-0" />
								{askNeverFires === 1
									? 'Path rules are evaluated first: one Ask action is already allowed there, so it will run without asking.'
									: `Path rules are evaluated first: ${askNeverFires.toLocaleString()} Ask actions are already allowed there, so they will run without asking.`}
							</p>
						)}
					</div>
				</div>
			</ExpandReveal>

			{(tester || apiReference || extraTools) && (
				// Fixed order: Effective access, Try a request, extraTools.
				<ul className="-ml-2 space-y-0.5 pt-1" data-testid={`${p}-tools`}>
					{apiReference && (
						<li>
							<Disclosure
								open={open === 'effective'}
								onToggle={() => toggle('effective')}
								icon={<ScanEye className="h-3.5 w-3.5" />}
								label={
									<>
										Effective access
										{counted && (
											<span className="text-foreground-faint font-normal tabular-nums">
												{' '}
												· {operationCountsText(counted)}
											</span>
										)}
									</>
								}
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

/** The action rule as a read-only row; "Edit" returns to the checklist that owns its ids. */
function ActionRuleRow({
	operations,
	effect,
	catalog,
	index,
	disabled,
	onEdit,
	onRemove,
	testId,
}: {
	operations: readonly string[];
	effect: PermissionRule['effect'];
	/** The API's operation list, to name each action. */
	catalog: ActionCatalog;
	/** Its position in the saved list, so the row's number matches the dry run's. */
	index: number;
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
				<span
					className="bg-surface-sheet text-foreground-sub inline-flex h-5 min-w-6 shrink-0 items-center justify-center rounded-[5px] px-1 font-mono text-[11px] font-semibold"
					title={`Evaluated #${index + 1}`}
				>
					#{index + 1}
				</span>
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
