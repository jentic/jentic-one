/**
 * WorkspaceSummaryBar — the Library's workspace panel for viewports below
 * `xl`, where the two-column layout collapses. There the docked panel would
 * land BELOW an infinitely-scrolling catalog (6000+ APIs), i.e. unreachable,
 * so instead a one-line summary sits above the catalog:
 *
 *   Your workspace · 6 APIs · ⚠ 5 need attention · Adding 1…
 *
 * Tapping it opens the full panel content (filterable full API list
 * included) in a bottom sheet — the SAME
 * {@link WorkspacePanelBody} / footer the docked card renders, fed by the same
 * {@link WorkspaceDigest}; nothing is fetched or derived here beyond counts.
 *
 * The sheet is a modal dialog (SheetPrimitive): Escape / backdrop close it,
 * focus is trapped inside and returns to the bar on close. It portals above
 * the mobile BottomNavbar and pads for the device's bottom safe area. Any link
 * inside closes it as it navigates; a "no credential" API closes it and
 * hands over to the host's in-place Add credential flow (like "Import your own
 * API"), rather than stacking a drawer on the sheet.
 */
import { useEffect, useId, useState, type MouseEvent, type ReactNode } from 'react';
import { AlertTriangle, CheckCircle2, ChevronUp, Layers, Loader2, X } from 'lucide-react';
import { Button, SheetBody, SheetFooter, SheetHeader, SheetPrimitive } from '@/shared/ui';
import { cn } from '@/shared/lib/utils';
import type { WorkspaceDigest, WorkspaceDigestRow } from '@/modules/discover/api';
import type { CredentialAddedNotice } from '@/modules/discover/components/usePanelCredentialFlow';
import {
	WorkspaceApiCount,
	WorkspacePanelBody,
	WorkspacePanelFooterActions,
	type PendingImport,
} from '@/modules/discover/components/WorkspaceDockPanel';
import { useWorkspaceListFilter } from '@/modules/discover/lib/workspaceListFilter';

export interface WorkspaceSummaryBarProps {
	digest: WorkspaceDigest;
	pendingImports: PendingImport[];
	/** Opens the import-your-own-spec dialog. */
	onImportOwn: () => void;
	/** Opens the in-place Add credential flow for a "no credential" API. */
	onAddCredential?: (row: WorkspaceDigestRow) => void;
	credentialNotice?: CredentialAddedNotice | null;
	onDismissCredentialNotice?: () => void;
	className?: string;
}

function Dot() {
	return (
		<span aria-hidden="true" className="text-meta-separator">
			·
		</span>
	);
}

export function WorkspaceSummaryBar({
	digest,
	pendingImports,
	onImportOwn,
	onAddCredential,
	credentialNotice,
	onDismissCredentialNotice,
	className,
}: WorkspaceSummaryBarProps) {
	const [open, setOpen] = useState(false);
	const titleId = useId();
	// A filtered workspace link (`/library?status=draft`, e.g. a redirected
	// `/library/workspace?…` or a catalog row's "Open" over several versions)
	// is a request to see the list — open the sheet on it. Keyed on the values,
	// so closing the sheet with a filter applied keeps it closed.
	const listFilter = useWorkspaceListFilter();
	const filterKey = listFilter.active ? `${listFilter.q}\n${listFilter.status}` : null;
	useEffect(() => {
		if (filterKey != null) setOpen(true);
	}, [filterKey]);
	const contentId = useId();

	const apiCount = digest.rows.length;
	// The same count the sheet's "Needs attention · N" heading shows: one per
	// attention item, never a different tally of the APIs behind them.
	const needCount = digest.attention.length;
	const importing = pendingImports.length;

	// Any link inside the sheet navigates away — close as it goes.
	function closeOnLink(e: MouseEvent<HTMLDivElement>) {
		if ((e.target as Element).closest('a[href]')) setOpen(false);
	}

	function importOwn() {
		// Hand over to the import dialog rather than stacking it on the sheet.
		setOpen(false);
		onImportOwn();
	}

	const addCredential = onAddCredential
		? (row: WorkspaceDigestRow) => {
				// Same handoff: close the sheet, then open the flow's drawer.
				setOpen(false);
				onAddCredential(row);
			}
		: undefined;

	let status: ReactNode = null;
	if (digest.error && !digest.complete) {
		status = <span className="text-muted-foreground">Couldn't load</span>;
	} else if (digest.isPending) {
		status = <span className="text-muted-foreground">Loading…</span>;
	} else if (digest.complete && apiCount === 0 && importing === 0) {
		status = <span className="text-muted-foreground">Empty — add an API</span>;
	} else if (needCount > 0) {
		status = (
			<span
				className="text-foreground-lighter inline-flex items-center gap-1 font-semibold"
				data-testid="workspace-summary-attention"
			>
				<AlertTriangle className="text-warning h-3.5 w-3.5" aria-hidden="true" />
				{needCount} need{needCount === 1 ? 's' : ''} attention
			</span>
		);
	} else if (digest.attentionComplete) {
		status = (
			<span
				className="text-muted-foreground inline-flex items-center gap-1"
				data-testid="workspace-summary-all-good"
			>
				<CheckCircle2 className="text-success h-3.5 w-3.5" aria-hidden="true" />
				All good
			</span>
		);
	}

	return (
		<>
			<Button
				variant="secondary"
				fullWidth
				onClick={() => setOpen(true)}
				aria-haspopup="dialog"
				aria-expanded={open}
				aria-controls={open ? contentId : undefined}
				className={cn(
					'bg-surface-1 hover:bg-surface-1-hover rounded-panel justify-start gap-2.5 border-0 px-4 py-3 text-left text-[13px] font-normal active:scale-100',
					className,
				)}
				data-testid="workspace-summary-bar"
			>
				<Layers className="text-foreground-sub h-4 w-4 shrink-0" aria-hidden="true" />
				<span className="flex min-w-0 flex-1 flex-wrap items-center gap-x-1.5 gap-y-1">
					<span className="font-heading text-foreground text-[14px] font-bold">
						Your workspace
					</span>
					{digest.complete && apiCount > 0 && (
						<>
							<Dot />
							<span className="text-muted-foreground">
								{apiCount} API{apiCount === 1 ? '' : 's'}
							</span>
						</>
					)}
					{status && (
						<>
							<Dot />
							{status}
						</>
					)}
					{credentialNotice && (
						<>
							<Dot />
							<span
								className="text-success inline-flex min-w-0 items-center gap-1 text-xs"
								data-testid="workspace-summary-credential-added"
							>
								<CheckCircle2 className="h-3 w-3 shrink-0" aria-hidden="true" />
								<span className="truncate">
									Credential added for {credentialNotice.label}
								</span>
							</span>
						</>
					)}
					{importing > 0 && (
						<>
							<Dot />
							<span
								className="text-primary inline-flex items-center gap-1 text-xs font-semibold"
								data-testid="workspace-summary-importing"
							>
								<Loader2 className="h-3 w-3 animate-spin" aria-hidden="true" />
								Adding {importing}…
							</span>
						</>
					)}
				</span>
				<ChevronUp className="text-muted-foreground h-4 w-4 shrink-0" aria-hidden="true" />
			</Button>

			<SheetPrimitive
				open={open}
				onClose={() => setOpen(false)}
				side="bottom"
				ariaLabelledBy={titleId}
				className="pb-[env(safe-area-inset-bottom)]"
			>
				<div
					id={contentId}
					className="flex min-h-0 flex-1 flex-col"
					onClickCapture={closeOnLink}
					data-testid="workspace-summary-sheet"
				>
					<SheetHeader className="items-center justify-between gap-2">
						<div className="flex min-w-0 items-baseline gap-2">
							<h2
								id={titleId}
								className="font-heading text-foreground text-[15.5px] font-bold"
							>
								Your workspace
							</h2>
							<WorkspaceApiCount digest={digest} />
						</div>
						<div className="flex items-center gap-1">
							<Button
								variant="ghost"
								size="icon-xs"
								onClick={() => setOpen(false)}
								className="h-8 w-8"
								aria-label="Close"
								data-testid="workspace-summary-sheet-close"
							>
								<X className="h-4 w-4" aria-hidden="true" />
							</Button>
						</div>
					</SheetHeader>
					<SheetBody
						className="overscroll-contain pt-0"
						data-testid="workspace-panel-scroll"
					>
						<WorkspacePanelBody
							digest={digest}
							onImportOwn={importOwn}
							onAddCredential={addCredential}
							credentialNotice={credentialNotice}
							onDismissCredentialNotice={onDismissCredentialNotice}
						/>
					</SheetBody>
					<SheetFooter className="justify-start">
						<WorkspacePanelFooterActions onImportOwn={importOwn} />
					</SheetFooter>
				</div>
			</SheetPrimitive>
		</>
	);
}
