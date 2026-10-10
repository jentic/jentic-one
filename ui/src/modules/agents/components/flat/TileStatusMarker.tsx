/**
 * TileStatusMarker — draws a `TileStatus` (see `lib/tileStatus`) the same way
 * everywhere it appears: as the tile's one status line (`text`) or as the chip
 * beside the access sheet's title (`chip`). Blocked is actionable — on a tile
 * it is a small button that opens the rules editor — and so is an unreadable
 * status, which offers a Retry beside its word.
 */
import {
	CheckCircle2,
	ChevronRight,
	CircleHelp,
	Loader2,
	LogIn,
	MinusCircle,
	PauseCircle,
	ShieldOff,
} from 'lucide-react';
import { InlineAction, StatusChip, StatusText, Tooltip, type InlineActionProps } from '@/shared/ui';
import { HOVER_INTENT_IGNORE } from '@/shared/hooks';
import { cn } from '@/shared/lib/utils';
import {
	BLOCKED_HINT,
	TILE_STATUS_CARD_LABEL,
	TILE_STATUS_CHIP_LABEL,
	TILE_STATUS_LABEL,
	isBlockedStatus,
	type TileStatus,
} from '@/modules/agents/lib/tileStatus';

const TEXT_TONE = {
	suspended: { tone: 'muted', icon: PauseCircle },
	'not-serving': { tone: 'muted', icon: undefined },
	'sign-in-needed': { tone: 'warning', icon: undefined },
	'blocked-no-rules': { tone: 'caution', icon: ShieldOff },
	'blocked-all-denied': { tone: 'caution', icon: ShieldOff },
	checking: { tone: 'muted', icon: undefined },
	unavailable: { tone: 'muted', icon: CircleHelp },
	ready: { tone: 'success', icon: undefined },
} as const;

const CHIP = {
	suspended: { tone: 'caution', icon: PauseCircle },
	'not-serving': { tone: 'neutral', icon: MinusCircle },
	'sign-in-needed': { tone: 'warning', icon: LogIn },
	'blocked-no-rules': { tone: 'caution', icon: ShieldOff },
	'blocked-all-denied': { tone: 'caution', icon: ShieldOff },
	checking: { tone: 'neutral', icon: Loader2 },
	unavailable: { tone: 'neutral', icon: CircleHelp },
	ready: { tone: 'success', icon: CheckCircle2 },
} as const;

/**
 * A status that is also an action (Blocked → the rules, Retry): it reads as
 * the status first. An `InlineAction` above the tile's stretched overlay —
 * its 8px × 3px padding always there and cancelled by an equal negative
 * margin, so nothing moves between rest and hover. On hover or focus it takes
 * the ghost fill, the word brightens a step, and a small trailing arrow
 * slides in (fades only, under reduced motion) to say it goes somewhere. The
 * status keeps its own tone (the action sets no colour of its own). A
 * hover-intent dead zone: reaching for it never previews the row.
 */
function StatusAction({ className, children, ...props }: InlineActionProps) {
	return (
		<InlineAction
			{...HOVER_INTENT_IGNORE}
			{...props}
			className={cn(
				'group/status -mx-2 max-w-[calc(100%+1rem)] px-2 text-inherit hover:text-inherit',
				className,
			)}
		>
			{children}
			<ChevronRight
				aria-hidden="true"
				className={cn(
					'text-foreground-sub h-3 w-3 shrink-0 -translate-x-0.5 opacity-0',
					'transition-[opacity,transform] duration-150 ease-out',
					'group-hover/status:translate-x-0 group-hover/status:opacity-100',
					'group-focus-visible/status:translate-x-0 group-focus-visible/status:opacity-100',
					'motion-reduce:translate-x-0 motion-reduce:transition-opacity',
				)}
			/>
		</InlineAction>
	);
}

/** The marker on a list row. Its actions (Blocked → rules, Retry) are
 * `StatusAction`s — hover-intent dead zones that never preview the row. */
export function TileStatusText({
	status,
	onOpenRules,
	onRetry,
	apiTitle,
}: {
	status: TileStatus;
	/** Blocked only: open the access sheet on its rules editor. */
	onOpenRules?: () => void;
	/** Unavailable only: re-read the binding's rules. */
	onRetry?: () => void;
	apiTitle: string;
}) {
	const { tone, icon } = TEXT_TONE[status];
	const marker = (brighten: boolean) => (
		<StatusText
			tone={tone}
			size="xs"
			icon={icon}
			data-testid="tile-status-chip"
			data-status={status}
			role={status === 'checking' ? 'status' : undefined}
			className={cn(
				'min-w-0',
				brighten &&
					'group-hover/status:text-foreground group-focus-visible/status:text-foreground transition-colors duration-150 ease-out',
			)}
		>
			<span className="truncate">{TILE_STATUS_LABEL[status]}</span>
		</StatusText>
	);
	if (status === 'unavailable' && onRetry) {
		return (
			<span className="inline-flex max-w-full items-center gap-1">
				{marker(false)}
				<span aria-hidden="true" className="text-muted-foreground text-[11.5px]">
					·
				</span>
				<StatusAction
					onClick={onRetry}
					aria-label={`Retry reading the access rules for ${apiTitle}`}
					data-testid="tile-status-retry"
					className="text-foreground-sub hover:text-foreground mx-0 text-[11.5px] font-bold"
				>
					Retry
				</StatusAction>
			</span>
		);
	}
	if (!isBlockedStatus(status) || !onOpenRules) return marker(false);
	return (
		<Tooltip content={BLOCKED_HINT[status]} interactiveChild>
			<StatusAction
				onClick={onOpenRules}
				aria-label={`${TILE_STATUS_LABEL[status]} — add access rules for ${apiTitle}`}
				data-testid="tile-status-blocked"
			>
				{marker(true)}
			</StatusAction>
		</Tooltip>
	);
}

export function TileStatusChip({ status, className }: { status: TileStatus; className?: string }) {
	const { tone, icon } = CHIP[status];
	return (
		<StatusChip
			tone={tone}
			icon={icon}
			data-testid="sidebar-status-chip"
			data-status={status}
			className={className}
		>
			{TILE_STATUS_CHIP_LABEL[status]}
		</StatusChip>
	);
}

/**
 * TileCardStatus — the one-word status line on a dense "Can call" card. Uses the
 * SAME `TEXT_TONE` colours as the row's status marker (so a card and its row
 * read the same), with the one-word {@link TILE_STATUS_CARD_LABEL}; Blocked
 * keeps the ShieldOff caution glyph. Not actionable here — the whole card opens
 * the sheet — so it is a plain, non-interactive marker, unlike the row's
 * clickable blocked/retry affordances.
 */
export function TileCardStatus({ status }: { status: TileStatus }) {
	const { tone, icon } = TEXT_TONE[status];
	return (
		<StatusText
			tone={tone}
			size="xs"
			icon={icon}
			data-testid="card-status"
			data-status={status}
		>
			{TILE_STATUS_CARD_LABEL[status]}
		</StatusText>
	);
}
