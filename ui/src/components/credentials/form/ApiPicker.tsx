import { useEffect, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Search, ChevronRight, Loader2 } from 'lucide-react';
import type { ApiOut } from '@/api/types';
import { api } from '@/api/client';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';

/**
 * Step 1 of the credential add flow — a debounced search over the
 * combined "local + catalog" API surface.
 *
 * Self-contained: owns its own input state, debounce, autofocus, and
 * data fetching. Parents only need to handle `onSelect`.
 *
 * Lives under `components/credentials/form/` because it's reused by
 * three credential entry surfaces — the legacy full-page form, the
 * upcoming sheet-based edit (when re-targeting a credential to a
 * different API), and the toolkit-anchored add dialog. Keep it
 * presentation-pure: no routing, no toasts, no global stores.
 */
export interface ApiPickerProps {
	onSelect: (api: ApiOut) => void;
}

function useDebounce<T>(value: T, ms: number): T {
	const [debounced, setDebounced] = useState(value);
	useEffect(() => {
		const t = setTimeout(() => setDebounced(value), ms);
		return () => clearTimeout(t);
	}, [value, ms]);
	return debounced;
}

export function ApiPicker({ onSelect }: ApiPickerProps) {
	const [query, setQuery] = useState('');
	const debouncedQuery = useDebounce(query, 250);
	const inputRef = useRef<HTMLInputElement>(null);

	useEffect(() => {
		inputRef.current?.focus();
	}, []);

	const { data, isLoading } = useQuery({
		queryKey: ['apis-search', debouncedQuery],
		queryFn: () => api.listApis(1, 30, undefined, debouncedQuery),
		enabled: debouncedQuery.length > 0,
		placeholderData: (prev) => prev,
	});

	const items = (data?.items ?? (data as any)?.data ?? []) as ApiOut[];
	const local = items.filter((a: ApiOut) => a.source === 'local');
	const catalog = items.filter((a: ApiOut) => a.source === 'catalog');

	return (
		<div className="space-y-3">
			<div className="relative">
				<Input
					ref={inputRef}
					type="text"
					value={query}
					onChange={(e) => setQuery(e.target.value)}
					placeholder="Search APIs (GitHub, Gmail, Stripe…)"
					aria-label="Search APIs"
					startIcon={<Search className="h-4 w-4" />}
					className="bg-background py-2.5 pr-3"
				/>
				{isLoading && (
					<Loader2 className="text-muted-foreground absolute top-1/2 right-3 h-4 w-4 -translate-y-1/2 animate-spin" />
				)}
			</div>

			{items.length === 0 && !isLoading && debouncedQuery && (
				<p className="text-muted-foreground py-4 text-center text-sm">
					No APIs found for "{debouncedQuery}"
				</p>
			)}

			{local.length > 0 && (
				<div>
					<p className="text-muted-foreground mb-1.5 px-1 font-mono text-[10px] tracking-widest uppercase">
						Available locally
					</p>
					<div className="space-y-1">
						{local.map((a: ApiOut) => (
							<ApiRow key={a.id} api={a} onSelect={onSelect} />
						))}
					</div>
				</div>
			)}

			{catalog.length > 0 && (
				<div>
					<p className="text-muted-foreground mb-1.5 px-1 font-mono text-[10px] tracking-widest uppercase">
						From the Jentic public catalog
					</p>
					{local.length === 0 && (
						<p className="text-muted-foreground/80 mb-2 text-xs">
							Picking an available API imports it into your workspace as part of
							saving this credential — there's no separate import step.
						</p>
					)}
					<div className="space-y-1">
						{catalog.map((a: ApiOut) => (
							<ApiRow key={a.id} api={a} onSelect={onSelect} />
						))}
					</div>
				</div>
			)}

			{!debouncedQuery && items.length === 0 && !isLoading && (
				<p className="text-muted-foreground py-6 text-center text-sm">
					Start typing to search 10,000+ APIs
				</p>
			)}
		</div>
	);
}

function ApiRow({ api: a, onSelect }: { api: ApiOut; onSelect: (api: ApiOut) => void }) {
	const hasCreds = !!a.has_credentials;
	return (
		<Button
			variant="ghost"
			onClick={() => onSelect(a)}
			className="bg-background hover:bg-muted/60 border-border group flex w-full items-center justify-between gap-3 rounded-lg border px-3 py-2.5 text-left transition-colors"
		>
			<div className="min-w-0">
				<div className="flex items-center gap-2">
					<span className="text-foreground truncate text-sm font-medium">
						{a.name ?? a.id}
					</span>
					{hasCreds && (
						<span className="bg-success/15 text-success border-success/30 shrink-0 rounded border px-1.5 py-0.5 font-mono text-[10px]">
							configured
						</span>
					)}
				</div>
				{a.description && (
					<p className="text-muted-foreground mt-0.5 truncate text-xs">
						{a.description as string}
					</p>
				)}
			</div>
			<ChevronRight className="text-muted-foreground group-hover:text-foreground h-4 w-4 shrink-0 transition-colors" />
		</Button>
	);
}
