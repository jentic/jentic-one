import { useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';

/**
 * URL-driven open state for the OAuth connection detail sheet.
 *
 * Pipedream-managed ("OAuth connections") credentials don't go through
 * `<CredentialEditSheet>` — their secret lives upstream, so there's no
 * value to rotate locally. Clicking such a card instead opens a
 * read-mostly detail sheet (`<OAuthConnectionDetailSheet>`) that shows
 * the connection facts and lets the user edit the sync-safe metadata
 * (label via the broker, description locally), reconnect, or delete.
 *
 * This mirrors `useCredentialEditSheet` exactly but on a *separate*
 * `?oauth=<credentialId>` param so the manual-edit sheet and the OAuth
 * detail sheet can coexist on the same page without fighting over one
 * URL key.
 *
 *   - `connId` / `open` — what the sheet renders.
 *   - `stickyId` — last-seen id, kept stable through the close
 *     animation so the body doesn't unmount mid-slide-out.
 *   - `openSheet(id)` / `closeSheet()` — toggle the URL param while
 *     preserving everything else in `?…`.
 *   - `clearSticky()` — call from `onAfterClose` once the animation
 *     finishes so hot-clicks across connections don't resurrect a
 *     stale body.
 */
export interface UseOAuthConnectionSheetOptions {
	/**
	 * Search-param key under which the credential id is encoded.
	 * Defaults to `oauth`. The default deliberately differs from
	 * `useCredentialEditSheet`'s `edit` so both sheets can mount on
	 * the same page.
	 */
	paramName?: string;
}

export interface OAuthConnectionSheetState {
	/** Current id from the URL (live). */
	connId: string | null;
	/** Sticky mirror that survives the close animation. */
	stickyId: string | null;
	/** Whether the sheet should be visually open. */
	open: boolean;
	/** Open the sheet for `id`. Replaces history entry. */
	openSheet: (id: string) => void;
	/** Close the sheet. Drops the URL param; sticky id remains until cleared. */
	closeSheet: () => void;
	/** Release the sticky mirror (call after the close animation). */
	clearSticky: () => void;
}

export function useOAuthConnectionSheet(
	options: UseOAuthConnectionSheetOptions = {},
): OAuthConnectionSheetState {
	const { paramName = 'oauth' } = options;
	const [searchParams, setSearchParams] = useSearchParams();

	const connId = searchParams.get(paramName);
	const [stickyId, setStickyId] = useState<string | null>(connId);

	useEffect(() => {
		if (connId) {
			setStickyId(connId);
		}
	}, [connId]);

	const openSheet = (id: string) => {
		setSearchParams(
			(prev) => {
				const p = new URLSearchParams(prev);
				p.set(paramName, id);
				return p;
			},
			{ replace: true },
		);
	};

	const closeSheet = () => {
		setSearchParams(
			(prev) => {
				const p = new URLSearchParams(prev);
				p.delete(paramName);
				return p;
			},
			{ replace: true },
		);
	};

	return {
		connId,
		stickyId,
		open: connId !== null,
		openSheet,
		closeSheet,
		clearSticky: () => setStickyId(null),
	};
}
