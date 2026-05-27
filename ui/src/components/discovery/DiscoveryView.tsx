import { useCallback, useEffect, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Bookmark, Compass } from 'lucide-react';
import { ApiDetailSheet } from './ApiDetailSheet';
import { BrowseResults } from './BrowseResults';
import { CatalogStatusRow, DiscoverToolbar } from './DiscoverToolbar';
import { WorkflowDetailSheet } from './WorkflowDetailSheet';
import type { DiscoveryEntity, DiscoverySource } from './DiscoveryCard';
import { DiscoverySection } from './DiscoverySection';
import { RecentlyUsedStrip } from './RecentlyUsedStrip';
import { pushRecent } from './recentInspectStore';
import { api } from '@/api/client';
import { AppLink } from '@/components/ui/AppLink';
import { KeyboardShortcutsBar, MOD_KEY } from '@/components/ui/KeyboardShortcutsBar';
import { toast } from '@/components/ui/toastStore';
import { useImportCatalogApi } from '@/hooks/useImportCatalogApi';
import { useCredentialImportedSync } from '@/hooks/useCredentialImportedSync';

export interface DiscoveryViewProps {
	forcedSource?: DiscoverySource;
	mode?: 'single' | 'sectioned';
}

export function DiscoveryView({ forcedSource, mode = 'single' }: DiscoveryViewProps = {}) {
	const [searchParams, setSearchParams] = useSearchParams();

	const initialQ = searchParams.get('q') ?? '';
	const [input, setInput] = useState(initialQ);
	const [query, setQuery] = useState(initialQ);
	const [expandedId, setExpandedId] = useState<string | null>(null);
	const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
	const searchInputRef = useRef<HTMLInputElement | null>(null);

	// ── Detail sheet state ────────────────────────────────────────────────
	const inspectParam = searchParams.get('inspect');
	const opParam = searchParams.get('op');
	const wfParam = searchParams.get('wf');
	const [stickyInspect, setStickyInspect] = useState<string | null>(inspectParam);
	const [selectedEntity, setSelectedEntity] = useState<DiscoveryEntity | undefined>(undefined);

	useEffect(() => {
		if (inspectParam) {
			setStickyInspect(inspectParam);
			setSelectedEntity((prev) => (prev?.id === inspectParam ? prev : undefined));
		}
	}, [inspectParam]);

	// ── Workflow detail sheet state ──────────────────────────────────────
	const inspectWfParam = searchParams.get('inspect_wf');
	const [stickyInspectWf, setStickyInspectWf] = useState<string | null>(inspectWfParam);
	const [selectedWfEntity, setSelectedWfEntity] = useState<DiscoveryEntity | undefined>(
		undefined,
	);

	useEffect(() => {
		if (inspectWfParam) {
			setStickyInspectWf(inspectWfParam);
			setSelectedWfEntity((prev) => (prev?.id === inspectWfParam ? prev : undefined));
		}
	}, [inspectWfParam]);

	// Deep-link fixup: stale `?inspect_wf=catalog:workflows:<id>` → API sheet
	useEffect(() => {
		if (!inspectWfParam || !inspectWfParam.startsWith('catalog:workflows:')) return;
		const apiId = inspectWfParam.slice('catalog:workflows:'.length);
		setSearchParams(
			(prev) => {
				const p = new URLSearchParams(prev);
				p.delete('inspect_wf');
				if (apiId) p.set('inspect', apiId);
				return p;
			},
			{ replace: true },
		);
	}, [inspectWfParam, setSearchParams]);

	// Deep-link fixup: strip legacy `?type=` param
	const typeParam = searchParams.get('type');
	useEffect(() => {
		if (typeParam == null) return;
		setSearchParams(
			(prev) => {
				const p = new URLSearchParams(prev);
				p.delete('type');
				return p;
			},
			{ replace: true },
		);
	}, [typeParam, setSearchParams]);

	// Direct-import flow for directory cards
	const { importApi, pendingApiId } = useImportCatalogApi();
	const handleImport = useCallback(
		(entity: DiscoveryEntity) => {
			void importApi({ apiId: entity.id, specUrl: entity.specUrl });
		},
		[importApi],
	);

	// Push onto recents ring on every `?inspect=` transition
	useEffect(() => {
		if (!inspectParam) return;
		pushRecent({
			apiId: inspectParam,
			name: selectedEntity?.summary ?? selectedEntity?.id ?? inspectParam,
			source: selectedEntity?.source,
		});
		// eslint-disable-next-line react-hooks/exhaustive-deps
	}, [inspectParam]);

	// ── Global keyboard shortcuts ────────────────────────────────────────
	useEffect(() => {
		function isTypingTarget(target: EventTarget | null): boolean {
			if (!(target instanceof HTMLElement)) return false;
			const tag = target.tagName;
			if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return true;
			if (target.isContentEditable) return true;
			return false;
		}

		function onKeyDown(e: KeyboardEvent) {
			if (e.metaKey || e.ctrlKey || e.altKey) return;

			if (e.key === '/' && !isTypingTarget(e.target)) {
				e.preventDefault();
				searchInputRef.current?.focus();
				searchInputRef.current?.select();
				return;
			}

			if (
				e.key === 'Escape' &&
				!isTypingTarget(e.target) &&
				!inspectParam &&
				!inspectWfParam
			) {
				if (input) {
					setInput('');
					setQuery('');
					lastCommittedQ.current = '';
					setSearchParams(
						(prev) => {
							const p = new URLSearchParams(prev);
							p.delete('q');
							return p;
						},
						{ replace: true },
					);
				}
			}
		}

		document.addEventListener('keydown', onKeyDown);
		return () => {
			document.removeEventListener('keydown', onKeyDown);
		};
	}, [input, inspectParam, inspectWfParam, setSearchParams]);

	// ── Credential import listener ───────────────────────────────────────
	const queryClient = useQueryClient();
	const selectedEntityRef = useRef(selectedEntity);
	selectedEntityRef.current = selectedEntity;

	const handleCredentialImported = useCallback((apiId: string) => {
		setSelectedEntity((prev) => {
			if (prev?.id === apiId) {
				return { ...prev, source: 'workspace' };
			}
			return prev;
		});
	}, []);

	useCredentialImportedSync({
		onImported: handleCredentialImported,
	});

	// Sync URL → state for external navigation only
	const urlQ = searchParams.get('q') ?? '';
	const lastCommittedQ = useRef(urlQ);
	useEffect(() => {
		if (urlQ === lastCommittedQ.current) return;
		lastCommittedQ.current = urlQ;
		setInput(urlQ);
		setQuery(urlQ);
	}, [urlQ]);

	const handleInput = useCallback(
		(value: string) => {
			setInput(value);
			if (debounceRef.current) clearTimeout(debounceRef.current);
			debounceRef.current = setTimeout(() => {
				const trimmed = value.trim();
				setQuery(trimmed);
				setExpandedId(null);
				lastCommittedQ.current = trimmed;
				setSearchParams(
					(prev) => {
						const p = new URLSearchParams(prev);
						if (trimmed) p.set('q', trimmed);
						else p.delete('q');
						return p;
					},
					{ replace: true },
				);
			}, 400);
		},
		[setSearchParams],
	);

	useEffect(() => {
		return () => {
			if (debounceRef.current) clearTimeout(debounceRef.current);
		};
	}, []);

	function handleCardClick(entity: DiscoveryEntity) {
		if (entity.type === 'api') {
			setSelectedEntity(entity);
			const wasSheetOpen = inspectParam !== null;
			setSearchParams(
				(prev) => {
					const p = new URLSearchParams(prev);
					p.set('inspect', entity.id);
					p.delete('op');
					return p;
				},
				{ replace: wasSheetOpen },
			);
			setExpandedId(entity.id);
			return;
		}
		if (entity.type === 'workflow') {
			if (entity.source === 'directory') {
				const apiId = entity.involvedApis?.[0];
				if (apiId) {
					const apiEntity: DiscoveryEntity = {
						id: apiId,
						type: 'api',
						source: 'directory',
						summary: apiId,
						description: entity.description,
						hasWorkflows: true,
						raw: entity.raw,
					};
					handleCardClick(apiEntity);
					return;
				}
				return;
			}
			setSelectedWfEntity(entity);
			const wasWfSheetOpen = inspectWfParam !== null;
			setSearchParams(
				(prev) => {
					const p = new URLSearchParams(prev);
					p.set('inspect_wf', entity.id);
					return p;
				},
				{ replace: wasWfSheetOpen },
			);
			setExpandedId(entity.id);
			return;
		}
		setExpandedId((prev) => (prev === entity.id ? null : entity.id));
	}

	function handleCloseSheet() {
		setSearchParams(
			(prev) => {
				const p = new URLSearchParams(prev);
				p.delete('inspect');
				p.delete('op');
				p.delete('wf');
				return p;
			},
			{ replace: true },
		);
		setExpandedId((prev) => (prev === stickyInspect ? null : prev));
	}

	function handleCloseWfSheet() {
		setSearchParams(
			(prev) => {
				const p = new URLSearchParams(prev);
				p.delete('inspect_wf');
				return p;
			},
			{ replace: true },
		);
		setExpandedId((prev) => (prev === stickyInspectWf ? null : prev));
	}

	function handleSelectOp(opId: string | null) {
		setSearchParams(
			(prev) => {
				const p = new URLSearchParams(prev);
				if (opId) p.set('op', opId);
				else p.delete('op');
				return p;
			},
			{ replace: true },
		);
	}

	function handleSelectWf(wfId: string | null) {
		setSearchParams(
			(prev) => {
				const p = new URLSearchParams(prev);
				if (wfId) p.set('wf', wfId);
				else p.delete('wf');
				return p;
			},
			{ replace: true },
		);
	}

	function handleSelectApi(nextApiId: string) {
		setSelectedEntity(undefined);
		setSearchParams(
			(prev) => {
				const p = new URLSearchParams(prev);
				p.set('inspect', nextApiId);
				p.delete('op');
				return p;
			},
			{ replace: true },
		);
	}

	function clearSearch() {
		setInput('');
		setQuery('');
		setExpandedId(null);
		lastCommittedQ.current = '';
		setSearchParams(
			(prev) => {
				const p = new URLSearchParams(prev);
				p.delete('q');
				return p;
			},
			{ replace: true },
		);
	}

	const isSectioned = mode === 'sectioned';

	// ── Catalog status ───────────────────────────────────────────────────
	const showCatalogStatus = forcedSource === 'directory' && !query;
	const catalogStatusQuery = useQuery({
		queryKey: ['catalog', 'status'],
		queryFn: () => api.listCatalog(undefined, 1),
		enabled: showCatalogStatus,
		staleTime: 60_000,
	});

	const refreshCatalogMutation = useMutation({
		mutationFn: () => api.refreshCatalog(),
		onSuccess: () => {
			queryClient.invalidateQueries({ queryKey: ['apis'] });
			queryClient.invalidateQueries({ queryKey: ['catalog'] });
			toast({
				title: 'Catalog refreshed',
				description: 'Pulled the latest manifest from the Jentic public catalog.',
				variant: 'success',
			});
		},
		onError: (err: unknown) => {
			toast({
				title: 'Refresh failed',
				description: err instanceof Error ? err.message : 'Could not reach the catalog.',
				variant: 'error',
			});
		},
	});

	const [shownCount, setShownCount] = useState<number | null>(null);
	const catalogStatusData = catalogStatusQuery.data as
		| { catalog_total?: number; manifest_age_seconds?: number | null }
		| undefined;
	const catalogTotal = catalogStatusData?.catalog_total ?? null;
	const lastSyncSeconds = catalogStatusData?.manifest_age_seconds ?? null;

	return (
		<>
			<DiscoverToolbar
				input={input}
				onInput={handleInput}
				onClear={clearSearch}
				isFetching={false}
				placeholder={
					forcedSource === 'directory'
						? 'Search the catalog…'
						: isSectioned
							? 'Search APIs by name…'
							: undefined
				}
				searchInputRef={searchInputRef}
			/>

			<div className="mt-4 space-y-4">
				{showCatalogStatus && (
					<CatalogStatusRow
						shown={shownCount}
						total={catalogTotal}
						lastSyncSeconds={lastSyncSeconds}
						isLoading={catalogStatusQuery.isLoading}
						isRefreshing={refreshCatalogMutation.isPending}
						onRefresh={() => refreshCatalogMutation.mutate()}
					/>
				)}

				{isSectioned && !query ? (
					<div className="space-y-8" data-testid="discovery-sectioned">
						<DiscoverySection
							id="your-workspace"
							icon={<Bookmark size={16} aria-hidden="true" />}
							title="Your workspace"
						>
							<RecentlyUsedStrip onSelectApi={handleSelectApi} />
							<BrowseResults
								expandedId={expandedId}
								onCardClick={handleCardClick}
								forcedSource="workspace"
								emptyMode="inline"
								onImport={handleImport}
								importPendingApiId={pendingApiId}
							/>
						</DiscoverySection>

						<div
							className="border-border/40 border-t pt-6"
							data-testid="discovery-section-divider"
						>
							<DiscoverySection
								id="from-the-catalog"
								icon={<Compass size={16} aria-hidden="true" />}
								title="From the catalog"
								rightSlot={
									<AppLink
										href="/discover"
										className="text-primary hover:text-primary/80 inline-flex items-center gap-1 text-xs font-medium"
										data-testid="browse-all-discover"
									>
										Browse all in Discover →
									</AppLink>
								}
							>
								<BrowseResults
									expandedId={expandedId}
									onCardClick={handleCardClick}
									forcedSource="directory"
									emptyMode="inline"
									onImport={handleImport}
									importPendingApiId={pendingApiId}
								/>
							</DiscoverySection>
						</div>
					</div>
				) : (
					// `forcedSource` is optional on `DiscoveryViewProps` but
					// every production mounting site sets it (`/discover`
					// → `'directory'`, `/workspace` → sectioned mode).
					// Default to `'directory'` for the unreachable
					// single-mode-without-forcedSource branch — it's the
					// conservative production default (the `/catalog`
					// surface that historically rendered the toggle was a
					// router redirect to `/discover` even before the
					// toggle was deleted).
					<BrowseResults
						query={query || undefined}
						expandedId={expandedId}
						onCardClick={handleCardClick}
						forcedSource={forcedSource ?? 'directory'}
						onShownCountChange={!query ? setShownCount : undefined}
						onImport={handleImport}
						importPendingApiId={pendingApiId}
					/>
				)}
			</div>

			<ApiDetailSheet
				apiId={stickyInspect}
				open={inspectParam !== null}
				inspectedOp={opParam}
				inspectedWf={wfParam}
				initialEntity={selectedEntity}
				onClose={handleCloseSheet}
				onAfterClose={() => {
					setStickyInspect(null);
					setSelectedEntity(undefined);
				}}
				onSelectOp={handleSelectOp}
				onSelectWf={handleSelectWf}
				onSelectApi={handleSelectApi}
			/>

			<WorkflowDetailSheet
				workflowId={stickyInspectWf}
				open={inspectWfParam !== null}
				initialEntity={selectedWfEntity}
				onClose={handleCloseWfSheet}
				onAfterClose={() => {
					setStickyInspectWf(null);
					setSelectedWfEntity(undefined);
				}}
			/>

			<KeyboardShortcutsBar
				shortcuts={[
					{ keys: ['/'], label: 'search' },
					{ keys: ['↑', '↓', '←', '→'], label: 'navigate' },
					{ keys: ['Enter'], label: 'open' },
					{ keys: ['Esc'], label: 'close' },
					{ keys: [MOD_KEY, '/'], chord: true, label: 'help' },
				]}
			/>
		</>
	);
}
