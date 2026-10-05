/**
 * CatalogLedger — the Library's public catalog as a ledger.
 *
 *   - Browse: "In your workspace" first, then A–Z letter groups of vendors
 *     and `0–9 & symbols` last (rules in `lib/catalogGroups` — paging only
 *     ever appends), with a sticky A–Z·# rail that jumps by vendor and
 *     follows the scroll position. Jumping to a letter that hasn't loaded
 *     starts a second keyset range AT the letter (`jump` / `onJump` — one
 *     page), shown after a "not loaded yet" gap; scrolling up into the gap
 *     prepends whole letters (scroll position pinned) until it meets the
 *     head and the two merge. Without `onJump` (or if the server rejects the
 *     jump cursor) it pages forward in big pages with a "Loading…" chip.
 *   - Search: a flat ranked list with the query highlighted; no rail.
 *   - Columns API · Vendor · Version · Status; on phones the status sits under
 *     the API name. A blank status means "available"; row actions (GitHub ·
 *     Add / Open → / Review update →) fade in on hover/focus, and are always
 *     shown on phones and touch screens.
 *   - Keyboard: plain Tab order through each row's real controls (the name
 *     button previews; GitHub · Add / Open) — no custom shortcuts.
 *   - Drag a row onto "Your workspace" to add it (`useDragToAdd`, wired by
 *     the page); draggable rows show a grab cursor + grip on hover.
 *   - Keyset infinite scroll: a sentinel rooted on the shell's scroller.
 */
import {
	useCallback,
	useEffect,
	useLayoutEffect,
	useMemo,
	useRef,
	useState,
	type PointerEvent as ReactPointerEvent,
	type RefObject,
} from 'react';
import { Compass, Upload } from 'lucide-react';
import {
	AlphaRail,
	Button,
	EmptyState,
	ErrorAlert,
	Ledger,
	LedgerGroupHeading,
	LedgerHead,
	LedgerHeadCell,
	LoadingSpinner,
	Skeleton,
	type AlphaRailLetter,
} from '@/shared/ui';
import { shellScroller, shellScrollRoot, vendorIconPropsFor } from '@/shared/lib';
import { useMediaQuery } from '@/shared/hooks';
import type { Credential } from '@/shared/credentials/api';
import type { DiscoveryEntity, WorkspaceDigestRow } from '@/modules/discover/api';
import {
	readyCredentialsForEntry,
	workspaceHrefFor,
} from '@/modules/discover/lib/catalogRelations';
import { versionLabel } from '@/modules/discover/lib/catalogSpec';
import {
	buildCatalogLedger,
	letterHeading,
	railLetterLabel,
	frontierKeyOf,
	jumpStartKey,
	railLetterReachable,
	seekStatus,
	vendorOf,
	type LedgerItem,
	type RailLetter,
} from '@/modules/discover/lib/catalogGroups';
import type { DragIdentity } from '@/modules/discover/lib/useDragToAdd';
import {
	CatalogApiRow,
	CatalogGapRow,
	CatalogVendorRow,
	CatalogVendorSummaryRow,
} from '@/modules/discover/components/CatalogLedgerRows';

/** API · Vendor · Version · Status (≥ sm); on phones the status stacks under the name. */
const GROUPED_COLS =
	'[--ledger-cols:minmax(0,1fr)_auto] sm:[--ledger-cols:minmax(0,1fr)_minmax(96px,22%)_80px_128px]';
/** Search rows carry vendor + version inline: API · Status. */
const FLAT_COLS = '[--ledger-cols:minmax(0,1fr)_auto] sm:[--ledger-cols:minmax(0,1fr)_128px]';
/** Tailwind `sm` — below it a row's status stacks under its name. */
const SM_QUERY = '(min-width: 640px)';
/** Below the sticky toolbar (its height + a little air). */
const SPY_OFFSET_PX = 84;
/** Where a jumped-to letter heading lands (under the sticky toolbar). */
const HEADING_SCROLL_MARGIN_PX = 76;

/**
 * A rail jump's range (see `useCatalogJump`): the feed started at a letter,
 * shown after the head with a "not loaded yet" gap until the two meet.
 */
interface CatalogLedgerJump {
	/** The keyset start (`jumpStartKey(letter)`). */
	startKey: string;
	entities: DiscoveryEntity[];
	hasNextPage: boolean;
	isFetchingNextPage: boolean;
	isPending: boolean;
	isFetched: boolean;
	error: Error | null;
	onLoadMore: () => void;
	/** Grow the range back by one letter (towards the head). */
	onLoadEarlier: () => void;
	isLoadingEarlier: boolean;
}

/** Load when `ref` scrolls into view (re-armed after each load). */
function useLoadWhenVisible(
	ref: RefObject<HTMLElement | null>,
	{
		enabled,
		busy,
		onLoad,
		rootMargin,
	}: { enabled: boolean; busy: boolean; onLoad: () => void; rootMargin: string },
) {
	useEffect(() => {
		const node = ref.current;
		if (!node || !enabled) return;
		const observer = new IntersectionObserver(
			(observed) => {
				// Entries can batch (out, then back in): the LAST one is current.
				// Reading `[0]` saw a stale "not intersecting" and stalled paging.
				const latest = observed[observed.length - 1];
				if (latest?.isIntersecting && !busy) onLoad();
			},
			{ root: shellScrollRoot(), rootMargin },
		);
		observer.observe(node);
		return () => observer.disconnect();
	}, [ref, enabled, busy, onLoad, rootMargin]);
}

interface CatalogLedgerDrag {
	onRowPointerDown: (event: ReactPointerEvent<HTMLElement>, identity: DragIdentity) => void;
	consumeDragClick: () => boolean;
	draggingId: string | null;
}

interface CatalogLedgerProps {
	entities: DiscoveryEntity[];
	loading: boolean;
	error: Error | null;
	/** Re-run the catalog feed (the error state's Try again). */
	onRetry?: () => void;
	retrying?: boolean;
	activeId: string | null;
	onOpen: (entity: DiscoveryEntity) => void;
	onImport: (entity: DiscoveryEntity) => void;
	pendingApiIds: Set<string>;
	/** The committed (debounced) search query; non-empty → flat list. */
	query: string;
	hasNextPage: boolean;
	isFetchingNextPage: boolean;
	/** Next page; `bulk` = a seek's big page. */
	onLoadMore: (options?: { bulk?: boolean }) => void;
	workspaceByCatalogId?: Map<string, WorkspaceDigestRow[]>;
	credentials?: Credential[] | null;
	onImportOwn: () => void;
	drag?: CatalogLedgerDrag;
	/** Polite live-region text (e.g. "Adding X to your workspace"). */
	announcement?: string;
	/** All catalog entries in your workspace, for a complete top group. */
	workspaceEntities?: DiscoveryEntity[];
	/** The current rail-jump range, if any. */
	jump?: CatalogLedgerJump;
	/**
	 * Start a jump range at a letter that isn't loaded (one page, instead of
	 * paging through everything before it). Without it — or if the server
	 * rejects the jump — the ledger pages forward to the letter instead.
	 */
	onJump?: (letter: RailLetter) => void;
}

function rowFacts(
	entity: DiscoveryEntity,
	workspaceByCatalogId: Map<string, WorkspaceDigestRow[]> | undefined,
	credentials: Credential[] | null,
) {
	const matches = entity.registered ? workspaceByCatalogId?.get(entity.apiId) : undefined;
	const match = matches?.length === 1 ? matches[0] : null;
	const title = match?.title ?? entity.summary;
	const domain = vendorOf(entity);
	return {
		title,
		icon: match
			? vendorIconPropsFor({
					title: match.title,
					host: match.host,
					vendor: match.ref.vendor,
					iconUrl: match.iconUrl,
				})
			: { name: entity.summary, vendor: entity.vendor },
		vendorLabel: domain !== title ? domain : '',
		versionLabel: entity.version ? versionLabel(entity.version) : null,
		openHref: entity.registered ? workspaceHrefFor(matches ?? []) : null,
		openLabel: match ? `Open ${match.title} in your workspace` : 'Open your workspace',
		reviewHref: entity.registered && entity.updateAvailable && match ? match.href : null,
		readyCredentials: entity.registered
			? null
			: readyCredentialsForEntry(
					entity,
					workspaceByCatalogId?.get(entity.apiId),
					credentials,
				),
	};
}

function LedgerSkeleton() {
	return (
		<div
			className="bg-surface-1 rounded-lg px-2 pt-0.5 pb-2"
			data-testid="catalog-ledger-loading"
			aria-busy="true"
		>
			<div className="h-9" />
			{Array.from({ length: 8 }).map((_, i) => (
				<div
					key={i}
					className={`flex h-[38px] items-center gap-2.5 rounded-md px-2.5 ${i % 2 ? 'bg-surface-zebra' : ''}`}
				>
					<Skeleton className="h-6 w-6 rounded-[7px]" />
					<Skeleton className="h-3.5 w-40" />
					<Skeleton className="ml-auto h-3 w-16" />
				</div>
			))}
		</div>
	);
}

export function CatalogLedger({
	entities,
	loading,
	error,
	onRetry,
	retrying = false,
	activeId,
	onOpen,
	onImport,
	pendingApiIds,
	query,
	hasNextPage,
	isFetchingNextPage,
	onLoadMore,
	workspaceByCatalogId,
	credentials = null,
	onImportOwn,
	drag,
	announcement = '',
	workspaceEntities,
	jump: jumpProp,
	onJump,
}: CatalogLedgerProps) {
	// A failed jump (e.g. the server rejected the cursor) is ignored: the
	// rail falls back to paging forward.
	const jump = jumpProp && !jumpProp.error ? jumpProp : undefined;
	const searching = query.trim().length > 0;
	// One query for the whole list (not one per memoised row).
	const stackStatus = !useMediaQuery(SM_QUERY);
	const [expanded, setExpanded] = useState<ReadonlySet<string>>(new Set());
	const [currentLetter, setCurrentLetter] = useState<string | null>(null);
	const [seekLetter, setSeekLetter] = useState<RailLetter | null>(null);
	/** A jumped-to letter waiting for its range's first page, to scroll to it. */
	const [jumpLetter, setJumpLetter] = useState<RailLetter | null>(null);
	const sentinelRef = useRef<HTMLDivElement | null>(null);
	const gapRef = useRef<HTMLDivElement | null>(null);
	const headingRefs = useRef(new Map<string, HTMLDivElement>());

	const model = useMemo(
		() =>
			buildCatalogLedger(entities, {
				searching,
				hasNextPage,
				expandedVendors: expanded,
				workspaceEntities,
				jump: jump
					? {
							entities: jump.entities,
							startKey: jump.startKey,
							hasNextPage: jump.hasNextPage,
						}
					: undefined,
			}),
		[entities, searching, hasNextPage, expanded, workspaceEntities, jump],
	);
	// Which feed pages at the bottom: the jump range (it's the tail), the head
	// once merged only if it has overtaken the jump, else the head.
	const tailIsJump =
		!!jump && (!model.jumpMerged || frontierKeyOf(jump.entities) >= frontierKeyOf(entities));
	const tail = tailIsJump
		? {
				hasNext: model.jumpMerged ? hasNextPage && jump.hasNextPage : jump.hasNextPage,
				busy: jump.isFetchingNextPage,
				load: jump.onLoadMore,
			}
		: {
				hasNext: model.jumpMerged ? hasNextPage && !!jump?.hasNextPage : hasNextPage,
				busy: isFetchingNextPage,
				load: onLoadMore,
			};
	const anyMore = hasNextPage || !!jump?.hasNextPage;
	// Per-entity facts, cached by entity object (pages keep theirs), so a new
	// page or a jump doesn't hand every memoised row fresh props.
	const factsFor = useMemo(() => {
		const cache = new WeakMap<DiscoveryEntity, ReturnType<typeof rowFacts>>();
		return (e: DiscoveryEntity) => {
			let f = cache.get(e);
			if (!f) {
				f = rowFacts(e, workspaceByCatalogId, credentials);
				cache.set(e, f);
			}
			return f;
		};
	}, [workspaceByCatalogId, credentials]);
	const facts = useMemo(() => {
		const map = new Map<string, ReturnType<typeof rowFacts>>();
		for (const e of [...(workspaceEntities ?? []), ...entities, ...(jump?.entities ?? [])]) {
			map.set(e.id, factsFor(e));
		}
		return map;
	}, [entities, jump?.entities, workspaceEntities, factsFor]);

	// One stable handler for every row, so the memoised rows don't all
	// re-render whenever the ledger does (a new page, a jump, a selection).
	const startDrag = drag?.onRowPointerDown;
	const factsRef = useRef(facts);
	useEffect(() => {
		factsRef.current = facts;
	}, [facts]);
	const onRowPointerDown = useCallback(
		(e: ReactPointerEvent<HTMLElement>, entity: DiscoveryEntity) => {
			const f = factsRef.current.get(entity.id);
			if (!f || !startDrag) return;
			startDrag(e, { entity, name: f.title, vendor: vendorOf(entity), icon: f.icon });
		},
		[startDrag],
	);

	/** Expand/collapse a big vendor, by its item key (`vendor:<domain>`). */
	const toggleVendor = useCallback((key: string, open: boolean) => {
		setExpanded((prev) => {
			const next = new Set(prev);
			if (open) next.add(key);
			else next.delete(key);
			return next;
		});
	}, []);

	// Infinite scroll — rooted on the shell's scroller (a viewport root's
	// margin can't reach past `<main>`'s clip). Both feeds' loaders are stable.
	useLoadWhenVisible(sentinelRef, {
		enabled: tail.hasNext,
		busy: tail.busy,
		onLoad: tail.load,
		rootMargin: '200px',
	});
	// The gap before a jump range fills backward — a letter at a time, just
	// above the jumped range — as it scrolls into view. Only when the reader
	// scrolls UP into it — not as a jump's scroll passes over it going down.
	const scrollingUp = useRef(false);
	useEffect(() => {
		const scroller = shellScroller();
		const pos = () => (scroller instanceof Window ? window.scrollY : scroller.scrollTop);
		let last = pos();
		const onScroll = () => {
			const now = pos();
			scrollingUp.current = now < last;
			last = now;
		};
		scroller.addEventListener('scroll', onScroll, { passive: true });
		return () => scroller.removeEventListener('scroll', onScroll);
	}, []);
	const loadEarlierFn = jump?.onLoadEarlier;
	// The prepended letter lands above the reader: pin the jumped range's
	// first heading where it was (browsers' scroll anchoring would pick a
	// head row above the gap, which doesn't move). Captured per request,
	// applied right after the rows commit.
	const pin = useRef<{ key: string; top: number } | null>(null);
	const headingTop = (key: string) => headingRefs.current.get(key)?.getBoundingClientRect().top;
	const afterGapKey = useCallback(() => {
		const i = model.items.findIndex((it) => it.kind === 'gap');
		const next = i >= 0 ? model.items.slice(i + 1).find((it) => it.kind === 'group') : null;
		return next?.key ?? null;
	}, [model]);
	const requestEarlier = useCallback(() => {
		const key = afterGapKey();
		const top = key ? headingTop(key) : undefined;
		pin.current = key && top != null ? { key, top } : null;
		loadEarlierFn?.();
	}, [afterGapKey, loadEarlierFn]);
	useLayoutEffect(() => {
		const p = pin.current;
		if (!p) return;
		const top = headingTop(p.key);
		if (top == null) return;
		const delta = top - p.top;
		if (Math.abs(delta) >= 1) {
			const scroller = shellScroller();
			if (scroller instanceof Window) window.scrollBy(0, delta);
			else scroller.scrollTop += delta;
		}
		pin.current = { key: p.key, top: top - delta };
		if (!jump?.isLoadingEarlier) pin.current = null;
	}, [model, jump?.isLoadingEarlier]);
	const loadEarlier = useCallback(() => {
		if (scrollingUp.current) requestEarlier();
	}, [requestEarlier]);
	useLoadWhenVisible(gapRef, {
		enabled: !!model.gap && !!loadEarlierFn && !jumpLetter,
		busy: !!jump?.isLoadingEarlier,
		onLoad: loadEarlier,
		rootMargin: '-140px 0px 0px 0px',
	});

	// Scroll-spy: the current letter is the last heading that has scrolled
	// up to the sticky toolbar.
	useEffect(() => {
		if (model.mode !== 'grouped') return;
		const scroller = shellScroller();
		let raf = 0;
		const update = () => {
			raf = 0;
			const top =
				(scroller instanceof Window ? 0 : scroller.getBoundingClientRect().top) +
				SPY_OFFSET_PX;
			let current: string | null = null;
			for (const entry of model.rail) {
				const el = entry.anchorKey ? headingRefs.current.get(entry.anchorKey) : undefined;
				if (!el) continue;
				if (current == null) current = entry.letter;
				if (el.getBoundingClientRect().top <= top) current = entry.letter;
			}
			setCurrentLetter(current);
		};
		const onScroll = () => {
			if (!raf) raf = window.requestAnimationFrame(update);
		};
		update();
		scroller.addEventListener('scroll', onScroll, { passive: true });
		return () => {
			scroller.removeEventListener('scroll', onScroll);
			if (raf) window.cancelAnimationFrame(raf);
		};
	}, [model]);

	/**
	 * Scroll a letter's heading under the toolbar. A rail jump (`focus`) also
	 * moves focus to that heading, so keyboard and screen-reader users land
	 * on the list instead of staying on the rail.
	 */
	const scrollToLetter = useCallback(
		(letter: RailLetter, { focus = false }: { focus?: boolean } = {}) => {
			const entry = model.rail.find((r) => r.letter === letter);
			const el = entry?.anchorKey ? headingRefs.current.get(entry.anchorKey) : undefined;
			if (!el) return false;
			const reduce = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
			// Scroll the shell's scroller only. `scrollIntoView` also scrolls
			// every scrollable ancestor — including the shell's `overflow-hidden`
			// frame — which shoved the whole page up (toolbar, rail top and the
			// workspace panel's header gone, a void below the list).
			const scroller = shellScroller();
			const base = scroller instanceof Window ? 0 : scroller.getBoundingClientRect().top;
			const from = scroller instanceof Window ? window.scrollY : scroller.scrollTop;
			scroller.scrollTo({
				top: Math.max(
					0,
					el.getBoundingClientRect().top - base + from - HEADING_SCROLL_MARGIN_PX,
				),
				behavior: reduce ? 'auto' : 'smooth',
			});
			setCurrentLetter(letter);
			if (focus) {
				el.tabIndex = -1;
				el.focus({ preventScroll: true });
			}
			return true;
		},
		[model],
	);

	// A letter that isn't loaded yet: page forward (big pages) until it
	// arrives, then jump. Pages only append, so rows already on screen stay
	// put meanwhile. If the feed passes the letter without any vendors under
	// it, land on the next letter that has some.
	useEffect(() => {
		if (!seekLetter) return;
		const status = seekStatus(model, seekLetter, hasNextPage);
		if (status === 'load') {
			if (!isFetchingNextPage) onLoadMore({ bulk: true });
			return;
		}
		setSeekLetter(null);
		if (status === 'ready') {
			scrollToLetter(seekLetter, { focus: true });
			return;
		}
		const after = model.rail.slice(model.rail.findIndex((r) => r.letter === seekLetter) + 1);
		const next = after.find((r) => r.anchorKey);
		if (next) scrollToLetter(next.letter, { focus: true });
	}, [seekLetter, model, scrollToLetter, hasNextPage, isFetchingNextPage, onLoadMore]);

	// A jump: once its range's first page is in, land on the letter — or, if
	// the catalog has nothing under it, on the next letter that has rows.
	useEffect(() => {
		if (!jumpLetter) return;
		if (jumpProp?.error) {
			// Fall back to paging forward to it.
			setJumpLetter(null);
			setSeekLetter(jumpLetter);
			return;
		}
		if (scrollToLetter(jumpLetter, { focus: true })) {
			setJumpLetter(null);
			return;
		}
		if (!jump || jump.startKey !== jumpStartKey(jumpLetter) || !jump.isFetched) return;
		if (jump.isFetchingNextPage) return;
		const after = model.rail.slice(model.rail.findIndex((r) => r.letter === jumpLetter) + 1);
		const next = after.find((r) => r.anchorKey);
		if (next) {
			setJumpLetter(null);
			scrollToLetter(next.letter, { focus: true });
		} else if (jump.hasNextPage) {
			jump.onLoadMore();
		} else {
			setJumpLetter(null);
		}
	}, [jumpLetter, jump, jumpProp?.error, model, scrollToLetter]);

	const railLetters: AlphaRailLetter[] = useMemo(
		() =>
			model.rail.map((entry) => ({
				key: entry.letter,
				glyph: entry.letter,
				enabled: railLetterReachable(entry, anyMore),
				description: railLetterLabel(entry, anyMore),
			})),
		[model, anyMore],
	);

	if (error) {
		return (
			<ErrorAlert
				title="Couldn't load the catalog"
				message={error.message}
				onRetry={onRetry}
				retrying={retrying}
			/>
		);
	}
	if (loading && entities.length === 0) return <LedgerSkeleton />;
	if (entities.length === 0) {
		return (
			<EmptyState
				icon={<Compass className="h-6 w-6" aria-hidden="true" />}
				title={searching ? 'No matching APIs' : 'No APIs yet'}
				description={
					searching
						? "Try a different search term, or switch the filter. Can't find it?"
						: 'The public catalog will appear here.'
				}
				action={
					<Button
						variant="outline"
						size="sm"
						onClick={onImportOwn}
						data-testid="discover-empty-upload-own"
					>
						<Upload size={14} aria-hidden="true" />
						Import your own API
					</Button>
				}
			/>
		);
	}

	let zebra = 0;
	const renderItem = (item: LedgerItem) => {
		if (item.kind === 'group') {
			zebra = 0;
			return (
				<LedgerGroupHeading
					key={item.key}
					label={item.label}
					detail={item.detail}
					id={
						item.letter
							? `catalog-letter-${item.letter === '#' ? 'num' : item.letter}`
							: undefined
					}
					ref={(el) => {
						if (el) headingRefs.current.set(item.key, el);
						else headingRefs.current.delete(item.key);
					}}
				/>
			);
		}
		if (item.kind === 'gap') {
			zebra = 0;
			return (
				<CatalogGapRow
					key={item.key}
					ref={gapRef}
					from={item.from}
					to={item.to}
					loading={!!jump?.isLoadingEarlier}
					onLoad={requestEarlier}
				/>
			);
		}
		const isZebra = zebra++ % 2 === 1;
		if (item.kind === 'vendor') {
			return (
				<CatalogVendorRow
					key={item.key}
					vendor={item.vendor}
					total={item.total}
					zebra={isZebra}
					collapsible={item.collapsible}
					onCollapse={() => toggleVendor(item.key, false)}
				/>
			);
		}
		if (item.kind === 'vendor-summary') {
			return (
				<CatalogVendorSummaryRow
					key={item.key}
					vendor={item.vendor}
					total={item.total}
					names={item.names}
					more={item.more}
					zebra={isZebra}
					onExpand={() => toggleVendor(item.key, true)}
				/>
			);
		}
		const f = facts.get(item.entity.id)!;
		return (
			<CatalogApiRow
				key={item.key}
				entity={item.entity}
				{...f}
				flat={model.mode === 'flat'}
				query={query}
				child={item.child}
				childContinues={item.child && !item.lastChild}
				zebra={isZebra}
				selected={item.entity.id === activeId}
				pending={pendingApiIds.has(item.entity.apiId)}
				dragging={drag?.draggingId === item.entity.id}
				onOpen={onOpen}
				onImport={onImport}
				consumeDragClick={drag?.consumeDragClick}
				onPointerDown={drag ? onRowPointerDown : undefined}
				stackStatus={stackStatus}
			/>
		);
	};

	const table = (
		<div className="min-w-0">
			<Ledger
				label="API catalog"
				columnsClassName={model.mode === 'flat' ? FLAT_COLS : GROUPED_COLS}
				data-testid="catalog-ledger"
				data-mode={model.mode}
			>
				<LedgerHead>
					<LedgerHeadCell sorted={model.mode === 'grouped'}>API</LedgerHeadCell>
					{model.mode === 'grouped' && (
						<>
							<LedgerHeadCell className="hidden sm:block">Vendor</LedgerHeadCell>
							<LedgerHeadCell className="hidden sm:block">Version</LedgerHeadCell>
						</>
					)}
					{/* Phones show each row's status under its name. */}
					{!stackStatus && <LedgerHeadCell>Status</LedgerHeadCell>}
				</LedgerHead>
				{model.items.map(renderItem)}
			</Ledger>
			{(seekLetter ?? jumpLetter) && (
				// Sticks to the viewport's bottom while the table is on screen.
				<div className="pointer-events-none sticky bottom-3 z-10 flex justify-center pt-2">
					<span
						className="bg-surface-tonal text-foreground-lighter inline-flex items-center gap-2 rounded-full px-3 py-1 text-xs font-semibold shadow-md"
						data-testid="catalog-seek-status"
						aria-hidden="true"
					>
						<LoadingSpinner size="sm" />
						Loading APIs under {letterHeading((seekLetter ?? jumpLetter)!)}…
					</span>
				</div>
			)}
			{tail.hasNext && (
				<div ref={sentinelRef} className="flex justify-center py-3">
					<Button
						variant="ghost"
						size="sm"
						loading={tail.busy}
						onClick={() => tail.load()}
						data-testid="discovery-load-more"
					>
						{tail.busy ? 'Loading…' : 'Load more'}
					</Button>
				</div>
			)}
			<span
				className="sr-only"
				role="status"
				aria-live="polite"
				data-testid="catalog-announcer"
			>
				{(seekLetter ?? jumpLetter)
					? `Loading APIs under ${letterHeading((seekLetter ?? jumpLetter)!)}…`
					: announcement}
			</span>
		</div>
	);

	if (model.mode === 'flat') return table;

	return (
		<div className="grid grid-cols-[24px_minmax(0,1fr)] gap-2.5">
			<AlphaRail
				// On a short viewport the 27 letters scroll inside the rail rather
				// than running under the fold.
				className="sticky top-[68px] max-h-[calc(100dvh-8.5rem)] [scrollbar-width:none] self-start overflow-y-auto"
				letters={railLetters}
				current={seekLetter ?? jumpLetter ?? currentLetter}
				busy={seekLetter ?? jumpLetter}
				onJump={(key) => {
					const letter = key as RailLetter;
					if (scrollToLetter(letter, { focus: true })) return;
					setSeekLetter(null);
					if (onJump) {
						onJump(letter);
						setJumpLetter(letter);
					} else {
						setSeekLetter(letter);
					}
				}}
			/>
			{table}
		</div>
	);
}
