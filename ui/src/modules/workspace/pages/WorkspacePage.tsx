/**
 * WorkspacePage — the Workspace half of the APIs surface: every API registered
 * in this jentic-one instance.
 *
 * The workspace is SHARED — every operator in the org sees the same list and
 * every agent they grant draws on it — so each row carries the context a
 * teammate needs before relying on or changing an API: is it serving, who is
 * calling it and is that working, does the gateway hold a credential for it.
 * "Needs attention" narrows to the APIs where one of those answers is bad.
 *
 * The page owns the import dialog open-state (one dialog reachable from the
 * header and the empty state), the in-memory filter, and the joins; the
 * public catalog is the other tab of the same surface (`ApisSectionNav`).
 */
import { useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router';
import { Compass, Upload } from 'lucide-react';
import { PageShell, PageHeader, PageHelp, Button, AppLink } from '@/shared/ui';
import { ApisSectionNav, ROUTES } from '@/shared/app';
import { ApiList } from '@/modules/workspace/components/ApiList';
import type { ApiRowProps } from '@/modules/workspace/components/ApiRow';
import { ImportSpecDialog } from '@/shared/credentials/components/ImportSpecDialog';
import {
	WorkspaceFilterBar,
	type WorkspaceScope,
} from '@/modules/workspace/components/WorkspaceFilterBar';
import {
	apiAttention,
	useWorkspaceApis,
	useWorkspaceCredentials,
	useWorkspaceTraffic,
} from '@/modules/workspace/api';

export default function WorkspacePage() {
	const [searchParams, setSearchParams] = useSearchParams();
	// Deep-link support: the Catalog view cross-links here with `?import=1` to open the
	// import dialog on arrival — landing on the Workspace is the point (the new
	// API appears in the list behind it), so Discover navigates rather than
	// embedding the dialog. Strip the param once consumed so a refresh or
	// back-nav doesn't re-trigger it.
	//
	// Seed the open state from the URL in the initializer AND re-sync in the
	// effect below on purpose: the initializer opens the dialog on the very
	// first paint (no closed-then-open flash), while the effect handles later
	// param changes and strips it. Don't collapse the two into one.
	const [importOpen, setImportOpen] = useState(() => searchParams.get('import') === '1');
	const [filter, setFilter] = useState('');
	const [scope, setScope] = useState<WorkspaceScope>('all');
	const query = useWorkspaceApis();
	const { forApi: trafficFor } = useWorkspaceTraffic();
	const { forApi: credentialsFor, complete: credentialsComplete } = useWorkspaceCredentials();

	useEffect(() => {
		if (searchParams.get('import') !== '1') return;
		setImportOpen(true);
		const next = new URLSearchParams(searchParams);
		next.delete('import');
		setSearchParams(next, { replace: true });
	}, [searchParams, setSearchParams]);

	const apis = query.data?.items;
	const rows = useMemo<ApiRowProps[]>(
		() =>
			(apis ?? []).map((api) => {
				const credentialCount = credentialsComplete
					? credentialsFor(api.api).length
					: undefined;
				const apiTraffic = trafficFor(api.api);
				return {
					api,
					traffic: apiTraffic,
					credentialCount,
					attention: apiAttention(api, { credentialCount, traffic: apiTraffic }),
				};
			}),
		[apis, credentialsFor, credentialsComplete, trafficFor],
	);
	// The count waits for the credential join: "0 need attention" while the
	// credential pages are still loading would be a claim, not a fact.
	const attentionCount = credentialsComplete
		? rows.filter((row) => row.attention.length > 0).length
		: undefined;

	const filtered = useMemo(() => {
		const needle = filter.trim().toLowerCase();
		return rows.filter(({ api, attention }) => {
			if (scope === 'attention' && attention.length === 0) return false;
			if (!needle) return true;
			const haystack = [
				api.displayName ?? '',
				api.description ?? '',
				api.api.vendor,
				api.api.name,
				api.api.host ?? '',
			]
				.join(' ')
				.toLowerCase();
			return haystack.includes(needle);
		});
	}, [rows, filter, scope]);

	const total = rows.length;
	const isFiltering = filter.trim().length > 0 || scope === 'attention';
	const resultsLabel = isFiltering ? `${filtered.length} of ${total}` : undefined;

	const importButton = (
		<Button
			variant="outline"
			size="sm"
			onClick={() => setImportOpen(true)}
			data-testid="workspace-import-open"
		>
			<Upload size={14} aria-hidden="true" />
			Import API
		</Button>
	);

	const emptyActions = (
		<div className="flex flex-wrap justify-center gap-2">
			<AppLink href={ROUTES.discover} variant="primary" size="sm">
				<Compass size={14} aria-hidden="true" />
				Browse the catalog
			</AppLink>
			{importButton}
		</div>
	);

	return (
		<PageShell>
			<PageHeader
				title="APIs"
				subtitle="Shared by everyone in this workspace, and by the agents you grant."
				actions={
					<>
						{importButton}
						<PageHelp
							title="About APIs"
							sections={[
								{
									heading: 'Workspace and Catalog',
									body: 'Workspace lists the APIs registered in this jentic-one instance. Catalog is the public Jentic catalog: import an API from there and it joins the workspace.',
								},
								{
									heading: 'A shared workspace',
									body: 'Everyone in your organisation sees the same APIs. Each row shows whether the API is serving, how many calls agents made to it in the last 7 days and how many failed, and whether a credential exists for it — check these before you change or remove an API someone else depends on.',
								},
								{
									heading: 'Needs attention',
									body: 'An API needs attention when its calls are failing, it declares a security scheme but has no credential, it has no live revision, or its upstream spec has an update you have not adopted.',
								},
								{
									heading: 'Adding your own API',
									body: 'Use "Import API" to register an OpenAPI spec by URL, paste, or file upload. A freshly imported API starts as a draft revision you promote to make its operations live.',
								},
							]}
						/>
					</>
				}
			/>

			<ApisSectionNav className="-mt-2" />

			<WorkspaceFilterBar
				value={filter}
				onChange={setFilter}
				resultsLabel={resultsLabel}
				scope={scope}
				onScopeChange={setScope}
				attentionCount={attentionCount}
			/>

			<ApiList
				rows={filtered}
				isLoading={query.isLoading}
				isError={query.isError}
				error={query.error}
				onRetry={() => query.refetch()}
				emptyAction={emptyActions}
				filtered={isFiltering}
			/>

			<ImportSpecDialog open={importOpen} onClose={() => setImportOpen(false)} />
		</PageShell>
	);
}
