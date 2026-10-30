/**
 * `?inbox=1` — a link that opens the rail on its Inbox tab (see
 * `reviewLinks`). The docked rail answers it at `xl+` and the drawer below,
 * so exactly one of them opens; whichever does strips the param with
 * `replace`, the way the Agents page strips `?credentials`, so history,
 * bookmarks and a reload never re-open it.
 */
import { useEffect, useRef } from 'react';
import { useSearchParams } from 'react-router';
import { INBOX_PARAM } from '@/shared/approvals';

/** Tailwind's `xl` breakpoint — the docked rail takes over from here. */
export const RAIL_DOCKED_QUERY = '(min-width: 1280px)';

/**
 * Call `onOpen` when the URL asks for the Inbox and this surface is the one on
 * screen: `docked` for the `xl+` rail, `drawer` for the sheet below `xl`.
 */
export function useInboxParam(surface: 'docked' | 'drawer', onOpen: () => void): void {
	const [params, setParams] = useSearchParams();
	const wanted = params.get(INBOX_PARAM) != null;
	const onOpenRef = useRef(onOpen);
	useEffect(() => {
		onOpenRef.current = onOpen;
	}, [onOpen]);

	useEffect(() => {
		if (!wanted) return;
		const docked = window.matchMedia(RAIL_DOCKED_QUERY).matches;
		if (docked !== (surface === 'docked')) return;
		onOpenRef.current();
		// `replace`, and only this key: the rest of the URL is the page's own.
		setParams(
			(prev) => {
				const next = new URLSearchParams(prev);
				next.delete(INBOX_PARAM);
				return next;
			},
			{ replace: true },
		);
	}, [wanted, surface, setParams]);
}
