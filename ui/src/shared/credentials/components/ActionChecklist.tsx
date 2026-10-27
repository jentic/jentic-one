/**
 * ActionChecklist — Custom rules → By action: the API's operations,
 * filterable, each set to Allow, Ask or Deny with a compact per-row choice —
 * or left unset (the default), in no rule. Pressing the pressed choice, or
 * Delete / Backspace on the row's control, unsets it. The listed rows can be
 * set together.
 *
 * The choices here are STAGED: `value` is what is on screen, `applied` what
 * the rule list holds. A row whose choice differs from its applied one is
 * marked, and the host's Apply turns the staged choices into rules.
 */
import { useMemo, useState, type ReactNode } from 'react';
import { AlertTriangle, Filter, Loader2, ShieldCheck } from 'lucide-react';
import {
	Button,
	MethodBadge,
	SearchInput,
	SegmentedToggle,
	type SegmentedToggleOption,
} from '@/shared/ui';
import { cn } from '@/shared/lib/utils';
import type { VendorOperation } from '@/shared/credentials/api/vendors-client';
import { accessMap, type ActionGroups } from '@/shared/credentials/lib/accessPresets';
import { operationText } from '@/shared/credentials/lib/actionRules';
import { PlainText } from '@/shared/credentials/components/PlainText';

/** One action's access: one of the three rules, or `null` — unset, in no rule. */
export type ActionAccess = keyof ActionGroups;

/**
 * The per-row choice, in the By path editor's colours: Allow the success tint,
 * Ask the caution tint with its shield, Deny the danger tint. With nothing
 * pressed all three are quiet.
 */
const ACCESS_OPTIONS: SegmentedToggleOption<ActionAccess>[] = [
	{
		value: 'allow',
		label: 'Allow',
		activeTint: { pill: 'bg-success/15', text: 'text-success' },
	},
	{
		value: 'ask',
		label: 'Ask',
		icon: <ShieldCheck className="h-3 w-3" />,
		activeTint: { pill: 'bg-warning/15', text: 'text-warning' },
	},
	{
		value: 'deny',
		label: 'Deny',
		activeTint: { pill: 'bg-danger/15', text: 'text-danger' },
	},
];

/** How many ids differ between two sets of choices. */
export function changedChoices(a: ActionGroups, b: ActionGroups): number {
	const x = accessMap(a);
	const y = accessMap(b);
	let n = 0;
	for (const id of new Set([...x.keys(), ...y.keys()])) if (x.get(id) !== y.get(id)) n += 1;
	return n;
}

/**
 * "5 allow · 9 ask · 2 deny · 27 not set" over the listed actions — choices,
 * not outcomes, with unset counted so the four add up to the list. Ids the list
 * doesn't show (another API's) are left out; they are named on their own line.
 */
export function choiceCounts(
	groups: ActionGroups,
	listed: readonly string[],
): { allow: number; ask: number; deny: number; unset: number } {
	const map = accessMap(groups);
	const n = { allow: 0, ask: 0, deny: 0, unset: 0 };
	for (const id of listed) n[map.get(id) ?? 'unset'] += 1;
	return n;
}

export function choiceCountsText(groups: ActionGroups, listed: readonly string[]): string {
	const n = choiceCounts(groups, listed);
	return [
		`${n.allow.toLocaleString()} allow`,
		`${n.ask.toLocaleString()} ask`,
		`${n.deny.toLocaleString()} deny`,
		`${n.unset.toLocaleString()} not set`,
	].join(' · ');
}

export interface ActionChecklistProps {
	ops: readonly VendorOperation[];
	complete: boolean;
	/** The read stopped before the last page: `ops` is all there will be for now. */
	failed: boolean;
	/** The choices on screen (staged). */
	value: ActionGroups;
	/** The choices the rule list holds; rows that differ are marked. */
	applied: ActionGroups;
	onChange: (groups: ActionGroups) => void;
	disabled: boolean;
	testId: string;
	/** List only these ids (editing one action rule). */
	only?: ReadonlySet<string> | null;
	/** Above the filter (e.g. "Editing #2 · Ask · 9 actions"). */
	header?: ReactNode;
}

export function ActionChecklist({
	ops,
	complete,
	failed,
	value,
	applied,
	onChange,
	disabled,
	testId,
	only,
	header,
}: ActionChecklistProps) {
	const scoped = useMemo(
		() =>
			(only ? ops.filter((op) => only.has(op.operation_id)) : [...ops]).sort((a, b) =>
				a.path.localeCompare(b.path),
			),
		[ops, only],
	);
	const count = scoped.length.toLocaleString();
	const [q, setQ] = useState('');
	const access = useMemo(() => accessMap(value), [value]);
	const was = useMemo(() => accessMap(applied), [applied]);
	// Each action in plain English (the spec's words, or described from its
	// method and path) — shown on every row and searched by the filter.
	const plain = useMemo(
		() => new Map(scoped.map((op) => [op.operation_id, operationText(op)])),
		[scoped],
	);
	const shown = useMemo(() => {
		const needle = q.trim().toLowerCase();
		return needle
			? scoped.filter((op) =>
					`${plain.get(op.operation_id)?.text ?? ''} ${op.method} ${op.path} ${op.operation_id}`
						.toLowerCase()
						.includes(needle),
				)
			: scoped;
	}, [scoped, q, plain]);
	const listedIds = useMemo(() => scoped.map((op) => op.operation_id), [scoped]);
	/** Set some actions' access (`null` = unset), keeping every other action's as it is. */
	const setAccess = (ids: readonly string[], to: ActionAccess | null): void => {
		const moving = new Set(ids);
		const next: ActionGroups = {
			allow: value.allow.filter((x) => !moving.has(x)),
			ask: value.ask.filter((x) => !moving.has(x)),
			deny: value.deny.filter((x) => !moving.has(x)),
		};
		if (to) next[to].push(...ids);
		onChange(next);
	};
	const choice = (ids: readonly string[], current: ActionAccess | null, label: string) => (
		<SegmentedToggle<ActionAccess>
			as="choice"
			size="xs"
			tone="inset"
			ariaLabel={label}
			options={ACCESS_OPTIONS}
			value={current}
			onChange={(to) => setAccess(ids, to)}
			onClear={() => setAccess(ids, null)}
			disabled={disabled}
			className="shrink-0"
		/>
	);
	const filtered = q.trim().length > 0;
	const shownIds = useMemo(() => shown.map((op) => op.operation_id), [shown]);
	const bulkValue = useMemo<ActionAccess | null>(() => {
		const first = shownIds.length > 0 ? (access.get(shownIds[0]!) ?? null) : null;
		return first && shownIds.every((id) => access.get(id) === first) ? first : null;
	}, [shownIds, access]);
	return (
		<div
			className="space-y-2"
			data-testid={testId}
			data-complete={complete || undefined}
			aria-busy={!complete && !failed ? true : undefined}
		>
			{header}
			<div className="flex items-center gap-2">
				{/* The app's standard filter field (as the Library's): its own
				    fill, the shared control edge, the focus ring, a leading icon. */}
				<SearchInput
					value={q}
					onValueChange={setQ}
					size="sm"
					tone="inset"
					field
					icon={<Filter className="h-3.5 w-3.5" />}
					placeholder={
						complete
							? `Filter ${count} operations…`
							: `Filter the ${count} loaded so far…`
					}
					aria-label="Filter operations"
					className="min-w-0 flex-1"
				/>
				<span
					className="text-muted-foreground shrink-0 text-xs tabular-nums"
					data-testid={`${testId}-count`}
				>
					{choiceCountsText(value, listedIds)}
				</span>
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
				{shown.length > 0 && (
					// "Set all N" is the list's header row, on the rows' own grid:
					// the same padding and the same choice control last, so Allow ·
					// Ask · Deny sit exactly above every row's — at any width, and
					// with the scrollbar, since it scrolls (sticky) inside the list.
					// It acts on exactly the rows on screen: all, or the filter's.
					// Above the rows' choice labels (`z-10` inside each toggle), which
					// would otherwise paint over it as they scroll under.
					<li
						className="bg-surface-inset sticky top-0 z-20 flex min-w-0 items-center gap-2 rounded-md px-1.5 py-1"
						data-testid={`${testId}-bulk`}
					>
						<span aria-hidden="true" className="h-1.5 w-1.5 shrink-0" />
						<span className="text-foreground-sub min-w-0 flex-1 truncate text-xs">
							Set {filtered ? 'the' : 'all'} {shown.length.toLocaleString()}
							{filtered ? ' shown' : ''} to
						</span>
						<Button
							variant="ghost"
							size="xs"
							disabled={disabled}
							onClick={() => setAccess(shownIds, null)}
							className="text-foreground-sub hover:text-foreground h-6 shrink-0 px-1.5"
						>
							Clear
						</Button>
						{/* Pressed when every row on screen already has that choice. */}
						{choice(
							shownIds,
							bulkValue,
							`Set the ${shown.length.toLocaleString()} ${filtered ? 'filtered' : 'listed'} operations`,
						)}
					</li>
				)}
				{shown.length === 0 && (
					<li className="text-muted-foreground px-1 py-2 text-xs">
						{complete || failed
							? 'No operations match.'
							: 'None of the operations loaded so far match.'}
					</li>
				)}
				{shown.map((op) => {
					const id = op.operation_id;
					const current = access.get(id) ?? null;
					const changed = current !== (was.get(id) ?? null);
					const action = `${op.method.toUpperCase()} ${op.path}`;
					return (
						<li
							key={id}
							className={cn(
								'hover:bg-tint-2 flex min-w-0 items-center gap-2 rounded-md px-1.5 py-1',
								changed && 'bg-warning/8',
							)}
							data-access={current ?? 'unset'}
							data-testid={`${testId}-row`}
							data-changed={changed || undefined}
						>
							<span
								aria-hidden="true"
								className={cn(
									'h-1.5 w-1.5 shrink-0 rounded-full',
									changed ? 'bg-caution' : 'bg-transparent',
								)}
							/>
							<MethodBadge method={op.method} />
							<span
								className={cn(
									'min-w-0 flex-1 truncate font-mono text-[11.5px]',
									current === null ? 'text-foreground-sub' : 'text-foreground',
								)}
							>
								{op.path}
							</span>
							<PlainText value={plain.get(id)} className="max-w-[45%]" />
							{changed && <span className="sr-only">(not applied)</span>}
							{choice([id], current, `Access for ${action}`)}
						</li>
					);
				})}
			</ul>
		</div>
	);
}
