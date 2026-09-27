/**
 * ApiDetailPage — a single workspace API's detail view.
 *
 * The overview and the serving-state line (with who last changed it and any
 * overlays waiting for review) sit above three tabs:
 *
 *   - Operations — what the live revision exposes.
 *   - Revisions & overlays — the change history: promote/archive, and the
 *     overlay review queue. Counted by pending overlays, since those are
 *     changes someone is waiting on.
 *   - Access — the credentials the gateway holds for this API (who added
 *     them, which agents each serves) and who has been calling it. The
 *     workspace is shared, so this is the blast radius of changing it.
 *
 * The tab is in the URL (`?tab=`) so a link to an API's overlays lands there.
 *
 * The route carries the `(vendor, name, version)` triple as three path
 * segments (`/workspace/:vendor/:name/:version`, → `/app/workspace/...` in the
 * browser). A malformed token or an unknown API renders a not-found / error
 * state rather than issuing a bad request.
 */
import { useState } from 'react';
import { useNavigate, useParams, useSearchParams } from 'react-router';
import { FileJson } from 'lucide-react';
import {
	PageShell,
	PageHeader,
	BackButton,
	Skeleton,
	ErrorAlert,
	Button,
	CascadeDeleteDialog,
	TabNav,
	VendorIcon,
} from '@/shared/ui';
import type { TabNavOption } from '@/shared/ui';
import { OverviewStrip } from '@/modules/workspace/components/OverviewStrip';
import { OperationsSection } from '@/modules/workspace/components/OperationsSection';
import { RevisionsSection } from '@/modules/workspace/components/RevisionsSection';
import { OverlaysSection } from '@/modules/workspace/components/OverlaysSection';
import { ServingStateStrip } from '@/modules/workspace/components/ServingStateStrip';
import { SpecViewerDialog } from '@/modules/workspace/components/SpecViewerDialog';
import { AccessSection } from '@/modules/workspace/components/AccessSection';
import { ApiActionsMenu } from '@/modules/workspace/components/ApiActionsMenu';
import {
	formatApiKey,
	diffBaseFor,
	importedBy,
	pendingOverlayCount,
	useApiRevisions,
	useOverlays,
	useDeleteApi,
	useWorkspaceApi,
} from '@/modules/workspace/api';
import type { ApiKey, SpecDiffBase } from '@/modules/workspace/api';
import { apiRefDisplayName } from '@/shared/lib';
import { ROUTES } from '@/shared/app/routes';

type DetailTab = 'operations' | 'changes' | 'access';
const DETAIL_TABS: readonly DetailTab[] = ['operations', 'changes', 'access'];

function parseTab(raw: string | null): DetailTab {
	return DETAIL_TABS.includes(raw as DetailTab) ? (raw as DetailTab) : 'operations';
}

/** Build the identity triple from route params, decoding each segment. */
function keyFromParams(params: {
	vendor?: string;
	name?: string;
	version?: string;
}): ApiKey | null {
	const { vendor, name, version } = params;
	if (!vendor || !name || !version) return null;
	try {
		return {
			vendor: decodeURIComponent(vendor),
			name: decodeURIComponent(name),
			version: decodeURIComponent(version),
		};
	} catch {
		return null;
	}
}

export default function ApiDetailPage() {
	const params = useParams<{ vendor: string; name: string; version: string }>();
	const apiKey = keyFromParams(params);
	const query = useWorkspaceApi(apiKey);
	// Shared cache with RevisionsSection — used to pick the header spec
	// viewer's diff base (the revision created just before the live one).
	const revisionsQuery = useApiRevisions(apiKey);
	// Shared cache with OverlaysSection — counts the review queue on its tab.
	const overlaysQuery = useOverlays(apiKey);
	const navigate = useNavigate();
	const [searchParams, setSearchParams] = useSearchParams();
	const tab = parseTab(searchParams.get('tab'));
	const deleteApi = useDeleteApi();
	const [specOpen, setSpecOpen] = useState(false);
	const [deleteOpen, setDeleteOpen] = useState(false);

	if (!apiKey) {
		return (
			<PageShell>
				<BackButton to={ROUTES.workspace} label="All APIs" />
				<ErrorAlert message="That API reference is malformed." />
			</PageShell>
		);
	}

	const api = query.data;

	// The header "View spec" shows the LIVE document, opening in FULL mode (the
	// label promises the raw document); a Diff toggle vs the revision created
	// just before the live one is available when the list carries one.
	const revisions = revisionsQuery.items;
	const live = revisions.find((r) => r.isCurrent) ?? null;
	const liveDiffBase: SpecDiffBase | null = live ? diffBaseFor(live, revisions) : null;

	function selectTab(next: DetailTab) {
		setSearchParams(
			(prev) => {
				const params = new URLSearchParams(prev);
				if (next === 'operations') params.delete('tab');
				else params.set('tab', next);
				return params;
			},
			{ replace: true },
		);
	}

	// Only once the page walks finish: a partial list would undercount (or
	// name the wrong first submitter).
	const revisionsComplete =
		!revisionsQuery.isLoading && !revisionsQuery.isLoadingAll && !revisionsQuery.isError;
	// Only once the page walk finishes: a partial list would undercount.
	const pendingOverlays =
		!overlaysQuery.isLoading && !overlaysQuery.isLoadingAll && !overlaysQuery.isError
			? pendingOverlayCount(overlaysQuery.items)
			: 0;
	const tabs: TabNavOption<DetailTab>[] = [
		{ value: 'operations', label: 'Operations' },
		{
			value: 'changes',
			label: 'Revisions & overlays',
			count: pendingOverlays > 0 ? pendingOverlays : undefined,
		},
		{ value: 'access', label: 'Access' },
	];

	// Route the title through the shared friendly-name rule so a draft-only API
	// (no user-set display_name) reads as its humanised sub-API/vendor name
	// instead of the raw `vendor/name` tuple, matching the workspace tile.
	// `apiRefDisplayName` can return '' for generic/empty identity fields, so
	// chain the same guaranteed non-empty fallback `apiTitle` uses. The
	// early return above guarantees `apiKey.vendor` is a non-empty string (a
	// blank vendor makes `keyFromParams` return null), so it's the final
	// fallback — the title also feeds the VendorIcon name + the Remove/aria
	// labels, so it must never be blank.
	const title =
		apiRefDisplayName({
			displayName: api?.displayName,
			catalogApiId: api?.catalogApiId,
			vendor: apiKey.vendor,
			name: apiKey.name,
		}) || apiKey.vendor;

	return (
		<PageShell>
			<BackButton to={ROUTES.workspace} label="All APIs" />
			<PageHeader
				title={query.isLoading ? 'Loading…' : title}
				subtitle={formatApiKey(apiKey)}
				icon={
					api ? (
						<VendorIcon
							name={title}
							vendor={api.api.host ?? api.api.vendor}
							iconUrl={api.iconUrl}
							size="lg"
						/>
					) : undefined
				}
				actions={
					<>
						<Button
							variant="secondary"
							size="sm"
							onClick={() => setSpecOpen(true)}
							disabled={!api || api.currentRevisionId === null}
							title={
								api && api.currentRevisionId === null
									? 'No live revision — promote a revision to view its spec'
									: undefined
							}
							data-testid="view-spec"
						>
							<FileJson size={14} aria-hidden="true" />
							View spec
						</Button>
						{/* The disabled button is unfocusable, so its title hint is
						    hover-only; mirror it for keyboard/SR users. */}
						{api && api.currentRevisionId === null ? (
							<span className="sr-only">
								View spec is unavailable: no live revision — promote a revision to
								view its spec.
							</span>
						) : null}
						<ApiActionsMenu
							apiId={formatApiKey(apiKey)}
							title={title}
							disabled={!api}
							onRemove={() => setDeleteOpen(true)}
						/>
					</>
				}
			/>

			{query.isLoading ? (
				<div className="space-y-4" aria-busy="true">
					<Skeleton className="h-28 w-full rounded-xl" />
					<Skeleton className="h-64 w-full rounded-xl" />
				</div>
			) : query.isError || !api ? (
				<div className="space-y-3">
					<ErrorAlert
						message={
							query.error instanceof Error
								? query.error
								: 'This API could not be loaded.'
						}
					/>
					<Button variant="secondary" size="sm" onClick={() => query.refetch()}>
						Try again
					</Button>
				</div>
			) : (
				<>
					<OverviewStrip
						api={api}
						importedBy={
							revisionsComplete ? importedBy(revisionsQuery.items) : undefined
						}
					/>
					<ServingStateStrip
						apiKey={apiKey}
						onReviewOverlays={() => selectTab('changes')}
					/>
					<TabNav
						options={tabs}
						value={tab}
						onChange={selectTab}
						ariaLabel="API sections"
						getTabId={(value) => `api-tab-${value}`}
						getControls={(value) => `api-panel-${value}`}
					/>
					<div
						role="tabpanel"
						id={`api-panel-${tab}`}
						aria-labelledby={`api-tab-${tab}`}
						className="space-y-6"
					>
						{tab === 'operations' ? (
							<OperationsSection apiKey={apiKey} totalCount={api.operationCount} />
						) : tab === 'changes' ? (
							<>
								<OverlaysSection
									apiKey={apiKey}
									currentRevisionId={api.currentRevisionId}
								/>
								<RevisionsSection apiKey={apiKey} />
							</>
						) : (
							<AccessSection api={api} />
						)}
					</div>
				</>
			)}

			<SpecViewerDialog
				apiKey={apiKey}
				open={specOpen}
				onClose={() => setSpecOpen(false)}
				revisionLabel="live"
				diffAgainst={liveDiffBase}
				defaultMode="full"
			/>

			<CascadeDeleteDialog
				open={deleteOpen}
				entityType="api"
				entityName={title}
				loading={deleteApi.isPending}
				error={deleteApi.error}
				onClose={() => setDeleteOpen(false)}
				onConfirm={() =>
					deleteApi.mutate(apiKey, {
						onSuccess: () => {
							setDeleteOpen(false);
							navigate(ROUTES.workspace);
						},
					})
				}
			/>
		</PageShell>
	);
}
