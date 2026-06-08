import { motion } from 'framer-motion';
import { AlertTriangle, Ban, Bot, Calendar, ChevronRight, Key, KeyRound } from 'lucide-react';
import { VendorIcon } from '@/components/discovery/VendorIcon';
import { VendorPile } from '@/components/discovery/VendorPile';
import { AppLink } from '@/components/ui/AppLink';
import { timeAgo } from '@/lib/time';

/**
 * Minimal toolkit shape this card reads. The `/toolkits` list endpoint is
 * typed as `Record<string, any>` in the generated client, and returns extra
 * roll-up counts (`key_count` / `credential_count`) that aren't on the
 * hand-written detail type — so we accept a permissive structural shape
 * here and read everything defensively.
 */
export interface ToolkitCardData {
	id: string;
	name: string;
	description?: string | null;
	created_at?: number | null;
	simulate?: boolean;
	disabled?: boolean;
	key_count?: number;
	credential_count?: number;
	keys?: unknown[];
	credentials?: unknown[];
	/**
	 * Distinct upstream API ids this toolkit touches (derived from its bound
	 * credentials' `api_id`s). Optional — when present, rendered as a
	 * `VendorPile` so the card hints at *which* APIs, not just how many.
	 */
	apiIds?: string[];
	/** Count of agents granted this toolkit, surfaced in the meta row. */
	agentCount?: number;
}

/**
 * Toolkit list-card used on `ToolkitsPage`.
 *
 * Mirrors the `WorkspaceTile` idiom (gradient `VendorIcon`, `bg-card`
 * surface, subtle hover-lift, bottom meta row) so the Toolkits list reads
 * like the rest of the dashboard rather than the older flatter `bg-muted`
 * card. A toolkit isn't a vendor, so we seed `VendorIcon` with the toolkit
 * name — it falls back to a deterministic gradient + initials tile, giving
 * each toolkit a stable, colourful identity.
 *
 * The whole card is an `AppLink` (router link) to the detail page, so it
 * stays a single keyboard/AT target. Status is conveyed with compact pills
 * whose literal labels ("SUSPENDED", "simulate") are preserved for the
 * existing tests and platform vocabulary.
 *
 * `framer-motion` is used only for the per-item entrance; the parent grid
 * (`ToolkitCardGrid`) drives the stagger via `staggerChildren`.
 */
export const toolkitCardVariants = {
	hidden: { opacity: 0, y: 8 },
	visible: { opacity: 1, y: 0, transition: { duration: 0.2, ease: 'easeOut' } },
} as const;

export interface ToolkitCardProps {
	toolkit: ToolkitCardData;
	/** Pending access-requests count, surfaced as a warning pill. */
	pendingCount?: number;
	/**
	 * Optional click handler. When provided, the card opens the toolkit
	 * detail sheet in-place (the click is intercepted) instead of
	 * navigating to `/toolkits/:id`. The `href` is preserved so ⌘/Ctrl/
	 * middle-click still deep-links to the full page in a new tab.
	 */
	onOpen?: (id: string) => void;
}

export function ToolkitCard({ toolkit, pendingCount = 0, onOpen }: ToolkitCardProps) {
	const disabled = !!toolkit.disabled;
	const keyCount = toolkit.key_count ?? toolkit.keys?.length ?? null;
	const credentialCount = toolkit.credential_count ?? toolkit.credentials?.length ?? null;
	const apiIds = toolkit.apiIds ?? [];
	const agentCount = toolkit.agentCount;

	return (
		<motion.div variants={toolkitCardVariants}>
			<AppLink
				href={`/toolkits/${toolkit.id}`}
				onClick={
					onOpen
						? (e) => {
								// Let modified clicks (new tab / new window) and
								// non-primary buttons fall through to the real href.
								if (
									e.metaKey ||
									e.ctrlKey ||
									e.shiftKey ||
									e.altKey ||
									e.button !== 0
								) {
									return;
								}
								e.preventDefault();
								onOpen(toolkit.id);
							}
						: undefined
				}
				className={`group bg-card focus-visible:ring-primary/40 flex h-full w-full min-w-0 flex-col gap-3 overflow-hidden rounded-xl border p-4 text-left transition-all hover:-translate-y-0.5 hover:shadow-sm focus-visible:ring-2 focus-visible:outline-none ${
					disabled
						? 'border-danger/40 hover:border-danger/60'
						: 'border-border/60 hover:border-border hover:bg-muted/30'
				}`}
			>
				<div className="flex items-center gap-3">
					<VendorIcon
						name={toolkit.name}
						size="lg"
						className={disabled ? 'opacity-50 grayscale' : undefined}
					/>
					<div className="min-w-0 flex-1">
						<div className="flex items-center justify-between gap-2">
							<h2 className="font-heading text-foreground truncate text-sm font-semibold">
								{toolkit.name}
							</h2>
							<ChevronRight
								size={16}
								aria-hidden="true"
								className="text-muted-foreground group-hover:text-foreground shrink-0 transition-colors"
							/>
						</div>
						{toolkit.description ? (
							<p className="text-muted-foreground mt-0.5 line-clamp-2 text-xs leading-snug break-words">
								{toolkit.description}
							</p>
						) : null}
					</div>
				</div>

				{disabled || pendingCount > 0 || toolkit.simulate ? (
					<div className="flex flex-wrap items-center gap-1.5">
						{disabled && (
							<span className="bg-danger/10 text-danger border-danger/30 inline-flex items-center gap-1 rounded-full border px-2 py-0.5 font-mono text-[10px]">
								<Ban className="h-3 w-3" aria-hidden="true" />
								SUSPENDED
							</span>
						)}
						{pendingCount > 0 && (
							<span className="bg-warning/10 text-warning border-warning/20 inline-flex items-center gap-1 rounded-full border px-2 py-0.5 font-mono text-[10px]">
								<AlertTriangle className="h-3 w-3" aria-hidden="true" />
								{pendingCount} pending
							</span>
						)}
						{toolkit.simulate && (
							<span className="bg-primary/10 text-primary border-primary/20 inline-flex items-center rounded-full border px-2 py-0.5 font-mono text-[10px]">
								simulate
							</span>
						)}
					</div>
				) : null}

				{apiIds.length > 0 ? (
					<VendorPile
						vendors={apiIds}
						ariaLabel={`Connects ${apiIds.length} API${apiIds.length === 1 ? '' : 's'}: ${apiIds.join(', ')}`}
					/>
				) : null}

				<div className="text-muted-foreground mt-auto flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px]">
					<span className="inline-flex items-center gap-1">
						<Key size={11} aria-hidden="true" />
						{keyCount ?? '—'} key{keyCount === 1 ? '' : 's'}
					</span>
					<span className="inline-flex items-center gap-1">
						<KeyRound size={11} aria-hidden="true" />
						{credentialCount ?? '—'} credential{credentialCount === 1 ? '' : 's'}
					</span>
					{typeof agentCount === 'number' ? (
						<span className="inline-flex items-center gap-1">
							<Bot size={11} aria-hidden="true" />
							{agentCount} agent{agentCount === 1 ? '' : 's'}
						</span>
					) : null}
					{toolkit.created_at ? (
						<span className="ml-auto inline-flex items-center gap-1">
							<Calendar size={11} aria-hidden="true" />
							{timeAgo(toolkit.created_at)}
						</span>
					) : null}
				</div>
			</AppLink>
		</motion.div>
	);
}

/**
 * Card-shaped skeleton that mirrors `ToolkitCard`'s layout exactly so the
 * list doesn't shift when real data resolves. Used by `ToolkitsListSkeleton`.
 */
export function ToolkitCardSkeleton() {
	return (
		<div className="border-border/60 bg-card flex h-full flex-col gap-3 rounded-xl border p-4">
			<div className="flex items-center gap-3">
				<div className="bg-muted h-12 w-12 shrink-0 animate-pulse rounded-xl" />
				<div className="min-w-0 flex-1 space-y-2">
					<div className="bg-muted h-4 w-2/3 animate-pulse rounded" />
					<div className="bg-muted h-3 w-full animate-pulse rounded" />
				</div>
			</div>
			<div className="mt-auto flex items-center gap-3">
				<div className="bg-muted h-3 w-16 animate-pulse rounded" />
				<div className="bg-muted h-3 w-24 animate-pulse rounded" />
			</div>
		</div>
	);
}

/**
 * Six card skeletons in the same responsive grid as the populated list,
 * with a small staggered shimmer delay. Drop-in replacement for a generic
 * spinner so the loading frame matches the loaded frame.
 */
export function ToolkitsListSkeleton({ count = 6 }: { count?: number }) {
	return (
		<div className="grid grid-cols-1 gap-4 md:grid-cols-2" aria-hidden="true">
			{Array.from({ length: count }).map((_, i) => (
				// eslint-disable-next-line react/no-array-index-key -- static placeholders, never reordered
				<div key={`tk-skeleton-${i}`} style={{ animationDelay: `${i * 60}ms` }}>
					<ToolkitCardSkeleton />
				</div>
			))}
		</div>
	);
}
