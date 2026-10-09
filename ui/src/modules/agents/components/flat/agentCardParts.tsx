/**
 * Small presentational parts the agent card (`AgentCard`) and the first-agent
 * flow (`firstAgentParts`) share, so the agent a first run finishes reads
 * exactly like the card it becomes in the fleet:
 *  - `AgentStatusMark` — the lifecycle glyph in its status tint and the word
 *    beside it (never a tinted pill);
 *  - `FactSep` — the facts line's quiet separator;
 *  - `StateBannerFrame` — the non-serving state's banner chrome: a tinted
 *    shell, an icon chip, a title and detail, and the actions at the end.
 */
import type { ReactNode } from 'react';
import { STATUS_ICON, STATUS_LABELS, STATUS_TINT } from '@/shared/ui';
import { cn } from '@/shared/lib/utils';
import type { ActorStatus } from '@/modules/agents/api';

/** The agent's status as the tab and the card show it: glyph, then word. */
export function AgentStatusMark({
	status,
	className,
	'data-testid': testId,
}: {
	status: ActorStatus;
	className?: string;
	'data-testid'?: string;
}) {
	const Icon = STATUS_ICON[status];
	return (
		<span
			data-testid={testId}
			data-status={status}
			className={cn(
				'text-foreground-sub inline-flex shrink-0 items-center gap-1.5 text-xs font-semibold',
				className,
			)}
		>
			<Icon aria-hidden="true" className={cn('size-3.5 shrink-0', STATUS_TINT[status])} />
			{STATUS_LABELS[status]}
		</span>
	);
}

/** The facts line's separator. */
export function FactSep() {
	return (
		<span aria-hidden="true" className="text-foreground-faint mx-[7px]">
			·
		</span>
	);
}

/** The states a banner is drawn for. */
export type BanneredStatus = Exclude<ActorStatus, 'active' | 'disabled'>;

/** Per-state banner tint — about attention, not editability. */
const BANNER_TINT: Record<BanneredStatus, { shell: string; chip: string }> = {
	pending: {
		shell: 'bg-warning/10',
		chip: 'bg-warning/15 text-warning',
	},
	rejected: {
		shell: 'bg-danger/10',
		chip: 'bg-danger/15 text-danger',
	},
	archived: {
		shell: 'bg-surface-1',
		chip: 'bg-surface-field text-muted-foreground',
	},
};

/**
 * The banner's chrome: the state's tinted shell and icon chip, a title over one
 * line of detail, and the actions at the end (wrapping under on a narrow
 * card). The content and the actions' behaviour are the caller's.
 */
export function StateBannerFrame({
	status,
	title,
	detail,
	actions,
	role,
	stackActions = false,
	className,
	'data-testid': testId,
}: {
	status: BanneredStatus;
	title: ReactNode;
	detail?: ReactNode;
	actions?: ReactNode;
	role?: 'status';
	/** Below `sm`, put the actions on their own line under the words, so a
	 * narrow card never squeezes the detail into a sliver. */
	stackActions?: boolean;
	className?: string;
	'data-testid'?: string;
}) {
	const { shell, chip } = BANNER_TINT[status];
	const Icon = STATUS_ICON[status];
	return (
		<div
			role={role}
			data-testid={testId}
			className={cn(
				'flex flex-wrap items-center gap-x-3 gap-y-3 rounded-lg p-3 sm:flex-nowrap',
				shell,
				className,
			)}
		>
			<span
				aria-hidden="true"
				className={cn('grid h-8 w-8 shrink-0 place-items-center rounded-lg', chip)}
			>
				<Icon className="h-4 w-4" />
			</span>
			<div className="min-w-0 flex-1 space-y-0.5">
				<p className="text-foreground text-sm leading-tight font-medium">{title}</p>
				{detail && <p className="text-muted-foreground text-xs leading-snug">{detail}</p>}
			</div>
			{actions && (
				<span
					className={cn(
						'flex shrink-0 items-center gap-2',
						stackActions && 'max-sm:basis-full max-sm:pl-11',
					)}
				>
					{actions}
				</span>
			)}
		</div>
	);
}
