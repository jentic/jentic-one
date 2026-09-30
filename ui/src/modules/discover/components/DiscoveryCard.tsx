/**
 * DiscoveryCard — one compact API tile in the Library catalog grid.
 *
 * Built from list data and already-loaded shared reads only (no per-tile
 * requests):
 *
 *   title row  vendor mark, friendly title, status marker (In your workspace /
 *              Available / Adding…), chevron on imported tiles. With exactly
 *              one workspace match the title and mark are that API's
 *              (`workspaceApiTitle` + `vendorIconPropsFor`, as in the panel,
 *              workspace tiles and hub), so one API never reads as two;
 *              otherwise the catalog entry's own.
 *   subtitle   `vendor · version` (version from `parseCatalogSpecUrl`, omitted
 *              when it doesn't parse). A matched tile keeps the catalog
 *              domain here when the workspace title doesn't already say it.
 *   state line (imported, exactly one workspace match) Live/Draft · ops ·
 *              agents from that API, plus Update available — the shared
 *              `ApiStateBadge` vocabulary.
 *   chips      (not imported) "Credential ready" — you already have an active
 *              credential that would cover this entry once imported (the
 *              broker's own scope rule, or one created for this exact
 *              entry). Links to the credential list on the Agents page. Match
 *              rule: `credentialsCoveringEntry` in `lib/catalogRelations`.
 *   actions    slim row pinned to the bottom so rows line up: Review update
 *              (left) + Open →, or GitHub (secondary icon) + Add to workspace.
 *
 * The whole tile opens the preview sheet via a stretched transparent button,
 * so chips/actions can hold real links without nesting them in a <button>.
 */
import {
	ArrowRight,
	ChevronRight,
	ExternalLink,
	KeyRound,
	Plus,
	RefreshCw,
	Zap,
} from 'lucide-react';
import { memo, type ReactNode } from 'react';
import { ApiStateBadge, AppLink, Button, VendorIcon, apiServingState } from '@/shared/ui';
import { ROUTE_PATHS } from '@/shared/app';
import { vendorIconPropsFor } from '@/shared/lib';
import type { Credential } from '@/shared/credentials/api';
import { CardStatusPill } from '@/modules/discover/components/CardStatusPill';
import { versionLabel } from '@/modules/discover/lib/catalogSpec';
import { workspaceHrefFor } from '@/modules/discover/lib/catalogRelations';
import type { DiscoveryEntity, WorkspaceDigestRow } from '@/modules/discover/api';

interface DiscoveryCardProps {
	entity: DiscoveryEntity;
	/** True while the detail sheet for this entity is open (highlights border). */
	active: boolean;
	/** Open the detail sheet for this entity. */
	onOpen: (entity: DiscoveryEntity) => void;
	/** Enqueue a direct add-to-workspace (available entities only). */
	onImport: (entity: DiscoveryEntity) => void;
	/** True while this entity's add-to-workspace job is in flight. */
	importPending: boolean;
	/** Workspace API(s) imported from this entry (`catalog_api_id`). Exactly one ⇒ its facts + hub link. */
	workspaceMatches?: WorkspaceDigestRow[];
	/** Agents with access to that single match; null while loading / unknowable. */
	matchAgentCount?: number | null;
	/** `matchAgentCount` is a floor (more bound agents than one read page) — "N+". */
	matchAgentsAtLeast?: boolean;
	/** Active credentials that would cover this entry once imported; null while loading. Not-imported only. */
	readyCredentials?: Credential[] | null;
}

const CHIP =
	'pointer-events-auto relative z-10 inline-flex max-w-full min-w-0 items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium ring-1 transition-colors focus-visible:ring-2 focus-visible:outline-none';

const CREDENTIAL_READY_HINT = 'You already have a credential that covers this API';

function Sep() {
	return (
		<span aria-hidden="true" className="text-muted-foreground/50">
			·
		</span>
	);
}

function Chip({
	href,
	icon,
	tone,
	testId,
	children,
	extra,
}: {
	href: string;
	icon: ReactNode;
	tone: string;
	testId: string;
	children: ReactNode;
	extra?: Record<string, string>;
}) {
	return (
		<AppLink href={href} className={`${CHIP} ${tone}`} data-testid={testId} {...extra}>
			<span className="shrink-0" aria-hidden="true">
				{icon}
			</span>
			<span className="truncate">{children}</span>
		</AppLink>
	);
}

/** Memoised: the catalog re-renders on every search keystroke; a tile only when its own props change. */
export const DiscoveryCard = memo(function DiscoveryCard({
	entity,
	active,
	onOpen,
	onImport,
	importPending,
	workspaceMatches = [],
	matchAgentCount = null,
	matchAgentsAtLeast = false,
	readyCredentials,
}: DiscoveryCardProps) {
	const { registered } = entity;
	const match = workspaceMatches.length === 1 ? workspaceMatches[0] : null;
	const title = match?.title ?? entity.summary;
	const icon = match
		? vendorIconPropsFor({
				title: match.title,
				host: match.host,
				vendor: match.ref.vendor,
				iconUrl: match.iconUrl,
			})
		: { name: entity.summary, vendor: entity.vendor };
	const subtitle =
		entity.subtitle ?? (match && entity.vendor !== title ? entity.vendor : undefined);
	const openHref = workspaceHrefFor(workspaceMatches);
	const credentials = !registered && readyCredentials?.length ? readyCredentials : null;
	const showUpdate = registered && entity.updateAvailable && !importPending;
	const showStateLine = registered && (match != null || showUpdate);

	const railClass = registered ? 'border-l-2 border-l-emerald-500/60' : '';
	const stateClass = active
		? 'border-primary/60 bg-card/80 ring-1 ring-primary/30'
		: 'hover:border-primary/40 hover:bg-card/80 hover:shadow-md hover:shadow-black/20';

	return (
		<div
			data-testid="discovery-card-api"
			data-registered={registered}
			className={`group border-border bg-card relative flex h-full flex-col overflow-hidden rounded-xl border transition-all duration-150 ${railClass} ${stateClass}`}
		>
			{/* Stretched surface: the whole tile opens the preview sheet. */}
			<button
				type="button"
				onClick={() => onOpen(entity)}
				aria-label={`View ${title}`}
				className="focus-visible:ring-primary/50 absolute inset-0 z-0 cursor-pointer rounded-xl focus-visible:ring-2 focus-visible:outline-none focus-visible:ring-inset"
			/>

			<div className="pointer-events-none relative flex flex-1 flex-col gap-2 px-3.5 pt-3 pb-2.5">
				<div className="flex items-start gap-3">
					<VendorIcon {...icon} />
					<div className="min-w-0 flex-1">
						<div className="flex min-w-0 items-center gap-2">
							<h3 className="text-foreground min-w-0 flex-1 truncate text-sm leading-tight font-semibold">
								{title}
							</h3>
							<span
								className="flex shrink-0 items-center"
								data-testid="discovery-card-footer"
							>
								<CardStatusPill registered={registered} pending={importPending} />
							</span>
							{registered && (
								<ChevronRight
									className="text-muted-foreground group-hover:text-foreground h-4 w-4 shrink-0 transition-colors"
									aria-hidden="true"
								/>
							)}
						</div>
						<p
							className="text-muted-foreground mt-1 flex min-w-0 items-center gap-1.5 text-xs"
							data-testid="discovery-card-subtitle"
						>
							{subtitle && <span className="truncate">{subtitle}</span>}
							{subtitle && entity.version && <Sep />}
							{entity.version && (
								<span
									className="shrink-0 font-mono text-[11px]"
									title="Spec version in the public catalog"
									data-testid="discovery-card-version"
								>
									{versionLabel(entity.version)}
								</span>
							)}
						</p>
					</div>
				</div>

				{showStateLine && (
					<div
						className="text-muted-foreground flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px]"
						data-testid="discovery-card-workspace-state"
					>
						{match && (
							<>
								<ApiStateBadge
									state={apiServingState(match).serving}
									className="px-1.5 py-0 text-[10px]"
								/>
								<Sep />
								<span className="inline-flex items-center gap-1">
									<Zap size={11} aria-hidden="true" />
									{match.operationCount} op{match.operationCount === 1 ? '' : 's'}
								</span>
								{matchAgentCount != null && matchAgentCount > 0 && (
									<>
										<Sep />
										<span data-testid="discovery-card-agents">
											{matchAgentsAtLeast
												? `${matchAgentCount}+ agents`
												: `${matchAgentCount} agent${matchAgentCount === 1 ? '' : 's'}`}
										</span>
									</>
								)}
							</>
						)}
						{showUpdate && (
							<ApiStateBadge state="update" className="px-1.5 py-0 text-[10px]" />
						)}
					</div>
				)}

				{credentials && (
					<div
						className="flex flex-wrap items-center gap-1.5"
						data-testid="discovery-card-notes"
					>
						<Chip
							href={ROUTE_PATHS.credentialInventory()}
							icon={<KeyRound size={11} />}
							tone="bg-success/10 text-success ring-success/30 hover:bg-success/20"
							testId="discovery-card-credential-ready"
							extra={{
								title: `${CREDENTIAL_READY_HINT}: ${credentials.map((c) => c.name).join(', ')}`,
								// The tooltip isn't announced; say what the chip means and where it goes.
								'aria-label': `Credential ready. ${CREDENTIAL_READY_HINT}. Opens the Credentials list on the Agents page.`,
							}}
						>
							Credential ready
						</Chip>
					</div>
				)}
			</div>

			<div className="border-border/60 relative z-10 flex items-center justify-end gap-1.5 border-t px-3 py-1.5">
				{registered ? (
					<>
						{entity.updateAvailable && match && (
							<AppLink
								href={match.href}
								className="text-warning hover:bg-muted mr-auto inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs font-medium transition-colors"
								data-testid="discovery-card-review-update"
							>
								<RefreshCw size={12} aria-hidden="true" />
								Review update
							</AppLink>
						)}
						<AppLink
							href={openHref}
							className="text-primary hover:bg-muted inline-flex h-7 items-center gap-1 rounded-md px-2 text-xs font-medium transition-colors"
							aria-label={
								match
									? `Open ${match.title} in your workspace`
									: 'Open your workspace'
							}
							data-testid="discovery-card-open-workspace"
						>
							{match ? 'Open' : 'Open your workspace'}
							<ArrowRight size={13} aria-hidden="true" />
						</AppLink>
					</>
				) : (
					<>
						{entity.githubUrl && (
							<AppLink
								href={entity.githubUrl}
								className="border-border text-muted-foreground hover:bg-muted hover:text-foreground inline-flex h-7 w-7 items-center justify-center rounded-md border transition-colors"
								aria-label={`View ${title} on GitHub`}
								title="View on GitHub"
							>
								<ExternalLink size={13} aria-hidden="true" />
							</AppLink>
						)}
						<Button
							variant="primary"
							size="sm"
							className="h-7 px-2.5 text-xs"
							loading={importPending}
							onClick={() => onImport(entity)}
							data-testid="discovery-card-import"
						>
							{!importPending && <Plus size={13} aria-hidden="true" />}
							{importPending ? 'Adding…' : 'Add to workspace'}
						</Button>
					</>
				)}
			</div>
		</div>
	);
});
