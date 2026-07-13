/**
 * AgentRail — persistent right-side surface, present on every authenticated
 * page at `xl+`. Backed by the REAL platform event feed (`/events` +
 * `/events/stream` SSE) via the `agentStream` provider.
 *
 * Composes:
 *   • RailHeader — live-status, filter input, filter chips, pause, stale warning
 *   • RailFeed   — grouped, density-aware list of events
 *   • RailFooter — toast scope + audio toggle
 *
 * Owns:
 *   • collapsed state (persisted to localStorage, broadcast to ToastHost)
 *   • paused state (manual toggle; hovering quietly holds new events)
 *   • feed filters (search, severities, kinds)
 *   • toast scope (mirrored to localStorage; ToastHost reads from there)
 *   • audio-on-critical preference + audio cue dispatch
 *
 * This is an org-wide platform feed; there is no per-agent lens because
 * `/events` carries no actor filter (tracked: jentic/jentic-one#387).
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQueryClient } from '@tanstack/react-query';
import { sharedQueryKeys } from '@/shared/api/queryKeys';
import { ChevronLeft } from 'lucide-react';
import { Button } from '@/shared/ui/Button';
import { toast } from '@/shared/ui';
import { AccessRequestDialog } from '@/shared/app/rail/AccessRequestDialog';
import { RailEventRow } from '@/shared/app/rail/RailEventRow';
import { RailFeed } from '@/shared/app/rail/RailFeed';
import type { RailFeedFilters } from '@/shared/app/rail/RailFeed';
import { RailFooter } from '@/shared/app/rail/RailFooter';
import { RailHeader } from '@/shared/app/rail/RailHeader';
import { playCriticalCue } from '@/shared/lib/audioCue';
import {
	RAIL_AUDIO_STORAGE_KEY,
	RAIL_COLLAPSE_CHANGE_EVENT,
	RAIL_COLLAPSED_STORAGE_KEY,
	buildTraceBundle,
	readToastScope,
	useAgentStream,
	writeToastScope,
} from '@/shared/lib/agentStream';
import type { InlineActionSpec, StreamEvent, ToastScope } from '@/shared/lib/agentStream';
import { cn } from '@/shared/lib/utils';

function readBool(key: string, fallback: boolean): boolean {
	if (typeof window === 'undefined') return fallback;
	try {
		const v = window.localStorage.getItem(key);
		return v === null ? fallback : v === '1';
	} catch {
		return fallback;
	}
}
function writeBool(key: string, value: boolean) {
	if (typeof window === 'undefined') return;
	try {
		window.localStorage.setItem(key, value ? '1' : '0');
	} catch {
		/* ignore */
	}
}

function notifyCollapseChange(collapsed: boolean) {
	if (typeof window === 'undefined') return;
	window.dispatchEvent(new CustomEvent(RAIL_COLLAPSE_CHANGE_EVENT, { detail: collapsed }));
}

export function AgentRail() {
	const {
		events,
		latest,
		status,
		acknowledge,
		decide,
		resolveEvent,
		loadOlderEvents,
		canLoadOlder,
		loadingOlder,
	} = useAgentStream();
	const navigate = useNavigate();
	const queryClient = useQueryClient();

	const [collapsed, setCollapsed] = useState<boolean>(() =>
		readBool(RAIL_COLLAPSED_STORAGE_KEY, false),
	);
	const [scope, setScope] = useState<ToastScope>(() => readToastScope());
	const [audioOnCritical, setAudioOnCritical] = useState<boolean>(() =>
		readBool(RAIL_AUDIO_STORAGE_KEY, true),
	);

	// Access-request detail dialog (per-item approve/deny), opened from a filed
	// event's "View" action.
	const [requestDialog, setRequestDialog] = useState<{
		requestId: string;
		eventId: string;
	} | null>(null);

	// Pause state. Two INDEPENDENT mechanisms, intentionally kept separate so the
	// control isn't confusing:
	//   • manualPaused — the explicit Pause/Play button. This is the ONLY thing
	//     that drives the visible "paused" chrome (amber dot, Play icon, PAUSED
	//     pill). It persists until the user toggles it back.
	//   • hoverFrozen — a QUIET convenience: while the cursor is inside the rail
	//     we stop admitting new events so the list doesn't shift out from under
	//     a click. It does NOT flip the button to "Resume" or recolor the status
	//     dot — earlier that made the button look already-paused on hover, so you
	//     couldn't tell what clicking it would do.
	const [hoverFrozen, setHoverFrozen] = useState(false);
	const [manualPaused, setManualPaused] = useState(false);
	// Feed freeze = either mechanism; visible "paused" state = manual only.
	const feedFrozen = hoverFrozen || manualPaused;

	// Filters
	const [search, setSearch] = useState('');
	const [severities, setSeverities] = useState<Set<StreamEvent['severity']>>(() => new Set());
	const [kinds, setKinds] = useState<Set<StreamEvent['kind']>>(() => new Set());

	// Pause snapshot — when paused, we freeze *which* events are visible (by id)
	// at pause time, but keep reading their live objects from the provider so an
	// acknowledge's optimistic flip still reflects while paused. The feed just
	// stops admitting *new* events.
	const [frozenIds, setFrozenIds] = useState<Set<string> | null>(null);

	useEffect(() => {
		if (feedFrozen && frozenIds === null) setFrozenIds(new Set(events.map((e) => e.id)));
		if (!feedFrozen && frozenIds !== null) setFrozenIds(null);
	}, [feedFrozen, frozenIds, events]);

	// Persist collapse + audio preference, broadcast to ToastHost.
	useEffect(() => {
		writeBool(RAIL_COLLAPSED_STORAGE_KEY, collapsed);
		notifyCollapseChange(collapsed);
	}, [collapsed]);
	useEffect(() => writeBool(RAIL_AUDIO_STORAGE_KEY, audioOnCritical), [audioOnCritical]);
	useEffect(() => writeToastScope(scope), [scope]);

	// Audio cue on critical (opt-in).
	const lastBeepedRef = useRef<string | null>(null);
	useEffect(() => {
		if (!audioOnCritical) return;
		if (!latest) return;
		if (latest.severity !== 'critical' && latest.severity !== 'error') return;
		if (lastBeepedRef.current === latest.id) return;
		lastBeepedRef.current = latest.id;
		playCriticalCue();
	}, [latest, audioOnCritical]);

	const filters: RailFeedFilters = useMemo(
		() => ({ search, severities, kinds }),
		[search, severities, kinds],
	);

	const renderEvents = useMemo(
		() => (frozenIds ? events.filter((e) => frozenIds.has(e.id)) : events),
		[frozenIds, events],
	);
	// "Reconnecting" is an honest connection signal, not a quiet-feed guess: the
	// backend emits events sparsely (a 30s gap is normal, not a fault). Show it
	// only when the SSE is actually struggling — errored, or re-connecting after
	// we'd already gone live (a drop). A first-load `connecting` shows the
	// dedicated "Connecting" pill instead, so don't double-signal there.
	const stale = status === 'error' || (status === 'connecting' && events.length > 0);

	function toggleSeverity(s: StreamEvent['severity']) {
		setSeverities((prev) => {
			const next = new Set(prev);
			if (next.has(s)) next.delete(s);
			else next.add(s);
			return next;
		});
	}
	function toggleKind(k: StreamEvent['kind']) {
		setKinds((prev) => {
			const next = new Set(prev);
			if (next.has(k)) next.delete(k);
			else next.add(k);
			return next;
		});
	}
	function clearFilters() {
		setSearch('');
		setSeverities(new Set());
		setKinds(new Set());
	}

	function handleLoadOlder() {
		void loadOlderEvents();
	}

	function handleExportTraceBundle() {
		const bundle = buildTraceBundle(events, 5 * 60 * 1000);
		if (bundle.eventCount === 0) {
			toast({
				variant: 'default',
				title: 'Nothing to export',
				description: 'The rail has no events loaded yet.',
			});
			return;
		}
		const blob = new Blob([JSON.stringify(bundle, null, 2)], { type: 'application/json' });
		const url = URL.createObjectURL(blob);
		const a = document.createElement('a');
		a.href = url;
		a.download = `rail-trace-bundle-${Date.now()}.json`;
		document.body.appendChild(a);
		a.click();
		document.body.removeChild(a);
		setTimeout(() => URL.revokeObjectURL(url), 0);
	}

	function handleAction(eventId: string, action: InlineActionSpec, reason?: string) {
		// Pure navigation actions: navigate, skip the RPC.
		if (action.href && !action.acknowledges && !action.decides) {
			const ev = renderEvents.find((e) => e.id === eventId);
			const target = ev ? action.href(ev) : null;
			if (target) navigate(target);
			return;
		}
		// Access-request decision (approve/deny via :decide).
		if (action.decides) {
			void decide(eventId, action.decides, reason);
			return;
		}
		if (action.acknowledges) {
			void acknowledge(eventId);
		}
	}

	if (collapsed) {
		return (
			<aside className="bg-muted border-border hidden w-10 shrink-0 flex-col items-center border-l xl:flex">
				<Button
					variant="ghost"
					size="icon"
					onClick={() => setCollapsed(false)}
					aria-label="Expand agent rail"
					title="Expand agent rail"
					className="mt-2"
				>
					<ChevronLeft className="text-muted-foreground h-4 w-4" />
				</Button>
				<div className="mt-3 flex flex-col items-center gap-1.5">
					<span
						role="img"
						aria-label={
							manualPaused
								? 'Feed paused'
								: status === 'live'
									? 'Stream live'
									: status === 'error'
										? 'Stream offline'
										: 'Connecting'
						}
						title={
							manualPaused
								? 'Feed paused'
								: status === 'live'
									? 'Stream live'
									: status === 'error'
										? 'Stream offline'
										: 'Connecting'
						}
						className={cn(
							'h-1.5 w-1.5 rounded-full',
							manualPaused
								? 'bg-warning'
								: status === 'live'
									? 'bg-success animate-pulse'
									: status === 'error'
										? 'bg-danger'
										: 'bg-muted-foreground',
						)}
					/>
					<span className="text-muted-foreground font-mono text-[10px] tracking-widest uppercase [writing-mode:vertical-rl]">
						Events · {events.length}
					</span>
				</div>
			</aside>
		);
	}

	return (
		<aside
			aria-label="Agent rail"
			className="bg-muted border-border hidden w-72 shrink-0 flex-col overflow-hidden border-l xl:flex"
			onMouseEnter={() => setHoverFrozen(true)}
			onMouseLeave={() => setHoverFrozen(false)}
		>
			<RailHeader
				eventCount={renderEvents.length}
				status={status}
				paused={manualPaused}
				hoverFrozen={hoverFrozen && !manualPaused}
				heldBack={feedFrozen ? Math.max(0, events.length - renderEvents.length) : 0}
				stale={stale}
				audioOnCritical={audioOnCritical}
				onTogglePause={() => setManualPaused((p) => !p)}
				onCollapse={() => setCollapsed(true)}
				onLoadOlder={handleLoadOlder}
				onExportTraceBundle={handleExportTraceBundle}
				loadingOlder={loadingOlder}
				canLoadOlder={canLoadOlder}
				search={search}
				onSearchChange={setSearch}
				severities={severities}
				onToggleSeverity={toggleSeverity}
				kinds={kinds}
				onToggleKind={toggleKind}
				onClearFilters={clearFilters}
			/>

			<div
				className="flex-1 overflow-y-auto px-2 py-2"
				role="log"
				aria-live="polite"
				aria-relevant="additions"
				aria-label="Agent event feed"
			>
				<RailFeed
					events={renderEvents}
					filters={filters}
					onAction={handleAction}
					onOpenRequest={(requestId, eventId) => setRequestDialog({ requestId, eventId })}
					onNavigate={(href) => navigate(href)}
				/>
			</div>

			<RailFooter
				scope={scope}
				onScopeChange={setScope}
				audioOnCritical={audioOnCritical}
				onAudioToggle={() => setAudioOnCritical((v) => !v)}
			/>

			<AccessRequestDialog
				open={requestDialog !== null}
				requestId={requestDialog?.requestId ?? null}
				eventId={requestDialog?.eventId ?? null}
				onClose={() => setRequestDialog(null)}
				onResolved={(eventId) => resolveEvent(eventId)}
				onDecided={() => {
					// A per-item decision from the dialog changes the durable queue
					// + dashboard counts + the nav badge. Invalidate the shared roots
					// (shared-layer code, no cross-module key imports) so every
					// approval surface refreshes — not just the nav badge, which was
					// the original stale-dashboard bug.
					queryClient.invalidateQueries({ queryKey: sharedQueryKeys.dashboardRoot });
					queryClient.invalidateQueries({ queryKey: sharedQueryKeys.accessRequestsRoot });
				}}
			/>
		</aside>
	);
}

// Re-export RailEventRow so future consumers can import it from the rail barrel.
export { RailEventRow };
