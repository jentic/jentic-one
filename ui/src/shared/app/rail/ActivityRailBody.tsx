/**
 * ActivityRailBody — everything inside the Activity rail, shared verbatim by
 * the docked `xl+` rail (`AgentRail`) and the below-`xl` drawer
 * (`ActivityDrawer`), so the two can never drift.
 *
 *   • RailHeader  — status, Filter (who / what), Failures only, pause, ⋯ menu
 *   • RailFeed    — folded, plain-language event rows
 *   • "N new ↑"   — while paused or scrolled down, what you're not seeing yet
 *   • RailFooter  — "Open full log in Monitor →"
 *
 * All of the view state — who, what, Failures only, pause — lives in the
 * stream provider: ONE set of choices, the user's, for both surfaces and every
 * page — navigating never changes it. On an agent's page the Filter offers
 * "Only this agent" as a one-click shortcut instead of switching by itself.
 */
import { useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router';
import { AnimatePresence, motion, useReducedMotion } from 'framer-motion';
import { ArrowUp } from 'lucide-react';
import { toast } from '@/shared/ui';
import { RailFeed, passesFeedFilters } from '@/shared/app/rail/RailFeed';
import { RailFilter, type ActorOption } from '@/shared/app/rail/RailFilter';
import { RailFooter } from '@/shared/app/rail/RailFooter';
import { RailHeader } from '@/shared/app/rail/RailHeader';
import { useRouteAgentId } from '@/shared/app/rail/useRouteAgentId';
import { useActorDirectory } from '@/shared/hooks/useActorDirectory';
import {
	buildTraceBundle,
	freezeFeed,
	isAfterFreeze,
	matchesActivityScope,
	unacknowledgedFailureCount,
	useAgentStream,
} from '@/shared/lib/agentStream';
import type {
	ActivityScope,
	FeedFreeze,
	InlineActionSpec,
	StreamEvent,
} from '@/shared/lib/agentStream';

const ALL_VALUE = '';
/** Scrolled further than this from the top counts as "not watching the head". */
const AT_TOP_PX = 24;

function scopeToValue(scope: ActivityScope): string {
	return scope ? `${scope.actorType}:${scope.actorId}` : ALL_VALUE;
}

function valueToScope(value: string): ActivityScope {
	if (!value) return null;
	const i = value.indexOf(':');
	return { actorType: value.slice(0, i), actorId: value.slice(i + 1) };
}

function sectionFor(actorType: string): ActorOption['section'] {
	if (actorType === 'agent') return 'agents';
	if (actorType === 'user') return 'people';
	return 'other';
}

/** Events under the provider's current lens — shared with the collapsed strip. */
export function useScopedActivity() {
	const stream = useAgentStream();
	const { events, scope } = stream;
	const scoped = useMemo(
		() => events.filter((ev) => matchesActivityScope(ev, scope)),
		[events, scope],
	);
	const failureCount = useMemo(() => unacknowledgedFailureCount(scoped), [scoped]);
	return { ...stream, scoped, failureCount };
}

export type ActivityRailBodyProps = {
	variant?: 'rail' | 'drawer';
	/** Docked rail: collapse to the strip. Drawer: close the sheet. */
	onCollapse: () => void;
	/** Fired after any navigation out of the rail (the drawer closes itself). */
	onNavigated?: () => void;
};

export function ActivityRailBody({
	variant = 'rail',
	onCollapse,
	onNavigated,
}: ActivityRailBodyProps) {
	const {
		events,
		scoped,
		failureCount,
		scope,
		setScope,
		failuresOnly,
		setFailuresOnly,
		categories,
		setCategories,
		paused,
		setPaused,
		frozen,
		status,
		acknowledge,
		loadOlderEvents,
		canLoadOlder,
		loadingOlder,
	} = useScopedActivity();
	const navigate = useNavigate();
	const directory = useActorDirectory();
	const reduce = useReducedMotion();
	const routeAgent = useRouteAgentId();
	const routeAgentName = routeAgent ? directory.resolve(routeAgent) : undefined;
	const suggestion =
		routeAgent && routeAgentName && scope?.actorId !== routeAgent
			? {
					value: scopeToValue({ actorType: 'agent', actorId: routeAgent }),
					label: routeAgentName,
				}
			: null;

	const visible = useMemo(
		() => (frozen ? scoped.filter((e) => !isAfterFreeze(e, frozen)) : scoped),
		[frozen, scoped],
	);

	const filters = useMemo(() => ({ failuresOnly, categories }), [failuresOnly, categories]);

	const actorOptions = useMemo<ActorOption[]>(() => {
		const count = (actorType: string, actorId: string) =>
			events.filter((ev) => matchesActivityScope(ev, { actorType, actorId })).length;
		const options: ActorOption[] = [...directory.byId.values()]
			.sort((a, b) => a.name.localeCompare(b.name))
			.map((a) => ({
				value: scopeToValue({ actorType: a.actor_type, actorId: a.id }),
				label: a.name,
				section: sectionFor(a.actor_type),
				count: count(a.actor_type, a.id),
			}));
		// A lens set before the directory loads (or for an actor it doesn't
		// list) still needs an entry, or the Filter would read "Everyone".
		const current = scopeToValue(scope);
		if (scope && !options.some((o) => o.value === current)) {
			options.push({
				value: current,
				label: directory.resolve(scope.actorId) ?? scope.actorId,
				section: sectionFor(scope.actorType),
				count: count(scope.actorType, scope.actorId),
			});
		}
		return options;
	}, [directory, events, scope]);

	// "N new": events the user isn't seeing yet — held back by pause, or
	// arrived above while they're scrolled down reading older ones.
	const logRef = useRef<HTMLDivElement | null>(null);
	const [seenWhileAway, setSeenWhileAway] = useState<FeedFreeze | null>(null);
	const newCount = useMemo(() => {
		const baseline = frozen ?? seenWhileAway;
		if (!baseline) return 0;
		return scoped.filter((e) => isAfterFreeze(e, baseline) && passesFeedFilters(e, filters))
			.length;
	}, [frozen, seenWhileAway, scoped, filters]);

	function handleScroll() {
		const el = logRef.current;
		if (!el) return;
		const away = el.scrollTop > AT_TOP_PX;
		// Over everything loaded, not just this lens: switching the lens while
		// away must not make another actor's older rows look new.
		if (away && !seenWhileAway) setSeenWhileAway(freezeFeed(events));
		if (!away && seenWhileAway) setSeenWhileAway(null);
	}

	function showNew() {
		if (paused) setPaused(false);
		logRef.current?.scrollTo({ top: 0, behavior: reduce ? 'auto' : 'smooth' });
	}

	function resolveActor(ev: StreamEvent): string | undefined {
		if (!ev.actorId) return undefined;
		return directory.resolve(ev.actorId);
	}

	function go(href: string) {
		navigate(href);
		onNavigated?.();
	}

	function handleAction(eventId: string, action: InlineActionSpec) {
		// Pure navigation actions: navigate, skip the RPC.
		if (action.href && !action.acknowledges) {
			const ev = visible.find((e) => e.id === eventId);
			const target = ev ? action.href(ev) : null;
			if (target) go(target);
			return;
		}
		if (action.acknowledges) void acknowledge(eventId);
	}

	function handleExportTraceBundle() {
		const bundle = buildTraceBundle(scoped, 5 * 60 * 1000);
		if (bundle.eventCount === 0) {
			toast({
				variant: 'default',
				title: 'Nothing to export',
				description: 'No activity in the last 5 minutes.',
			});
			return;
		}
		const blob = new Blob([JSON.stringify(bundle, null, 2)], { type: 'application/json' });
		const url = URL.createObjectURL(blob);
		const a = document.createElement('a');
		a.href = url;
		a.download = `activity-trace-bundle-${Date.now()}.json`;
		document.body.appendChild(a);
		a.click();
		document.body.removeChild(a);
		setTimeout(() => URL.revokeObjectURL(url), 0);
	}

	return (
		<>
			<RailHeader
				variant={variant}
				status={status}
				paused={paused}
				onTogglePause={() => setPaused(!paused)}
				filter={
					<RailFilter
						value={scopeToValue(scope)}
						options={actorOptions}
						totalCount={events.length}
						onChange={(v) => setScope(valueToScope(v))}
						categories={categories}
						onCategoriesChange={setCategories}
						suggestion={suggestion}
					/>
				}
				failuresOnly={failuresOnly}
				onToggleFailuresOnly={() => setFailuresOnly(!failuresOnly)}
				failureCount={failureCount}
				onLoadOlder={() => void loadOlderEvents()}
				canLoadOlder={canLoadOlder}
				loadingOlder={loadingOlder}
				onExportTraceBundle={handleExportTraceBundle}
				onCollapse={onCollapse}
			/>
			<div className="relative min-h-0 flex-1">
				<AnimatePresence>
					{newCount > 0 && (
						<motion.button
							type="button"
							onClick={showNew}
							initial={reduce ? false : { opacity: 0, y: -8, scale: 0.94 }}
							animate={{ opacity: 1, y: 0, scale: 1 }}
							exit={reduce ? undefined : { opacity: 0, y: -8, scale: 0.94 }}
							transition={{ duration: 0.22, ease: [0.32, 0.72, 0, 1] }}
							style={{ x: '-50%' }}
							className="bg-primary text-primary-foreground absolute top-2 left-1/2 z-20 flex items-center gap-1 rounded-full px-2.5 py-1 text-[11px] font-semibold shadow-md"
						>
							<ArrowUp className="h-3 w-3" aria-hidden="true" />
							<span key={newCount} className="animate-pop inline-block tabular-nums">
								{newCount}
							</span>
							new
							{paused && <span className="sr-only"> — resume the live feed</span>}
						</motion.button>
					)}
				</AnimatePresence>
				<div
					ref={logRef}
					onScroll={handleScroll}
					className="h-full overflow-y-auto px-2 py-2"
					role="log"
					aria-live="polite"
					aria-relevant="additions"
					aria-label="Activity feed"
				>
					<RailFeed
						events={visible}
						filters={filters}
						resolveActor={resolveActor}
						onAction={handleAction}
						onNavigate={go}
						scopedTo={
							scope
								? {
										label:
											actorOptions.find(
												(o) => o.value === scopeToValue(scope),
											)?.label ?? scope.actorId,
										onClear: () => setScope(null),
									}
								: undefined
						}
					/>
				</div>
			</div>
			<RailFooter scope={scope} onNavigate={onNavigated} />
		</>
	);
}
