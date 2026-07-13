/**
 * OverviewStrip — the top "overview" ribbon on the API detail surface.
 *
 * Faithful to jentic-mini's `OverviewStrip`: a bordered, muted strip with an
 * optional server-URL header followed by a single flex-wrap row of labelled
 * stats (icon + UPPERCASE label + value) and a right-aligned "Imported X ago".
 * Adapted to jentic-one's revision model — credentials / toolkits / workflows
 * live in other modules, so the stats here are the API-owned facts: operations,
 * revisions, security schemes, and live-revision state.
 */
import { Activity, GitBranch, ShieldCheck, Zap } from 'lucide-react';
import type { WorkspaceApi } from '@/modules/workspace/api';

function relativeTime(iso: string): string | null {
	const ts = Date.parse(iso);
	if (Number.isNaN(ts)) return null;
	const diffMs = Date.now() - ts;
	const sec = Math.round(diffMs / 1000);
	if (sec < 60) return 'just now';
	const min = Math.round(sec / 60);
	if (min < 60) return `${min}m ago`;
	const hr = Math.round(min / 60);
	if (hr < 24) return `${hr}h ago`;
	const day = Math.round(hr / 24);
	if (day < 30) return `${day}d ago`;
	const mo = Math.round(day / 30);
	if (mo < 12) return `${mo}mo ago`;
	return `${Math.round(mo / 12)}y ago`;
}

function MetaItem({
	icon,
	label,
	value,
}: {
	icon: React.ReactNode;
	label: string;
	value: React.ReactNode;
}) {
	return (
		<span className="inline-flex items-baseline gap-2">
			<span className="text-muted-foreground/70 inline-flex items-center gap-1.5 self-center">
				{icon}
				<span className="text-[10px] tracking-wider uppercase">{label}</span>
			</span>
			<span className="text-foreground font-mono text-sm font-medium">{value}</span>
		</span>
	);
}

export function OverviewStrip({ api }: { api: WorkspaceApi }) {
	const hasLive = api.currentRevisionId !== null;
	const importedAgo = relativeTime(api.createdAt);

	return (
		<section
			className="border-border/60 bg-muted/20 rounded-xl border"
			data-testid="workspace-overview-strip"
		>
			{api.api.host ? (
				<div className="border-border/30 border-b px-4 py-3">
					<p className="text-muted-foreground mb-1.5 text-[11px] font-medium tracking-wide uppercase">
						Host
					</p>
					<code className="text-foreground block truncate font-mono text-xs">
						{api.api.host}
					</code>
				</div>
			) : null}
			<div className="text-muted-foreground flex flex-wrap items-center gap-x-6 gap-y-3 px-4 py-3 text-xs">
				<MetaItem
					icon={<Zap size={13} aria-hidden="true" />}
					label="Operations"
					value={api.operationCount.toLocaleString()}
				/>
				<MetaItem
					icon={<GitBranch size={13} aria-hidden="true" />}
					label="Revisions"
					value={api.revisionCount.toLocaleString()}
				/>
				<MetaItem
					icon={<ShieldCheck size={13} aria-hidden="true" />}
					label="Security"
					value={api.securitySchemes.length > 0 ? api.securitySchemes.join(', ') : 'None'}
				/>
				<MetaItem
					icon={<Activity size={13} aria-hidden="true" />}
					label="Live revision"
					value={hasLive ? 'Yes' : 'Draft only'}
				/>
				{importedAgo ? (
					<span className="text-muted-foreground ml-auto text-xs">
						Imported{' '}
						<time dateTime={new Date(api.createdAt).toISOString()}>{importedAgo}</time>
					</span>
				) : null}
			</div>
			{api.description ? (
				<div className="border-border/30 border-t px-4 py-3">
					<p className="text-muted-foreground text-sm">{api.description}</p>
				</div>
			) : null}
		</section>
	);
}
