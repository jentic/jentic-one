/**
 * ActionInboxBell — the "Needs your action" queue as a header bell + dropdown,
 * sitting beside Quick actions in the Dashboard page header.
 *
 * Why a bell instead of a body card: the Agent rail already streams the same
 * approvals/alerts live down the right edge, so a second full-width list on
 * the dashboard body read as duplication and pushed the actual data below the
 * fold. The bell keeps ONE persistent, universally-understood affordance —
 * a count badge that goes red when something severe is failing — while the
 * dropdown holds the durable, urgency-sorted triage list (unlike the rail,
 * which is transient SSE and can miss items filed outside its window).
 *
 * Sources stay independent (three queries), so one endpoint failing degrades
 * to an inline error row while the other rows keep rendering. Access requests
 * are decided in place via the shared decision dialog; agents and events
 * deep-link to the surfaces that own those flows. Every action closes the
 * panel — the queues refetch and the badge follows.
 */
import { useState, type ReactNode } from 'react';
import { useNavigate } from 'react-router';
import { useQueryClient } from '@tanstack/react-query';
import { Bell, CheckCircle2 } from 'lucide-react';
import {
	ActorLabel,
	AgentBadge,
	AppLink,
	Button,
	useDismissable,
	useViewportClamp,
} from '@/shared/ui';
import { AccessRequestDecisionDialog } from '@/shared/app';
import {
	usePendingAgents,
	usePendingAccessRequests,
	useActionableEvents,
	formatApproxCount,
	dashboardKeys,
	EventSeverity,
	type AccessRequest,
	type EventResponse,
} from '@/modules/dashboard/api';
import { ROUTES } from '@/shared/app/routes';
import { eventSeverityIcon } from '@/shared/lib';
import { timeAgo, cn } from '@/shared/lib/utils';

/** How many rows the dropdown shows before deferring to the owning surfaces. */
const MAX_ROWS = 8;

/** Lower rank = more urgent = higher in the list. */
const URGENCY = { alertHigh: 0, alertWarn: 1, access: 2, agent: 3 } as const;

interface InboxRow {
	key: string;
	urgency: number;
	/** Unix ms used to order rows inside the same urgency band (newest first). */
	tsMs: number;
	/** Rail-style left stripe class. */
	stripe: string;
	/** Mono uppercase kind tag, e.g. "Alert". */
	tag: { label: string; className: string };
	leading: ReactNode;
	title: ReactNode;
	subtitle?: ReactNode;
	tsIso?: string | null;
	action: { label: string; ariaLabel: string; onAct: () => void };
}

function alertRow(event: EventResponse, onView: () => void): InboxRow {
	const severe =
		event.severity === EventSeverity.ERROR || event.severity === EventSeverity.CRITICAL;
	const SeverityIcon = eventSeverityIcon(event.severity);
	return {
		key: `alert-${event.event_id}`,
		urgency: severe ? URGENCY.alertHigh : URGENCY.alertWarn,
		tsMs: Date.parse(event.created_at) || 0,
		stripe: severe ? 'border-l-danger' : 'border-l-warning',
		tag: {
			label: 'Alert',
			className: severe ? 'bg-danger/10 text-danger' : 'bg-warning/15 text-warning',
		},
		leading: (
			<span
				className={cn(
					'flex h-7 w-7 shrink-0 items-center justify-center rounded-lg ring-1',
					severe
						? 'bg-danger/12 text-danger ring-danger/20'
						: 'bg-warning/12 text-warning ring-warning/20',
				)}
				title={event.severity}
			>
				<SeverityIcon className="h-3.5 w-3.5" aria-hidden="true" />
			</span>
		),
		title: event.summary,
		subtitle:
			event.detail || event.actor_id ? (
				<>
					{event.detail}
					{event.detail && event.actor_id ? ' · ' : ''}
					{event.actor_id && (
						<ActorLabel actorId={event.actor_id} actorType={event.actor_type} />
					)}
				</>
			) : undefined,
		tsIso: event.created_at,
		action: {
			label: 'View',
			ariaLabel: `View ${event.severity} event: ${event.summary}`,
			onAct: onView,
		},
	};
}

function summarizeRequest(request: AccessRequest): string {
	const n = request.items.length;
	const head = request.items[0];
	const label = head ? `${head.resource_type} · ${head.action}` : 'access';
	return n > 1 ? `${label} +${n - 1} more` : label;
}

export function ActionInboxBell() {
	const agents = usePendingAgents();
	const requests = usePendingAccessRequests();
	const alerts = useActionableEvents();
	const navigate = useNavigate();
	const queryClient = useQueryClient();
	const [open, setOpen] = useState(false);
	const [active, setActive] = useState<AccessRequest | null>(null);
	const ref = useDismissable<HTMLDivElement>(open, () => setOpen(false));
	// On narrow screens the header actions wrap and the bell can land on the
	// LEFT half, so a right-anchored panel would hang off-screen — clamp it.
	const panelRef = useViewportClamp<HTMLDivElement>(open);

	// Read at render time to dodge the `@/shared/app` barrel's import-cycle TDZ.
	const eventsHref = `${ROUTES.monitor}?tab=events`;

	const isLoading = agents.isLoading || requests.isLoading || alerts.isLoading;

	/** Every row action dismisses the panel — a decision either navigates away
	 * or opens the modal dialog, and the badge refetches behind it. */
	function act(fn: () => void) {
		return () => {
			setOpen(false);
			fn();
		};
	}

	const rows: InboxRow[] = [
		...(alerts.data?.events ?? []).map((event) =>
			alertRow(
				event,
				act(() => navigate(eventsHref)),
			),
		),
		...(requests.data?.requests ?? []).map((request): InboxRow => ({
			key: `access-${request.id}`,
			urgency: URGENCY.access,
			tsMs: request.filed_at ? Date.parse(request.filed_at) || 0 : 0,
			stripe: 'border-l-primary',
			tag: { label: 'Access', className: 'bg-primary/10 text-primary' },
			leading: <AgentBadge id={request.actor_id} kind="Agent" size="sm" />,
			title: summarizeRequest(request),
			subtitle: (
				<>
					requested by <ActorLabel actorId={request.actor_id} />
				</>
			),
			tsIso: request.filed_at,
			action: {
				label: 'Decide',
				ariaLabel: `Decide access request ${summarizeRequest(request)}`,
				onAct: act(() => setActive(request)),
			},
		})),
		...(agents.data?.agents ?? []).map((agent): InboxRow => ({
			key: `agent-${agent.id}`,
			urgency: URGENCY.agent,
			tsMs: Date.parse(agent.created_at) || 0,
			stripe: 'border-l-primary',
			tag: { label: 'Agent', className: 'bg-primary/10 text-primary' },
			leading: <AgentBadge id={agent.id} name={agent.name} kind="Agent" size="sm" />,
			title: agent.name,
			subtitle: `registered ${timeAgo(agent.created_at)}${
				agent.description ? ` · ${agent.description}` : ''
			}`,
			tsIso: agent.created_at,
			action: {
				label: 'Review',
				ariaLabel: `Review agent ${agent.name}`,
				onAct: act(() => navigate(ROUTES.agents)),
			},
		})),
	]
		.sort((a, b) => a.urgency - b.urgency || b.tsMs - a.tsMs)
		.slice(0, MAX_ROWS);

	// One total across the three queues; "+" when any source saw a partial page.
	const counts = [agents.data?.count, requests.data?.count, alerts.data?.count];
	const total = {
		value: counts.reduce((sum, c) => sum + (c?.value ?? 0), 0),
		atLeast: counts.some((c) => c?.atLeast),
	};
	const hasSevereAlert = (alerts.data?.events ?? []).some(
		(e) => e.severity === EventSeverity.ERROR || e.severity === EventSeverity.CRITICAL,
	);

	const sourceErrors = [
		agents.isError && { key: 'agents', label: 'pending agents' },
		requests.isError && { key: 'access-requests', label: 'pending access requests' },
		alerts.isError && { key: 'alerts', label: 'alerts' },
	].filter(Boolean) as { key: string; label: string }[];

	const showBadge = !isLoading && (total.value > 0 || sourceErrors.length > 0);
	const badgeLabel =
		sourceErrors.length > 0 && total.value === 0 ? '!' : formatApproxCount(total);

	return (
		<div ref={ref} className="relative">
			<Button
				variant="secondary"
				size="sm"
				aria-haspopup="dialog"
				aria-expanded={open}
				aria-label={
					showBadge
						? `Needs your action (${badgeLabel})`
						: 'Needs your action (all clear)'
				}
				title="Needs your action"
				onClick={() => setOpen((v) => !v)}
				className="relative"
			>
				<Bell className="h-4 w-4" aria-hidden="true" />
				{showBadge && (
					<span
						aria-hidden="true"
						className={cn(
							'absolute -top-1.5 -right-1.5 flex h-4 min-w-4 items-center justify-center rounded-full px-1 font-mono text-[10px] leading-none font-semibold text-white tabular-nums',
							hasSevereAlert || sourceErrors.length > 0 ? 'bg-danger' : 'bg-warning',
						)}
					>
						{badgeLabel}
					</span>
				)}
			</Button>

			{open && (
				<div
					ref={panelRef}
					role="dialog"
					aria-label="Needs your action"
					className="border-border bg-background absolute top-full right-0 z-50 mt-1.5 w-[26rem] max-w-[calc(100vw-1.5rem)] rounded-lg border shadow-lg"
				>
					<header className="border-border/70 flex items-center gap-2 border-b px-4 py-2.5">
						<h2 className="font-heading text-foreground text-sm font-semibold">
							Needs your action
						</h2>
						{showBadge && total.value > 0 && (
							<span
								className={cn(
									'rounded-full px-1.5 py-0.5 font-mono text-[10px] leading-none font-semibold tabular-nums',
									hasSevereAlert
										? 'bg-danger/10 text-danger'
										: 'bg-warning/15 text-warning',
								)}
							>
								{formatApproxCount(total)}
							</span>
						)}
						<span className="text-muted-foreground ml-auto text-[11px]">
							also live in the Agent rail
						</span>
					</header>

					{isLoading ? (
						<p className="text-muted-foreground px-4 py-6 text-center text-sm">
							Loading…
						</p>
					) : rows.length === 0 && sourceErrors.length === 0 ? (
						<div className="flex items-center gap-2.5 px-4 py-5">
							<CheckCircle2
								className="text-success h-5 w-5 shrink-0"
								aria-hidden="true"
							/>
							<p className="text-sm">
								<span className="text-foreground font-medium">All clear.</span>{' '}
								<span className="text-muted-foreground">
									Nothing is waiting on you.
								</span>
							</p>
						</div>
					) : (
						<ul className="divide-border/70 max-h-[26rem] divide-y overflow-y-auto">
							{sourceErrors.map((source) => (
								<li
									key={source.key}
									role="alert"
									className="border-l-danger text-muted-foreground flex items-center gap-2.5 border-l-2 px-3 py-2.5 text-xs"
								>
									<span className="bg-danger/60 h-1.5 w-1.5 shrink-0 rounded-full" />
									Couldn&apos;t load {source.label} — the rest of the queue is
									unaffected.
								</li>
							))}
							{rows.map((row) => (
								<li
									key={row.key}
									className={cn(
										'flex items-center gap-2.5 border-l-2 px-3 py-2.5',
										row.stripe,
										row.urgency === URGENCY.alertHigh && 'bg-danger/[0.03]',
									)}
								>
									{row.leading}
									<div className="min-w-0 flex-1">
										<div className="flex items-center gap-1.5">
											<span
												className={cn(
													'shrink-0 rounded px-1 py-0.5 font-mono text-[9px] font-semibold tracking-wider uppercase',
													row.tag.className,
												)}
											>
												{row.tag.label}
											</span>
											<span className="text-foreground truncate text-[13px] leading-tight font-medium">
												{row.title}
											</span>
										</div>
										<div className="text-muted-foreground mt-0.5 flex items-baseline gap-1.5 text-[11px] leading-tight">
											{row.subtitle && (
												<span className="min-w-0 truncate">
													{row.subtitle}
												</span>
											)}
											{row.tsIso && (
												<time
													dateTime={row.tsIso}
													title={new Date(row.tsIso).toLocaleString()}
													className="ml-auto shrink-0 font-mono text-[10px] whitespace-nowrap tabular-nums"
												>
													{timeAgo(row.tsIso)}
												</time>
											)}
										</div>
									</div>
									<Button
										variant="secondary"
										size="sm"
										onClick={row.action.onAct}
										aria-label={row.action.ariaLabel}
										className="h-6 shrink-0 px-2 text-[11px]"
									>
										{row.action.label}
									</Button>
								</li>
							))}
						</ul>
					)}

					{!isLoading && (rows.length > 0 || sourceErrors.length > 0) && (
						<footer className="border-border/70 text-muted-foreground flex flex-wrap items-center gap-x-3 gap-y-1 border-t px-4 py-2 text-[11px]">
							{total.value > rows.length && (
								<span>Showing the {rows.length} most urgent.</span>
							)}
							<AppLink
								href={ROUTES.accessRequests}
								onClick={() => setOpen(false)}
								className="text-primary font-medium hover:underline"
							>
								All access requests
							</AppLink>
							<AppLink
								href={ROUTES.agents}
								onClick={() => setOpen(false)}
								className="text-primary font-medium hover:underline"
							>
								All agents
							</AppLink>
							<AppLink
								href={eventsHref}
								onClick={() => setOpen(false)}
								className="text-primary font-medium hover:underline"
							>
								All events
							</AppLink>
						</footer>
					)}
				</div>
			)}

			<AccessRequestDecisionDialog
				request={active}
				onClose={() => setActive(null)}
				onDecided={() => {
					queryClient.invalidateQueries({ queryKey: dashboardKeys.all });
					queryClient.invalidateQueries({
						queryKey: dashboardKeys.accessRequestsRoot,
					});
				}}
			/>
		</div>
	);
}
