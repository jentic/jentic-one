import { useCallback } from 'react';

/**
 * Arrow-key navigation between focusable elements in a CSS grid.
 *
 * Computes column count dynamically from the first row's `offsetTop` band
 * so Up/Down skip by the correct stride regardless of responsive breakpoint.
 * In a single-column layout (mobile, list density) Up/Down become single-step
 * movers because the list is strictly vertical.
 */
export function useRovingGridFocus(
	containerRef: React.RefObject<HTMLElement | null>,
	cardSelector: string,
) {
	return useCallback(
		(e: React.KeyboardEvent<HTMLDivElement>) => {
			const container = containerRef.current;
			if (!container) return;
			const cards = Array.from(container.querySelectorAll<HTMLElement>(cardSelector));
			if (cards.length === 0) return;
			const active = document.activeElement as HTMLElement | null;
			const currentIdx = active ? cards.indexOf(active) : -1;
			if (currentIdx === -1) return;

			let nextIdx = currentIdx;

			const row0Top = cards[0].offsetTop;
			const columnCount = Math.max(1, cards.filter((c) => c.offsetTop === row0Top).length);

			switch (e.key) {
				case 'ArrowRight':
					nextIdx = Math.min(cards.length - 1, currentIdx + 1);
					break;
				case 'ArrowLeft':
					nextIdx = Math.max(0, currentIdx - 1);
					break;
				case 'ArrowDown':
					nextIdx = Math.min(cards.length - 1, currentIdx + columnCount);
					break;
				case 'ArrowUp':
					nextIdx = Math.max(0, currentIdx - columnCount);
					break;
				case 'Home':
					nextIdx = 0;
					break;
				case 'End':
					nextIdx = cards.length - 1;
					break;
				default:
					return;
			}

			if (nextIdx !== currentIdx) {
				e.preventDefault();
				cards[nextIdx]?.focus();
			}
		},
		[containerRef, cardSelector],
	);
}
