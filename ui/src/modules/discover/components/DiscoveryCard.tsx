/**
 * DiscoveryCard — one API row in the Discover grid.
 *
 * Every row is a public-catalog entry; its `registered` flag drives two visual
 * modes, mirroring jentic-mini's API card:
 *
 *   imported (registered)  — emerald left rail; the surface is a button that
 *                            opens the detail sheet (trailing chevron), plus a
 *                            footer "Open Workspace" link to where the imported
 *                            API now lives.
 *   available (!registered) — no rail; inline "Import" CTA (shared Button) plus
 *                            an optional GitHub link. Clicking the surface also
 *                            opens the sheet so the user can preview operations
 *                            before importing.
 *
 * The import action is the shared `Button`
 * primitive (never a raw styled <button>), and the external GitHub link is the
 * shared `AppLink` (safe new-tab handling).
 */
import { ArrowRight, ChevronRight, ExternalLink, Plus } from 'lucide-react';
import { AppLink, Button, VendorIcon } from '@/shared/ui';
import { ROUTES } from '@/shared/app';
import { CardStatusPill } from '@/modules/discover/components/CardStatusPill';
import type { DiscoveryEntity } from '@/modules/discover/api';

interface DiscoveryCardProps {
	entity: DiscoveryEntity;
	/** True while the detail sheet for this entity is open (highlights border). */
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
	const railClass = registered ? 'border-l-2 border-l-emerald-500/60' : '';
	const activeClass = active
		? 'border-primary/50 bg-card/80'
		: 'hover:border-primary/50 hover:bg-card/80';

	return (
		<div
			data-testid="discovery-card-api"
			data-registered={registered}
			className={`group border-border bg-card relative flex h-full flex-col overflow-hidden rounded-xl border transition-all ${railClass} ${activeClass}`}
		>
			<button
				type="button"
				onClick={() => onOpen(entity)}
				aria-label={`View ${entity.summary}`}
				className="flex w-full flex-1 cursor-pointer items-start gap-4 p-5 text-left"
			>
				<VendorIcon name={entity.summary} vendor={entity.vendor} />

				<div className="flex h-full min-w-0 flex-1 flex-col">
					<h3 className="text-foreground truncate leading-tight font-semibold">
						{entity.summary}
					</h3>
					{entity.subtitle && (
						<p className="text-muted-foreground mt-0.5 truncate text-xs">
							{entity.subtitle}
						</p>
					)}

					<div
						className="mt-auto flex w-full flex-wrap items-center gap-1.5 pt-2.5"
						data-testid="discovery-card-footer"
					>
						<CardStatusPill registered={registered} pending={importPending} />
					</div>
				</div>

				{registered && (
					<ChevronRight
						className="text-muted-foreground h-4 w-4 shrink-0 self-center"
						aria-hidden="true"
					/>
				)}
			</button>

			{registered && (
				<div className="border-border/60 flex items-center justify-end gap-1.5 border-t px-5 py-2.5">
					{/*
					 * Links to the Workspace **list**, not a per-API deep link: a
					 * catalog entry carries no resolved `(vendor, name, version)`
					 * triple, and its `vendor` maps to 0..N workspace rows, so a
					 * precise jump isn't derivable client-side. Deterministic
					 * deep-linking is tracked by backend prerequisite #507.
					 */}
					<AppLink
						href={ROUTES.workspace}
						className="text-primary hover:bg-muted inline-flex items-center gap-1 rounded-lg px-2.5 py-1 text-sm font-medium transition-colors"
						aria-label="Open your workspace"
						data-testid="discovery-card-open-workspace"
					>
						Open Workspace
						<ArrowRight size={14} aria-hidden="true" />
					</AppLink>
				</div>
			)}

			{!registered && (
				<div className="border-border/60 flex items-center justify-end gap-1.5 border-t px-5 py-2.5">
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
				</div>
			)}
		</div>
	);
}
