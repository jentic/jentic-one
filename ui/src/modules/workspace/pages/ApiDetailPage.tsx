/**
 * ApiDetailPage — a single workspace API's detail view.
 *
 * Ported from jentic-mini's `ApiDetailPage`, narrowed to the API-only surface
 * jentic-one's registry exposes: overview, operations (current revision), and
 * revision history with promote/archive. Credentials, toolkits, and workflows
 * — present on mini's detail page — belong to other modules and are out of
 * scope here.
 *
 * The route carries the `(vendor, name, version)` triple as three path
 * segments (`/workspace/:vendor/:name/:version`, → `/app/workspace/...` in the
 * browser). A malformed token or an unknown API renders a not-found / error
 * state rather than issuing a bad request.
 */
import { useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
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
import { SpecViewerDialog } from '@/modules/workspace/components/SpecViewerDialog';
import { formatApiKey, useDeleteApi, useWorkspaceApi } from '@/modules/workspace/api';
import type { ApiKey } from '@/modules/workspace/api';
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
	const title = api?.displayName ?? `${apiKey.vendor}/${apiKey.name}`;

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
					<RevisionsSection apiKey={apiKey} />
				</>
			)}

			<SpecViewerDialog apiKey={apiKey} open={specOpen} onClose={() => setSpecOpen(false)} />

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
