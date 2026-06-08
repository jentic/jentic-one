import { useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';

/**
 * URL-driven open state for the toolkit detail sheet.
 *
 * Mirrors `useCredentialEditSheet` (the `?edit=` pattern) but on a
 * dedicated `?toolkit=<id>` param so the two can coexist on the same URL
 * (e.g. `/toolkits?toolkit=rest`) without fighting. Returns everything a
 * host needs to render `<ToolkitDetailSheet>`:
 *
 *   - `toolkitId` / `open` — what the sheet renders.
 *   - `stickyId` — last-seen id, kept stable through the close animation so
 *     the sheet body doesn't unmount mid-slide-out.
 *   - `openSheet(id)` / `closeSheet()` — toggle the URL param while
 *     preserving everything else in `?…`.
 *   - `clearSticky()` — release the sticky id once the close animation
 *     finishes (call from `<ToolkitDetailSheet onAfterClose>`).
 *
 * Unlike the credential hook, `openSheet` pushes a real history entry
 * (`replace: false`) so the browser Back button closes the sheet and
 * returns to the bare list URL. `closeSheet` replaces, so closing via the
 * sheet's own affordances doesn't leave a dangling forward entry.
 */
export interface UseToolkitDetailSheetOptions {
	/** Search-param key. Defaults to `toolkit`. */
	paramName?: string;
}

export interface ToolkitDetailSheetState {
	toolkitId: string | null;
	stickyId: string | null;
	open: boolean;
	openSheet: (id: string) => void;
	closeSheet: () => void;
	clearSticky: () => void;
}

export function useToolkitDetailSheet(
	options: UseToolkitDetailSheetOptions = {},
): ToolkitDetailSheetState {
	const { paramName = 'toolkit' } = options;
	const [searchParams, setSearchParams] = useSearchParams();

	const toolkitId = searchParams.get(paramName);
	const [stickyId, setStickyId] = useState<string | null>(toolkitId);

	useEffect(() => {
		if (toolkitId) {
			setStickyId(toolkitId);
		}
	}, [toolkitId]);

	const openSheet = (id: string) => {
		setSearchParams(
			(prev) => {
				const p = new URLSearchParams(prev);
				p.set(paramName, id);
				return p;
			},
			{ replace: false },
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
		toolkitId,
		stickyId,
		open: toolkitId !== null,
		openSheet,
		closeSheet,
		clearSticky: () => setStickyId(null),
	};
}
