/**
 * DocsTopNav — the sticky product chrome for the docs portal.
 *
 * The docs route renders OUTSIDE the authenticated app shell (see
 * `modules/docs/routes`), so it has no global navbar of its own. This supplies
 * one: the Jentic One logo (home link) on the left and a global search on the
 * right that spans every searchable thing on the page — sections, CLI commands,
 * scopes, and API endpoints — jumping to the match's anchor on select.
 *
 * Search is keyboard-driven (⌘/Ctrl-K to focus, ↑/↓ to move, ↵ to jump, Esc to
 * dismiss) and closes on outside click via `useDismissable`.
 */
import { useEffect, useId, useMemo, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { Search, FileText, SquareTerminal, ShieldCheck, Plug, Box } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { JenticLogo, Input, useDismissable } from '@/shared/ui';
import { ROUTES } from '@/shared/app/routes';
import {
	buildSearchIndex,
	searchIndex,
	type BrokerSearchSource,
	type SearchItem,
	type SearchKind,
} from '@/modules/docs/lib/search';
import type { CliBinary, ReferencePayload } from '@/modules/docs/api/types';
import { cn } from '@/shared/lib/utils';

const KIND_META: Record<SearchKind, { icon: LucideIcon; label: string }> = {
	section: { icon: FileText, label: 'Section' },
	cli: { icon: SquareTerminal, label: 'CLI' },
	scope: { icon: ShieldCheck, label: 'Scope' },
	endpoint: { icon: Plug, label: 'Endpoint' },
	model: { icon: Box, label: 'Model' },
};

export interface DocsTopNavProps {
	reference?: ReferencePayload;
	binaries?: CliBinary[];
	/** Component-schema names ("Models"), for the search index. */
	models?: string[];
	/** Broker operations + models for the search index (namespaced anchors). */
	broker?: BrokerSearchSource;
	/** Scroll the page to an anchor id (smooth + hash + active state). */
	onJump: (anchor: string) => void;
}

export function DocsTopNav({ reference, binaries, models, broker, onJump }: DocsTopNavProps) {
	const [query, setQuery] = useState('');
	const [open, setOpen] = useState(false);
	const [cursor, setCursor] = useState(0);
	const inputRef = useRef<HTMLInputElement>(null);
	const listboxRef = useRef<HTMLDivElement>(null);
	const containerRef = useDismissable<HTMLDivElement>(open, () => setOpen(false));
	const listboxId = useId();
	const optionId = (i: number) => `${listboxId}-opt-${i}`;

	const index = useMemo(
		() => buildSearchIndex(reference, binaries, models, broker),
		[reference, binaries, models, broker],
	);
	const results = useMemo(() => searchIndex(index, query), [index, query]);

	// ⌘K / Ctrl-K focuses the search from anywhere on the page.
	useEffect(() => {
		const onKey = (e: KeyboardEvent) => {
			if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
				e.preventDefault();
				inputRef.current?.focus();
				setOpen(true);
			}
		};
		window.addEventListener('keydown', onKey);
		return () => window.removeEventListener('keydown', onKey);
	}, []);

	useEffect(() => setCursor(0), [query]);

	const listboxOpen = open && query.trim().length > 0;

	// Keep the highlighted option scrolled into view as the cursor moves with
	// the arrow keys — a long result list otherwise hides the active row.
	useEffect(() => {
		if (!listboxOpen) return;
		const el = listboxRef.current?.querySelector<HTMLElement>(
			`#${CSS.escape(optionId(cursor))}`,
		);
		el?.scrollIntoView({ block: 'nearest' });
		// optionId is derived from a stable listboxId; cursor/listboxOpen are the
		// real inputs.
		// eslint-disable-next-line react-hooks/exhaustive-deps
	}, [cursor, listboxOpen]);

	const choose = (item: SearchItem) => {
		onJump(item.anchor);
		setOpen(false);
		setQuery('');
		inputRef.current?.blur();
	};

	const onInputKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
		if (e.key === 'Escape') {
			setOpen(false);
			(e.target as HTMLInputElement).blur();
			return;
		}
		if (!results.length) return;
		if (e.key === 'ArrowDown') {
			e.preventDefault();
			setCursor((c) => (c + 1) % results.length);
		} else if (e.key === 'ArrowUp') {
			e.preventDefault();
			setCursor((c) => (c - 1 + results.length) % results.length);
		} else if (e.key === 'Enter') {
			e.preventDefault();
			const item = results[cursor];
			if (item) choose(item);
		}
	};

	return (
		<header className="border-border bg-background/85 sticky top-0 z-40 border-b backdrop-blur">
			<div className="px-page-gutter mx-auto flex max-w-[100rem] items-center gap-4 py-2.5">
				<Link
					to={ROUTES.app}
					aria-label="Jentic One home"
					className="focus-visible:ring-primary/50 shrink-0 rounded-md focus-visible:ring-2 focus-visible:outline-none"
				>
					<JenticLogo />
				</Link>

				<span className="text-foreground/40 hidden text-sm sm:inline">Docs</span>

				<div ref={containerRef} className="relative ml-auto w-full max-w-md">
					<Input
						ref={inputRef}
						value={query}
						onChange={(e) => {
							setQuery(e.target.value);
							setOpen(true);
						}}
						onFocus={() => setOpen(true)}
						onKeyDown={onInputKeyDown}
						placeholder="Search docs, commands, scopes, endpoints…"
						aria-label="Search documentation"
						role="combobox"
						aria-expanded={listboxOpen}
						aria-controls={listboxId}
						aria-autocomplete="list"
						aria-activedescendant={
							listboxOpen && results.length > 0 ? optionId(cursor) : undefined
						}
						size="sm"
						startIcon={<Search className="h-3.5 w-3.5" />}
						className="pr-12"
					/>
					<kbd className="text-foreground/40 border-border bg-muted/40 pointer-events-none absolute top-1/2 right-2 hidden -translate-y-1/2 rounded border px-1.5 py-0.5 font-mono text-[10px] sm:block">
						⌘K
					</kbd>

					{listboxOpen && (
						<div
							ref={listboxRef}
							id={listboxId}
							role="listbox"
							aria-label="Search results"
							className="border-border bg-card absolute right-0 left-0 z-50 mt-1.5 max-h-[60vh] overflow-y-auto rounded-lg border p-1 shadow-xl"
						>
							{results.length === 0 ? (
								<p className="text-foreground/50 px-3 py-4 text-sm">
									No matches for “{query.trim()}”.
								</p>
							) : (
								results.map((item, i) => {
									const meta = KIND_META[item.kind];
									const Icon = meta.icon;
									const active = i === cursor;
									return (
										<button
											key={`${item.kind}-${item.anchor}-${item.title}-${i}`}
											id={optionId(i)}
											type="button"
											role="option"
											aria-selected={active}
											onMouseEnter={() => setCursor(i)}
											onClick={() => choose(item)}
											className={cn(
												'flex w-full items-center gap-3 rounded-md px-3 py-2 text-left',
												active ? 'bg-primary/10' : 'hover:bg-muted',
											)}
										>
											<Icon
												className="text-foreground/45 h-4 w-4 shrink-0"
												aria-hidden="true"
											/>
											<span className="min-w-0 flex-1">
												<span className="text-foreground block truncate font-mono text-[13px]">
													{item.title}
												</span>
												{item.subtitle && (
													<span className="text-foreground/50 block truncate text-xs">
														{item.subtitle}
													</span>
												)}
											</span>
											<span className="text-foreground/35 shrink-0 text-[10px] font-semibold tracking-wide uppercase">
												{meta.label}
											</span>
										</button>
									);
								})
							)}
						</div>
					)}
				</div>
			</div>
		</header>
	);
}
