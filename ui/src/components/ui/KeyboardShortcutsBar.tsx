import type { ReactNode } from 'react';
import { Kbd } from '@/components/ui/Kbd';
import { cn } from '@/lib/utils';

const isMac = (() => {
	if (typeof navigator === 'undefined') return false;
	const nav = navigator as Navigator & {
		userAgentData?: { platform: string };
	};
	if (nav.userAgentData?.platform) {
		return nav.userAgentData.platform === 'macOS';
	}
	return /Mac|iPhone|iPad|iPod/.test(navigator.userAgent ?? '');
})();

/** Platform-aware modifier key label: `⌘` on Mac, `Ctrl` elsewhere. */
export const MOD_KEY = isMac ? '⌘' : 'Ctrl';

export interface KeyboardShortcut {
	/**
	 * Keys that make up the shortcut. Multiple entries are rendered as
	 * separate pills — by default with no separator (reads as "any of
	 * these keys", e.g. `["↑","↓","←","→"]`).
	 *
	 * For chord shortcuts (e.g. `Cmd+/`) set `chord: true` so the keys
	 * render with a `+` between pills.
	 */
	keys: string[];
	/** Plain-language description of what the shortcut does. */
	label: string;
	/**
	 * When true, keys are rendered as a chord with `+` between pills
	 * (e.g. ⌘ + /). When false/omitted, keys render side-by-side as
	 * alternatives (e.g. ↑ ↓ ← →).
	 */
	chord?: boolean;
	/**
	 * Optional secondary trigger. Some shortcuts have synonyms (e.g. a
	 * help dialog opened by `?` and `Shift+/`). Pass them as a
	 * separate string array; rendered with a thin "or" between groups.
	 */
	altKeys?: string[];
}

export interface KeyboardShortcutsBarProps {
	shortcuts: KeyboardShortcut[];
	/**
	 * Where to render the bar.
	 *
	 *  - `floating` (default): pinned to the bottom of the viewport on
	 *    `md+` viewports, hidden on mobile (where the BottomNavbar
	 *    already owns that strip). A subtle gradient fade above the
	 *    bar prevents content from looking abruptly cut off.
	 *
	 *  - `inline`: renders in the document flow at the call site.
	 *    Useful for empty states, command palettes, and anywhere a
	 *    "you can also press X" hint should sit close to the affected
	 *    UI rather than at the page edge.
	 */
	placement?: 'floating' | 'inline';
	/**
	 * Optional left/right slot for branding or context (e.g. the page
	 * name) on the floating bar. Ignored when `placement="inline"`.
	 */
	leadingSlot?: ReactNode;
	className?: string;
}

function ShortcutItem({ shortcut }: { shortcut: KeyboardShortcut }) {
	return (
		<div className="flex items-center gap-1.5">
			<span className="flex items-center gap-0.5">
				{shortcut.keys.map((k, idx) => (
					<span
						key={`${shortcut.label}-${idx}-${k}`}
						className="flex items-center gap-0.5"
					>
						{shortcut.chord && idx > 0 && (
							<span className="text-muted-foreground/50 text-[10px]">+</span>
						)}
						<Kbd variant="solid">{k}</Kbd>
					</span>
				))}
				{shortcut.altKeys && (
					<>
						<span className="text-muted-foreground/60 mx-1 text-[10px] uppercase">
							or
						</span>
						{shortcut.altKeys.map((k, idx) => (
							<Kbd key={`${shortcut.label}-alt-${idx}-${k}`} variant="solid">
								{k}
							</Kbd>
						))}
					</>
				)}
			</span>
			<span className="whitespace-nowrap">{shortcut.label}</span>
		</div>
	);
}

/**
 * Compact horizontal strip of `Kbd` chip + label pairs documenting
 * the keyboard shortcuts available on the current page.
 *
 * Ported from `jentic-webapp`'s `<KeyboardShortcutsBar>` and
 * generalised: in webapp the shortcuts list was hardcoded inside the
 * component, here we accept it as a prop so the same primitive can
 * advertise different bindings on Discover, Toolkits, Credentials,
 * etc. (each page knows what its own keyboard map does).
 *
 * Layered z-indexes: floating bar sits at `z-30` so it stays below
 * the toaster (`z-[60]`) and slide-out sheets (`z-50`) but above
 * regular page content. On mobile we render nothing — the
 * `BottomNavbar` already occupies that strip and packing two fixed
 * bars at the bottom of a phone is hostile.
 */
export function KeyboardShortcutsBar({
	shortcuts,
	placement = 'floating',
	leadingSlot,
	className,
}: KeyboardShortcutsBarProps) {
	if (shortcuts.length === 0) return null;

	const items = (
		<div className="text-muted-foreground flex flex-wrap items-center justify-center gap-x-5 gap-y-1 text-xs">
			{shortcuts.map((s) => (
				<ShortcutItem key={s.label} shortcut={s} />
			))}
		</div>
	);

	if (placement === 'inline') {
		return (
			<div
				className={cn('w-full py-2', className)}
				role="region"
				aria-label="Keyboard shortcuts"
				data-testid="keyboard-shortcuts-bar"
			>
				{items}
			</div>
		);
	}

	return (
		// `pointer-events-none` on the wrapper + `pointer-events-auto`
		// on the bar itself means the gradient fade above never blocks
		// clicks on cards beneath it (the hit area is just the bar
		// strip). Hidden below `md` because the mobile BottomNavbar
		// owns the bottom of the viewport on phones.
		<div
			className={cn(
				'pointer-events-none fixed right-0 bottom-0 left-0 z-30 hidden md:block',
				className,
			)}
			role="region"
			aria-label="Keyboard shortcuts"
			data-testid="keyboard-shortcuts-bar"
		>
			<div className="from-background h-6 bg-gradient-to-t to-transparent" />
			<div className="border-border/60 bg-background/85 pointer-events-auto border-t backdrop-blur-sm">
				<div className="px-page-gutter mx-auto flex items-center justify-center gap-4 py-1.5">
					{leadingSlot && (
						<div className="text-muted-foreground/80 hidden text-[10px] font-medium tracking-wider uppercase lg:block">
							{leadingSlot}
						</div>
					)}
					{items}
				</div>
			</div>
		</div>
	);
}
