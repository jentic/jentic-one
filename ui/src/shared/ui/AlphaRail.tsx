/**
 * AlphaRail — a compact vertical A–Z index beside a long grouped list.
 *
 * Letters with entries are buttons (with a tooltip describing what's under
 * them); letters without entries are inert and dimmed. The current letter
 * (from the list's scroll position — the caller's scroll-spy) is highlighted
 * and marked `aria-current`. A `busy` letter (the list is loading toward
 * it) pulses and is marked `aria-busy`. Stays sticky beside the list.
 *
 * Keyboard: the rail is ONE tab stop (roving tabindex — the last-focused
 * letter, else the current one, else the first enabled); Arrow Up/Down move
 * between enabled letters, Home/End jump to the ends, Enter/Space jump the
 * list. Each letter is a 24×24 target (WCAG 2.5.8).
 */
import { useState, type KeyboardEvent } from 'react';
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
	const [focusKey, setFocusKey] = useState<string | null>(null);
	const enabled = letters.filter((l) => l.enabled);
	const isEnabledKey = (key: string | null): key is string =>
		key != null && enabled.some((l) => l.key === key);
	const tabStop = isEnabledKey(focusKey)
		? focusKey
		: isEnabledKey(current)
			? current
			: (enabled[0]?.key ?? null);

	const onKeyDown = (e: KeyboardEvent<HTMLElement>): void => {
		if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(e.key) || enabled.length === 0)
			return;
		const at = enabled.findIndex((l) => l.key === tabStop);
		const next =
			e.key === 'Home'
				? 0
				: e.key === 'End'
					? enabled.length - 1
					: e.key === 'ArrowDown'
						? Math.min(enabled.length - 1, at + 1)
						: Math.max(0, at - 1);
		e.preventDefault();
		const key = enabled[next].key;
		setFocusKey(key);
		e.currentTarget.querySelector<HTMLElement>(`[data-letter="${CSS.escape(key)}"]`)?.focus();
	};

	return (
		<nav
			aria-label={label}
			data-testid="alpha-rail"
			className={cn('flex flex-col items-center pt-1', className)}
			onKeyDown={onKeyDown}
		>
			{letters.map((letter) => {
				const isCurrent = letter.key === current;
				const isBusy = letter.key === busy;
				const cls = cn(
					'font-heading grid h-6 w-6 place-items-center rounded-[5px] text-[10.5px] font-semibold leading-none',
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
							tabIndex={letter.key === tabStop ? 0 : -1}
							onFocus={() => setFocusKey(letter.key)}
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
