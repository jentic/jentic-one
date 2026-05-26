import { useCallback, useRef, useImperativeHandle, forwardRef } from 'react';
import { Filter } from 'lucide-react';
import { SearchInput } from '@/components/ui/SearchInput';

/**
 * Single filter input for the Workspace page. Narrows the visible
 * tiles client-side by name / description match — *not* a BM25
 * fan-out, *not* a navigation away. The Workspace page is small
 * enough (tens of entries, not thousands) that an in-memory filter
 * is the right tool; users who want catalog-wide semantic search go
 * to `/discover`.
 *
 * The leading icon is a funnel (`Filter`), not a magnifying glass,
 * to make the in-memory nature obvious at a glance — Discover gets
 * the magnifying glass because that's where searching actually lives.
 */
export interface WorkspaceSearchProps {
	value: string;
	onChange: (next: string) => void;
	/** Optional summary line, e.g. "12 results". */
	resultsLabel?: string;
}

export interface WorkspaceSearchHandle {
	focus: () => void;
}

export const WorkspaceSearch = forwardRef<WorkspaceSearchHandle, WorkspaceSearchProps>(
	function WorkspaceSearch({ value, onChange, resultsLabel }, ref) {
		const inputRef = useRef<HTMLInputElement | null>(null);

		useImperativeHandle(ref, () => ({
			focus() {
				inputRef.current?.focus();
				inputRef.current?.select();
			},
		}));

		const setInputRef = useCallback((el: HTMLInputElement | null) => {
			inputRef.current = el;
			if (el && !document.querySelector('dialog[open]')) {
				el.focus({ preventScroll: true });
			}
		}, []);

		return (
			<>
				<div
					className="-mx-page-gutter px-page-gutter border-border/40 bg-background/85 sticky top-12 z-20 -mt-3 border-y py-3 backdrop-blur"
					data-testid="workspace-search"
				>
					<SearchInput
						ref={setInputRef}
						value={value}
						onValueChange={onChange}
						icon={<Filter className="h-3.5 w-3.5" />}
						placeholder="Filter your workspace by name or description…"
						aria-label="Filter workspace"
					/>
				</div>
				{value && resultsLabel ? (
					<p className="text-muted-foreground text-xs">{resultsLabel}</p>
				) : null}
			</>
		);
	},
);
