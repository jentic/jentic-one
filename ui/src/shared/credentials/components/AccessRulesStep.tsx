/**
 * AccessRulesStep — "What can this agent call?": the access presets (Read only ·
 * Full access · Custom rules), the coverage caveat for a credential that reaches
 * beyond one pinned API, and under Custom rules a "By action" checklist (saved as
 * one operation-id allow rule) and a "By path" rule editor. Every rule that will
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
import {
	Badge,
	Button,
	Checkbox,
	DetailsLine,
	ExpandReveal,
	MethodBadge,
	RadioCardGroup,
	SearchInput,
	SegmentedToggle,
} from '@/shared/ui';
import { cn } from '@/shared/lib/utils';
import { useVendorOperationsProgress } from '@/shared/credentials/api/vendors-hooks';
import type { VendorOperation } from '@/shared/credentials/api/vendors-client';
import type { PermissionRule } from '@/shared/credentials/api/vendors-types';
import { RuleListEditor } from '@/shared/credentials/components/RuleListEditor';
import { DraftRuleTester } from '@/shared/credentials/components/RuleRequestTester';
import {
	OperationImpactPreview,
	useAllowedOperationCount,
} from '@/shared/credentials/components/OperationImpactPreview';
import {
	actionRule,
	coverageNote,
	draftRulesForPreset,
	joinCustomRules,
	presetOptions,
	splitCustomRules,
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
const PRESET_MATCH: Record<Exclude<RulesPreset, 'custom'>, () => string> = {
	read: () => '— allowed by “Read only”',
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
	const { pathRules, actionRules } = useMemo(() => splitCustomRules(customRules), [customRules]);
	const chosenActions = useMemo(
		() => actionRules.flatMap((r) => r.operations ?? []),
		[actionRules],
	);
	const setActions = (next: string[]): void =>
		onCustomRulesChange(
			joinCustomRules(pathRules, next.length === 0 ? [] : [actionRule(next)]),
		);
	const setPathRules = (next: PermissionRule[]): void =>
		onCustomRulesChange(joinCustomRules(next, actionRules));

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
						{/* Path rules first, in broker evaluation order (the action rule
						    is saved last). Kept at the same tree position so a half-typed
						    rule survives the loading → toggle swap. */}
						{(pathEditorOpen || pathRules.length > 0) && (
							<RuleListEditor
								rules={pathRules}
								onChange={setPathRules}
								pathSuggestions={pathSuggestions}
								opTemplates={opsComplete ? pathSuggestions : undefined}
								opsLoaded={opsComplete && pathSuggestions.length > 0}
								openWhenEmpty={pathEditorOpen}
								hideAdd={!pathEditorOpen}
							/>
						)}
						{buildMode === 'action' ? (
							<ActionChecklist
								ops={ops}
								complete={opsComplete}
								failed={!opsComplete && opsQuery.isError}
								selected={chosenActions}
								onChange={setActions}
								disabled={disabled}
								testId={`${p}-actions`}
							/>
						) : (
							chosenActions.length > 0 && (
								<ActionRuleRow
									operations={chosenActions}
									index={pathRules.length}
									disabled={disabled}
									onEdit={
										actionsAvailable
											? (): void => setBuild('action')
											: undefined
									}
									onRemove={(): void => setActions([])}
									testId={`${p}-action-rule`}
								/>
							)
						)}
						{chosenActions.length > 0 && pathRules.some((r) => r.effect === 'deny') && (
							<p
								role="status"
								className="text-foreground-sub flex items-center gap-1.5 text-xs"
								data-testid={`${p}-actions-shadowed`}
							>
								<AlertTriangle className="text-caution h-3.5 w-3.5 shrink-0" />
								Path rules are evaluated first: a deny there can block these
								actions.
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
												· {counted.allowed.toLocaleString()} of{' '}
												{counted.total.toLocaleString()} allowed
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

/** Custom rules → By action: the API's operations, filterable, each ticked to allow it. */
function ActionChecklist({
	ops,
	complete,
	failed,
	selected,
	onChange,
	disabled,
	testId,
}: {
	ops: readonly VendorOperation[];
	complete: boolean;
	/** The read stopped before the last page: `ops` is all there will be for now. */
	failed: boolean;
	selected: readonly string[];
	onChange: (operations: string[]) => void;
	disabled: boolean;
	testId: string;
}) {
	const count = ops.length.toLocaleString();
	const [q, setQ] = useState('');
	const chosen = useMemo(() => new Set(selected), [selected]);
	const shown = useMemo(() => {
		const needle = q.trim().toLowerCase();
		const list = needle
			? ops.filter((op) =>
					`${op.name ?? ''} ${op.method} ${op.path} ${op.operation_id}`
						.toLowerCase()
						.includes(needle),
				)
			: ops;
		return [...list].sort((a, b) => a.path.localeCompare(b.path));
	}, [ops, q]);
	const set = (id: string, on: boolean) => {
		const next = new Set(chosen);
		if (on) next.add(id);
		else next.delete(id);
		onChange([...next]);
	};
	return (
		<div
			className="space-y-2"
			data-testid={testId}
			data-complete={complete || undefined}
			aria-busy={!complete && !failed ? true : undefined}
		>
			<div className="flex items-center gap-2">
				<SearchInput
					value={q}
					onValueChange={setQ}
					size="sm"
					placeholder={
						complete
							? `Filter ${count} operations…`
							: `Filter the ${count} loaded so far…`
					}
					aria-label="Filter operations"
					className="min-w-0 flex-1"
				/>
				<span className="text-muted-foreground shrink-0 text-xs tabular-nums">
					{selected.length} chosen
				</span>
				{selected.length > 0 && (
					<Button
						variant="ghost"
						size="xs"
						disabled={disabled}
						onClick={() => onChange([])}
					>
						Clear
					</Button>
				)}
			</div>
			{!complete && (
				<p
					role="status"
					className="text-muted-foreground flex items-center gap-1.5 px-1 text-xs"
					data-testid={`${testId}-loading`}
				>
					{failed ? (
						<AlertTriangle
							aria-hidden="true"
							className="text-caution h-3.5 w-3.5 shrink-0"
						/>
					) : (
						<Loader2
							aria-hidden="true"
							className="h-3.5 w-3.5 shrink-0 animate-spin motion-reduce:animate-none"
						/>
					)}
					{failed
						? `Couldn't read every operation — showing the ${count} that loaded.`
						: `Loading more operations — ${count} so far. The filter searches only these.`}
				</p>
			)}
			<ul aria-label="Operations" className="max-h-64 overflow-y-auto">
				{shown.length === 0 && (
					<li className="text-muted-foreground px-1 py-2 text-xs">
						{complete || failed
							? 'No operations match.'
							: 'None of the operations loaded so far match.'}
					</li>
				)}
				{shown.map((op) => (
					<li key={op.operation_id}>
						<Checkbox
							size="sm"
							checked={chosen.has(op.operation_id)}
							onChange={(on) => set(op.operation_id, on)}
							disabled={disabled}
							className="hover:bg-tint-2 w-full rounded-md px-1.5 py-1 [&>span:last-child]:min-w-0 [&>span:last-child]:flex-1"
						>
							<span className="flex min-w-0 flex-1 items-center gap-2 text-left">
								<MethodBadge method={op.method} />
								<span className="text-foreground min-w-0 truncate font-mono text-[11.5px]">
									{op.path}
								</span>
								{op.name && (
									<span className="text-muted-foreground ml-auto hidden min-w-0 truncate text-xs sm:inline">
										{op.name}
									</span>
								)}
							</span>
						</Checkbox>
					</li>
				))}
			</ul>
		</div>
	);
}

/** The action rule as a read-only row; "Edit" returns to the checklist that owns its ids. */
function ActionRuleRow({
	operations,
	index,
	disabled,
	onEdit,
	onRemove,
	testId,
}: {
	operations: readonly string[];
	/** Its position in the saved list, so the row's number matches the dry run's. */
	index: number;
	disabled: boolean;
	onEdit?: () => void;
	onRemove: () => void;
	testId: string;
}) {
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
				<Badge variant="success" className="shrink-0 rounded-[5px] font-semibold">
					allow
				</Badge>
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
						aria-label="Remove the actions rule"
						disabled={disabled}
						onClick={onRemove}
						className="text-foreground-sub hover:bg-danger/10 hover:text-danger"
					>
						<Trash2 className="h-3.5 w-3.5" />
					</Button>
				</div>
			</div>
			{/* The ids themselves: an approver has to see exactly what is granted. */}
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
		</div>
	);
}
