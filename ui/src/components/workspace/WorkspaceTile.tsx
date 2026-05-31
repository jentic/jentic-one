import {
	ChevronRight,
	KeyRound,
	ListTree,
	AlertTriangle,
	Layers,
	Workflow,
	Zap,
	Calendar,
} from 'lucide-react';
import { VendorIcon } from '@/components/discovery/VendorIcon';
import { timeAgo } from '@/lib/time';

/**
 * Home-tile card used by the Workspace page.
 *
 * Deliberately *not* a `DiscoveryCard`. Workspace is a home surface — the
 * user already owns every tile here, so we don't show "Source" or
 * "Available" pills, and we lean into the meta the user actually cares
 * about: when they last opened it, whether a credential is in place,
 * which toolkits already wrap it, and (for workflows) how many steps it
 * has and which APIs it touches.
 *
 * Visual differences from `DiscoveryCard`:
 *  - Larger vendor icon (lg = 48px vs DiscoveryCard's md = 40px) so the
 *    page reads more "dashboard tile" and less "search result row".
 *  - Single content column with a meta row at the bottom — no inline pills
 *    in the header.
 *  - Workflow tiles surface a small stack of vendor logos for the APIs
 *    they touch, so the user sees Stripe + Zendesk at a glance instead
 *    of "2 APIs".
 *  - Subtle hover lift instead of a coloured rail/border. The page chrome
 *    (sectioned header, stats strip) carries the workspace identity.
 */
export type WorkspaceTileEntity =
	| {
			kind: 'api';
			id: string;
			name: string;
			description?: string;
			hasCredentials: boolean;
			/** Names of toolkits whose bound credentials cover this API. */
			toolkitNames?: string[];
			operationCount?: number;
			credentialCount?: number;
			workflowCount?: number;
			importedAt?: number;
	  }
	| {
			kind: 'workflow';
			id: string;
			slug?: string;
			name: string;
			description?: string;
			stepsCount?: number;
			involvedApis?: string[];
			importedAt?: number;
	  };

export interface WorkspaceTileProps {
	entity: WorkspaceTileEntity;
	/** Click handler — page decides whether to open the sheet or navigate. */
	onOpen: (entity: WorkspaceTileEntity) => void;
}

export function WorkspaceTile({ entity, onOpen }: WorkspaceTileProps) {
	const vendorKey = entity.kind === 'api' ? entity.id : (entity.involvedApis?.[0] ?? entity.id);

	return (
		<button
			type="button"
			onClick={() => onOpen(entity)}
			data-testid={`workspace-tile-${entity.kind}`}
			data-tile-id={entity.id}
			className="group border-border/60 bg-card hover:border-border hover:bg-muted/30 focus-visible:ring-primary/40 flex w-full min-w-0 flex-col gap-3 overflow-hidden rounded-xl border p-4 text-left transition-all hover:-translate-y-0.5 hover:shadow-sm focus-visible:ring-2 focus-visible:outline-none"
		>
			<div className="flex items-center gap-3">
				<VendorIcon name={entity.name} vendor={vendorKey} size="lg" />
				<div className="min-w-0 flex-1">
					<div className="flex items-center justify-between gap-2">
						<h3 className="text-foreground truncate text-sm font-semibold">
							{entity.name}
						</h3>
						<ChevronRight
							size={16}
							aria-hidden="true"
							className="text-muted-foreground group-hover:text-foreground shrink-0 transition-colors"
						/>
					</div>
					{entity.description ? (
						<p className="text-muted-foreground mt-0.5 line-clamp-2 text-xs leading-snug break-words">
							{entity.description}
						</p>
					) : null}
				</div>
			</div>

			{entity.kind === 'workflow' && entity.involvedApis && entity.involvedApis.length > 0 ? (
				<WorkflowVendorPile vendors={entity.involvedApis} />
			) : null}

			<div className="text-muted-foreground mt-auto flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px]">
				{entity.kind === 'api' ? (
					<>
						{entity.operationCount != null && entity.operationCount > 0 && (
							<span className="inline-flex items-center gap-1">
								<Zap size={11} aria-hidden="true" />
								{entity.operationCount} op{entity.operationCount === 1 ? '' : 's'}
							</span>
						)}
						{entity.workflowCount != null && entity.workflowCount > 0 && (
							<span className="inline-flex items-center gap-1">
								<Workflow size={11} aria-hidden="true" />
								{entity.workflowCount} workflow
								{entity.workflowCount === 1 ? '' : 's'}
							</span>
						)}
						{entity.credentialCount != null && entity.credentialCount > 0 ? (
							<span className="inline-flex items-center gap-1">
								<KeyRound size={11} aria-hidden="true" />
								{entity.credentialCount} credential
								{entity.credentialCount === 1 ? '' : 's'}
							</span>
						) : (
							<span className="inline-flex items-center gap-1 text-amber-600 dark:text-amber-400">
								<AlertTriangle size={11} aria-hidden="true" />
								No credential
							</span>
						)}
						{entity.toolkitNames && entity.toolkitNames.length > 0 ? (
							<span className="inline-flex items-center gap-1">
								<Layers size={11} aria-hidden="true" />
								{entity.toolkitNames.length} toolkit
								{entity.toolkitNames.length === 1 ? '' : 's'}
							</span>
						) : null}
						{entity.importedAt ? (
							<span className="ml-auto inline-flex items-center gap-1">
								<Calendar size={11} aria-hidden="true" />
								{timeAgo(entity.importedAt)}
							</span>
						) : null}
					</>
				) : (
					<>
						{typeof entity.stepsCount === 'number' ? (
							<span
								className="inline-flex items-center gap-1"
								data-testid="workspace-tile-steps"
							>
								<ListTree size={11} aria-hidden="true" />
								{entity.stepsCount} step{entity.stepsCount === 1 ? '' : 's'}
							</span>
						) : null}
						{entity.involvedApis && entity.involvedApis.length > 0 ? (
							<span
								className="inline-flex items-center gap-1"
								data-testid="workspace-tile-apis"
							>
								{entity.involvedApis.length} API
								{entity.involvedApis.length === 1 ? '' : 's'}
							</span>
						) : null}
						{entity.importedAt ? (
							<span className="ml-auto inline-flex items-center gap-1">
								<Calendar size={11} aria-hidden="true" />
								{timeAgo(entity.importedAt)}
							</span>
						) : null}
					</>
				)}
			</div>
		</button>
	);
}

/**
 * Compact vendor-logo pile shown on workflow tiles. Renders up to
 * `MAX_VENDORS` icons inline with a `+N` counter for the rest. Uses
 * sm-sized `<VendorIcon>` so the row stays under ~22 px tall and
 * doesn't visually compete with the workflow name.
 */
const MAX_VENDORS = 4;
function WorkflowVendorPile({ vendors }: { vendors: string[] }) {
	const visible = vendors.slice(0, MAX_VENDORS);
	const overflow = vendors.length - visible.length;
	return (
		<div
			className="mt-2 flex items-center gap-1.5"
			data-testid="workspace-tile-vendor-pile"
			aria-label={`Touches ${vendors.length} API${vendors.length === 1 ? '' : 's'}: ${vendors.join(', ')}`}
		>
			{visible.map((v) => (
				<span key={v} title={v} className="inline-flex">
					<VendorIcon name={v} vendor={v} size="sm" />
				</span>
			))}
			{overflow > 0 ? (
				<span className="text-muted-foreground bg-muted/60 inline-flex h-6 min-w-6 items-center justify-center rounded-md px-1 text-[10px] font-medium">
					+{overflow}
				</span>
			) : null}
		</div>
	);
}
