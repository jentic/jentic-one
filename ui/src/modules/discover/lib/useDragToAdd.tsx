/**
 * useDragToAdd — drag a catalog row onto the docked "Your workspace" panel to
 * add it. An extra, never the only path: every row keeps its Add button
 * (always visible on touch), and the preview sheet its "Add to workspace" CTA.
 *
 *   - Starts on a primary-button mouse/pen press that travels > 6px. A press
 *     on a control inside the row (`[data-nodrag]`, buttons, links, inputs)
 *     never drags, and a plain click (no travel) still opens the preview.
 *     No drag from touch — touch scrolls.
 *   - The drop target is the panel (`[data-testid="workspace-dock-panel"]`),
 *     hit-tested against the pointer. The panel shows its own drop states
 *     from `drop` (`dragging` / `over`).
 *   - Dropping calls `onDrop(entity)` — the same mutation as the Add button —
 *     and the ghost flies to the panel's drop slot and fades (skipped under
 *     reduced motion). Escape or releasing elsewhere cancels.
 *   - `announcement` is a polite live-region string ("Adding X to your
 *     workspace") for the caller to render.
 */
import {
	useCallback,
	useEffect,
	useRef,
	useState,
	type PointerEvent as ReactPointerEvent,
} from 'react';
import { createPortal } from 'react-dom';
import { VendorIcon } from '@/shared/ui';
import type { DiscoveryEntity } from '@/modules/discover/api';

const DRAG_THRESHOLD_PX = 6;
const DOCK_SELECTOR = '[data-testid="workspace-dock-panel"]';
const SLOT_SELECTOR = '[data-testid="workspace-drop-slot"]';
const NO_DRAG_SELECTOR = '[data-nodrag],button,a,input,textarea,select,[role="button"]';
const FLY_MS = 380;

/** A catalog row being dragged toward the panel (the panel's drop states). */
export interface DragDropState {
	/** `dragging` — a drag is under way; `over` — it is over the panel. */
	phase: 'dragging' | 'over';
	/** The dragged API's display name ("Drop to add {name}"). */
	name: string;
}

export interface DragIdentity {
	entity: DiscoveryEntity;
	/** Display name (the row's title). */
	name: string;
	/** Secondary line in the ghost (vendor domain). */
	vendor?: string;
	icon: { name: string; vendor?: string; iconUrl?: string | null };
}

interface GhostState {
	identity: DragIdentity;
	x: number;
	y: number;
	over: boolean;
	flyTo: { x: number; y: number } | null;
}

interface Options {
	enabled: boolean;
	onDrop: (entity: DiscoveryEntity) => void;
}

function prefersReducedMotion(): boolean {
	return (
		typeof window !== 'undefined' &&
		!!window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
	);
}

function pointOver(selector: string, x: number, y: number): boolean {
	const el = document.querySelector(selector);
	if (!el) return false;
	const r = el.getBoundingClientRect();
	return x >= r.left && x <= r.right && y >= r.top && y <= r.bottom;
}

export function useDragToAdd({ enabled, onDrop }: Options) {
	const [ghost, setGhost] = useState<GhostState | null>(null);
	const [announcement, setAnnouncement] = useState('');
	const pressRef = useRef<{
		x: number;
		y: number;
		identity: DragIdentity;
		started: boolean;
	} | null>(null);
	const suppressClickRef = useRef(false);
	const cleanupRef = useRef<(() => void) | null>(null);
	const onDropRef = useRef(onDrop);
	onDropRef.current = onDrop;

	const endDrag = useCallback(() => {
		cleanupRef.current?.();
		cleanupRef.current = null;
		pressRef.current = null;
		document.body.style.removeProperty('cursor');
		document.body.style.removeProperty('user-select');
	}, []);

	useEffect(() => endDrag, [endDrag]);

	const onRowPointerDown = useCallback(
		(event: ReactPointerEvent<HTMLElement>, identity: DragIdentity) => {
			if (!enabled || event.button !== 0 || event.pointerType === 'touch') return;
			if ((event.target as HTMLElement).closest(NO_DRAG_SELECTOR)) return;
			endDrag();
			pressRef.current = { x: event.clientX, y: event.clientY, identity, started: false };

			const onMove = (e: PointerEvent) => {
				const press = pressRef.current;
				if (!press) return;
				if (!press.started) {
					if (Math.hypot(e.clientX - press.x, e.clientY - press.y) <= DRAG_THRESHOLD_PX)
						return;
					press.started = true;
					document.body.style.cursor = 'grabbing';
					document.body.style.userSelect = 'none';
				}
				const over = pointOver(DOCK_SELECTOR, e.clientX, e.clientY);
				setGhost({
					identity: press.identity,
					x: e.clientX,
					y: e.clientY,
					over,
					flyTo: null,
				});
			};
			const finish = (e: PointerEvent | null, cancelled: boolean) => {
				const press = pressRef.current;
				endDrag();
				if (!press?.started) return;
				// The release ends a drag, not a click: swallow the click that follows.
				suppressClickRef.current = true;
				window.setTimeout(() => {
					suppressClickRef.current = false;
				}, 0);
				const over =
					!cancelled && e != null && pointOver(DOCK_SELECTOR, e.clientX, e.clientY);
				if (!over) {
					setGhost(null);
					return;
				}
				const slot = document.querySelector(SLOT_SELECTOR)?.getBoundingClientRect();
				onDropRef.current(press.identity.entity);
				setAnnouncement(`Adding ${press.identity.name} to your workspace`);
				if (!slot || prefersReducedMotion()) {
					setGhost(null);
					return;
				}
				setGhost((g) =>
					g
						? {
								...g,
								over: true,
								flyTo: { x: slot.left + 28, y: slot.top + slot.height / 2 },
							}
						: null,
				);
				window.setTimeout(() => setGhost(null), FLY_MS + 20);
			};
			const onUp = (e: PointerEvent) => finish(e, false);
			const onCancel = () => finish(null, true);
			const onKey = (e: KeyboardEvent) => {
				if (e.key === 'Escape') finish(null, true);
			};
			window.addEventListener('pointermove', onMove);
			window.addEventListener('pointerup', onUp);
			window.addEventListener('pointercancel', onCancel);
			window.addEventListener('keydown', onKey);
			cleanupRef.current = () => {
				window.removeEventListener('pointermove', onMove);
				window.removeEventListener('pointerup', onUp);
				window.removeEventListener('pointercancel', onCancel);
				window.removeEventListener('keydown', onKey);
			};
		},
		[enabled, endDrag],
	);

	/** Call from the row's click handler: true when the click ended a drag. */
	const consumeDragClick = useCallback(() => suppressClickRef.current, []);

	const dragging = ghost != null && ghost.flyTo == null;
	const drop: DragDropState | null = dragging
		? { phase: ghost.over ? 'over' : 'dragging', name: ghost.identity.name }
		: null;

	const ghostElement =
		ghost && typeof document !== 'undefined'
			? createPortal(<DragGhost ghost={ghost} />, document.body)
			: null;

	return {
		onRowPointerDown,
		consumeDragClick,
		/** The entity being dragged (its row dims), or null. */
		draggingId: dragging ? ghost.identity.entity.id : null,
		drop,
		ghostElement,
		announcement,
	};
}

function DragGhost({ ghost }: { ghost: GhostState }) {
	const { identity, over, flyTo } = ghost;
	const x = flyTo?.x ?? ghost.x;
	const y = flyTo?.y ?? ghost.y;
	return (
		<div
			aria-hidden="true"
			data-testid="drag-ghost"
			className="bg-surface-ghost text-foreground pointer-events-none fixed z-[99] flex items-center gap-2.5 rounded-lg py-2 pr-3.5 pl-2 text-sm font-bold whitespace-nowrap shadow-[0_18px_40px_-12px_hsl(var(--shadow)/calc(.7*var(--shadow-k))),0_0_0_1px_hsl(var(--primary)/0.18)]"
			style={{
				left: x,
				top: y,
				transform: over
					? 'translate(-20px,-50%) scale(1.03)'
					: 'translate(-20px,-50%) rotate(-1.5deg)',
				transition: flyTo
					? `left ${FLY_MS}ms var(--ease-fly), top ${FLY_MS}ms var(--ease-fly), opacity ${FLY_MS}ms var(--ease-fly), transform ${FLY_MS}ms var(--ease-fly)`
					: 'transform 120ms',
				opacity: flyTo ? 0 : 1,
				['--ease-fly' as string]: 'cubic-bezier(0.22, 1, 0.36, 1)',
			}}
		>
			<VendorIcon {...identity.icon} size="sm" />
			<span>
				{identity.name}
				{identity.vendor && identity.vendor !== identity.name && (
					<small className="text-muted-foreground ml-1 font-normal">
						{identity.vendor}
					</small>
				)}
			</span>
		</div>
	);
}
