/**
 * AlphaRail — a compact vertical A–Z index beside a long grouped list.
 *
 * Letters with entries are buttons (with a tooltip describing what's under
 * them); letters without entries are inert and dimmed. The current letter
 * (from the list's scroll position — the caller's scroll-spy) is highlighted
 * and marked `aria-current`. A `busy` letter (the list is loading toward
 * it) pulses and is marked `aria-busy`. Stays sticky beside the list.
 */
import { cn } from '@/shared/lib/utils';
import { Button } from '@/shared/ui/Button';
import { Tooltip } from '@/shared/ui/Tooltip';

export interface AlphaRailLetter {
	/** Stable key, e.g. `A` or `#`. */
	key: string;
	/** What the button shows (`#`, `A`…). */
	glyph: string;
	/** Can be jumped to. */
	enabled: boolean;
	/** Tooltip + accessible description ("A — 312 vendors · acme … azure"). */
	description: string;
}

interface AlphaRailProps {
	letters: AlphaRailLetter[];
	current: string | null;
	/** A letter the list is still loading toward (pulses, `aria-busy`). */
	busy?: string | null;
	onJump: (key: string) => void;
	/** Accessible name for the nav. Default "Jump to letter". */
	label?: string;
	className?: string;
}

export function AlphaRail({
	letters,
	current,
	busy = null,
	onJump,
	label = 'Jump to letter',
	className,
}: AlphaRailProps) {
	return (
		<nav
			aria-label={label}
			data-testid="alpha-rail"
			className={cn('flex flex-col items-center pt-1.5', className)}
		>
			{letters.map((letter) => {
				const isCurrent = letter.key === current;
				const isBusy = letter.key === busy;
				const cls = cn(
					'font-heading grid h-[18px] w-[22px] place-items-center rounded-[5px] text-[10.5px] font-semibold leading-none',
					letter.enabled
						? 'text-muted-foreground hover:text-white focus-visible:ring-ring cursor-pointer focus-visible:ring-2 focus-visible:outline-none'
						: 'text-foreground-disabled cursor-default',
					isCurrent && 'bg-surface-tonal text-white',
					isBusy && 'motion-safe:animate-pulse',
				);
				if (!letter.enabled) {
					return (
						<span
							key={letter.key}
							aria-hidden="true"
							className={cls}
							data-letter={letter.key}
						>
							{letter.glyph}
						</span>
					);
				}
				return (
					<Tooltip
						key={letter.key}
						content={letter.description}
						placement="right"
						delayMs={150}
						interactiveChild
					>
						<Button
							variant="ghost"
							size="icon-xs"
							className={cn(
								cls,
								'hover:bg-transparent focus-visible:ring-offset-0',
								isCurrent && 'hover:bg-surface-tonal',
							)}
							data-letter={letter.key}
							aria-label={letter.description}
							aria-current={isCurrent ? 'true' : undefined}
							aria-busy={isBusy || undefined}
							onClick={() => onJump(letter.key)}
						>
							{letter.glyph}
						</Button>
					</Tooltip>
				);
			})}
		</nav>
	);
}
