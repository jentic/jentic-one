import { useEffect } from 'react';
import { useLocation, useNavigationType } from 'react-router-dom';

const scrollPositions = new Map<string, number>();

/**
 * Saves scroll position when leaving a page and restores it on POP (back/forward).
 * Call this in any page component that should preserve scroll on return.
 */
export function useScrollRestore() {
	const { key } = useLocation();
	const navType = useNavigationType();

	useEffect(() => {
		if (navType === 'POP') {
			const saved = scrollPositions.get(key);
			if (saved != null) {
				requestAnimationFrame(() => {
					window.scrollTo(0, saved);
				});
			}
		} else {
			window.scrollTo(0, 0);
		}
	}, [key, navType]);

	useEffect(() => {
		const save = () => {
			scrollPositions.set(key, window.scrollY);
		};
		window.addEventListener('scroll', save, { passive: true });
		return () => {
			window.removeEventListener('scroll', save);
		};
	}, [key]);
}
