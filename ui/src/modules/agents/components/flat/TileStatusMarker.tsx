/**
 * TileStatusMarker — draws a `TileStatus` (see `lib/tileStatus`) the same way
 * everywhere it appears: as the tile's one status line (`text`) or as the chip
 * beside the access sheet's title (`chip`). Blocked is the one actionable
 * status — on a tile it is a small button that opens the rules editor.
 */
import { CheckCircle2, LogIn, MinusCircle, PauseCircle, ShieldOff } from 'lucide-react';
import { StatusChip, StatusText, Tooltip } from '@/shared/ui';
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
	ready: { tone: 'success', icon: undefined },
} as const;

const CHIP = {
	suspended: { tone: 'caution', icon: PauseCircle },
	'not-serving': { tone: 'neutral', icon: MinusCircle },
	'sign-in-needed': { tone: 'warning', icon: LogIn },
	'blocked-no-rules': { tone: 'caution', icon: ShieldOff },
	'blocked-all-denied': { tone: 'caution', icon: ShieldOff },
	ready: { tone: 'success', icon: CheckCircle2 },
} as const;

export function TileStatusText({
	status,
	onOpenRules,
	apiTitle,
}: {
	status: TileStatus;
	/** Blocked only: open the access sheet on its rules editor. */
	onOpenRules?: () => void;
	apiTitle: string;
}) {
	const { tone, icon } = TEXT_TONE[status];
	const marker = (
		<StatusText tone={tone} size="xs" icon={icon} data-testid="tile-status-chip">
			{TILE_STATUS_LABEL[status]}
		</StatusText>
	);
	if (!isBlockedStatus(status) || !onOpenRules) return marker;
	return (
		<Tooltip content={BLOCKED_HINT[status]} interactiveChild>
			<button
				type="button"
				onClick={onOpenRules}
				aria-label={`${TILE_STATUS_LABEL[status]} — add access rules for ${apiTitle}`}
				data-testid="tile-status-blocked"
				className={cn(
					// Above the tile's stretched overlay; a quiet hit area that only
					// lights on hover/focus, so it reads as a status first.
					'relative z-10 -mx-1.5 -my-1 inline-flex max-w-full rounded-md px-1.5 py-1',
					'hover:bg-surface-tonal focus-visible:ring-ring transition-colors focus-visible:ring-2 focus-visible:outline-none',
				)}
			>
				{marker}
			</button>
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
