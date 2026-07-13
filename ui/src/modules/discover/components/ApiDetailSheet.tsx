/**
 * ApiDetailSheet — slide-over detail for a Discover API entity.
 *
 * Two views inside one sheet (jentic-mini parity):
 *   - summary: API identity (vendor icon, name, status pill, github link), the
 *     markdown `info.description`, and the filterable operations list.
 *   - operation: a drill-down for one clicked operation (method/path, summary,
 *     description, parameters + auth tables), reached via the list rows and
 *     dismissed with a Back button.
 *
 * For directory (un-imported) entities the summary view offers a primary
 * "Import to workspace" action. This is a read/peek surface, not a form —
 * there's no draft to preserve. The operations query is
 * keyed by the open entity's catalog id and disabled when the sheet is closed
 * (apiId = null), so closing and reopening a different API refetches cleanly.
 * The selected operation resets whenever the open entity changes.
 */
import { useCallback, useEffect, useId, useMemo, useRef, useState } from 'react';
import { ChevronLeft, ExternalLink, Plus } from 'lucide-react';
import { AppLink, Button, CopyButton, SheetPrimitive, VendorIcon } from '@/shared/ui';
import { ApiSummary } from '@/modules/discover/components/ApiSummary';
import { CardStatusPill } from '@/modules/discover/components/CardStatusPill';
import { OperationPreviewList, opKey } from '@/modules/discover/components/OperationPreviewList';
import { OperationDetail } from '@/modules/discover/components/OperationDetail';
import { useDebouncedValue } from '@/modules/discover/lib/useDebouncedValue';
import { useOperationPreview } from '@/modules/discover/api';
import type { DiscoveryEntity } from '@/modules/discover/api';

interface ApiDetailSheetProps {
	entity: DiscoveryEntity | null;
	open: boolean;
	onClose: () => void;
	onImport: (entity: DiscoveryEntity) => void;
	importPending: boolean;
}

export function ApiDetailSheet({
	entity,
	open,
	onClose,
	onImport,
	importPending,
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

	// Preview the catalog entry's operations; disabled while the sheet is closed.
	const previewId = open ? (entity?.apiId ?? null) : null;
	const preview = useOperationPreview(previewId, { q: debouncedSearch, tag: activeTag });

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
	}, [debouncedSearch, activeTag]);

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

	return (
		<SheetPrimitive
			open={open}
			onClose={onClose}
			side="right"
			ariaLabelledBy={titleId}
			className="flex flex-col"
		>
			{entity && (
				<>
					<header className="border-border flex items-start gap-4 border-b p-5">
						<VendorIcon name={entity.summary} vendor={entity.vendor} size="lg" />
						<div className="min-w-0 flex-1">
							<h2
								id={titleId}
								className="text-foreground truncate text-lg leading-tight font-semibold"
							>
								{entity.summary}
							</h2>
							{entity.subtitle && (
								<p className="text-muted-foreground mt-0.5 truncate text-sm">
									{entity.subtitle}
								</p>
							)}
							<div className="mt-1.5 flex flex-wrap items-center gap-1.5">
								<CardStatusPill
									registered={entity.registered}
									pending={importPending}
								/>
								<span className="text-muted-foreground inline-flex items-center gap-1 font-mono text-xs">
									{entity.apiId}
									<CopyButton value={entity.apiId} />
								</span>
							</div>
						</div>
					</header>

					<div className="min-h-0 flex-1 overflow-y-auto p-5">
						{selectedOperation ? (
							<>
								<button
									ref={backButtonRef}
									type="button"
									onClick={handleBack}
									className="text-muted-foreground hover:text-foreground mb-4 inline-flex items-center gap-0.5 text-xs font-medium transition-colors"
									data-testid="operation-back"
								>
									<ChevronLeft className="h-3.5 w-3.5" aria-hidden="true" />
									All operations
								</button>
								<OperationDetail
									operation={selectedOperation}
									securitySchemes={preview.securitySchemes}
								/>
							</>
						) : (
							<>
								<ApiSummary description={preview.info?.description} />
								<h3 className="text-foreground mb-2 text-sm font-semibold">
									Operations
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
					</div>

					<footer className="border-border flex items-center justify-end gap-2 border-t p-4">
						{entity.githubUrl && (
							<AppLink
								href={entity.githubUrl}
								className="text-muted-foreground hover:text-foreground inline-flex items-center gap-1.5 text-sm"
								aria-label={`View ${entity.summary} on GitHub`}
							>
								<ExternalLink size={14} aria-hidden="true" />
								GitHub
							</AppLink>
						)}
						{!entity.registered && (
							<Button
								variant="primary"
								loading={importPending}
								onClick={() => onImport(entity)}
								data-testid="sheet-import"
							>
								{!importPending && <Plus size={16} aria-hidden="true" />}
								{importPending ? 'Importing…' : 'Import to workspace'}
							</Button>
						)}
					</footer>
				</>
			)}
		</SheetPrimitive>
	);
}
