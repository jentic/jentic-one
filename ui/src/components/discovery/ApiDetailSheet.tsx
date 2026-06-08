/**
 * ApiDetailSheet
 *
 * Right-side slide-out panel for the Discover surface. Shows a unified
 * preview body regardless of whether the API is imported (workspace) or
 * catalog-only (directory).
 *
 * Two URL params drive it (owned by `DiscoverPage`):
 *   ?inspect=<api_id>      → sheet open on this API's summary view
 *   ?inspect=<api_id>&op=<capability_id>
 *                          → sheet open on the operation drill-down view
 */

import { useQuery } from '@tanstack/react-query';
import { InspectPanel } from './InspectPanel';
import { SheetHeader } from './ApiDetailSheetHeader';
import { SheetBody } from './SheetBody';
import { DirectoryInspectPanel } from './DirectoryBody';
import { WorkflowInspectPanel } from './WorkflowInspectPanel';
import type { DiscoveryEntity } from './DiscoveryCard';
import { SheetPrimitive } from '@/components/ui/SheetPrimitive';
import { isDirectoryOpKey } from '@/lib/directoryOpKey';
import { api } from '@/api/client';

export interface ApiDetailSheetProps {
	/** The api_id currently displayed. Null = sheet has no content. */
	apiId: string | null;
	/** Drives the SheetPrimitive open/close animation. */
	open: boolean;
	/** If set, render the operation drill-down view instead of the summary. */
	inspectedOp: string | null;
	/** If set, render the workflow drill-down view (Phase 2). */
	inspectedWf: string | null;
	/**
	 * Optional cached entity from the discover list — lets the header
	 * render instantly with name/source info instead of waiting
	 * for the operations query to come back.
	 */
	initialEntity?: DiscoveryEntity;
	onClose: () => void;
	/** After the closing animation completes — parent uses this to drop the
	 *  sticky apiId so the next open isn't stuck rendering stale data. */
	onAfterClose?: () => void;
	/** Push/replace `?op=` in the URL. Pass null to clear. */
	onSelectOp: (opId: string | null) => void;
	/** Push/replace `?wf=` in the URL. Pass null to clear. */
	onSelectWf: (wfId: string | null) => void;
	/** Switch the sheet to a different api_id from the recents strip. */
	onSelectApi?: (apiId: string) => void;
}

export function ApiDetailSheet({
	apiId,
	open,
	inspectedOp,
	inspectedWf,
	initialEntity,
	onClose,
	onAfterClose,
	onSelectOp,
	onSelectWf,
	onSelectApi,
}: ApiDetailSheetProps) {
	return (
		<SheetPrimitive
			open={open}
			onClose={onClose}
			onAfterClose={onAfterClose}
			side="right"
			ariaLabelledBy="api-detail-title"
		>
			{apiId && (
				<ApiDetailSheetContent
					apiId={apiId}
					inspectedOp={inspectedOp}
					inspectedWf={inspectedWf}
					initialEntity={initialEntity}
					onClose={onClose}
					onSelectOp={onSelectOp}
					onSelectWf={onSelectWf}
					onSelectApi={onSelectApi}
				/>
			)}
		</SheetPrimitive>
	);
}

// ── Content ───────────────────────────────────────────────────────────────────

interface ContentProps {
	apiId: string;
	inspectedOp: string | null;
	inspectedWf: string | null;
	initialEntity?: DiscoveryEntity;
	onClose: () => void;
	onSelectOp: (opId: string | null) => void;
	onSelectWf: (wfId: string | null) => void;
	onSelectApi?: (apiId: string) => void;
}

function ApiDetailSheetContent({
	apiId,
	inspectedOp,
	inspectedWf,
	initialEntity,
	onClose,
	onSelectOp,
	onSelectWf,
	onSelectApi,
}: ContentProps) {
	// Resolve source: trust initialEntity if present, otherwise ask the server.
	// ALWAYS query when the initial source is 'directory' — after import the
	// initialEntity prop may be stale (parent list hasn't re-rendered yet).
	const shouldResolve = !initialEntity?.source || initialEntity.source === 'directory';
	const { data: resolvedApi } = useQuery({
		queryKey: ['sheet-resolve-source', apiId],
		// `q=` is a substring filter, so a leaf like `slack.com` matches
		// `slack.com/openai` etc. We need enough rows in the response to
		// guarantee the exact id (if present) lands on the first page —
		// otherwise the workspace check below false-negatives. limit=20
		// is cheap and covers every realistic vendor sub-API count.
		queryFn: () => api.listApis(1, 20, 'local', apiId),
		enabled: shouldResolve,
		staleTime: 10_000,
	});

	const source: 'workspace' | 'directory' = (() => {
		if (shouldResolve && resolvedApi) {
			// `q=` is a substring filter on the backend, so a query for
			// the catalog leaf `slack.com` will return the locally-imported
			// sub-api `slack.com/openai` and trigger a false-positive
			// "imported" badge in the sheet header. Pin the resolution to
			// an exact-id match so siblings can't bleed across the
			// workspace/directory boundary.
			const rows = ((resolvedApi as { data?: Array<{ id?: string }> } | undefined)?.data ??
				[]) as Array<{ id?: string }>;
			const exact = rows.some((r) => r?.id === apiId);
			return exact ? 'workspace' : 'directory';
		}
		if (initialEntity?.source === 'workspace') return 'workspace';
		return 'directory';
	})();

	const sourceResolving = shouldResolve && !resolvedApi;

	if (inspectedOp) {
		const isDirectory = isDirectoryOpKey(inspectedOp);
		return (
			<div className="flex h-full flex-col">
				<SheetHeader
					title={initialEntity?.summary ?? apiId}
					apiId={apiId}
					source={source}
					onClose={onClose}
					onBack={() => onSelectOp(null)}
				/>
				<div className="flex-1 overflow-y-auto">
					{isDirectory ? (
						<DirectoryInspectPanel
							apiId={apiId}
							opKey={inspectedOp}
							onClose={() => onSelectOp(null)}
							specUrl={initialEntity?.specUrl ?? initialEntity?.raw?.spec_url}
							source={source}
							sourceResolving={sourceResolving}
						/>
					) : (
						<InspectPanel
							capabilityId={inspectedOp}
							onClose={() => onSelectOp(null)}
							variant="sheet"
						/>
					)}
				</div>
			</div>
		);
	}

	if (inspectedWf) {
		return (
			<div className="flex h-full flex-col">
				<SheetHeader
					title={initialEntity?.summary ?? apiId}
					apiId={apiId}
					source={source}
					onClose={onClose}
					onBack={() => onSelectWf(null)}
				/>
				<div className="flex-1 overflow-y-auto">
					<WorkflowInspectPanel
						apiId={apiId}
						workflowId={inspectedWf}
						onClose={() => onSelectWf(null)}
						source={source}
						sourceResolving={sourceResolving}
					/>
				</div>
			</div>
		);
	}

	return (
		<div className="flex h-full flex-col">
			<SheetHeader
				title={initialEntity?.summary ?? apiId}
				apiId={apiId}
				source={source}
				hasWorkflows={initialEntity?.hasWorkflows}
				onClose={onClose}
				onSelectApi={onSelectApi}
			/>
			<div className="flex-1 overflow-y-auto">
				<SheetBody
					apiId={apiId}
					initialEntity={initialEntity}
					source={source}
					sourceResolving={sourceResolving}
					onSelectOp={onSelectOp}
					onSelectWf={onSelectWf}
				/>
			</div>
		</div>
	);
}
