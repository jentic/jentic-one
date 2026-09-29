/**
 * WorkspaceSummaryBar — the Library's workspace panel for viewports below
 * `xl`, where the two-column layout collapses. There the docked panel would
 * land BELOW an infinitely-scrolling catalog (6000+ APIs), i.e. unreachable,
 * so instead a one-line summary sits above the catalog:
 *
 *   Your workspace · 6 APIs · ⚠ 5 need attention · Adding 1…
 *
 * Tapping it opens the full panel content in a bottom sheet — the SAME
 * {@link WorkspacePanelBody} / footer the docked card renders, fed by the same
 * {@link WorkspaceDigest}; nothing is fetched or derived here beyond counts.
 *
 * The sheet is a modal dialog (SheetPrimitive): Escape / backdrop close it,
 * focus is trapped inside and returns to the bar on close. It portals above
 * the mobile BottomNavbar and pads for the device's bottom safe area. Any link
 * inside closes it as it navigates.
 */
import { useId, useState, type MouseEvent, type ReactNode } from 'react';
import {
	AlertTriangle,
	CheckCircle2,
	ChevronUp,
	Layers,
	Loader2,
	Maximize2,
	X,
} from 'lucide-react';
import { AppLink, Button, SheetPrimitive } from '@/shared/ui';
import { ROUTES } from '@/shared/app/routes';
import { cn } from '@/shared/lib/utils';
import type { WorkspaceDigest } from '@/modules/discover/api';
import {
	WorkspaceApiCount,
	WorkspacePanelBody,
	WorkspacePanelFooterActions,
	type PendingImport,
} from '@/modules/discover/components/WorkspaceDockPanel';

export interface WorkspaceSummaryBarProps {
	digest: WorkspaceDigest;
	pendingImports: PendingImport[];
	/** Opens the import-your-own-spec dialog. */
	onImportOwn: () => void;
	className?: string;
}

/** Distinct APIs across every attention entry (an API in two entries counts once). */
function attentionCount(digest: WorkspaceDigest): { count: number; atLeast: boolean } {
	const keys = new Set<string>();
	let atLeast = false;
	for (const entry of digest.attention) {
		for (const row of entry.rows) keys.add(row.key);
		if (entry.atLeast) atLeast = true;
	}
	return { count: keys.size, atLeast };
}

function Dot() {
	return (
		<span aria-hidden="true" className="text-muted-foreground/60">
			·
		</span>
	);
}

export function WorkspaceSummaryBar({
	digest,
	pendingImports,
	onImportOwn,
	className,
}: WorkspaceSummaryBarProps) {
	const [open, setOpen] = useState(false);
	const titleId = useId();
	const contentId = useId();

	const apiCount = digest.rows.length;
	const { count: needCount, atLeast } = attentionCount(digest);
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
				className="bg-warning/10 border-warning/30 text-warning inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-medium"
				data-testid="workspace-summary-attention"
			>
				<AlertTriangle className="h-3 w-3" aria-hidden="true" />
				{needCount}
				{atLeast ? '+' : ''} need{needCount === 1 && !atLeast ? 's' : ''} attention
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
					'bg-card hover:bg-muted/40 justify-start gap-2 rounded-xl px-3 py-2.5 text-left font-normal active:scale-100',
					className,
				)}
				data-testid="workspace-summary-bar"
			>
				<Layers className="text-primary h-4 w-4 shrink-0" aria-hidden="true" />
				<span className="flex min-w-0 flex-1 flex-wrap items-center gap-x-1.5 gap-y-1">
					<span className="text-foreground font-medium">Your workspace</span>
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
					{importing > 0 && (
						<>
							<Dot />
							<span
								className="text-primary inline-flex items-center gap-1 text-xs"
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
					<div className="border-border/60 flex items-center justify-between gap-2 border-b px-4 py-3">
						<div className="flex min-w-0 items-baseline gap-2">
							<h2 id={titleId} className="text-foreground text-base font-semibold">
								Your workspace
							</h2>
							<WorkspaceApiCount digest={digest} />
						</div>
						<div className="flex items-center gap-1">
							<AppLink
								href={ROUTES.workspace}
								variant="ghost"
								size="sm"
								className="h-8 w-8 p-0"
								aria-label="Open the full workspace"
								title="Open the full workspace"
							>
								<Maximize2 className="h-4 w-4" aria-hidden="true" />
							</AppLink>
							<Button
								variant="ghost"
								size="icon"
								onClick={() => setOpen(false)}
								className="h-8 w-8 p-0"
								aria-label="Close"
								data-testid="workspace-summary-sheet-close"
							>
								<X className="h-4 w-4" aria-hidden="true" />
							</Button>
						</div>
					</div>
					<div className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-3 py-3">
						<WorkspacePanelBody
							digest={digest}
							pendingImports={pendingImports}
							onImportOwn={importOwn}
						/>
					</div>
					<div className="border-border/60 flex items-center justify-between gap-2 border-t px-4 py-2">
						<WorkspacePanelFooterActions onImportOwn={importOwn} />
					</div>
				</div>
			</SheetPrimitive>
		</>
	);
}
