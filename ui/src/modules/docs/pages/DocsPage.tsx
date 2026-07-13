/**
 * Docs page — the in-app developer documentation portal (public route).
 *
 * A single scrolling document with a sticky left rail (DocsSidebar) that walks
 * the reader from "what is this" to the raw API reference, in narrative order:
 *
 *   Get started — Overview · Installation · Quickstart
 *   Concepts    — Architecture · Permissions & scopes
 *   Reference   — CLI · API reference
 *
 * The API reference is the LAST destination, not the front door — this is the
 * Stripe/Supabase pattern: lead with concepts and a guided path, end with the
 * exhaustive reference. The reference is rendered natively (see ApiReference)
 * from our own data — the `reference` payload (the authoritative auth model)
 * enriched with parameters/responses from the OpenAPI spec — so it shares the
 * page's theme, rail, and scroll-spy instead of embedding a second app.
 */
import { useCallback, useMemo, useState } from 'react';
import { BookOpen, Network } from 'lucide-react';
import { LoadingState, ErrorAlert } from '@/shared/ui';
import { useDocs, useCliReference, useBrokerSpec } from '@/modules/docs/api/hooks';
import type { ReferencePayload } from '@/modules/docs/api/types';
import { DocsSidebar } from '@/modules/docs/components/DocsSidebar';
import { DocsMobileNav } from '@/modules/docs/components/DocsMobileNav';
import { DocsTopNav } from '@/modules/docs/components/DocsTopNav';
import { DocsSectionBlock } from '@/modules/docs/components/DocsSectionBlock';
import { OverviewSection } from '@/modules/docs/components/sections/OverviewSection';
import { InstallationSection } from '@/modules/docs/components/sections/InstallationSection';
import { QuickstartSection } from '@/modules/docs/components/sections/QuickstartSection';
import { ArchitectureSection } from '@/modules/docs/components/sections/ArchitectureSection';
import { PermissionsSection } from '@/modules/docs/components/sections/PermissionsSection';
import { CliReferenceView } from '@/modules/docs/components/CliReference';
import {
	ApiReferenceView,
	tagGroupAnchorId,
	MODELS_ANCHOR,
} from '@/modules/docs/components/ApiReference';
import { parseSpec } from '@/modules/docs/lib/apiSpec';
import { scrollToAnchor } from '@/modules/docs/lib/anchor';
import { DOCS_SECTIONS } from '@/modules/docs/lib/nav';
import type { DocsSubSection } from '@/modules/docs/lib/nav';
import { useScrollSpy } from '@/modules/docs/lib/useScrollSpy';

const SECTION_IDS = DOCS_SECTIONS.map((s) => s.id);
/** Sub-anchor ids (e.g. CLI binaries) for the secondary scroll-spy. */
const SUB_IDS = DOCS_SECTIONS.flatMap((s) => s.children?.map((c) => c.id) ?? []);

/** Anchor namespace for the Broker reference, so its anchors never collide with
 *  the control-plane reference's (both reuse ApiReferenceView). */
const BROKER_ANCHOR_PREFIX = 'broker';

/** The Broker is authed solely by bearer token and has no scope/actor rows, so
 *  it renders with an empty reference payload — the spec carries everything. */
const EMPTY_REFERENCE: ReferencePayload = {
	schema: '',
	total: 0,
	groups: [],
	endpoints: [],
};

export default function DocsPage() {
	const { data, isPending, error, refetch } = useDocs();
	const cli = useCliReference();
	const broker = useBrokerSpec();
	const [activeId, setActiveId] = useState<string>(SECTION_IDS[0]);
	const observedActive = useScrollSpy(SECTION_IDS);
	// Secondary spy for CLI binary sub-anchors — only lights up while in view
	// (defaultFirst = false), so the rail's sub-items track the scroll position
	// as the reader moves from one binary's block to the next.
	const observedSub = useScrollSpy(SUB_IDS, '-120px 0px -60% 0px', false);

	// The API reference's sub-groups come from the spec (its x-tagGroups), so the
	// rail's children under "API reference" are computed at runtime. Models gets
	// a trailing entry. Falls back to the static (none) when the spec is absent.
	const parsedApi = useMemo(() => (data?.spec ? parseSpec(data.spec) : null), [data?.spec]);

	const apiChildren = useMemo<DocsSubSection[]>(() => {
		if (!parsedApi) return [];
		const groups: DocsSubSection[] = parsedApi.groups.map((g) => ({
			id: tagGroupAnchorId(g.name),
			label: g.name,
		}));
		if (parsedApi.models.length > 0) groups.push({ id: MODELS_ANCHOR, label: 'Models' });
		return groups;
	}, [parsedApi]);

	const modelNames = useMemo(() => parsedApi?.models.map((m) => m.name) ?? [], [parsedApi]);

	// The Broker is a separate data-plane service with its own spec; parse it for
	// the rail children + scroll-spy the same way as the control-plane reference.
	const parsedBroker = useMemo(
		() => (broker.data ? parseSpec(broker.data) : null),
		[broker.data],
	);

	const brokerChildren = useMemo<DocsSubSection[]>(() => {
		if (!parsedBroker) return [];
		const groups: DocsSubSection[] = parsedBroker.groups.map((g) => ({
			id: tagGroupAnchorId(g.name, BROKER_ANCHOR_PREFIX),
			label: g.name,
		}));
		if (parsedBroker.models.length > 0)
			groups.push({ id: `${BROKER_ANCHOR_PREFIX}-${MODELS_ANCHOR}`, label: 'Models' });
		return groups;
	}, [parsedBroker]);

	const extraChildren = useMemo(
		() => ({ api: apiChildren, broker: brokerChildren }),
		[apiChildren, brokerChildren],
	);

	// API sub-anchor ids feed the secondary spy too, so the rail highlights the
	// current tag-group while reading the reference.
	const apiSubIds = useMemo(() => apiChildren.map((c) => c.id), [apiChildren]);
	const observedApiSub = useScrollSpy(apiSubIds, '-120px 0px -70% 0px', false);

	const brokerSubIds = useMemo(() => brokerChildren.map((c) => c.id), [brokerChildren]);
	const observedBrokerSub = useScrollSpy(brokerSubIds, '-120px 0px -70% 0px', false);

	// Flatten the Broker spec into search entries (namespaced anchors) so its
	// operations + models are reachable from the global top-nav search too.
	const brokerSearch = useMemo(() => {
		if (!parsedBroker) return undefined;
		return {
			anchorPrefix: BROKER_ANCHOR_PREFIX,
			operations: parsedBroker.groups.flatMap((g) =>
				g.tags.flatMap((t) =>
					t.operations.map((op) => ({
						method: op.method,
						path: op.path,
						summary: op.summary,
						operationId: op.operationId,
					})),
				),
			),
			models: parsedBroker.models.map((m) => m.name),
		};
	}, [parsedBroker]);

	const handleNavigate = useCallback((id: string) => {
		const el = document.getElementById(id);
		if (el) {
			// Re-pinning scroll: lazily-mounted blocks above the target grow as
			// they mount, so a one-shot scroll drifts. scrollToAnchor re-scrolls
			// until the target settles (and respects each block's scroll-margin).
			scrollToAnchor(id);
			history.replaceState(null, '', `#${id}`);
			// Only top-level sections drive the rail's active state; sub-anchors
			// (CLI commands, binaries) let scroll-spy resolve the parent section.
			if (SECTION_IDS.includes(id)) setActiveId(id);
		}
	}, []);

	if (isPending) {
		return <LoadingState message="Loading the documentation…" />;
	}

	if (error || !data) {
		return (
			<div className="px-page-gutter mx-auto max-w-2xl space-y-2 py-10">
				<ErrorAlert
					message={
						error?.message.includes('/reference/endpoints.json')
							? "Couldn't load the endpoint reference. This server may predate the endpoint reference (jentic-one #602)."
							: (error?.message ?? 'Failed to load documentation.')
					}
				/>
				<button
					type="button"
					onClick={() => void refetch()}
					className="text-primary text-sm font-medium hover:underline"
				>
					Retry
				</button>
			</div>
		);
	}

	const active = observedActive ?? activeId;

	// Pick the sub-anchor highlight from whichever parent section is active, so
	// the CLI sub-spy doesn't "stick" to its last command after the reader has
	// scrolled past CLI into the API reference (each sub-spy reports the last
	// anchor it passed, which never clears on its own once fully scrolled by).
	const activeSubId =
		active === 'api'
			? observedApiSub
			: active === 'broker'
				? observedBrokerSub
				: active === 'cli'
					? observedSub
					: null;

	return (
		<div className="jentic-docs-root min-h-screen">
			<DocsTopNav
				reference={data.reference}
				binaries={cli.data?.binaries}
				models={modelNames}
				broker={brokerSearch}
				onJump={handleNavigate}
			/>

			<div className="px-page-gutter mx-auto grid max-w-[100rem] grid-cols-1 gap-8 pt-0 pb-6 lg:grid-cols-[15rem_minmax(0,1fr)] lg:pt-6">
				{/* Sticky left rail (desktop) */}
				<aside className="hidden lg:sticky lg:top-[4.5rem] lg:block lg:h-[calc(100vh-5.5rem)] lg:overflow-y-auto">
					<DocsSidebar
						activeId={active}
						activeSubId={activeSubId}
						extraChildren={extraChildren}
						onNavigate={handleNavigate}
					/>
				</aside>

				{/* Single scrolling document */}
				<main className="min-w-0 space-y-14">
					{/* Mobile section navigator (replaces the offscreen rail below lg) */}
					<DocsMobileNav activeId={active} onNavigate={handleNavigate} />

					<OverviewSection />
					<InstallationSection />
					<QuickstartSection />
					<ArchitectureSection />
					<PermissionsSection payload={data.reference} />

					{/* CLI */}
					<DocsSectionBlock
						id="cli"
						title="CLI"
						intro="Every command, flag, and example for both binaries — generated from the CLI source so it never drifts from --help."
					>
						{cli.isPending && <LoadingState message="Loading the CLI reference…" />}
						{cli.error && (
							<div className="space-y-2">
								<ErrorAlert message="Couldn't load the CLI reference (cli-reference.json). It's a build-time artifact; regenerate with `make cli-reference`." />
								<button
									type="button"
									onClick={() => void cli.refetch()}
									className="text-primary text-sm font-medium hover:underline"
								>
									Retry
								</button>
							</div>
						)}
						{cli.data && <CliReferenceView binaries={cli.data.binaries} />}
					</DocsSectionBlock>

					{/* API reference — native, rendered from our own data so it shares
				    the page's theme, rail, and scroll-spy. The reference payload is
				    the spine (authoritative scopes/actors); the OpenAPI spec enriches
				    each operation with parameters and responses. */}
					<DocsSectionBlock
						id="api"
						title="API reference"
						icon={BookOpen}
						intro="Every operation on this instance, with the scopes it requires up front. Filter by path, method, or scope; the index on the left tracks where you are."
					>
						<ApiReferenceView
							payload={data.reference}
							spec={data.spec}
							parsedSpec={parsedApi ?? undefined}
						/>
					</DocsSectionBlock>

					{/* Broker API — the data plane. It runs as its own standalone
					    service, so its spec is never part of this instance's
					    /openapi.json; the docs render a committed artifact
					    (broker-openapi.json) as its own reference, namespaced so its
					    anchors never collide with the control-plane reference above. */}
					<DocsSectionBlock
						id="broker"
						title="Broker API"
						icon={Network}
						intro="The data plane: a standalone proxy that injects your stored credentials and forwards the call to the upstream API. It runs as its own service — separate from the control plane above — so point requests at the broker's own base URL."
					>
						{broker.isPending && (
							<LoadingState message="Loading the Broker reference…" />
						)}
						{broker.error && (
							<div className="space-y-2">
								<ErrorAlert message="Couldn't load the Broker reference (broker-openapi.json). It's a build-time artifact; regenerate with `make broker-reference`." />
								<button
									type="button"
									onClick={() => void broker.refetch()}
									className="text-primary text-sm font-medium hover:underline"
								>
									Retry
								</button>
							</div>
						)}
						{broker.data && (
							<ApiReferenceView
								payload={EMPTY_REFERENCE}
								spec={broker.data}
								parsedSpec={parsedBroker ?? undefined}
								anchorPrefix={BROKER_ANCHOR_PREFIX}
							/>
						)}
					</DocsSectionBlock>
				</main>
			</div>
		</div>
	);
}
