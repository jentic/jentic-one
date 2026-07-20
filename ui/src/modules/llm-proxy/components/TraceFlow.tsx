/**
 * TraceFlow — the interactive trace-flow canvas (plan §6.2, idea 7b).
 *
 * The agent forest is drawn as a HORIZONTAL left→right dendrogram (hand-rolled —
 * no graph library): the root orchestrator sits on the LEFT, vertically CENTERED
 * against the full height of its subtree, and its subagents branch OUTWARD to the
 * RIGHT, stacked vertically. Each child that itself has children becomes a
 * sub-root of its own left→right subtree.
 *
 * Connectors are drawn by a SINGLE global SVG overlay that lives inside the same
 * transformed content wrapper as the nodes. Every agent card registers its DOM
 * element by id; for each visible parent→child edge the overlay measures both
 * card rects relative to the wrapper, normalises by the zoom factor, and draws a
 * bezier from the parent card's RIGHT-center to the child card's LEFT-center. A
 * single overlay (rather than nested per-subtree SVGs) means a freely DRAGGED
 * node re-routes its edges with no clipping.
 *
 * Each agent's tool calls render as a HORIZONTAL ARROW CHAIN of colour-coded
 * CallBlock chips laid out IN THE SAME ROW to the RIGHT of the card, fully
 * visible. The card + chain form one "head" unit: the parent's outgoing
 * connector starts at the head's right edge (right of the chain) so edges never
 * cross the chips. A per-agent toggle collapses an agent's own chain. A
 * single-agent session skips the tree scaffolding and shows that one agent
 * prominently centered.
 *
 * Interactions (all hand-rolled — no pan/zoom/drag library):
 *   • ZOOM — a CSS scale on the content wrapper (nodes + connector overlay
 *     together), driven by the button cluster, the +/-/0 keys, and a smooth
 *     exponential wheel/trackpad zoom that is anchored at the cursor (the point
 *     under the pointer stays fixed). Cmd/Ctrl + wheel and trackpad pinch (a
 *     ctrl-synthesised wheel) zoom; a plain wheel instead TRAVELS the canvas
 *     (pan via the raw deltas; shift maps a vertical wheel to horizontal).
 *     Range is 10%–400%.
 *   • PAN — hold SPACE + left-drag, or MIDDLE-mouse drag, on the canvas
 *     background to translate the wrapper; pan + zoom compose in one transform.
 *   • NODE DRAG — a plain left-drag on a node CARD repositions THAT node only via
 *     an in-memory `{dx,dy}` offset (deltas divided by zoom so it tracks the
 *     cursor 1:1). A sub-threshold press is still a click (opens drawers / toggles).
 *   • FULLSCREEN — the native Fullscreen API on the canvas, overlay fallback.
 * "Reset view" zeroes zoom + pan AND clears all custom node positions.
 */
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import type React from 'react';
import { motion } from 'framer-motion';
import {
	ArrowUpRight,
	CircleCheck,
	HelpCircle,
	Maximize,
	Maximize2,
	Minimize2,
	Minus,
	Plus,
} from 'lucide-react';
import type { FinalOutput, ProxyAgent, ProxyCall } from '@/modules/llm-proxy/api';
import { AgentNode } from '@/modules/llm-proxy/components/AgentNode';
import { CallChain } from '@/modules/llm-proxy/components/CallBlock';
import { ResultNode, previewOf } from '@/modules/llm-proxy/components/ResultNode';
import { cn } from '@/shared/lib/utils';

/** Synthetic card id for the convergent Result node in the connector registry. */
const RESULT_ID = '__result__';

const ZOOM_MIN = 0.1;
const ZOOM_MAX = 4;
const ZOOM_STEP = 0.1;
/**
 * Wheel/trackpad zoom sensitivity for the exponential (multiplicative) zoom:
 * `next = z * exp(-deltaY * SENSITIVITY)`. Tuned so a trackpad two-finger
 * swipe feels continuous and a mouse-wheel notch is a comfortable step.
 */
const WHEEL_ZOOM_SENSITIVITY = 0.002;
/** Pixel multiplier for plain-wheel canvas travel (raw deltas ≈ 1:1). */
const PAN_WHEEL_FACTOR = 1;
/** `⌘` on Mac, `Ctrl` elsewhere — for the zoom gesture + the controls legend. */
const IS_MAC =
	typeof navigator !== 'undefined' &&
	/mac|iphone|ipad|ipod/i.test(navigator.userAgent ?? navigator.platform ?? '');
const MOD_KEY_LABEL = IS_MAC ? '⌘' : 'Ctrl';
/** Continuous clamp (no percent snapping) — used by the smooth wheel path. */
const clampZoom = (z: number) => Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, z));
/** Discrete clamp with light percent snapping — used by buttons/keys. */
const clampZoomStep = (z: number) =>
	Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, Math.round(z * 100) / 100));

/** Pointer travel (screen px) before a card press becomes a drag, not a click. */
const DRAG_THRESHOLD = 4;

interface Point {
	x: number;
	y: number;
}

interface TreeNode {
	agent: ProxyAgent;
	calls: ProxyCall[];
	children: TreeNode[];
}

interface TraceFlowProps {
	agents: ProxyAgent[];
	calls: ProxyCall[];
	onOpenCall: (call: ProxyCall) => void;
	onOpenChat: (agent: ProxyAgent) => void;
	/** Select an agent node (plain click) → open its detail drawer. */
	onSelectAgent: (agent: ProxyAgent) => void;
	/** The run's closing synthesis, shown by the Result node + Outcome bar. */
	finalOutput: FinalOutput | null;
	/** Open the full final-output drawer (Result node click or Outcome bar). */
	onOpenResult: () => void;
	/**
	 * Reports the element the drawers should portal into: the canvas element
	 * while it is in NATIVE fullscreen (so drawers land in the browser top
	 * layer, on top of the canvas), else `null` (portal to `document.body` as
	 * usual). The overlay-fallback fullscreen stays in normal DOM flow, so no
	 * reparenting is needed there.
	 */
	onFullscreenChange?: (fullscreenEl: HTMLElement | null) => void;
}

/** Build the agent forest from `parent_id`, attaching each node's own calls. */
function buildForest(agents: ProxyAgent[], calls: ProxyCall[]): TreeNode[] {
	const byId = new Map<string, TreeNode>();
	for (const agent of agents) {
		byId.set(agent.id, {
			agent,
			calls: calls.filter((c) => c.agent_id === agent.id),
			children: [],
		});
	}
	const roots: TreeNode[] = [];
	for (const node of byId.values()) {
		const parent = node.agent.parent_id ? byId.get(node.agent.parent_id) : null;
		if (parent) parent.children.push(node);
		else roots.push(node);
	}
	const bySpawn = (a: TreeNode, b: TreeNode) =>
		a.agent.spawned_at.localeCompare(b.agent.spawned_at);
	roots.sort(bySpawn);
	for (const n of byId.values()) n.children.sort(bySpawn);
	return roots;
}

/** Flatten the visible parent→child edges given the expand state. */
function visibleEdges(roots: TreeNode[], expandedIds: Set<string>): Array<[string, string]> {
	const edges: Array<[string, string]> = [];
	const walk = (node: TreeNode) => {
		if (node.children.length > 0 && expandedIds.has(node.agent.id)) {
			for (const child of node.children) {
				edges.push([node.agent.id, child.agent.id]);
				walk(child);
			}
		}
	};
	for (const r of roots) walk(r);
	return edges;
}

/**
 * The visible LEAF nodes — each subtree's "tail". A node is a tail when it has
 * no children, or its children are collapsed (hidden). These are the rightmost
 * heads in the tree; their right edges feed the convergent Result node.
 */
function visibleLeaves(roots: TreeNode[], expandedIds: Set<string>): string[] {
	const leaves: string[] = [];
	const walk = (node: TreeNode) => {
		const expanded = node.children.length > 0 && expandedIds.has(node.agent.id);
		if (expanded) {
			for (const child of node.children) walk(child);
		} else {
			leaves.push(node.agent.id);
		}
	};
	for (const r of roots) walk(r);
	return leaves;
}

interface SubtreeSharedProps {
	onOpenCall: (call: ProxyCall) => void;
	onOpenChat: (agent: ProxyAgent) => void;
	onSelectAgent: (agent: ProxyAgent) => void;
	expandedIds: Set<string>;
	toggleExpand: (id: string) => void;
	hoveredId: string | null;
	setHoveredId: React.Dispatch<React.SetStateAction<string | null>>;
	/** Register/unregister an agent HEAD element (card + chain) in the global map. */
	registerCard: (id: string, el: HTMLDivElement | null) => void;
	/** Custom in-memory drag offset for this agent (unscaled canvas units). */
	positions: Record<string, Point>;
	/** Begin a potential node drag from a card press (click-vs-drag resolved later). */
	beginNodeDrag: (id: string, e: React.PointerEvent<HTMLDivElement>) => void;
	/** The agent currently being dragged (for cursor / z-index affordance). */
	draggingId: string | null;
	/** Agents whose OWN tool-call chain is collapsed (hidden). */
	callCollapsedIds: Set<string>;
	/** Toggle the collapse of an agent's own tool-call chain. */
	toggleCallsCollapse: (id: string) => void;
}

/**
 * A node's head — the connector anchor unit. Lays out the agent CARD and its
 * horizontal CALL CHAIN SIDE BY SIDE in one row (`[card] → [chip]→[chip]→(output)`),
 * fully visible. The whole head is what registers for connector measurement, so
 * a parent's outgoing edge starts at the head's RIGHT edge (right of the chain)
 * and lands on the child head's LEFT edge (its card) — never crossing the chain.
 * The head also carries the per-node drag offset so the card + chain move as one.
 */
function AgentHead({
	node,
	shared,
	prominent = false,
}: {
	node: TreeNode;
	shared: SubtreeSharedProps;
	prominent?: boolean;
}) {
	const {
		onOpenCall,
		onOpenChat,
		onSelectAgent,
		expandedIds,
		toggleExpand,
		hoveredId,
		setHoveredId,
		registerCard,
		positions,
		beginNodeDrag,
		draggingId,
		callCollapsedIds,
		toggleCallsCollapse,
	} = shared;
	const hasChildren = node.children.length > 0;
	const expanded = expandedIds.has(node.agent.id);
	const id = node.agent.id;
	const offset = positions[id] ?? { x: 0, y: 0 };
	const dragging = draggingId === id;
	const hasCalls = node.calls.length > 0;
	const callsCollapsed = callCollapsedIds.has(id);

	return (
		<div
			ref={(el) => registerCard(id, el)}
			onPointerDown={(e) => beginNodeDrag(id, e)}
			className={cn(
				'relative z-10 flex shrink-0 items-center gap-4',
				dragging ? 'cursor-grabbing' : 'cursor-grab',
			)}
			style={{
				// While THIS node is actively dragging, the pointer handler owns
				// `transform` imperatively (a stray re-render must not clobber the
				// live position); otherwise reflect the committed offset.
				transform: dragging
					? undefined
					: offset.x || offset.y
						? `translate(${offset.x}px, ${offset.y}px)`
						: undefined,
				zIndex: dragging ? 40 : undefined,
			}}
		>
			<div className="relative z-10 shrink-0">
				<AgentNode
					agent={node.agent}
					calls={node.calls}
					hasChildren={hasChildren}
					expanded={expanded}
					onToggleExpand={() => toggleExpand(id)}
					onOpenChat={onOpenChat}
					onSelect={onSelectAgent}
					showCard={hoveredId === id}
					onShowCard={() => setHoveredId(id)}
					onHideCard={() => setHoveredId((current) => (current === id ? null : current))}
					prominent={prominent}
					hasCalls={hasCalls}
					callsCollapsed={callsCollapsed}
					onToggleCalls={() => toggleCallsCollapse(id)}
				/>
			</div>
			{hasCalls && !callsCollapsed && (
				<div className="w-max shrink-0">
					<CallChain calls={node.calls} onOpenCall={onOpenCall} />
				</div>
			)}
		</div>
	);
}

/**
 * A recursive left→right subtree, pure LAYOUT (connectors are drawn globally).
 * A row: the node's head on the LEFT, vertically centered against a COLUMN of
 * child subtrees on the RIGHT.
 */
function AgentSubtree({ node, shared }: { node: TreeNode; shared: SubtreeSharedProps }) {
	const { expandedIds } = shared;
	const hasChildren = node.children.length > 0;
	const showChildren = hasChildren && expandedIds.has(node.agent.id);

	return (
		<div className="relative flex items-center">
			<div className="relative z-10 shrink-0">
				<AgentHead node={node} shared={shared} />
			</div>

			{showChildren && (
				<motion.div
					initial={{ opacity: 0, x: 8 }}
					animate={{ opacity: 1, x: 0 }}
					transition={{ duration: 0.2 }}
					className="relative z-10 ml-24 flex flex-col justify-center gap-8 py-2"
				>
					{node.children.map((child) => (
						<AgentSubtree key={child.agent.id} node={child} shared={shared} />
					))}
				</motion.div>
			)}
		</div>
	);
}

/** The single-agent presentation: one prominent, centered agent — no tree. */
function SingleAgent({ node, shared }: { node: TreeNode; shared: SubtreeSharedProps }) {
	return (
		<div className="flex min-h-full w-full items-center justify-center">
			<AgentHead node={node} shared={shared} prominent />
		</div>
	);
}

/**
 * The single global connector overlay. For each visible parent→child edge it
 * measures both card rects relative to the shared content wrapper, normalises by
 * zoom, and draws a bezier from the parent's right-center to the child's
 * left-center. Living in the transformed wrapper (overflow-visible) means a
 * freely dragged node re-routes its edges without any clipping.
 */
function Connectors({
	edges,
	resultEdges,
	cardRefs,
	contentRef,
	zoom,
	positions,
	callCollapsedIds,
	measureRef,
}: {
	edges: Array<[string, string]>;
	/** Leaf-tail → Result-node edges (drawn with an accent stroke). */
	resultEdges: Array<[string, string]>;
	cardRefs: React.MutableRefObject<Map<string, HTMLDivElement>>;
	contentRef: React.RefObject<HTMLDivElement | null>;
	zoom: number;
	/** Drag offsets — a transform change doesn't fire ResizeObserver, so it's a dep. */
	positions: Record<string, Point>;
	/** Call-chain collapse set — changes head width, so re-measure on change. */
	callCollapsedIds: Set<string>;
	/**
	 * Exposes an imperative one-shot re-measure so a LIVE node drag can keep its
	 * connectors attached (rAF-coalesced) without pushing `positions` to state
	 * every frame. It reads the CURRENT zoom via a ref so it stays correct.
	 */
	measureRef: React.MutableRefObject<(() => void) | null>;
}) {
	const [paths, setPaths] = useState<string[]>([]);
	const [resultPaths, setResultPaths] = useState<string[]>([]);
	// Live zoom mirror so the imperative re-measure normalises by the right scale.
	const zoomLive = useRef(zoom);
	zoomLive.current = zoom;

	useLayoutEffect(() => {
		let rafId = 0;
		const measure = (retriesLeft = 0) => {
			const content = contentRef.current;
			if (!content) return;
			const wRect = content.getBoundingClientRect();
			if (wRect.width === 0 || wRect.height === 0) {
				if (retriesLeft > 0) rafId = requestAnimationFrame(() => measure(retriesLeft - 1));
				return;
			}
			const z = zoomLive.current || 1;
			let sawZeroRect = false;
			const edgePath = ([fromId, toId]: [string, string]): string | null => {
				const from = cardRefs.current.get(fromId);
				const to = cardRefs.current.get(toId);
				if (!from || !to) {
					sawZeroRect = true;
					return null;
				}
				const fRect = from.getBoundingClientRect();
				const tRect = to.getBoundingClientRect();
				if (
					fRect.width === 0 ||
					fRect.height === 0 ||
					tRect.width === 0 ||
					tRect.height === 0
				) {
					sawZeroRect = true;
					return null;
				}
				const startX = (fRect.right - wRect.left) / z;
				const startY = (fRect.top + fRect.height / 2 - wRect.top) / z;
				const endX = (tRect.left - wRect.left) / z;
				const endY = (tRect.top + tRect.height / 2 - wRect.top) / z;
				if (![startX, startY, endX, endY].every(Number.isFinite)) return null;
				// Horizontal shoulder; clamp the control offset so a dragged child
				// that sits left of / level with its parent still gets a sane curve.
				const dx = Math.max(24, Math.abs(endX - startX) / 2);
				return `M ${startX} ${startY} C ${startX + dx} ${startY}, ${endX - dx} ${endY}, ${endX} ${endY}`;
			};

			const next: string[] = [];
			for (const e of edges) {
				const d = edgePath(e);
				if (d) next.push(d);
			}
			const nextResult: string[] = [];
			for (const e of resultEdges) {
				const d = edgePath(e);
				if (d) nextResult.push(d);
			}
			setPaths(next);
			setResultPaths(nextResult);
			// If any card wasn't laid out yet on first paint, retry next frame
			// (bounded) so connectors appear after a hard refresh with no interaction.
			if (sawZeroRect && retriesLeft > 0) {
				rafId = requestAnimationFrame(() => measure(retriesLeft - 1));
			}
		};

		// Kick off with a retry budget (~10 frames) to cover refs/layout/fonts
		// not being ready on the first synchronous run after a hard refresh.
		measure(10);
		const raf1 = requestAnimationFrame(() => measure(10));
		const raf2 = requestAnimationFrame(() => requestAnimationFrame(() => measure(6)));
		const t = setTimeout(() => measure(4), 120);
		// Web-font loading shifts card widths after first paint; re-measure then.
		if (typeof document !== 'undefined' && document.fonts?.ready) {
			void document.fonts.ready.then(() => measure(4));
		}
		const onResize = () => measure(0);
		const ro = new ResizeObserver(() => measure(0));
		if (contentRef.current) ro.observe(contentRef.current);
		for (const el of cardRefs.current.values()) ro.observe(el);
		window.addEventListener('resize', onResize);
		// Publish a bound, no-retry measure so a live node drag can re-route the
		// dragged node's edges each frame without touching React state.
		measureRef.current = () => measure(0);
		return () => {
			cancelAnimationFrame(raf1);
			cancelAnimationFrame(raf2);
			cancelAnimationFrame(rafId);
			clearTimeout(t);
			ro.disconnect();
			window.removeEventListener('resize', onResize);
			measureRef.current = null;
		};
		// eslint-disable-next-line react-hooks/exhaustive-deps
	}, [edges, resultEdges, zoom, positions, callCollapsedIds]);

	return (
		<svg
			className="pointer-events-none absolute inset-0 z-0 h-full w-full"
			style={{ overflow: 'visible' }}
			aria-hidden="true"
		>
			{paths.map((d, i) => (
				<path
					key={`e${i}`}
					d={d}
					fill="none"
					className="stroke-border/70"
					strokeWidth={1.5}
				/>
			))}
			{resultPaths.map((d, i) => (
				<path
					key={`r${i}`}
					d={d}
					fill="none"
					className="stroke-accent-green/50"
					strokeWidth={1.75}
					strokeDasharray="4 3"
				/>
			))}
		</svg>
	);
}

export function TraceFlow({
	agents,
	calls,
	onOpenCall,
	onOpenChat,
	onSelectAgent,
	finalOutput,
	onOpenResult,
	onFullscreenChange,
}: TraceFlowProps) {
	const forest = useMemo(() => buildForest(agents, calls), [agents, calls]);
	const singleNode = forest.length === 1 && forest[0].children.length === 0 ? forest[0] : null;

	const [expandedIds, setExpandedIds] = useState<Set<string>>(
		() => new Set(agents.filter((a) => a.depth <= 0).map((a) => a.id)),
	);
	const toggleExpand = useCallback(
		(id: string) =>
			setExpandedIds((prev) => {
				const next = new Set(prev);
				if (next.has(id)) next.delete(id);
				else next.add(id);
				return next;
			}),
		[],
	);
	const collapsibleIds = useMemo(
		() => agents.filter((a) => agents.some((c) => c.parent_id === a.id)).map((a) => a.id),
		[agents],
	);
	const allExpanded = collapsibleIds.every((id) => expandedIds.has(id));

	const [hoveredId, setHoveredId] = useState<string | null>(null);

	// Per-agent collapse of the OWN tool-call chain (separate from the Workers /
	// child-agent expand). In-memory; default expanded (empty set = all shown).
	const [callCollapsedIds, setCallCollapsedIds] = useState<Set<string>>(() => new Set());
	const toggleCallsCollapse = useCallback(
		(id: string) =>
			setCallCollapsedIds((prev) => {
				const next = new Set(prev);
				if (next.has(id)) next.delete(id);
				else next.add(id);
				return next;
			}),
		[],
	);

	// Hand-rolled zoom + pan on the content wrapper (nodes + connector overlay).
	//
	// PERF MODEL — pan (and the live phase of a zoom gesture) is a pure transform
	// of the whole content layer, so it must NOT flow through React state each
	// frame: doing so would reconcile the entire node subtree AND re-run the
	// connector measurement (getBoundingClientRect of every card) on every wheel
	// tick / pointermove. Instead the live gesture mutates `panRef`/`zoomRef` and
	// writes `transform` straight to the content DOM node via a rAF-coalesced
	// writer (`scheduleTransform`); React `zoom`/`pan` state is only the COMMITTED
	// value, flushed when the gesture goes idle/ends. Connectors ride along inside
	// the transformed layer for free during pan and re-measure only when the
	// committed `zoom` (or layout) actually changes.
	const [zoom, setZoom] = useState(1);
	const [pan, setPan] = useState<Point>({ x: 0, y: 0 });
	// Live mirrors — the single source of truth DURING a gesture. The wheel
	// handler reads these synchronously to compute the cursor-anchor correction.
	const zoomRef = useRef(zoom);
	const panRef = useRef(pan);
	// Per-node in-memory drag offsets (unscaled canvas units). Cleared on reset.
	const [positions, setPositions] = useState<Record<string, Point>>({});

	const canvasRef = useRef<HTMLDivElement | null>(null);
	const scrollRef = useRef<HTMLDivElement | null>(null);
	const contentRef = useRef<HTMLDivElement | null>(null);

	// Imperative transform writer — the hot path. Writes translate3d + scale
	// (translate3d promotes the layer onto the GPU) directly to the content node,
	// coalesced to one write per animation frame. Called by every live gesture.
	const rafId = useRef(0);
	const writeTransform = useCallback(() => {
		rafId.current = 0;
		const el = contentRef.current;
		if (!el) return;
		const p = panRef.current;
		const z = zoomRef.current;
		el.style.transform = `translate3d(${p.x}px, ${p.y}px, 0) scale(${z})`;
	}, []);
	const scheduleTransform = useCallback(() => {
		if (rafId.current) return;
		rafId.current = requestAnimationFrame(writeTransform);
	}, [writeTransform]);
	useEffect(() => {
		return () => {
			if (rafId.current) cancelAnimationFrame(rafId.current);
		};
	}, []);

	// Commit the live refs back into React state (flushes connector re-measure +
	// controls readout). Debounced so a continuous wheel gesture commits once it
	// settles rather than on every tick.
	const commitTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
	const commitView = useCallback(() => {
		setZoom(zoomRef.current);
		setPan(panRef.current);
	}, []);
	const scheduleCommit = useCallback(
		(delay = 90) => {
			if (commitTimer.current) clearTimeout(commitTimer.current);
			commitTimer.current = setTimeout(commitView, delay);
		},
		[commitView],
	);
	useEffect(() => {
		return () => {
			if (commitTimer.current) clearTimeout(commitTimer.current);
		};
	}, []);

	// Keep the committed transform on the DOM in sync when zoom/pan change via
	// React state (button zoom, keyboard, reset) rather than a live gesture, and
	// keep the live refs aligned with the committed value between gestures.
	useLayoutEffect(() => {
		zoomRef.current = zoom;
		panRef.current = pan;
		const el = contentRef.current;
		if (el) el.style.transform = `translate3d(${pan.x}px, ${pan.y}px, 0) scale(${zoom})`;
	}, [zoom, pan]);

	const applyZoomStep = useCallback((delta: number) => {
		const next = clampZoomStep(zoomRef.current + delta);
		zoomRef.current = next;
		setZoom(next);
	}, []);
	const zoomIn = useCallback(() => applyZoomStep(ZOOM_STEP), [applyZoomStep]);
	const zoomOut = useCallback(() => applyZoomStep(-ZOOM_STEP), [applyZoomStep]);
	const resetView = useCallback(() => {
		zoomRef.current = 1;
		panRef.current = { x: 0, y: 0 };
		setZoom(1);
		setPan({ x: 0, y: 0 });
		setPositions({});
	}, []);

	// Global card registry: every AgentHead registers its card element here so
	// the single connector overlay can measure any parent/child pair.
	const cardRefs = useRef<Map<string, HTMLDivElement>>(new Map());
	const registerCard = useCallback((id: string, el: HTMLDivElement | null) => {
		if (el) cardRefs.current.set(id, el);
		else cardRefs.current.delete(id);
	}, []);
	// Imperative connector re-measure, published by <Connectors>. Called during a
	// live node drag so the dragged node's edges follow without a state write.
	const connectorsMeasureRef = useRef<(() => void) | null>(null);
	const edges = useMemo(() => visibleEdges(forest, expandedIds), [forest, expandedIds]);
	// Each visible leaf's tail feeds the convergent Result node on the far right.
	const resultEdges = useMemo<Array<[string, string]>>(
		() => visibleLeaves(forest, expandedIds).map((id) => [id, RESULT_ID]),
		[forest, expandedIds],
	);

	// SPACE arming for pan (tracked only while the canvas is focused).
	const [spaceHeld, setSpaceHeld] = useState(false);

	// NODE DRAG — a plain left-press on a card MAY become a drag. We track the
	// pointer on the window; only once it travels past DRAG_THRESHOLD do we treat
	// it as a drag (so a sub-threshold press stays a click → drawer/toggle/chat).
	// Deltas are divided by zoom so the node tracks the cursor 1:1 on screen.
	const [draggingId, setDraggingId] = useState<string | null>(null);
	const nodeDrag = useRef<{
		id: string;
		startX: number;
		startY: number;
		baseX: number;
		baseY: number;
		moved: boolean;
		pointerId: number;
		/** Live offset written to the head DOM node this frame (committed on up). */
		lastX: number;
		lastY: number;
	} | null>(null);
	// rAF handle for the drag's connector re-route (coalesced to one per frame).
	const dragRaf = useRef(0);

	const beginNodeDrag = useCallback(
		(id: string, e: React.PointerEvent<HTMLDivElement>) => {
			// Left button only, and never when space/middle is arming a canvas pan.
			if (e.button !== 0 || spaceHeld) return;
			// Ignore presses that originate on a real interactive control (footer
			// Chat / Workers-expand / Calls-collapse buttons or a link) so those
			// keep their own click semantics. We deliberately do NOT match the
			// card-root `role="button"` here: that role is on the whole card, and
			// the card BODY is exactly what we want to be grabbable for a drag (the
			// sub-threshold press still opens the detail drawer via onSelect).
			if ((e.target as HTMLElement).closest('button, a')) return;
			const base = positions[id] ?? { x: 0, y: 0 };
			nodeDrag.current = {
				id,
				startX: e.clientX,
				startY: e.clientY,
				baseX: base.x,
				baseY: base.y,
				moved: false,
				pointerId: e.pointerId,
				lastX: base.x,
				lastY: base.y,
			};
		},
		[positions, spaceHeld],
	);

	useEffect(() => {
		const onMove = (e: PointerEvent) => {
			const s = nodeDrag.current;
			if (!s || e.pointerId !== s.pointerId) return;
			const dxScreen = e.clientX - s.startX;
			const dyScreen = e.clientY - s.startY;
			if (!s.moved && Math.hypot(dxScreen, dyScreen) < DRAG_THRESHOLD) return;
			if (!s.moved) {
				s.moved = true;
				setDraggingId(s.id);
			}
			e.preventDefault();
			// Live gesture: write the head's transform imperatively and re-route
			// only THIS node's connectors (rAF-coalesced). No `positions` state
			// write per frame → the node subtree does not reconcile while dragging.
			const z = zoomRef.current || 1;
			s.lastX = s.baseX + dxScreen / z;
			s.lastY = s.baseY + dyScreen / z;
			const el = cardRefs.current.get(s.id);
			if (el) el.style.transform = `translate(${s.lastX}px, ${s.lastY}px)`;
			if (!dragRaf.current) {
				dragRaf.current = requestAnimationFrame(() => {
					dragRaf.current = 0;
					connectorsMeasureRef.current?.();
				});
			}
		};
		const onUp = () => {
			const s = nodeDrag.current;
			nodeDrag.current = null;
			if (dragRaf.current) {
				cancelAnimationFrame(dragRaf.current);
				dragRaf.current = 0;
			}
			// Commit the final offset to state (keeps it after re-render, and lets
			// the connector effect re-measure from the settled layout).
			if (s?.moved) {
				setPositions((prev) => ({ ...prev, [s.id]: { x: s.lastX, y: s.lastY } }));
				setDraggingId(null);
			}
		};
		// A card press that turns into a drag must not also fire a click.
		const onClickCapture = (e: MouseEvent) => {
			if (draggingId) {
				e.preventDefault();
				e.stopPropagation();
			}
		};
		window.addEventListener('pointermove', onMove, { passive: false });
		window.addEventListener('pointerup', onUp);
		window.addEventListener('click', onClickCapture, true);
		return () => {
			window.removeEventListener('pointermove', onMove);
			window.removeEventListener('pointerup', onUp);
			window.removeEventListener('click', onClickCapture, true);
		};
	}, [draggingId]);

	// CANVAS PAN — middle-mouse, or left-mouse while SPACE is held, on the
	// background. Node drag is separate (plain left-press on a card). The live
	// drag writes the transform imperatively (rAF-coalesced) and commits `pan`
	// to state only on release, so panning never reconciles the node subtree.
	const [panning, setPanning] = useState(false);
	const panDrag = useRef<{ startX: number; startY: number; panX: number; panY: number } | null>(
		null,
	);
	const beginPan = useCallback(
		(e: React.MouseEvent<HTMLDivElement>) => {
			const isMiddle = e.button === 1;
			const isSpaceLeft = e.button === 0 && spaceHeld;
			if (!isMiddle && !isSpaceLeft) return;
			e.preventDefault();
			canvasRef.current?.focus();
			const p = panRef.current;
			panDrag.current = { startX: e.clientX, startY: e.clientY, panX: p.x, panY: p.y };
			setPanning(true);
		},
		[spaceHeld],
	);
	useEffect(() => {
		if (!panning) return;
		const onMove = (e: MouseEvent) => {
			const s = panDrag.current;
			if (!s) return;
			panRef.current = {
				x: s.panX + (e.clientX - s.startX),
				y: s.panY + (e.clientY - s.startY),
			};
			scheduleTransform();
		};
		const onUp = () => {
			panDrag.current = null;
			setPanning(false);
			commitView();
		};
		window.addEventListener('mousemove', onMove);
		window.addEventListener('mouseup', onUp);
		return () => {
			window.removeEventListener('mousemove', onMove);
			window.removeEventListener('mouseup', onUp);
		};
	}, [panning, scheduleTransform, commitView]);

	// WHEEL / TRACKPAD — Figma/FigJam model. The listener is non-passive and
	// always preventDefault() so the page never scrolls; it branches on the
	// modifier:
	//   • Cmd/Ctrl + wheel, or a trackpad pinch (which the browser delivers as a
	//     ctrlKey wheel) → smooth exponential ZOOM, cursor-anchored (the point
	//     under the pointer stays fixed via pan' = pan + c*(z - nextZoom)).
	//   • plain wheel → TRAVEL: translate `pan` by the raw deltas (two-finger
	//     trackpad scroll pans both axes; Shift maps a vertical wheel to
	//     horizontal, as most mice do).
	useEffect(() => {
		const el = scrollRef.current;
		if (!el) return;
		const onWheel = (e: WheelEvent) => {
			e.preventDefault();

			if (e.ctrlKey || e.metaKey) {
				const content = contentRef.current;
				const z = zoomRef.current;
				// Exponential zoom tracks the delta magnitude (smooth, uniform
				// across the whole range) instead of a fixed additive step.
				const nextZoom = clampZoom(z * Math.exp(-e.deltaY * WHEEL_ZOOM_SENSITIVITY));
				if (nextZoom === z) return;
				if (!content) {
					zoomRef.current = nextZoom;
					scheduleTransform();
					scheduleCommit();
					return;
				}
				// Anchor at the cursor. With transformOrigin '0 0' the content-space
				// point under the cursor is c = (mouse - origin) / z, where `origin`
				// is the content layer's UNTRANSFORMED top-left. The live content
				// rect gives origin + pan, so origin = rect.(left|top) − pan; this
				// stays exact across the whole range and captures padding/centering.
				const p = panRef.current;
				const cRect = content.getBoundingClientRect();
				const originX = cRect.left - p.x;
				const originY = cRect.top - p.y;
				const cx = (e.clientX - originX) / z;
				const cy = (e.clientY - originY) / z;
				// Keep the point fixed: pan' = pan + c * (z - nextZoom) per axis.
				panRef.current = {
					x: p.x + cx * (z - nextZoom),
					y: p.y + cy * (z - nextZoom),
				};
				zoomRef.current = nextZoom;
				// Flush synchronously so back-to-back zoom ticks read an up-to-date
				// content rect (zoom bursts are infrequent; no per-frame thrash).
				if (rafId.current) {
					cancelAnimationFrame(rafId.current);
					rafId.current = 0;
				}
				writeTransform();
				scheduleCommit();
				return;
			}

			// Plain wheel → travel. Shift promotes a vertical-only wheel (mice) to
			// horizontal; otherwise honour both axes (trackpads report deltaX too).
			const p = panRef.current;
			if (e.shiftKey && e.deltaX === 0) {
				panRef.current = { x: p.x - e.deltaY * PAN_WHEEL_FACTOR, y: p.y };
			} else {
				panRef.current = {
					x: p.x - e.deltaX * PAN_WHEEL_FACTOR,
					y: p.y - e.deltaY * PAN_WHEEL_FACTOR,
				};
			}
			scheduleTransform();
			scheduleCommit();
		};
		el.addEventListener('wheel', onWheel, { passive: false });
		return () => el.removeEventListener('wheel', onWheel);
	}, [scheduleTransform, scheduleCommit, writeTransform]);

	const onKeyDown = useCallback(
		(e: React.KeyboardEvent<HTMLDivElement>) => {
			if (e.key === ' ' || e.code === 'Space') {
				e.preventDefault();
				setSpaceHeld(true);
				return;
			}
			if (e.key === '+' || e.key === '=') {
				e.preventDefault();
				zoomIn();
			} else if (e.key === '-' || e.key === '_') {
				e.preventDefault();
				zoomOut();
			} else if (e.key === '0') {
				e.preventDefault();
				resetView();
			}
		},
		[zoomIn, zoomOut, resetView],
	);
	const onKeyUp = useCallback((e: React.KeyboardEvent<HTMLDivElement>) => {
		if (e.key === ' ' || e.code === 'Space') setSpaceHeld(false);
	}, []);
	const onBlur = useCallback(() => setSpaceHeld(false), []);

	// FULLSCREEN: native API on the canvas; fixed-inset overlay fallback.
	const [overlayFs, setOverlayFs] = useState(false);
	const [nativeFs, setNativeFs] = useState(false);
	// Report the drawer portal target without re-subscribing the listener when
	// the parent passes a fresh closure each render.
	const onFullscreenChangeRef = useRef(onFullscreenChange);
	onFullscreenChangeRef.current = onFullscreenChange;
	useEffect(() => {
		const onChange = () => {
			const isCanvasFs = document.fullscreenElement === canvasRef.current;
			setNativeFs(isCanvasFs);
			// Only reparent drawers under NATIVE fullscreen; else portal to body.
			onFullscreenChangeRef.current?.(isCanvasFs ? canvasRef.current : null);
		};
		document.addEventListener('fullscreenchange', onChange);
		return () => document.removeEventListener('fullscreenchange', onChange);
	}, []);
	const isFullscreen = nativeFs || overlayFs;
	const toggleFullscreen = useCallback(() => {
		const el = canvasRef.current;
		if (!el) return;
		if (document.fullscreenElement) {
			void document.exitFullscreen();
			return;
		}
		if (overlayFs) {
			setOverlayFs(false);
			return;
		}
		if (el.requestFullscreen) {
			el.requestFullscreen().catch(() => setOverlayFs(true));
		} else {
			setOverlayFs(true);
		}
	}, [overlayFs]);
	useEffect(() => {
		if (!overlayFs) return;
		const onKey = (e: KeyboardEvent) => {
			if (e.key === 'Escape') setOverlayFs(false);
		};
		window.addEventListener('keydown', onKey);
		return () => window.removeEventListener('keydown', onKey);
	}, [overlayFs]);

	const shared: SubtreeSharedProps = {
		onOpenCall,
		onOpenChat,
		onSelectAgent,
		expandedIds,
		toggleExpand,
		hoveredId,
		setHoveredId,
		registerCard,
		positions,
		beginNodeDrag,
		draggingId,
		callCollapsedIds,
		toggleCallsCollapse,
	};

	return (
		<div className="flex h-full min-h-0 flex-col space-y-3">
			<div className="flex items-center justify-between">
				<div className="text-muted-foreground text-xs">
					{agents.length} agent{agents.length === 1 ? '' : 's'} ·{' '}
					{singleNode ? 'single agent' : 'left→right agent tree'} · drag a node to move
					it; hold space or middle-drag to pan
				</div>
				{!singleNode && collapsibleIds.length > 0 && (
					<button
						type="button"
						onClick={() =>
							setExpandedIds(allExpanded ? new Set() : new Set(collapsibleIds))
						}
						className="text-muted-foreground hover:text-foreground focus-visible:ring-ring rounded text-xs font-medium transition-colors focus-visible:ring-2 focus-visible:outline-none"
					>
						{allExpanded ? 'Collapse all' : 'Expand all'}
					</button>
				)}
			</div>

			{/*
			 * Outer canvas container: NON-scrolling, focusable, holds the pinned
			 * controls and the dotted-grid backdrop. The controls are absolute
			 * children of THIS box (not the scrolled content), so they stay glued
			 * to the top-right corner through any scroll/pan/zoom.
			 */}
			<div
				ref={canvasRef}
				tabIndex={0}
				role="application"
				aria-label="Trace-flow canvas. Cmd/Ctrl + scroll (or trackpad pinch) to zoom toward the cursor (10%–400%); scroll or shift+scroll to move around; drag a node to reposition it; hold space or middle-mouse to pan; +/- to zoom, 0 to reset view."
				onKeyDown={onKeyDown}
				onKeyUp={onKeyUp}
				onBlur={onBlur}
				className={cn(
					'border-border/50 from-card/40 to-background/60 focus-visible:ring-ring/60 relative min-h-0 flex-1 overflow-hidden rounded-xl border bg-gradient-to-br focus-visible:ring-2 focus-visible:outline-none',
					'[background-image:radial-gradient(theme(colors.border/0.25)_1px,transparent_1px)] [background-size:22px_22px]',
					isFullscreen && 'bg-background',
					overlayFs && 'fixed inset-0 z-50 rounded-none',
				)}
			>
				<ViewControls
					zoom={zoom}
					onZoomIn={zoomIn}
					onZoomOut={zoomOut}
					onReset={resetView}
					isFullscreen={isFullscreen}
					onToggleFullscreen={toggleFullscreen}
				/>

				{/* Scroll viewport: the only scrolling box; pans/zooms beneath the pinned controls. */}
				<div
					ref={scrollRef}
					onMouseDown={beginPan}
					className={cn(
						'h-full w-full overflow-auto p-6',
						panning ? 'cursor-grabbing select-none' : spaceHeld && 'cursor-grab',
					)}
				>
					{/*
					 * Transform target: pan translate ∘ zoom scale on the wrapper that
					 * holds the nodes AND the single connector overlay, so lines stay
					 * attached under both. `min-h-full` centers the root vertically at
					 * the base view; zoom expands from the top-left origin (0 0) so the
					 * cursor-anchored wheel math stays a simple pan correction on both
					 * axes.
					 */}
					<div
						ref={contentRef}
						className="relative flex min-h-full min-w-fit items-center"
						style={{
							transform: `translate3d(${pan.x}px, ${pan.y}px, 0) scale(${zoom})`,
							transformOrigin: '0 0',
							// Promote to its own GPU layer so live pan/zoom is a cheap
							// composite (no repaint of the node subtree each frame).
							willChange: 'transform',
						}}
					>
						<Connectors
							edges={edges}
							resultEdges={resultEdges}
							cardRefs={cardRefs}
							contentRef={contentRef}
							zoom={zoom}
							positions={positions}
							callCollapsedIds={callCollapsedIds}
							measureRef={connectorsMeasureRef}
						/>
						{singleNode ? (
							<SingleAgent node={singleNode} shared={shared} />
						) : (
							<div className="relative z-10 flex flex-col justify-center gap-12">
								{forest.map((root) => (
									<AgentSubtree key={root.agent.id} node={root} shared={shared} />
								))}
							</div>
						)}

						{/*
						 * Convergent Result node — the run's single destination. Sits to
						 * the RIGHT of the whole tree, vertically centered; leaf tails feed
						 * accent connectors into it (drawn by the overlay above).
						 */}
						<div className="relative z-10 ml-32 flex shrink-0 items-center">
							<ResultNode
								ref={(el) => registerCard(RESULT_ID, el)}
								finalOutput={finalOutput}
								onOpen={onOpenResult}
							/>
						</div>
					</div>
				</div>

				<OutcomeBar finalOutput={finalOutput} onOpen={onOpenResult} />
				<ControlsLegend />
			</div>
		</div>
	);
}

/**
 * ControlsLegend — a small, pinned "Controls" chip (bottom-left, an absolute
 * child of the NON-scrolling canvas container, so it never scrolls/pans away).
 * Collapsed by default; expands on hover OR click into a compact popover that
 * lists each interaction as a gesture→action row with kbd-style key chips. The
 * modifier chip shows ⌘ on Mac and Ctrl elsewhere.
 */
function Kbd({ children }: { children: React.ReactNode }) {
	return (
		<kbd className="border-border/70 bg-muted/60 text-muted-foreground inline-flex items-center rounded border px-1.5 py-0.5 font-mono text-[10px] leading-none font-medium">
			{children}
		</kbd>
	);
}

function ControlsLegend() {
	const [pinned, setPinned] = useState(false);
	const [hovered, setHovered] = useState(false);
	const open = pinned || hovered;

	const rows: Array<{ keys: React.ReactNode; action: string }> = [
		{
			keys: (
				<>
					<Kbd>{MOD_KEY_LABEL}</Kbd>
					<span className="text-muted-foreground/60">+</span>
					<Kbd>scroll</Kbd>
				</>
			),
			action: 'Zoom',
		},
		{
			keys: (
				<>
					<Kbd>Scroll</Kbd>
					<span className="text-muted-foreground/60">·</span>
					<Kbd>Shift</Kbd>
					<span className="text-muted-foreground/60">+</span>
					<Kbd>scroll</Kbd>
				</>
			),
			action: 'Move around',
		},
		{ keys: <Kbd>Drag a node</Kbd>, action: 'Reposition it' },
		{
			keys: (
				<>
					<Kbd>Space</Kbd>
					<span className="text-muted-foreground/60">+</span>
					<Kbd>drag</Kbd>
				</>
			),
			action: 'Pan canvas',
		},
	];

	return (
		<div
			onMouseDown={(e) => e.stopPropagation()}
			onMouseEnter={() => setHovered(true)}
			onMouseLeave={() => setHovered(false)}
			className="absolute bottom-3 left-3 z-30 flex flex-col items-start gap-2"
		>
			{open && (
				<div className="border-border/60 bg-card/90 w-max rounded-lg border p-2.5 shadow-md backdrop-blur">
					<div className="text-muted-foreground/70 mb-1.5 px-0.5 text-[10px] font-semibold tracking-wide uppercase">
						Controls
					</div>
					<div className="flex flex-col gap-1.5">
						{rows.map((row, i) => (
							<div key={i} className="flex items-center justify-between gap-4">
								<span className="flex items-center gap-1">{row.keys}</span>
								<span className="text-muted-foreground text-xs">{row.action}</span>
							</div>
						))}
					</div>
				</div>
			)}
			<button
				type="button"
				onClick={() => setPinned((p) => !p)}
				aria-expanded={open}
				aria-label="Canvas controls"
				className="border-border/60 bg-card/80 text-muted-foreground hover:text-foreground focus-visible:ring-ring inline-flex items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-xs font-medium shadow-sm backdrop-blur transition-colors focus-visible:ring-2 focus-visible:outline-none"
			>
				<HelpCircle className="h-3.5 w-3.5" />
				Controls
			</button>
		</div>
	);
}

/**
 * OutcomeBar — a thin strip pinned to the TOP of the canvas region, always
 * visible while the user pans/scrolls/zooms (it is an absolute child of the
 * NON-scrolling canvas container, like the zoom controls). Shows a one-line
 * truncated preview of the run's final output and opens the same full-output
 * drawer as the Result node, so the punchline is never off-screen.
 */
function OutcomeBar({
	finalOutput,
	onOpen,
}: {
	finalOutput: FinalOutput | null;
	onOpen: () => void;
}) {
	const hasOutput = Boolean(finalOutput?.summary?.trim());
	const preview = previewOf(finalOutput);
	return (
		<div
			onMouseDown={(e) => e.stopPropagation()}
			className="absolute top-3 left-3 z-30 flex max-w-[calc(100%-11rem)] items-center gap-2"
		>
			<button
				type="button"
				onClick={onOpen}
				aria-label="Open the run's final output"
				title={hasOutput ? 'Open the run\u2019s final output' : 'No final output recorded'}
				className={cn(
					'border-border/60 bg-card/80 focus-visible:ring-ring group flex min-w-0 items-center gap-2 rounded-lg border px-2.5 py-1.5 shadow-sm backdrop-blur transition-colors focus-visible:ring-2 focus-visible:outline-none',
					hasOutput ? 'hover:border-accent-green/50' : 'hover:border-border',
				)}
			>
				<span
					className={cn(
						'flex shrink-0 items-center gap-1 text-[10px] font-semibold tracking-wide uppercase',
						hasOutput ? 'text-accent-green' : 'text-muted-foreground',
					)}
				>
					<CircleCheck className="h-3.5 w-3.5" />
					Outcome
				</span>
				<span className="bg-border/60 h-4 w-px shrink-0" aria-hidden="true" />
				<span className="text-muted-foreground group-hover:text-foreground min-w-0 truncate text-xs transition-colors">
					{preview}
				</span>
				<ArrowUpRight className="text-muted-foreground/60 group-hover:text-foreground h-3.5 w-3.5 shrink-0 transition-colors" />
			</button>
		</div>
	);
}

function ViewControls({
	zoom,
	onZoomIn,
	onZoomOut,
	onReset,
	isFullscreen,
	onToggleFullscreen,
}: {
	zoom: number;
	onZoomIn: () => void;
	onZoomOut: () => void;
	onReset: () => void;
	isFullscreen: boolean;
	onToggleFullscreen: () => void;
}) {
	return (
		<div
			onMouseDown={(e) => e.stopPropagation()}
			className="border-border/60 bg-card/80 absolute top-3 right-3 z-30 flex items-center gap-1 rounded-lg border p-1 shadow-sm backdrop-blur"
		>
			<button
				type="button"
				onClick={onZoomOut}
				disabled={zoom <= ZOOM_MIN}
				aria-label="Zoom out"
				className="text-muted-foreground hover:text-foreground hover:bg-muted focus-visible:ring-ring inline-flex h-7 w-7 items-center justify-center rounded transition-colors focus-visible:ring-2 focus-visible:outline-none disabled:cursor-not-allowed disabled:opacity-40"
			>
				<Minus className="h-4 w-4" />
			</button>
			<button
				type="button"
				onClick={onReset}
				aria-label="Reset view: 100% zoom, recenter, clear moved nodes"
				title="Reset view (zoom + pan + moved nodes)"
				className="text-muted-foreground hover:text-foreground focus-visible:ring-ring inline-flex h-7 min-w-[3rem] items-center justify-center gap-1 rounded px-1 text-xs font-medium tabular-nums transition-colors focus-visible:ring-2 focus-visible:outline-none"
			>
				<Maximize className="h-3.5 w-3.5" />
				{Math.round(zoom * 100)}%
			</button>
			<button
				type="button"
				onClick={onZoomIn}
				disabled={zoom >= ZOOM_MAX}
				aria-label="Zoom in"
				className="text-muted-foreground hover:text-foreground hover:bg-muted focus-visible:ring-ring inline-flex h-7 w-7 items-center justify-center rounded transition-colors focus-visible:ring-2 focus-visible:outline-none disabled:cursor-not-allowed disabled:opacity-40"
			>
				<Plus className="h-4 w-4" />
			</button>
			<span className="bg-border/60 mx-0.5 h-5 w-px" aria-hidden="true" />
			<button
				type="button"
				onClick={onToggleFullscreen}
				aria-label={isFullscreen ? 'Exit fullscreen' : 'Enter fullscreen'}
				title={isFullscreen ? 'Exit fullscreen' : 'Fullscreen'}
				className="text-muted-foreground hover:text-foreground hover:bg-muted focus-visible:ring-ring inline-flex h-7 w-7 items-center justify-center rounded transition-colors focus-visible:ring-2 focus-visible:outline-none"
			>
				{isFullscreen ? (
					<Minimize2 className="h-4 w-4" />
				) : (
					<Maximize2 className="h-4 w-4" />
				)}
			</button>
		</div>
	);
}
