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
import { Activity, GitBranch, RefreshCw, ShieldCheck, Zap } from 'lucide-react';
import { Badge, Button, Tooltip } from '@/shared/ui';
import { useReimportFromCatalog } from '@/modules/workspace/api';
import type { ApiKey, WorkspaceApi } from '@/modules/workspace/api';

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
	const key: ApiKey = api.api;
	const { reimport, isReimporting } = useReimportFromCatalog(key);

	// Re-import is only safe for catalog-origin APIs. Overlay-origin APIs still
	// surface the "Update available" signal, but a silent re-import would clobber
	// confirmed overlays (blocked on an internal issue), so the button is
	// disabled with a tooltip steering the user to resolve overlays first. When
	// `origin` is absent (older backend), we gate conservatively on catalog only.
	const isCatalogOrigin = api.origin === 'catalog';
	// The catalog entry is addressed by its `api_id` (the manifest domain, e.g.
	// `stripe.com`), which for a catalog-origin API equals the API's vendor. The
	// `/apis` payload doesn't expose the catalog `api_id` directly, so we thread
	// the vendor — correct for the common (non-umbrella) case.
	const catalogApiId = api.api.vendor;

	return (
		<section
			className="border-border/60 bg-muted/20 rounded-xl border"
			data-testid="workspace-overview-strip"
		>
			{api.updateAvailable ? (
				<div
					className="border-border/30 flex flex-wrap items-center justify-between gap-3 border-b px-4 py-3"
					data-testid="workspace-update-available"
				>
					<div className="flex items-center gap-2">
						<Badge variant="warning" dot>
							Update available
						</Badge>
						<span className="text-muted-foreground text-xs">
							The upstream spec has changed since this API was imported.
						</span>
					</div>
					{isCatalogOrigin ? (
						<Button
							variant="outline"
							size="sm"
							loading={isReimporting}
							onClick={() => reimport(catalogApiId)}
							data-testid="workspace-reimport"
						>
							<RefreshCw size={14} aria-hidden="true" />
							Re-import
						</Button>
					) : (
						<Tooltip
							content="Resolve overlays first — re-importing would overwrite this API's confirmed overlays."
							interactiveChild
						>
							<Button
								variant="outline"
								size="sm"
								disabled
								data-testid="workspace-reimport"
							>
								<RefreshCw size={14} aria-hidden="true" />
								Re-import
							</Button>
						</Tooltip>
					)}
				</div>
			) : null}
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
