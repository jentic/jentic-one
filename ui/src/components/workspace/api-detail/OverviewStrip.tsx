import { KeyRound, Layers, Activity, Zap, Workflow } from 'lucide-react';
import { MetaItem } from './MetaItem';
import { timeAgo } from '@/lib/time';

interface OverviewStripProps {
	servers: Array<{ url: string }>;
	credentialsCount: number;
	toolkitsCount: number;
	operationsCount: number;
	workflowsCount: number;
	lastActivityTs: number | null;
	createdAt?: number;
}

/**
 * Top "overview" strip on the API detail surface: optional server
 * URLs followed by a one-line stats row (credentials / toolkits /
 * operations / workflows / last activity) and a right-aligned
 * "Imported X ago" timestamp.
 */
export function OverviewStrip({
	servers,
	credentialsCount,
	toolkitsCount,
	operationsCount,
	workflowsCount,
	lastActivityTs,
	createdAt,
}: OverviewStripProps) {
	return (
		<section className="border-border/60 bg-muted/20 rounded-xl border">
			{servers.length > 0 && (
				<div className="border-border/30 border-b px-4 py-3">
					<p className="text-muted-foreground mb-1.5 text-[11px] font-medium tracking-wide uppercase">
						Server{servers.length > 1 ? 's' : ''}
					</p>
					<div className="space-y-1">
						{servers.map((s, i) => (
							<code
								key={i}
								className="text-foreground block truncate font-mono text-xs"
							>
								{s.url}
							</code>
						))}
					</div>
				</div>
			)}
			<div className="text-muted-foreground flex flex-wrap items-center gap-x-6 gap-y-3 px-4 py-3 text-xs">
				<MetaItem
					icon={<KeyRound size={13} aria-hidden="true" />}
					label="Credentials"
					value={String(credentialsCount)}
				/>
				<MetaItem
					icon={<Layers size={13} aria-hidden="true" />}
					label="Toolkits"
					value={String(toolkitsCount)}
				/>
				<MetaItem
					icon={<Zap size={13} aria-hidden="true" />}
					label="Operations"
					value={String(operationsCount)}
				/>
				<MetaItem
					icon={<Workflow size={13} aria-hidden="true" />}
					label="Workflows"
					value={String(workflowsCount)}
				/>
				<MetaItem
					icon={<Activity size={13} aria-hidden="true" />}
					label="Last activity"
					value={lastActivityTs ? timeAgo(lastActivityTs) : '—'}
				/>
				{createdAt && (
					<span className="text-muted-foreground ml-auto text-xs">
						Imported{' '}
						<time dateTime={new Date(createdAt * 1000).toISOString()}>
							{timeAgo(createdAt)}
						</time>
					</span>
				)}
			</div>
		</section>
	);
}
