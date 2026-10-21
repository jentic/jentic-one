import { useMemo, useState } from 'react';
import { AlertTriangle, Loader2, ShieldCheck } from 'lucide-react';
import {
	Button,
	MethodBadge,
	SearchInput,
	SegmentedToggle,
	type SegmentedToggleOption,
} from '@/shared/ui';
import { cn } from '@/shared/lib/utils';
import type { VendorOperation } from '@/shared/credentials/api/vendors-client';
import type { ActionGroups } from '@/shared/credentials/lib/accessPresets';

/** One action's access in the list: one of the three rules, or `null` — unset, in no rule. */
type ActionAccess = keyof ActionGroups;

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

const BULK_LABEL: Record<ActionAccess, string> = { allow: 'Allow', ask: 'Ask', deny: 'Deny' };

/** "2 allowed · 1 ask · 1 denied", only the parts that aren't zero; "None set" otherwise. */
function accessSummary(groups: ActionGroups): string {
	const parts = [
		[groups.allow.length, 'allowed'],
		[groups.ask.length, 'ask'],
		[groups.deny.length, 'denied'],
	]
		.filter(([n]) => (n as number) > 0)
		.map(([n, word]) => `${(n as number).toLocaleString()} ${word}`);
	return parts.length > 0 ? parts.join(' · ') : 'None set';
}

/**
 * Custom rules → By action: the API's operations, filterable, each set to
 * Allow, Ask or Deny with a compact per-row choice — or left unset (the
 * default), in no rule. Pressing the pressed choice, or Delete / Backspace on
 * the row's control, unsets it. The filtered rows can be set together.
 */
export function ActionChecklist({
	ops,
	complete,
	failed,
	groups,
	onChange,
	disabled,
	testId,
}: {
	ops: readonly VendorOperation[];
	complete: boolean;
	/** The read stopped before the last page: `ops` is all there will be for now. */
	failed: boolean;
	groups: ActionGroups;
	onChange: (groups: ActionGroups) => void;
	disabled: boolean;
	testId: string;
}) {
	const count = ops.length.toLocaleString();
	const [q, setQ] = useState('');
	const access = useMemo(() => {
		const map = new Map<string, ActionAccess>();
		for (const id of groups.allow) map.set(id, 'allow');
		for (const id of groups.ask) map.set(id, 'ask');
		for (const id of groups.deny) map.set(id, 'deny');
		return map;
	}, [groups]);
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
	/** Set some actions' access (`null` = unset), keeping every other action's as it is. */
	const setAccess = (ids: readonly string[], to: ActionAccess | null): void => {
		const moving = new Set(ids);
		const next: ActionGroups = {
			allow: groups.allow.filter((x) => !moving.has(x)),
			ask: groups.ask.filter((x) => !moving.has(x)),
			deny: groups.deny.filter((x) => !moving.has(x)),
		};
		if (to) next[to].push(...ids);
		onChange(next);
	};
	const filtered = q.trim().length > 0;
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
				<span
					className="text-muted-foreground shrink-0 text-xs tabular-nums"
					data-testid={`${testId}-count`}
				>
					{accessSummary(groups)}
				</span>
			</div>
			{shown.length > 0 && (
				// The bulk verb acts on exactly the rows on screen — all of them, or
				// the filter's matches.
				<div
					role="group"
					aria-label={`Set the ${shown.length.toLocaleString()} ${filtered ? 'filtered' : 'listed'} operations`}
					className="text-muted-foreground flex flex-wrap items-center gap-x-1 px-1.5 text-xs"
					data-testid={`${testId}-bulk`}
				>
					<span>
						Set {filtered ? 'the' : 'all'} {shown.length.toLocaleString()}
						{filtered ? ' shown' : ''} to
					</span>
					{(['allow', 'ask', 'deny', null] as const).map((to) => (
						<Button
							key={to ?? 'clear'}
							variant="ghost"
							size="xs"
							disabled={disabled}
							onClick={() =>
								setAccess(
									shown.map((op) => op.operation_id),
									to,
								)
							}
							className="text-foreground-sub hover:text-foreground h-6 px-1.5 underline-offset-2 hover:underline"
						>
							{to ? BULK_LABEL[to] : 'Clear'}
						</Button>
					))}
				</div>
			)}
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
				{shown.map((op) => {
					const id = op.operation_id;
					const value = access.get(id) ?? null;
					const action = `${op.method.toUpperCase()} ${op.path}`;
					return (
						<li
							key={id}
							className="hover:bg-tint-2 flex min-w-0 items-center gap-2 rounded-md px-1.5 py-1"
							data-access={value ?? 'unset'}
						>
							<MethodBadge method={op.method} />
							<span
								className={cn(
									'min-w-0 flex-1 truncate font-mono text-[11.5px]',
									value === null ? 'text-foreground-sub' : 'text-foreground',
								)}
							>
								{op.path}
							</span>
							{op.name && (
								<span className="text-muted-foreground hidden max-w-[45%] min-w-0 truncate text-xs sm:inline">
									{op.name}
								</span>
							)}
							<SegmentedToggle<ActionAccess>
								as="choice"
								size="xs"
								tone="inset"
								ariaLabel={`Access for ${action}`}
								options={ACCESS_OPTIONS}
								value={value}
								onChange={(to) => setAccess([id], to)}
								onClear={() => setAccess([id], null)}
								disabled={disabled}
								className="shrink-0"
							/>
						</li>
					);
				})}
			</ul>
		</div>
	);
}
