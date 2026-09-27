/**
 * OverviewStrip — the top "overview" ribbon on the API detail surface.
 *
 * A bordered, muted strip with an
 * optional server-URL header followed by a single flex-wrap row of labelled
 * stats (icon + UPPERCASE label + value) and a right-aligned "Imported X ago".
 * Scoped to jentic-one's revision model — credentials / agents
 * live in other modules, so the stats here are the API-owned facts: operations,
 * revisions, security schemes, and live-revision state.
 */
import { useState } from 'react';
import { Activity, BellOff, GitBranch, RefreshCw, ShieldCheck, Zap } from 'lucide-react';
import { ActorLabel, Badge, Button } from '@/shared/ui';
import { formatAgo, useReimportFromCatalog, useSnoozeCatalogUpdate } from '@/modules/workspace/api';
import { ConfirmDialog } from '@/modules/workspace/components/ConfirmDialog';
import type { ApiKey, WorkspaceApi } from '@/modules/workspace/api';

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

export function OverviewStrip({
	api,
	importedBy,
}: {
	api: WorkspaceApi;
	/** Who submitted the API's first revision, once the full list is known. */
	importedBy?: string | null;
}) {
	const hasLive = api.currentRevisionId !== null;
	const importedAgo = formatAgo(api.createdAt);
	const key: ApiKey = api.api;
	const { reimport, isReimporting } = useReimportFromCatalog(key);
	const { snooze, isSnoozing } = useSnoozeCatalogUpdate(key);
	const [confirmOpen, setConfirmOpen] = useState(false);

	// Re-import adopts the upstream spec. For a catalog-origin API it's a plain
	// re-import. For an overlay-origin API it's now ENABLED but destructive — it
	// deprecates the API's confirmed overlay(s) — so it routes through a confirm
	// dialog first (the "close the overlay-update loop" flow). When `origin` is
	// absent (older backend), we treat it as catalog and re-import directly.
	const isOverlayOrigin = api.origin === 'overlay';
	// The catalog entry is addressed by its `api_id`. For umbrella specs that id is
	// `domain/sub` (e.g. `nytimes.com/article_search`), which the API's vendor alone
	// can't resolve — so prefer the real `catalogApiId` surfaced by the backend and
	// fall back to the vendor only for older backends that don't emit it.
	const catalogApiId = api.catalogApiId ?? api.api.vendor;

	const handleReimportClick = () => {
		if (isOverlayOrigin) {
			setConfirmOpen(true);
		} else {
			reimport(catalogApiId);
		}
	};

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
					<div className="flex shrink-0 items-center gap-2">
						<Button
							variant="ghost"
							size="sm"
							loading={isSnoozing}
							onClick={() => snooze(catalogApiId)}
							data-testid="workspace-snooze"
						>
							<BellOff size={14} aria-hidden="true" />
							Mute
						</Button>
						<Button
							variant="outline"
							size="sm"
							loading={isReimporting}
							onClick={handleReimportClick}
							data-testid="workspace-reimport"
						>
							<RefreshCw size={14} aria-hidden="true" />
							Re-import
						</Button>
					</div>
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
						{importedBy ? (
							<>
								{' '}
								by <ActorLabel actorId={importedBy} className="text-foreground" />
							</>
						) : null}
					</span>
				) : null}
			</div>
			{api.description ? (
				<div className="border-border/30 border-t px-4 py-3">
					<p className="text-muted-foreground text-sm">{api.description}</p>
				</div>
			) : null}

			<ConfirmDialog
				open={confirmOpen}
				title="Re-import & deprecate overlays?"
				body="Re-importing will deprecate the confirmed overlay(s) on this API and adopt the upstream spec. This can't be undone automatically. Proceed?"
				confirmLabel="Re-import & deprecate overlays"
				confirmTestId="workspace-reimport-confirm"
				pending={isReimporting}
				onClose={() => setConfirmOpen(false)}
				onConfirm={() => {
					setConfirmOpen(false);
					reimport(catalogApiId);
				}}
			/>
		</section>
	);
}
