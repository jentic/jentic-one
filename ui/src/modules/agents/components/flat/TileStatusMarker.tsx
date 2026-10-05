/**
 * TileStatusMarker — draws a `TileStatus` (see `lib/tileStatus`) the same way
 * everywhere it appears: as the tile's one status line (`text`) or as the chip
 * beside the access sheet's title (`chip`). Blocked is actionable — on a tile
 * it is a small button that opens the rules editor — and so is an unreadable
 * status, which offers a Retry beside its word.
 */
import {
	CheckCircle2,
	CircleHelp,
	Loader2,
	LogIn,
	MinusCircle,
	PauseCircle,
	ShieldOff,
} from 'lucide-react';
import { Button, StatusChip, StatusText, Tooltip } from '@/shared/ui';
import { cn } from '@/shared/lib/utils';
import {
	BLOCKED_HINT,
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

// Above the tile's stretched overlay; a quiet hit area that only lights on
// hover/focus, so the control reads as a status first.
const INLINE_ACTION = 'relative z-10 h-auto -my-1 px-1.5 py-1 font-normal hover:bg-surface-tonal';

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
	const marker = (
		<StatusText
			tone={tone}
			size="xs"
			icon={icon}
			data-testid="tile-status-chip"
			data-status={status}
			role={status === 'checking' ? 'status' : undefined}
		>
			{TILE_STATUS_LABEL[status]}
		</StatusText>
	);
	if (status === 'unavailable' && onRetry) {
		return (
			<span className="inline-flex max-w-full items-center gap-1">
				{marker}
				<span aria-hidden="true" className="text-muted-foreground text-[11.5px]">
					·
				</span>
				<Button
					variant="ghost"
					size="xs"
					onClick={onRetry}
					aria-label={`Retry reading the access rules for ${apiTitle}`}
					data-testid="tile-status-retry"
					className={cn(INLINE_ACTION, 'text-foreground-sub text-[11.5px] font-bold')}
				>
					Retry
				</Button>
			</span>
		);
	}
	if (!isBlockedStatus(status) || !onOpenRules) return marker;
	return (
		<Tooltip content={BLOCKED_HINT[status]} interactiveChild>
			<Button
				variant="ghost"
				size="xs"
				onClick={onOpenRules}
				aria-label={`${TILE_STATUS_LABEL[status]} — add access rules for ${apiTitle}`}
				data-testid="tile-status-blocked"
				className={cn(INLINE_ACTION, '-mx-1.5 max-w-full')}
			>
				{marker}
			</Button>
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
