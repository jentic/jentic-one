/**
 * ApiDetailSheet — slide-over detail for a Discover API entity.
 *
 * Two views inside one sheet:
 *   - summary: API identity (vendor icon, name, workspace pill, api id), the
 *     markdown `info.description`, how the API authenticates, and the
 *     filterable operations list; the footer holds the GitHub link and CTA.
 *   - operation: a drill-down for one clicked operation (method/path, summary,
 *     description, parameters + auth tables), reached via the list rows and
 *     dismissed with a Back button.
 *
 * For directory (un-imported) entities the summary view offers a primary
 * "Add to workspace" action. This is a read/peek surface, not a form —
 * there's no draft to preserve. The operations query is
 * keyed by the open entity's catalog id and disabled when the sheet is closed
 * (apiId = null), so closing and reopening a different API refetches cleanly.
 * The selected operation resets whenever the open entity changes.
 */
import type { ReactNode } from 'react';
import { useCallback, useEffect, useId, useMemo, useRef, useState } from 'react';
import { ArrowRight, ChevronLeft, Plus, X } from 'lucide-react';
import {
	AppLink,
	Button,
	CopyButton,
	GitHubMark,
	SheetBody,
	SheetFooter,
	SheetHeader,
	SheetPrimitive,
	VendorIcon,
} from '@/shared/ui';
import { cn } from '@/shared/lib/utils';
import { ApiAuthRow, ApiSummary } from '@/modules/discover/components/ApiSummary';
import { versionLabel } from '@/modules/discover/lib/catalogSpec';
import { OperationPreviewList, opKey } from '@/modules/discover/components/OperationPreviewList';
import { OperationDetail } from '@/modules/discover/components/OperationDetail';
import { useDebouncedValue } from '@/shared/hooks';
import { useOperationPreview, type DiscoveryEntity } from '@/modules/discover/api';
import type { VendorIconFacets } from '@/modules/discover/lib/catalogRelations';

interface ApiDetailSheetProps {
	entity: DiscoveryEntity | null;
	open: boolean;
	onClose: () => void;
	onImport: (entity: DiscoveryEntity) => void;
	importPending: boolean;
	/**
	 * Display title + avatar for the open entity — the SAME derivation the
	 * ledger row uses ({@link entityDisplay}), so a matched workspace API reads
	 * as its humanised name ("GitHub") and shares the row's avatar colour rather
	 * than showing the raw catalog domain. Omitted ⇒ the entity's own `summary`.
	 */
	title?: string;
	icon?: VendorIconFacets;
	/**
	 * Where "Open in your workspace" goes for an imported entry: the matched
	 * API's hub when the registry maps its `catalog_api_id` to exactly one API,
	 * else the workspace view. Omitted ⇒ no link.
	 */
	workspaceHref?: string | null;
}

export function ApiDetailSheet({
	entity,
	open,
	onClose,
	onImport,
	importPending,
	title,
	icon,
	workspaceHref,
}: ApiDetailSheetProps) {
	const titleId = useId();
	const [selectedOp, setSelectedOp] = useState<string | null>(null);
	const backButtonRef = useRef<HTMLButtonElement>(null);
	// The list row that opened the current drill-down, so focus can return to it
	// when the user navigates back (keyboard/SR users don't lose their place).
	const returnFocusRef = useRef<HTMLElement | null>(null);

	// Server-side operation filters (cover the whole spec, not just the loaded
	// page). `search` is debounced into the query `q`; `activeTag` drives `tag`.
	const [search, setSearch] = useState('');
	const [activeTag, setActiveTag] = useState<string | null>(null);
	const debouncedSearch = useDebouncedValue(search, 250);
	// Clearing the field (or the reset on a new entity) applies at once — the
	// lagging debounced value would otherwise query the next API with the old
	// filter for a tick.
	const q = search ? debouncedSearch : '';

	// Preview the catalog entry's operations; disabled while the sheet is closed.
	const previewId = open ? (entity?.apiId ?? null) : null;
	const preview = useOperationPreview(previewId, { q, tag: activeTag });

	const operations = preview.operations;

	// Reset the drill-down + filters whenever the open entity changes or the
	// sheet closes, so reopening a different API lands on a clean summary view.
	useEffect(() => {
		setSelectedOp(null);
		returnFocusRef.current = null;
		setSearch('');
		setActiveTag(null);
	}, [entity?.apiId, open]);

	// Changing the filter re-queries the operation set; the previously selected
	// op may no longer be present, so drop the drill-down back to the list.
	useEffect(() => {
		setSelectedOp(null);
	}, [q, activeTag]);

	const selectedOperation = useMemo(() => {
		if (!selectedOp) return null;
		const idx = operations.findIndex((op, i) => opKey(op, i) === selectedOp);
		return idx >= 0 ? operations[idx] : null;
	}, [operations, selectedOp]);

	// Remember the triggering element when drilling into an operation so we can
	// restore focus to it on the way back.
	const handleSelectOp = useCallback((key: string) => {
		returnFocusRef.current =
			document.activeElement instanceof HTMLElement ? document.activeElement : null;
		setSelectedOp(key);
	}, []);

	const handleBack = useCallback(() => {
		setSelectedOp(null);
	}, []);

	// On entering the operation detail, move focus to the Back button; on
	// returning to the list, restore focus to the row that opened it. Without
	// this the internal view swap drops focus to <body>.
	useEffect(() => {
		if (selectedOperation) {
			backButtonRef.current?.focus();
		} else if (returnFocusRef.current) {
			const el = returnFocusRef.current;
			returnFocusRef.current = null;
			// Defer to let the list re-render before focusing the row.
			requestAnimationFrame(() => {
				if (el.isConnected) el.focus();
			});
		}
	}, [selectedOperation]);

	// "domain · v1.0.0" under the title: the umbrella domain for a sub-API, else
	// the api id's host segment.
	const domain = entity ? (entity.subtitle ?? entity.apiId.split('/')[0]) : '';
	// Title + avatar come resolved from the ledger (a matched workspace API wins);
	// fall back to the entity's own summary when the host renders the sheet alone.
	const displayTitle = title ?? entity?.summary ?? '';
	const iconProps = icon ?? { name: entity?.summary ?? '', vendor: entity?.vendor };
	// A bare-domain entry (`adyen.com`) titles, domains and ids as the same
	// string — so drop the domain from the subtitle when it only repeats the
	// title (the version, if any, still reads), as the ledger row's Vendor column does.
	const subtitleDomain = domain && domain !== displayTitle ? domain : '';
	const inWorkspace = !!entity?.registered;
	// Mid-import the CTA's "Adding…" is the honest state, so the pill waits.
	const updateAvailable = inWorkspace && !!entity?.updateAvailable && !importPending;

	return (
		<SheetPrimitive
			open={open}
			onClose={onClose}
			side="right"
			size="md"
			ariaLabelledBy={titleId}
			className="flex flex-col"
		>
			{entity && (
				<>
					<SheetHeader>
						<VendorIcon {...iconProps} size="lg" />
						<div className="min-w-0 flex-1">
							<h2
								id={titleId}
								className="font-heading truncate text-lg leading-[1.25] font-semibold text-white/92"
							>
								{displayTitle}
							</h2>
							{(subtitleDomain || entity.version) && (
								<p
									className="text-muted-foreground mt-0.5 truncate text-[13.5px]"
									data-testid="sheet-subtitle"
								>
									{subtitleDomain}
									{entity.version && (
										<>
											{subtitleDomain && ' · '}
											<span className="font-mono text-[12.5px]">
												{versionLabel(entity.version)}
											</span>
										</>
									)}
								</p>
							)}
							<div className="mt-2 flex flex-wrap items-center gap-2">
								{updateAvailable ? (
									<SoftPill tone="warning" data-testid="sheet-update-available">
										Update available
									</SoftPill>
								) : (
									inWorkspace && (
										<SoftPill
											tone="success"
											data-testid="sheet-status-imported"
										>
											In your workspace
										</SoftPill>
									)
								)}
								<span className="text-muted-foreground inline-flex min-w-0 items-center gap-1 font-mono text-[11.5px]">
									<span className="truncate">{entity.apiId}</span>
									<CopyButton
										value={entity.apiId}
										variant="ghost"
										size="icon"
										className="hover:bg-tint-2 h-[22px] w-[22px] rounded-[7px] p-0 hover:text-white [&_svg]:h-3 [&_svg]:w-3"
									/>
								</span>
							</div>
						</div>
						{/* Last in the header so Tab reaches the content first; 40px
						    touch target on mobile, the compact 32px from `sm`. */}
						<Button
							variant="ghost"
							size="icon"
							onClick={onClose}
							aria-label="Close"
							className="text-muted-foreground hover:bg-tint-2 -mt-1 -mr-1.5 ml-auto h-10 w-10 shrink-0 rounded-[7px] p-0 hover:text-white sm:h-8 sm:w-8"
							data-testid="api-detail-sheet-close"
						>
							<X className="h-4 w-4" aria-hidden="true" />
						</Button>
					</SheetHeader>

					<SheetBody>
						{selectedOperation ? (
							<>
								<Button
									ref={backButtonRef}
									variant="ghost"
									onClick={handleBack}
									className="text-muted-foreground mb-4 h-auto gap-0.5 p-0 text-xs font-medium hover:bg-transparent hover:text-white active:scale-100"
									data-testid="operation-back"
								>
									<ChevronLeft className="h-3.5 w-3.5" aria-hidden="true" />
									All operations
								</Button>
								<OperationDetail
									operation={selectedOperation}
									securitySchemes={preview.securitySchemes}
								/>
							</>
						) : (
							<>
								<ApiSummary description={preview.info?.description} />
								<ApiAuthRow schemes={preview.securitySchemes} />
								<h3 className="mb-2.5 flex items-baseline gap-2 text-sm font-semibold text-white">
									Operations
									{preview.total > 0 && (
										<span className="text-foreground-faint text-[12.5px] font-normal">
											{preview.total}
										</span>
									)}
								</h3>
								<OperationPreviewList
									operations={operations}
									loading={preview.isPending && previewId != null}
									error={preview.error}
									total={preview.total}
									filter={search}
									onFilterChange={setSearch}
									activeTag={activeTag}
									onTagChange={setActiveTag}
									hasNextPage={preview.hasNextPage}
									isFetchingNextPage={preview.isFetchingNextPage}
									onLoadMore={preview.fetchNextPage}
									onSelect={handleSelectOp}
								/>
							</>
						)}
					</SheetBody>

					<SheetFooter>
						{entity.githubUrl && (
							<AppLink
								href={entity.githubUrl}
								className="text-muted-foreground inline-flex items-center gap-1.5 text-[13.5px] transition-colors hover:text-white"
								aria-label={`View ${displayTitle} on GitHub`}
							>
								<GitHubMark size={14} />
								GitHub
							</AppLink>
						)}
						{entity.registered && workspaceHref && (
							<AppLink
								href={workspaceHref}
								// Several versions ⇒ the link filters the workspace panel
								// on this same page; close so the panel shows.
								onClick={onClose}
								variant="primary"
								className={CTA_CLASSES}
								data-testid="sheet-open-workspace"
							>
								Open in your workspace
								<ArrowRight size={14} aria-hidden="true" />
							</AppLink>
						)}
						{!entity.registered && (
							<Button
								variant="primary"
								loading={importPending}
								onClick={() => onImport(entity)}
								className={cn(
									CTA_CLASSES,
									// In flight: a quiet, non-accent button — the spinner says it all.
									importPending &&
										'bg-surface-quiet-cta text-foreground-lighter hover:bg-surface-quiet-cta disabled:opacity-100',
								)}
								data-testid="sheet-import"
							>
								{!importPending && <Plus size={16} aria-hidden="true" />}
								{importPending ? 'Adding…' : 'Add to workspace'}
							</Button>
						)}
					</SheetFooter>
				</>
			)}
		</SheetPrimitive>
	);
}

/** The sheet's one filled action: 36px, field radius, bold. */
const CTA_CLASSES =
	'rounded-field hover:bg-foreground-lighter h-9 gap-[7px] px-4 py-0 text-[13.5px] font-bold shadow-none';

/** Borderless status pill with a leading dot (workspace relation). */
function SoftPill({
	tone,
	children,
	...rest
}: {
	tone: 'success' | 'warning';
	children: ReactNode;
	'data-testid'?: string;
}) {
	return (
		<span
			className={cn(
				'inline-flex shrink-0 items-center gap-1.5 rounded-full px-2 py-0.5 text-[11.5px] font-bold whitespace-nowrap',
				// An update is a fact to note: a neutral tonal chip, only its dot warm.
				tone === 'success'
					? 'bg-success/10 text-success'
					: 'bg-surface-tonal text-foreground-lighter',
			)}
			{...rest}
		>
			<span
				className={cn(
					'h-1.5 w-1.5 shrink-0 rounded-full',
					tone === 'success' ? 'bg-success' : 'bg-caution',
				)}
				aria-hidden="true"
			/>
			{children}
		</span>
	);
}
