/**
 * WebhookEndpointTable — the endpoint list as a two-up card grid.
 *
 * Clicking a card navigates to the endpoint's detail page
 * (`/webhooks/:endpointId`) — the same routed-detail pattern the Agents and
 * Toolkits consoles use, so the whole card is a single `AppLink` (one keyboard /
 * AT target, middle-click friendly). Everything actionable — Send test / Edit /
 * Rotate / Delete and the delivery log — still lives on the detail page.
 *
 * The layout mirrors the Toolkits console: narrower cards, two per row from `md`
 * up (`grid grid-cols-1 gap-4 md:grid-cols-2`), each led by the shared
 * {@link AgentBadge} identity tile (a deterministic monogram, same treatment as
 * Toolkits/Agents/Credentials). A card surfaces the answers an operator scans a
 * webhooks console for — is it live (active/paused), is it healthy (a
 * success/failure pill derived from `/stats`, text + icon so it never relies on
 * colour alone), when did it last fire ("2m ago"), and what does it listen for
 * (event-type count). Health is per-endpoint and degrades independently: an
 * endpoint whose stats have not loaded shows a neutral "Health unknown" pill
 * rather than blocking the card or faking a number.
 */
import type { ReactNode } from 'react';
import {
	AlertTriangle,
	CheckCircle2,
	ChevronRight,
	Clock,
	CircleDashed,
	CircleHelp,
	Webhook,
} from 'lucide-react';
import { AgentBadge, AppLink, Badge, Button, EmptyState } from '@/shared/ui';
import { timeAgo } from '@/shared/lib/utils';
import type { WebhookEndpointEntity, WebhookEndpointStats } from '@/modules/webhooks/api';
import { endpointHealth, type HealthTone } from '@/modules/webhooks/lib/health';
import { ROUTE_PATHS } from '@/shared/app/routes';

interface WebhookEndpointTableProps {
	endpoints: WebhookEndpointEntity[];
	/** Per-endpoint delivery health, keyed by id. A missing/undefined entry = not yet loaded. */
	statsById: Map<string, WebhookEndpointStats | undefined>;
	canWrite: boolean;
	onCreate: () => void;
}

const HEALTH_STYLES: Record<HealthTone, { icon: ReactNode; className: string; srPrefix: string }> =
	{
		healthy: {
			icon: <CheckCircle2 className="h-3.5 w-3.5" aria-hidden="true" />,
			className: 'bg-success/12 text-success border-success/25',
			srPrefix: 'Delivery health',
		},
		degraded: {
			icon: <AlertTriangle className="h-3.5 w-3.5" aria-hidden="true" />,
			className: 'bg-accent-orange/12 text-accent-orange border-accent-orange/25',
			srPrefix: 'Delivery health',
		},
		failing: {
			icon: <AlertTriangle className="h-3.5 w-3.5" aria-hidden="true" />,
			className: 'bg-danger/12 text-danger border-danger/25',
			srPrefix: 'Delivery health',
		},
		idle: {
			icon: <CircleDashed className="h-3.5 w-3.5" aria-hidden="true" />,
			className: 'bg-muted text-muted-foreground border-border',
			srPrefix: 'Delivery health',
		},
		unknown: {
			icon: <CircleHelp className="h-3.5 w-3.5" aria-hidden="true" />,
			className: 'bg-muted text-muted-foreground border-border',
			srPrefix: 'Delivery health',
		},
	};

/**
 * The delivery-health pill: an icon + text label so pass/fail never rests on
 * colour alone. Aria-labelled with the endpoint name so a screen reader reading
 * the pill in isolation still has context.
 */
function HealthPill({
	tone,
	label,
	endpointName,
}: {
	tone: HealthTone;
	label: string;
	endpointName: string;
}) {
	const style = HEALTH_STYLES[tone];
	return (
		<span
			className={`inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 font-mono text-xs font-medium ${style.className}`}
			aria-label={`${style.srPrefix} for ${endpointName}: ${label}`}
		>
			{style.icon}
			<span>{label}</span>
		</span>
	);
}

function eventSummary(endpoint: WebhookEndpointEntity): string {
	const n = endpoint.eventTypes.length;
	if (n === 0) return 'All events';
	return `${n} event type${n === 1 ? '' : 's'}`;
}

export function WebhookEndpointTable({
	endpoints,
	statsById,
	canWrite,
	onCreate,
}: WebhookEndpointTableProps) {
	if (endpoints.length === 0) {
		return (
			<EmptyState
				icon={<Webhook className="h-6 w-6" />}
				title="No webhook endpoints yet"
				description="Create your first endpoint to push signed platform events — like a failed execution or an expiring credential — out to a URL you own. See the Relay guide for the payload shape and a copy-pasteable receiver."
				action={
					canWrite ? (
						<Button variant="primary" onClick={onCreate}>
							Create your first endpoint
						</Button>
					) : undefined
				}
			/>
		);
	}

	return (
		<ul className="grid grid-cols-1 gap-4 md:grid-cols-2">
			{endpoints.map((endpoint) => {
				const health = endpointHealth(statsById.get(endpoint.id));
				return (
					<li key={endpoint.id}>
						<AppLink
							href={ROUTE_PATHS.webhookEndpoint(endpoint.id)}
							aria-label={`Open ${endpoint.name}`}
							className="group border-border/60 bg-card hover:border-border hover:bg-muted/30 focus-visible:ring-primary/40 flex h-full w-full min-w-0 flex-col gap-3 overflow-hidden rounded-xl border p-4 text-left transition-all hover:-translate-y-0.5 hover:shadow-sm focus-visible:ring-2 focus-visible:outline-none"
						>
							<div className="flex items-center gap-3">
								<AgentBadge
									id={endpoint.id}
									name={endpoint.name}
									kind="Webhook"
									size="md"
								/>
								<div className="min-w-0 flex-1">
									<div className="flex items-center gap-2">
										<h2 className="text-foreground min-w-0 flex-1 truncate font-medium">
											{endpoint.name}
										</h2>
										{endpoint.active ? (
											<Badge variant="success" dot>
												active
											</Badge>
										) : (
											<Badge variant="danger" dot>
												paused
											</Badge>
										)}
										<ChevronRight
											size={16}
											aria-hidden="true"
											className="text-muted-foreground group-hover:text-foreground shrink-0 transition-colors"
										/>
									</div>
									<p
										className="text-muted-foreground mt-0.5 truncate font-mono text-xs"
										title={endpoint.targetUrl ?? undefined}
									>
										{endpoint.targetUrl}
									</p>
								</div>
							</div>

							<div>
								<HealthPill
									tone={health.tone}
									label={health.label}
									endpointName={endpoint.name}
								/>
							</div>

							<div className="text-muted-foreground mt-auto flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px]">
								<span className="inline-flex items-center gap-1">
									<Clock className="h-3 w-3" aria-hidden="true" />
									<span>
										{(() => {
											if (!health.lastAttemptAt) return 'never';
											const rel = timeAgo(health.lastAttemptAt);
											// `timeAgo` returns "now" for a sub-second delta —
											// "now ago" would read wrong, so show it bare.
											return rel === 'now' ? 'now' : `${rel} ago`;
										})()}
									</span>
								</span>
								<span className="inline-flex items-center gap-1">
									<Webhook className="h-3 w-3" aria-hidden="true" />
									{eventSummary(endpoint)}
								</span>
								{health.recentTotal > 0 && (
									<span className="ml-auto">
										<span className="text-foreground">
											{health.recentTotal.toLocaleString()}
										</span>{' '}
										in 24h
									</span>
								)}
							</div>
						</AppLink>
					</li>
				);
			})}
		</ul>
	);
}
