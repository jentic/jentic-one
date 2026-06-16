import { useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';

/**
 * URL-driven open state for the credential edit sheet.
 *
 * Same pattern as `DiscoveryView`'s `?inspect=` handling: a host page
 * exposes `?edit=<credentialId>` in its URL, and this hook returns
 * everything needed to render `<CredentialEditSheet>` against it:
 *
 *   - `editId` / `open` — what the sheet renders.
 *   - `stickyId` — last-seen `editId`, kept stable through the close
 *     animation so the sheet body doesn't unmount mid-slide-out.
 *   - `openSheet(id)` / `closeSheet()` — toggle the URL param while
 *     preserving everything else in `?…`.
 *   - `clearSticky()` — call this from `<CredentialEditSheet
 *     onAfterClose>` to release the sticky id once the animation
 *     finishes (otherwise hot-clicks across multiple credentials
 *     would resurrect a stale credential body).
 *
 * Hosts: `CredentialsPage`, `ApiDetailPage` (workspace), and
 * `ToolkitDetailPage` (Phase 2). Any page can adopt the contract by
 * mounting the hook + the sheet — there is no global state.
 */
export interface UseCredentialEditSheetOptions {
	/**
	 * Search-param key under which the credential id is encoded.
	 * Defaults to `edit`. Override on a page that already uses
	 * `?edit=…` for something else (none today, but the seam is here
	 * if it becomes necessary).
	 */
	paramName?: string;
}

export interface CredentialEditSheetState {
	/** Current editId from the URL (live). */
	editId: string | null;
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

export function useCredentialEditSheet(
	options: UseCredentialEditSheetOptions = {},
): CredentialEditSheetState {
	const { paramName = 'edit' } = options;
	const [searchParams, setSearchParams] = useSearchParams();

	const editId = searchParams.get(paramName);
	const [stickyId, setStickyId] = useState<string | null>(editId);

	useEffect(() => {
		if (editId) {
			setStickyId(editId);
		}
	}, [editId]);

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
		editId,
		stickyId,
		open: editId !== null,
		openSheet,
		closeSheet,
		clearSticky: () => setStickyId(null),
	};
}
