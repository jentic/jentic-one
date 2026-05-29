import { ExternalLink } from 'lucide-react';
import { StepRow } from './StepRow';
import { AppLink } from '@/components/ui/AppLink';
import { VendorIcon } from '@/components/discovery/VendorIcon';

interface OverviewBodyProps {
	steps: any[];
	involvedApis: string[];
	/** Set of `api_id`s that exist in the user's workspace. */
	workspaceApiIds: Set<string>;
	/** Set of `api_id`s that have at least one credential configured. */
	credentialedApiIds: Set<string>;
	/** Set of `api_id`s that have a leaf entry in the public catalog. */
	catalogApiIds: Set<string>;
}

/**
 * Two-column overview pane for the workflow detail surface. Left
 * column is the ordered step list; right rail shows the APIs the
 * workflow touches.
 *
 * The right rail surfaces *workspace state* per API so the user can
 * see at a glance whether they can actually run this workflow:
 *
 *  - Imported + has credentials → quiet chip, links to the workspace
 *    API view.
 *  - Imported, no credentials   → chip + 'No credentials' badge,
 *    still links to the workspace API view (which has the
 *    "Add credential" affordance).
 *  - Not imported, in catalog   → non-link chip + 'Not in workspace'
 *    badge, with an inline "Open in Discover" affordance so the user
 *    can browse the catalog and import.
 *  - Not imported, not in catalog → non-link chip + 'Not available'
 *    badge, no Discover affordance — opening Discover for an id the
 *    catalog doesn't host lands the user on a sheet that fails to
 *    fetch the spec, so we hide that path.
 */
export function OverviewBody({
	steps,
	involvedApis,
	workspaceApiIds,
	credentialedApiIds,
	catalogApiIds,
}: OverviewBodyProps) {
	return (
		<div className="grid gap-4 md:grid-cols-3" data-testid="workflow-overview">
			<section
				className="border-border/60 bg-card rounded-xl border p-5 md:col-span-2"
				data-testid="workflow-overview-steps-card"
			>
				<div className="flex items-baseline justify-between">
					<h2 className="text-foreground text-sm font-semibold">Operations</h2>
					<span className="text-muted-foreground text-xs">
						{steps.length} step{steps.length === 1 ? '' : 's'}
					</span>
				</div>

				{steps.length === 0 ? (
					<p className="text-muted-foreground mt-4 text-xs italic">
						This workflow has no steps yet.
					</p>
				) : (
					<ol className="mt-4 space-y-2" data-testid="workflow-overview-steps">
						{steps.map((step, i) => (
							<StepRow
								key={step.stepId ?? step.step_id ?? step.id ?? step.name ?? i}
								index={i}
								isLast={i === steps.length - 1}
								step={step}
								involvedApis={involvedApis}
								workspaceApiIds={workspaceApiIds}
							/>
						))}
					</ol>
				)}
			</section>

			<aside className="space-y-4">
				<section
					className="border-border/60 bg-card rounded-xl border p-5"
					data-testid="workflow-overview-apis"
				>
					<div className="flex items-baseline justify-between">
						<h2 className="text-foreground text-sm font-semibold">APIs involved</h2>
						<span className="text-muted-foreground text-xs">{involvedApis.length}</span>
					</div>
					{involvedApis.length === 0 ? (
						<p className="text-muted-foreground mt-3 text-xs italic">
							No APIs declared.
						</p>
					) : (
						<ul className="mt-3 space-y-1.5">
							{involvedApis.map((apiId) => (
								<li key={apiId}>
									<ApiChip
										apiId={apiId}
										imported={workspaceApiIds.has(apiId)}
										credentialed={credentialedApiIds.has(apiId)}
										inCatalog={catalogApiIds.has(apiId)}
									/>
								</li>
							))}
						</ul>
					)}
				</section>
			</aside>
		</div>
	);
}

interface ApiChipProps {
	apiId: string;
	imported: boolean;
	credentialed: boolean;
	inCatalog: boolean;
}

/**
 * Single API chip in the right-rail "APIs involved" list. Render
 * shape depends on whether the API is in the workspace, has
 * credentials, and exists in the public catalog.
 */
function ApiChip({ apiId, imported, credentialed, inCatalog }: ApiChipProps) {
	const showNoCreds = imported && !credentialed;
	const showNotImported = !imported && inCatalog;
	const showUnavailable = !imported && !inCatalog;

	const subline = showUnavailable
		? 'Not available'
		: showNotImported
			? 'Not in workspace'
			: showNoCreds
				? 'No credentials'
				: null;

	const sublineTone = showUnavailable
		? 'text-muted-foreground'
		: showNotImported
			? 'text-warning'
			: 'text-muted-foreground';

	const chipBody = (
		<>
			<VendorIcon name={apiId} vendor={apiId} size="sm" />
			<div className="min-w-0 flex-1">
				<span className="text-foreground group-hover:text-primary block truncate text-xs font-medium">
					{apiId}
				</span>
				{subline && (
					<span className={`mt-0.5 block text-[10px] font-medium ${sublineTone}`}>
						{subline}
					</span>
				)}
			</div>
		</>
	);

	if (imported) {
		return (
			<AppLink
				href={`/workspace/apis/${encodeURIComponent(apiId)}`}
				className="border-border/40 hover:border-primary/40 hover:bg-muted/50 group flex items-center gap-2.5 rounded-lg border p-2 transition-colors"
				data-testid="workflow-overview-api-chip"
				data-api-id={apiId}
				data-imported="true"
				data-credentialed={credentialed ? 'true' : 'false'}
			>
				{chipBody}
			</AppLink>
		);
	}

	// Not imported, not in catalog: no useful destination — render a
	// quiet, non-interactive chip so the user can see the dependency
	// but isn't tempted to click into a broken Discover sheet.
	if (showUnavailable) {
		return (
			<div
				className="border-border/40 bg-muted/30 flex items-center gap-2.5 rounded-lg border p-2"
				data-testid="workflow-overview-api-chip"
				data-api-id={apiId}
				data-imported="false"
				data-in-catalog="false"
			>
				{chipBody}
			</div>
		);
	}

	// Not imported but available in the catalog: render the chip as a
	// non-link so we don't drop the user on a 404 workspace page, but
	// expose a small "Open in Discover" affordance so they can still
	// inspect / import the API.
	return (
		<div
			className="border-warning/30 bg-warning/5 flex items-center gap-2.5 rounded-lg border p-2"
			data-testid="workflow-overview-api-chip"
			data-api-id={apiId}
			data-imported="false"
			data-in-catalog="true"
		>
			{chipBody}
			<AppLink
				href={`/discover?inspect=${encodeURIComponent(apiId)}`}
				className="text-muted-foreground hover:text-primary inline-flex shrink-0 items-center gap-1 text-[10px] font-medium tracking-wide uppercase"
				aria-label={`Open ${apiId} in Discover to import`}
				data-testid="workflow-overview-api-import"
			>
				Discover
				<ExternalLink className="h-3 w-3" />
			</AppLink>
		</div>
	);
}
