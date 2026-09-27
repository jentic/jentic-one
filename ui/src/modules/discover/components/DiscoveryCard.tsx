/**
 * DiscoveryCard — one catalog entry as a row in the Catalog list.
 *
 * Rows, like the Workspace list beside it, so the two halves of the APIs
 * surface read as one: icon, title and vendor on the left, status in the
 * middle, the action on the right. The entry's `registered` flag drives the
 * action:
 *
 *   imported (registered)  — emerald left rail and an "In workspace" link to
 *                            where the imported API now lives.
 *   available (!registered) — an inline "Import" CTA (shared Button) plus an
 *                            optional GitHub link.
 *
 * Either way the main area is a button that opens the detail sheet, so the
 * user can preview operations before (or after) importing. The import action
 * is the shared `Button` primitive (never a raw styled <button>), and the
 * external GitHub link is the shared `AppLink` (safe new-tab handling).
 */
import { ArrowRight, ExternalLink, Plus } from 'lucide-react';
import { AppLink, Button, VendorIcon } from '@/shared/ui';
import { ROUTES } from '@/shared/app';
import { CardStatusPill } from '@/modules/discover/components/CardStatusPill';
import type { DiscoveryEntity } from '@/modules/discover/api';

interface DiscoveryCardProps {
	entity: DiscoveryEntity;
	/** True while the detail sheet for this entity is open (highlights the row). */
	active: boolean;
	/** Open the detail sheet for this entity. */
	onOpen: (entity: DiscoveryEntity) => void;
	/** Enqueue a direct import (available entities only). */
	onImport: (entity: DiscoveryEntity) => void;
	/** True while this entity's import is in flight. */
	importPending: boolean;
}

export function DiscoveryCard({
	entity,
	active,
	onOpen,
	onImport,
	importPending,
}: DiscoveryCardProps) {
	const { registered } = entity;
	const railClass = registered ? 'border-l-emerald-500/60' : 'border-l-transparent';

	return (
		<div
			data-testid="discovery-card-api"
			data-registered={registered}
			className={`group flex items-center gap-3 border-l-2 pr-4 transition-colors ${railClass} ${
				active ? 'bg-muted/60' : 'hover:bg-muted/40'
			}`}
		>
			<button
				type="button"
				onClick={() => onOpen(entity)}
				aria-label={`View ${entity.summary}`}
				className="focus-visible:ring-primary/40 flex min-w-0 flex-1 cursor-pointer items-center gap-3 py-3 pl-4 text-left focus-visible:ring-2 focus-visible:outline-none focus-visible:ring-inset"
			>
				<VendorIcon name={entity.summary} vendor={entity.vendor} size="sm" />
				<div className="min-w-0 flex-1">
					<h3 className="text-foreground truncate text-sm font-semibold">
						{entity.summary}
					</h3>
					{entity.subtitle && (
						<p className="text-muted-foreground truncate text-xs">{entity.subtitle}</p>
					)}
				</div>
				<div
					className="hidden shrink-0 items-center gap-1.5 sm:flex"
					data-testid="discovery-card-footer"
				>
					<CardStatusPill
						registered={registered}
						pending={importPending}
						updateAvailable={entity.updateAvailable}
					/>
				</div>
			</button>

			<div className="flex shrink-0 items-center gap-1.5">
				{entity.githubUrl && (
					<AppLink
						href={entity.githubUrl}
						className="text-muted-foreground hover:bg-muted hover:text-foreground inline-flex h-8 w-8 items-center justify-center rounded-lg transition-colors"
						aria-label={`View ${entity.summary} on GitHub`}
						title="View on GitHub"
					>
						<ExternalLink size={14} aria-hidden="true" />
					</AppLink>
				)}
				{registered ? (
					/*
					 * Links to the Workspace **list**, not a per-API deep link: a
					 * catalog entry carries no resolved `(vendor, name, version)`
					 * triple, and its `vendor` maps to 0..N workspace rows, so a
					 * precise jump isn't derivable client-side. Deterministic
					 * deep-linking is tracked by backend prerequisite #507.
					 */
					<AppLink
						href={ROUTES.workspace}
						className="text-primary hover:bg-muted inline-flex items-center gap-1 rounded-lg px-2.5 py-1 text-sm font-medium transition-colors"
						aria-label="Open your workspace"
						data-testid="discovery-card-open-workspace"
					>
						In workspace
						<ArrowRight size={14} aria-hidden="true" />
					</AppLink>
				) : (
					<Button
						variant="primary"
						size="sm"
						loading={importPending}
						onClick={() => onImport(entity)}
						data-testid="discovery-card-import"
					>
						{!importPending && <Plus size={14} aria-hidden="true" />}
						{importPending ? 'Importing…' : 'Import'}
					</Button>
				)}
			</div>
		</div>
	);
}
