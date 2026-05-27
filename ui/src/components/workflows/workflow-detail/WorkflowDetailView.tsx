import { lazy, Suspense, useMemo } from 'react';
import { useQueries, useQuery } from '@tanstack/react-query';
import { useSearchParams } from 'react-router-dom';
import { ArazzoErrorBoundary } from './ArazzoErrorBoundary';
import { OverviewBody } from './OverviewBody';
import { SlugChip } from './SlugChip';
import { api, apiUrl } from '@/api/client';
import { LoadingState } from '@/components/ui/LoadingState';
import { SegmentedToggle } from '@/components/ui/SegmentedToggle';
import { WorkflowMetaStrip } from '@/components/workflows/WorkflowMetaStrip';
import '@jentic/arazzo-ui/styles.css';

// Arazzo embeds its own runtime. Code-split it so the page shell paints
// quickly and we only pay for the viewer when a Diagram / Docs / Split
// tab actually mounts. Stylesheet stays eager (above) — it's tiny and
// referenced by a couple of other surfaces.
const ArazzoUI = lazy(async () => {
	const mod = await import('@jentic/arazzo-ui');
	return { default: mod.ArazzoUI };
});

type ArazzoView = 'diagram' | 'docs' | 'split';
export type DetailTab = 'overview' | ArazzoView;

const TAB_OPTIONS: { value: DetailTab; label: string }[] = [
	{ value: 'overview', label: 'Overview' },
	{ value: 'diagram', label: 'Diagram' },
	{ value: 'docs', label: 'Docs' },
	{ value: 'split', label: 'Split' },
];

function isDetailTab(value: string | null): value is DetailTab {
	return value === 'overview' || value === 'diagram' || value === 'docs' || value === 'split';
}

interface WorkflowDetailViewProps {
	slug: string;
	workflow: any;
}

/**
 * Renders the body of a single imported workflow: meta strip, slug
 * chip, tab toggle (Overview / Diagram / Docs / Split) and the active
 * tab's content. Owns the URL-backed tab state via `?view=…` so the
 * choice survives reloads and is shareable.
 *
 * The Arazzo viewer is lazy-loaded — opening any of the non-overview
 * tabs is what triggers both the chunk download and the underlying
 * `application/vnd.oai.workflows+json` request, so users who only
 * peek at the overview never pay either cost.
 */
export function WorkflowDetailView({ slug, workflow }: WorkflowDetailViewProps) {
	const [searchParams, setSearchParams] = useSearchParams();

	const tab = ((): DetailTab => {
		const v = searchParams.get('view');
		return isDetailTab(v) ? v : 'overview';
	})();
	const setTab = (next: DetailTab) => {
		setSearchParams(
			(prev) => {
				const p = new URLSearchParams(prev);
				if (next === 'overview') {
					p.delete('view');
				} else {
					p.set('view', next);
				}
				return p;
			},
			{ replace: true },
		);
	};

	const showArazzo = tab === 'diagram' || tab === 'docs' || tab === 'split';

	const { data: arazzoDoc, isLoading: isLoadingArazzo } = useQuery({
		queryKey: ['workflow-arazzo', slug],
		queryFn: async () => {
			const res = await fetch(apiUrl(`/workflows/${slug}`), {
				headers: { Accept: 'application/vnd.oai.workflows+json' },
				credentials: 'include',
			});
			if (!res.ok) throw new Error('Failed to fetch Arazzo document');
			return res.json();
		},
		enabled: !!slug && !!workflow && showArazzo,
	});

	const steps: any[] = workflow.steps ?? [];
	const involvedApis: string[] = workflow.involved_apis ?? [];

	// Per-involved-API workspace lookup. `getApi(id)` returns 200 when
	// the API is locally registered and 404 otherwise — so we can
	// resolve membership exactly without paginating /apis or hitting
	// the limit cap (le=100). Workflows usually declare 1-4 APIs,
	// so this is a handful of small requests rather than a bulk scan.
	//
	// Keyed under `['apis', 'membership', apiId]` so the broad
	// `invalidateQueries({ queryKey: ['apis'] })` already fired by
	// `useImportCatalogApi` / `useCredentialImportedSync` reaches us
	// after an import — the chip flips from "Not in workspace" to a
	// live link without a stale window.
	const membershipQueries = useQueries({
		queries: involvedApis.map((apiId) => ({
			queryKey: ['apis', 'membership', apiId] as const,
			queryFn: async () => {
				try {
					await api.getApi(apiId);
					return true;
				} catch (err) {
					if ((err as { status?: number })?.status === 404) return false;
					throw err;
				}
			},
			staleTime: 60_000,
			retry: (count: number, err: unknown) => {
				if ((err as { status?: number })?.status === 404) return false;
				return count < 2;
			},
		})),
	});
	const workspaceApiIds = useMemo(() => {
		const set = new Set<string>();
		involvedApis.forEach((apiId, i) => {
			if (membershipQueries[i]?.data === true) set.add(apiId);
		});
		return set;
		// eslint-disable-next-line react-hooks/exhaustive-deps
	}, [involvedApis.join('|'), membershipQueries.map((q) => q.data).join('|')]);

	// Per-involved-API catalog probe — does the public catalog have a
	// leaf entry under this id? Workflows can declare api_ids that
	// aren't catalog leaves (e.g. `hubspot.com`, where the catalog
	// only has sub-APIs like `hubspot.com/CRM-contacts`). When that
	// happens, sending the user to `/discover?inspect=<id>` lands
	// them on a sheet that fails to fetch the spec, so we hide the
	// "Open in Discover" affordance instead.
	const catalogQueries = useQueries({
		queries: involvedApis.map((apiId) => ({
			queryKey: ['catalog', 'entry', apiId] as const,
			queryFn: async () => {
				try {
					await api.getCatalogEntry(apiId);
					return true;
				} catch (err) {
					if ((err as { status?: number })?.status === 404) return false;
					throw err;
				}
			},
			staleTime: 5 * 60_000,
			retry: (count: number, err: unknown) => {
				if ((err as { status?: number })?.status === 404) return false;
				return count < 2;
			},
		})),
	});
	const catalogApiIds = useMemo(() => {
		const set = new Set<string>();
		involvedApis.forEach((apiId, i) => {
			if (catalogQueries[i]?.data === true) set.add(apiId);
		});
		return set;
		// eslint-disable-next-line react-hooks/exhaustive-deps
	}, [involvedApis.join('|'), catalogQueries.map((q) => q.data).join('|')]);

	// Per-involved-API credentials probe — `listCredentials(apiId)`
	// filters server-side, so a non-empty array means the user has at
	// least one credential bound to that api. Keyed under
	// `['credentials', 'for-api', apiId]` to ride the broad
	// `['credentials']` invalidator in `useCredentialImportedSync`.
	const credentialQueries = useQueries({
		queries: involvedApis.map((apiId) => ({
			queryKey: ['credentials', 'for-api', apiId] as const,
			queryFn: () => api.listCredentials(apiId),
			staleTime: 60_000,
		})),
	});
	const credentialedApiIds = useMemo(() => {
		const set = new Set<string>();
		involvedApis.forEach((apiId, i) => {
			const data = credentialQueries[i]?.data;
			const list: unknown[] = Array.isArray(data)
				? (data as unknown[])
				: Array.isArray((data as { data?: unknown[] })?.data)
					? (data as { data: unknown[] }).data
					: [];
			if (list.length > 0) set.add(apiId);
		});
		return set;
		// eslint-disable-next-line react-hooks/exhaustive-deps
	}, [involvedApis.join('|'), credentialQueries.map((q) => q.data).join('|')]);

	return (
		<>
			<WorkflowMetaStrip
				slug={slug}
				stepsCount={steps.length}
				involvedApis={involvedApis}
				createdAt={workflow.created_at}
			/>

			<div className="flex flex-wrap items-center justify-between gap-3">
				<SlugChip slug={workflow.slug} />
				<SegmentedToggle
					layoutId="workflow-detail-view"
					value={tab}
					onChange={setTab}
					options={TAB_OPTIONS}
				/>
			</div>

			{tab === 'overview' ? (
				<OverviewBody
					steps={steps}
					involvedApis={involvedApis}
					workspaceApiIds={workspaceApiIds}
					credentialedApiIds={credentialedApiIds}
					catalogApiIds={catalogApiIds}
				/>
			) : isLoadingArazzo ? (
				<LoadingState message="Loading workflow visualization..." />
			) : arazzoDoc ? (
				<ArazzoErrorBoundary slug={slug}>
					<div
						className="border-border bg-muted overflow-hidden rounded-xl border"
						style={{ height: 'min(75vh, 800px)' }}
						data-testid="workflow-arazzo-frame"
					>
						<Suspense
							fallback={<LoadingState message="Loading workflow visualization..." />}
						>
							<ArazzoUI
								document={arazzoDoc}
								view={tab as ArazzoView}
								onViewChange={(v: ArazzoView) => setTab(v)}
							/>
						</Suspense>
					</div>
				</ArazzoErrorBoundary>
			) : (
				<div className="text-muted-foreground py-16 text-center">
					Failed to load workflow visualization.
				</div>
			)}
		</>
	);
}
