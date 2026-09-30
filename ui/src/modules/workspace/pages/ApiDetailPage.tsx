/**
 * ApiDetailPage — a workspace API's hub in the Library
 * (`/library/workspace/:vendor/:name/:version`).
 *
 * The API's content, grouped into tabs so governance sits one click away
 * rather than at equal weight with everything:
 *
 *   - Overview   — the OverviewStrip (update banner with Mute / Re-import, host,
 *                  stats, imported-ago, description) plus who can use it
 *                  (credentials → bound agents), 7-day calls, notes, and this
 *                  API's recent events — each only where a real read backs it
 *   - Operations — the live revision's operations (OperationsSection)
 *   - Versions   — the serving-state line, revisions (promote/archive, diffs)
 *                  and overlays (confirm/rollback/deprecate) together, so the
 *                  revision ⇄ overlay cross-links stay on one screen
 *   - Spec       — the live OpenAPI document inline (diff vs previous too)
 *
 * The header carries View spec (opens the Spec tab), copy id and Remove API. The
 * active tab lives in `?tab=` (like the agent console) so each view is
 * shareable and the Library panel can deep-link straight to Versions.
 *
 * The route carries the `(vendor, name, version)` triple as three path
 * segments. A malformed token or an unknown API renders a not-found / error
 * state rather than issuing a bad request.
 */
import { useState, type ReactNode } from 'react';
import { useNavigate, useParams, useSearchParams } from 'react-router';
import { FileCode2, FileJson, GitBranch, LayoutDashboard, ListTree, Trash2 } from 'lucide-react';
import {
	ApiStateBadges,
	PageShell,
	PageHeader,
	BackButton,
	Skeleton,
	ErrorAlert,
	Button,
	CascadeDeleteDialog,
	CopyButton,
	TabNav,
	VendorIcon,
	type TabNavOption,
} from '@/shared/ui';
import { OverviewStrip } from '@/modules/workspace/components/OverviewStrip';
import { OperationsSection } from '@/modules/workspace/components/OperationsSection';
import { RevisionsSection } from '@/modules/workspace/components/RevisionsSection';
import { OverlaysSection } from '@/modules/workspace/components/OverlaysSection';
import { ServingStateStrip } from '@/modules/workspace/components/ServingStateStrip';
import { SpecViewerPanel } from '@/modules/workspace/components/SpecViewerDialog';
import { ApiHubOverview } from '@/modules/workspace/components/ApiHubOverview';
import {
	formatApiKey,
	diffBaseFor,
	useApiRevisions,
	useDeleteApi,
	useWorkspaceApi,
} from '@/modules/workspace/api';
import type { ApiKey, SpecDiffBase } from '@/modules/workspace/api';
import { vendorIconPropsFor, workspaceApiTitle } from '@/shared/lib';
import { usePendingOverlayCounts } from '@/shared/hooks';
import { ROUTES } from '@/shared/app/routes';

const HUB_TABS = ['overview', 'operations', 'versions', 'spec'] as const;
const NO_REFS: ApiKey[] = [];
type HubTab = (typeof HUB_TABS)[number];

function isHubTab(value: string | null): value is HubTab {
	return HUB_TABS.includes(value as HubTab);
}

const tabId = (tab: string) => `api-hub-tab-${tab}`;
const panelId = (tab: string) => `api-hub-panel-${tab}`;

/**
 * Tabs visited so far on this hub (always including the active one). A panel
 * mounts on first visit and then stays mounted — hidden, not unmounted — so
 * switching away and back keeps its local state (Operations filter / paging /
 * expanded rows, loaded revision + overlay pages, the spec view mode).
 */
function useVisitedTabs(active: HubTab, resetKey: string): ReadonlySet<HubTab> {
	const [state, setState] = useState<{ key: string; tabs: ReadonlySet<HubTab> }>(() => ({
		key: resetKey,
		tabs: new Set([active]),
	}));
	// Another API (same page instance, new route params) starts fresh; a newly
	// visited tab is added. Both are derived-state updates during render
	// (React's documented pattern) — no effect, so the panel mounts in the
	// same commit.
	const stale = state.key !== resetKey;
	const tabs = stale ? new Set<HubTab>([active]) : state.tabs;
	if (stale || !tabs.has(active)) {
		const next = new Set(tabs).add(active);
		setState({ key: resetKey, tabs: next });
		return next;
	}
	return tabs;
}

/**
 * One tab's panel. Not rendered until first visited; once visited it stays
 * mounted and inactive panels carry the native `hidden` attribute, which
 * removes them from layout and the accessibility tree (so their ids/test ids
 * don't surface to assistive tech or visible-only queries).
 */
function TabPanel({
	tab,
	active,
	visited,
	children,
}: {
	tab: HubTab;
	active: HubTab;
	visited: ReadonlySet<HubTab>;
	children: ReactNode;
}) {
	if (!visited.has(tab)) return null;
	const isActive = tab === active;
	return (
		<div
			role="tabpanel"
			id={panelId(tab)}
			aria-labelledby={tabId(tab)}
			hidden={!isActive}
			tabIndex={isActive ? 0 : -1}
			className="space-y-4 focus-visible:outline-none"
			data-testid={`api-hub-panel-${tab}`}
		>
			{children}
		</div>
	);
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
	// Shared cache with RevisionsSection — used to pick the Spec tab's diff
	// base (the revision created just before the live one).
	const revisionsQuery = useApiRevisions(apiKey);
	// The Versions tab's badge: the same `status=pending` read the Library panel
	// counts from (not the loaded pages of the full overlay list).
	const pendingOverlayCounts = usePendingOverlayCounts(apiKey ? [apiKey] : NO_REFS);
	const navigate = useNavigate();
	const deleteApi = useDeleteApi();
	const [deleteOpen, setDeleteOpen] = useState(false);
	const [searchParams, setSearchParams] = useSearchParams();
	const tabParam = searchParams.get('tab');
	const activeTab: HubTab = isHubTab(tabParam) ? tabParam : 'overview';
	const visited = useVisitedTabs(activeTab, apiKey ? formatApiKey(apiKey) : '');

	function setTab(tab: HubTab) {
		// Replaced, not pushed: tabs are views of one page, so neither the
		// browser Back button nor the hub's Back should step through every tab
		// visited. The URL still deep-links the current tab.
		setSearchParams(
			(prev) => {
				const next = new URLSearchParams(prev);
				if (tab === 'overview') next.delete('tab');
				else next.set('tab', tab);
				return next;
			},
			{ replace: true },
		);
	}

	if (!apiKey) {
		return (
			<PageShell>
				<BackButton to={ROUTES.workspace} label="Back" />
				<ErrorAlert message="That API reference is malformed." />
			</PageShell>
		);
	}

	const api = query.data;

	// The Spec tab shows the LIVE document in full; a Diff toggle vs the
	// revision created just before the live one is available when the list
	// carries one.
	const revisions = revisionsQuery.items;
	const live = revisions.find((r) => r.isCurrent) ?? null;
	const liveDiffBase: SpecDiffBase | null = live ? diffBaseFor(live, revisions) : null;
	const pendingRead = pendingOverlayCounts.byApi.get(formatApiKey(apiKey));

	// The shared never-empty title rule (same as the workspace tile and the
	// Library panel) — it also feeds the VendorIcon name and the Remove/aria
	// labels.
	const title = workspaceApiTitle({
		displayName: api?.displayName,
		catalogApiId: api?.catalogApiId,
		...apiKey,
	});

	const tabOptions: TabNavOption<HubTab>[] = [
		{ value: 'overview', label: 'Overview', icon: <LayoutDashboard className="h-4 w-4" /> },
		{
			value: 'operations',
			label: 'Operations',
			icon: <ListTree className="h-4 w-4" />,
			count: api?.operationCount,
		},
		{
			value: 'versions',
			label: 'Versions',
			icon: <GitBranch className="h-4 w-4" />,
			// The badge flags what's waiting for review, not the history length.
			count: pendingRead && pendingRead.count > 0 ? pendingRead.count : undefined,
		},
		{ value: 'spec', label: 'Spec', icon: <FileCode2 className="h-4 w-4" /> },
	];

	return (
		<PageShell>
			<PageHeader
				title={query.isLoading ? 'Loading…' : title}
				subtitle={formatApiKey(apiKey)}
				icon={
					api ? (
						<VendorIcon
							{...vendorIconPropsFor({ title, ...api.api, iconUrl: api.iconUrl })}
							size="lg"
						/>
					) : undefined
				}
				actions={
					<>
						<Button
							variant="secondary"
							size="sm"
							onClick={() => setTab('spec')}
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
			<div className="flex flex-wrap items-center justify-between gap-2">
				<BackButton to={ROUTES.workspace} label="Back" />
				{api ? (
					<div className="flex items-center gap-1.5" data-testid="hub-state">
						<ApiStateBadges
							currentRevisionId={api.currentRevisionId}
							// The Overview tab's update banner already says it (with
							// Mute / Re-import); other tabs keep the badge.
							updateAvailable={api.updateAvailable && activeTab !== 'overview'}
						/>
					</div>
				) : null}
			</div>

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
					<TabNav<HubTab>
						options={tabOptions}
						value={activeTab}
						onChange={setTab}
						ariaLabel="API hub sections"
						getTabId={tabId}
						// Only visited panels are in the DOM; an unvisited tab controls nothing yet.
						getControls={(tab) => (visited.has(tab) ? panelId(tab) : undefined)}
					/>
					<TabPanel tab="overview" active={activeTab} visited={visited}>
						<OverviewStrip api={api} />
						<ApiHubOverview api={api} />
					</TabPanel>
					<TabPanel tab="operations" active={activeTab} visited={visited}>
						<OperationsSection
							apiKey={apiKey}
							totalCount={api.operationCount}
							onShowVersions={() => setTab('versions')}
						/>
					</TabPanel>
					<TabPanel tab="versions" active={activeTab} visited={visited}>
						<ServingStateStrip apiKey={apiKey} />
						<RevisionsSection apiKey={apiKey} />
						<OverlaysSection
							apiKey={apiKey}
							currentRevisionId={api.currentRevisionId}
						/>
					</TabPanel>
					<TabPanel tab="spec" active={activeTab} visited={visited}>
						{api.currentRevisionId === null ? (
							<p
								className="text-muted-foreground text-sm"
								data-testid="hub-spec-draft"
							>
								No live revision yet — promote a revision in Versions to serve its
								spec. Each revision's own spec is viewable from its row there.
							</p>
						) : (
							<SpecViewerPanel apiKey={apiKey} diffAgainst={liveDiffBase} />
						)}
					</TabPanel>
				</>
			)}

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
