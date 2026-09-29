/**
 * useTyped — the landing's one typing effect (a task typed to the agent, a
 * command typed into a terminal). Reduced motion shows the whole text at once.
 */
import { useEffect, useState } from 'react';

/** Reveal `text` one character at a time while `active`; whole under reduced motion. */
export function useTyped(text: string, active: boolean, reduced: boolean, msPerChar = 45): string {
	const [n, setN] = useState(active && !reduced ? 0 : text.length);
	useEffect(() => {
		if (!active || reduced) {
			setN(text.length);
			return undefined;
		}
		setN(0);
		let typed = 0;
		const id = window.setInterval(() => {
			typed += 1;
			setN(typed);
			if (typed >= text.length) window.clearInterval(id);
		}, msPerChar);
		return () => window.clearInterval(id);
	}, [text, active, reduced, msPerChar]);
	return text.slice(0, n);
}
