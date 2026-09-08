/**
 * ApiDetailPage — a single workspace API's detail view.
 *
 * Covers the API-only surface
 * jentic-one's registry exposes: overview, operations (current revision), and
 * revision history with promote/archive. Credentials, toolkits, and workflows
 * belong to other modules and are out of
 * scope here.
 *
 * The route carries the `(vendor, name, version)` triple as three path
 * segments (`/workspace/:vendor/:name/:version`, → `/app/workspace/...` in the
 * browser). A malformed token or an unknown API renders a not-found / error
 * state rather than issuing a bad request.
 */
import { useState } from 'react';
import { useNavigate, useParams } from 'react-router';
import { FileJson, Trash2 } from 'lucide-react';
import {
	PageShell,
	PageHeader,
	BackButton,
	Skeleton,
	ErrorAlert,
	Button,
	CascadeDeleteDialog,
	CopyButton,
	VendorIcon,
} from '@/shared/ui';
import { OverviewStrip } from '@/modules/workspace/components/OverviewStrip';
import { OperationsSection } from '@/modules/workspace/components/OperationsSection';
import { RevisionsSection } from '@/modules/workspace/components/RevisionsSection';
import { OverlaysSection } from '@/modules/workspace/components/OverlaysSection';
import { ServingStateStrip } from '@/modules/workspace/components/ServingStateStrip';
import { SpecViewerDialog } from '@/modules/workspace/components/SpecViewerDialog';
import {
	formatApiKey,
	diffBaseFor,
	useApiRevisions,
	useDeleteApi,
	useWorkspaceApi,
} from '@/modules/workspace/api';
import type { ApiKey, SpecDiffBase } from '@/modules/workspace/api';
import { apiRefDisplayName } from '@/shared/lib';
import { ROUTES } from '@/shared/app/routes';

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
	const navigate = useNavigate();
	const deleteApi = useDeleteApi();
	const [specOpen, setSpecOpen] = useState(false);
	const [deleteOpen, setDeleteOpen] = useState(false);

	if (!apiKey) {
		return (
			<PageShell>
				<BackButton to={ROUTES.workspace} label="Back to Workspace" />
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

	// Route the title through the shared friendly-name rule so a draft-only API
	// (no user-set display_name) reads as its humanised sub-API/vendor name
	// instead of the raw `vendor/name` tuple, matching the workspace tile.
	// `apiRefDisplayName` can return '' for generic/empty identity fields, so
	// chain the same guaranteed non-empty fallback `ApiCard.titleFor` uses. The
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
						<CopyButton value={formatApiKey(apiKey)} />
						<Button
							variant="danger"
							size="sm"
							onClick={() => setDeleteOpen(true)}
							disabled={!api}
							aria-label={`Remove ${title}`}
							data-testid="remove-api"
						>
							<Trash2 size={14} aria-hidden="true" />
							Remove API
						</Button>
					</>
				}
			/>
			<BackButton to={ROUTES.workspace} label="Back to Workspace" />

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
					<OverviewStrip api={api} />
					<OperationsSection apiKey={apiKey} totalCount={api.operationCount} />
					<ServingStateStrip apiKey={apiKey} />
					<RevisionsSection apiKey={apiKey} />
					<OverlaysSection apiKey={apiKey} currentRevisionId={api.currentRevisionId} />
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
