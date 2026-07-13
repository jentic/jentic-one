import { motion } from 'framer-motion';
import { Ban, Calendar, ChevronRight, Key, KeyRound } from 'lucide-react';
import { AppLink } from '@/shared/ui';
import { timeAgo } from '@/modules/toolkits/lib/time';
import { ToolkitGlyph } from '@/modules/toolkits/components/ToolkitGlyph';
import type { Toolkit } from '@/modules/toolkits/api/types';
import { ROUTE_PATHS } from '@/shared/app/routes';

/**
 * Toolkit list-card on `ToolkitsPage`. The whole card is an `AppLink` to the
 * detail route so it stays a single keyboard/AT target. Counts come straight
 * from the list endpoint's roll-ups (`key_count` / `credential_count`); a
 * suspended toolkit (`active === false`) gets a danger treatment + pill.
 */
export const toolkitCardVariants = {
	hidden: { opacity: 0, y: 8 },
	visible: { opacity: 1, y: 0, transition: { duration: 0.2, ease: 'easeOut' } },
} as const;

export interface ToolkitCardProps {
	toolkit: Toolkit;
}

export function ToolkitCard({ toolkit }: ToolkitCardProps) {
	const suspended = !toolkit.active;
	const created = Date.parse(toolkit.created_at);

	return (
		<motion.div variants={toolkitCardVariants}>
			<AppLink
				href={ROUTE_PATHS.toolkit(toolkit.toolkit_id)}
				className={`group bg-card focus-visible:ring-primary/40 flex h-full w-full min-w-0 flex-col gap-3 overflow-hidden rounded-xl border p-4 text-left transition-all hover:-translate-y-0.5 hover:shadow-sm focus-visible:ring-2 focus-visible:outline-none ${
					suspended
						? 'border-danger/40 hover:border-danger/60'
						: 'border-border/60 hover:border-border hover:bg-muted/30'
				}`}
			>
				<div className="flex items-center gap-3">
					<ToolkitGlyph
						name={toolkit.name}
						size="lg"
						className={suspended ? 'opacity-50 grayscale' : undefined}
					/>
					<div className="min-w-0 flex-1">
						<div className="flex items-center gap-2">
							<h2 className="font-heading text-foreground min-w-0 flex-1 truncate text-sm font-semibold">
								{toolkit.name}
							</h2>
							{suspended ? (
								<span className="bg-danger/10 text-danger border-danger/30 inline-flex shrink-0 items-center gap-1 rounded-full border px-2 py-0.5 font-mono text-[10px]">
									<Ban className="h-3 w-3" aria-hidden="true" />
									SUSPENDED
								</span>
							) : null}
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

				<div className="text-muted-foreground mt-auto flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px]">
					<span className="inline-flex items-center gap-1">
						<Key size={11} aria-hidden="true" />
						{toolkit.key_count} key{toolkit.key_count === 1 ? '' : 's'}
					</span>
					<span className="inline-flex items-center gap-1">
						<KeyRound size={11} aria-hidden="true" />
						{toolkit.credential_count} credential
						{toolkit.credential_count === 1 ? '' : 's'}
					</span>
					{Number.isFinite(created) ? (
						<span className="ml-auto inline-flex items-center gap-1">
							<Calendar size={11} aria-hidden="true" />
							{timeAgo(created)}
						</span>
					) : null}
				</div>
			</AppLink>
		</motion.div>
	);
}

/** Card skeleton mirroring `ToolkitCard`'s footprint so the list doesn't shift. */
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

export function ToolkitsListSkeleton({ count = 6 }: { count?: number }) {
	return (
		<div className="grid grid-cols-1 gap-4 md:grid-cols-2" aria-hidden="true">
			{Array.from({ length: count }).map((_, i) => (
				<div key={`tk-skeleton-${i}`} style={{ animationDelay: `${i * 60}ms` }}>
					<ToolkitCardSkeleton />
				</div>
			))}
		</div>
	);
}
