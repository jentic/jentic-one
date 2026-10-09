import { useEffect, useState, type Dispatch, type SetStateAction } from 'react';
import { useSearchParams } from 'react-router';

/**
 * A one-shot `?{name}=1` deep link that opens something (e.g. `?import=1` →
 * the import dialog) and is then stripped from the URL, so a refresh or
 * back-navigation doesn't re-trigger it. Returns ordinary open state.
 *
 * The initializer reads the flag so the target opens on the very first paint
 * (no closed-then-open flash); the effect handles the flag arriving later and
 * strips it (replace, so no extra history entry).
 */
export function useConsumedFlagParam(name: string): [boolean, Dispatch<SetStateAction<boolean>>] {
	const [searchParams, setSearchParams] = useSearchParams();
	const [open, setOpen] = useState(() => searchParams.get(name) === '1');
	useEffect(() => {
		if (searchParams.get(name) !== '1') return;
		setOpen(true);
		const next = new URLSearchParams(searchParams);
		next.delete(name);
		setSearchParams(next, { replace: true });
	}, [name, searchParams, setSearchParams]);
	return [open, setOpen];
}
