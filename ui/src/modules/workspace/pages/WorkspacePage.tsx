/**
 * WorkspacePage — the user's home base: the APIs registered in this jentic-one
 * instance.
 *
 * Ported from jentic-mini's Workspace home, narrowed to **APIs only** (mini's
 * page also carried workflows + toolkits, which live in other modules here).
 * The page owns the import dialog open-state (a single dialog reachable from
 * both the header button and the empty-state CTA) and an in-memory filter over
 * the loaded rows. Catalog-wide search lives in Discover, not here.
 */
import { useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { Upload } from 'lucide-react';
import { PageShell, PageHeader, PageHelp, Button } from '@/shared/ui';
import { ApiGrid } from '@/modules/workspace/components/ApiGrid';
import { ImportSpecDialog } from '@/modules/workspace/components/ImportSpecDialog';
import { WorkspaceStatsStrip } from '@/modules/workspace/components/WorkspaceStatsStrip';
import { WorkspaceFilterBar } from '@/modules/workspace/components/WorkspaceFilterBar';
import { WorkspaceCatalogFooter } from '@/modules/workspace/components/WorkspaceCatalogFooter';
import { useWorkspaceApis } from '@/modules/workspace/api';

export default function WorkspacePage() {
	const [searchParams, setSearchParams] = useSearchParams();
	// Deep-link support: Discover cross-links here with `?import=1` to open the
	// import dialog on arrival (the import UI is a Workspace-module concern, so
	// Discover navigates rather than embedding the dialog). Strip the param once
	// consumed so a refresh or back-nav doesn't re-trigger it.
	//
	// Seed the open state from the URL in the initializer AND re-sync in the
	// effect below on purpose: the initializer opens the dialog on the very
	// first paint (no closed-then-open flash), while the effect handles later
	// param changes and strips it. Don't collapse the two into one.
	const [importOpen, setImportOpen] = useState(() => searchParams.get('import') === '1');
	const [filter, setFilter] = useState('');
	const query = useWorkspaceApis();

	useEffect(() => {
		if (searchParams.get('import') !== '1') return;
		setImportOpen(true);
		const next = new URLSearchParams(searchParams);
		next.delete('import');
		setSearchParams(next, { replace: true });
	}, [searchParams, setSearchParams]);

	const apis = query.data?.items;
	const filtered = useMemo(() => {
		const rows = apis ?? [];
		const needle = filter.trim().toLowerCase();
		if (!needle) return rows;
		return rows.filter((api) => {
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
	}, [apis, filter]);

	const total = apis?.length ?? 0;
	const isFiltering = filter.trim().length > 0;
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

	return (
		<PageShell>
			<PageHeader
				title="Workspace"
				subtitle="The APIs registered in this instance."
				actions={
					<>
						{importButton}
						<PageHelp
							title="About Workspace"
							sections={[
								{
									heading: 'What lives here',
									body: 'Every API you have imported into this jentic-one instance — its operations, revisions, and security schemes. Click an API to open its detail page.',
								},
								{
									heading: 'Adding an API',
									body: 'Use "Import API" to register an OpenAPI spec by URL, paste, or file upload. Imports run server-side; a freshly imported API starts as a draft revision you can promote to make its operations live.',
								},
								{
									heading: 'Filtering',
									body: 'The filter box narrows the APIs shown right now — an in-memory match over name, description, and vendor. To search the public catalog, open Discover.',
								},
							]}
						/>
					</>
				}
			/>

			<WorkspaceStatsStrip apis={apis ?? []} loading={query.isLoading} />

			<WorkspaceFilterBar value={filter} onChange={setFilter} resultsLabel={resultsLabel} />

			<ApiGrid
				apis={filtered}
				isLoading={query.isLoading}
				isError={query.isError}
				error={query.error}
				onRetry={() => query.refetch()}
				emptyAction={importButton}
				filtered={isFiltering}
			/>

			<WorkspaceCatalogFooter />

			<ImportSpecDialog open={importOpen} onClose={() => setImportOpen(false)} />
		</PageShell>
	);
}
