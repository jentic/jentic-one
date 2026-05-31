import React, { useRef, useEffect } from 'react';
import { RefreshButton } from '@/components/ui/RefreshButton';
import { SearchInput } from '@/components/ui/SearchInput';

// ── Sticky search + filters toolbar ──────────────────────────────────────────

interface ToolbarProps {
	input: string;
	onInput: (next: string) => void;
	onClear: () => void;
	isFetching: boolean;
	placeholder?: string;
	searchInputRef?: React.Ref<HTMLInputElement>;
}

export function DiscoverToolbar({
	input,
	onInput,
	onClear,
	isFetching,
	placeholder,
	searchInputRef,
}: ToolbarProps) {
	const sentinelRef = useRef<HTMLDivElement | null>(null);
	const toolbarRef = useRef<HTMLDivElement | null>(null);
	useEffect(() => {
		const sentinel = sentinelRef.current;
		const toolbar = toolbarRef.current;
		if (!sentinel || !toolbar || typeof IntersectionObserver === 'undefined') return;
		const obs = new IntersectionObserver(
			([entry]) => {
				toolbar.dataset.scrolled = entry.isIntersecting ? 'false' : 'true';
			},
			{ threshold: 0 },
		);
		obs.observe(sentinel);
		return () => obs.disconnect();
	}, []);
	return (
		<div
			ref={toolbarRef}
			data-scrolled="false"
			className="-mx-page-gutter px-page-gutter border-border/40 bg-background/85 sticky top-12 z-20 border-b py-3 backdrop-blur transition-shadow data-[scrolled=true]:shadow-[0_1px_0_0_rgb(0_0_0_/0.04)]"
			data-testid="discover-toolbar"
		>
			<div ref={sentinelRef} aria-hidden="true" className="absolute top-0 h-px w-full" />
			<div className="flex flex-col gap-2 md:flex-row md:items-center md:gap-3">
				<SearchInput
					autoFocus
					ref={searchInputRef}
					value={input}
					onValueChange={onInput}
					onClear={onClear}
					placeholder={placeholder ?? 'Search APIs by name…'}
					aria-label="Search APIs"
					loading={isFetching}
					className="md:flex-1"
				/>
			</div>
		</div>
	);
}

// ── Catalog status row ────────────────────────────────────────────────────────

export function formatRelativeAge(seconds: number | null): string {
	if (seconds == null || !Number.isFinite(seconds)) return 'unknown';
	if (seconds < 60) return 'just now';
	const minutes = Math.round(seconds / 60);
	if (minutes < 60) return `${minutes} min${minutes === 1 ? '' : 's'} ago`;
	const hours = Math.round(minutes / 60);
	if (hours < 24) return `${hours} hour${hours === 1 ? '' : 's'} ago`;
	const days = Math.round(hours / 24);
	return `${days} day${days === 1 ? '' : 's'} ago`;
}

interface CatalogStatusRowProps {
	shown: number | null;
	total: number | null;
	lastSyncSeconds: number | null;
	isLoading: boolean;
	isRefreshing: boolean;
	onRefresh: () => void;
}

export function CatalogStatusRow({
	shown,
	total,
	lastSyncSeconds,
	isLoading,
	isRefreshing,
	onRefresh,
}: CatalogStatusRowProps) {
	const showCounts = total !== null;
	return (
		<div
			className="text-muted-foreground flex flex-wrap items-center gap-x-3 gap-y-1 text-xs"
			data-testid="discover-catalog-status"
			aria-live="polite"
		>
			{isLoading && !showCounts ? (
				<span className="text-muted-foreground/70">Loading catalog…</span>
			) : (
				<span>
					Showing{' '}
					<span
						className="text-foreground font-medium tabular-nums"
						data-testid="discover-catalog-shown"
					>
						{(shown ?? 0).toLocaleString()}
					</span>{' '}
					of{' '}
					<span
						className="text-foreground font-medium tabular-nums"
						data-testid="discover-catalog-total"
					>
						{total?.toLocaleString() ?? '—'}
					</span>{' '}
					APIs
				</span>
			)}
			<div className="ml-auto flex items-center gap-2">
				{!isLoading || showCounts ? (
					<span>
						Last synced{' '}
						<span
							className="text-foreground font-medium"
							data-testid="discover-catalog-last-sync"
						>
							{formatRelativeAge(lastSyncSeconds)}
						</span>
					</span>
				) : null}
				<RefreshButton
					onRefresh={onRefresh}
					pending={isRefreshing}
					title="Refresh local catalog index"
					className="h-6 w-6"
					iconClassName="h-3.5 w-3.5"
					testId="discover-catalog-refresh"
				/>
			</div>
		</div>
	);
}
