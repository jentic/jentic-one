/**
 * WebhookEndpointDetailBody — the endpoint detail's tabbed shell.
 *
 * This is the routed successor to the old `WebhookEndpointDrawer`: the SAME
 * three-tab grammar (Overview / Deliveries / Settings) and the SAME tab content,
 * but rendered inline in the page body rather than in a slide-over — matching
 * how {@link ToolkitDetailBody} and the agent console lay out their detail
 * pages. It composes the shared detail primitives ({@link TabNav} +
 * {@link DetailSection} + a {@link StatCard} KPI strip + {@link DataTable}).
 *
 * Because it is now a real route (`/webhooks/:endpointId`), the active tab lives
 * in the `?tab=` search param (same pattern as the toolkit/agent consoles), so
 * each sub-tab is deep-linkable and the back button walks between them. This
 * resolves the drawer's earlier deviation of holding the tab in local state.
 *
 * Tab responsibilities (unchanged from the drawer):
 *   - Overview   KPI strip + a "Delivery Stats (last 24h)" health bar + latest
 *                activity — all from the aggregate `/stats` endpoint.
 *   - Deliveries the Message-Attempts table (per-attempt history, status
 *                filters, per-row Resend/Replay) — `DeliveryLogPanel`.
 *   - Settings   configuration summary + an *inline* grouped-events editor,
 *                secret rotation, and an **Advanced** disclosure hosting the
 *                IP/CIDR allowlist, plus the DangerZone delete.
 *
 * The header — name, target URL, grace badge, and the Send test action — lives
 * on the host page's `PageHeader` (mirroring the kill switch on the toolkit
 * console). Editing the endpoint's configuration happens inline in the Settings
 * tab (name / target URL / active, subscribed events, the CIDR allowlist), so
 * this body is handed callbacks only for the flows the page still owns — Rotate,
 * Delete, and the confirm dialogs plus the one-time secret reveal.
 */
import { useEffect, useState } from 'react';
import { useSearchParams } from 'react-router';
import {
	Activity,
	CheckCircle2,
	Clock,
	Gauge,
	ListChecks,
	SearchX,
	Send,
	Settings,
	ShieldCheck,
	Timer,
} from 'lucide-react';
import {
	Badge,
	Button,
	Checkbox,
	DangerZone,
	DetailSection,
	Disclosure,
	EmptyState,
	Input,
	Label,
	LoadingState,
	StatCard,
	TabNav,
	type TabNavOption,
} from '@/shared/ui';
import { timeAgo } from '@/shared/lib/utils';
import {
	useUpdateWebhookEndpoint,
	useWebhookEndpoint,
	useWebhookEndpointStats,
} from '@/modules/webhooks/api';
import type { WebhookEndpointEntity, WebhookEndpointStats } from '@/modules/webhooks/api';
import { DeliveryLogPanel } from '@/modules/webhooks/components/DeliveryLogPanel';
import { EventTypePicker } from '@/modules/webhooks/components/EventTypePicker';
import { CidrListField } from '@/modules/webhooks/components/CidrListField';
import { targetUrlServerError, validateTargetUrl } from '@/modules/webhooks/lib/targetUrl';

const DETAIL_TABS = ['overview', 'deliveries', 'settings'] as const;
type DetailTab = (typeof DETAIL_TABS)[number];

const TAB_OPTIONS: TabNavOption<DetailTab>[] = [
	{ value: 'overview', label: 'Overview', icon: <Activity className="h-4 w-4" /> },
	{ value: 'deliveries', label: 'Deliveries', icon: <ListChecks className="h-4 w-4" /> },
	{ value: 'settings', label: 'Settings', icon: <Settings className="h-4 w-4" /> },
];

const tabId = (tab: string) => `wh-detail-tab-${tab}`;
const panelId = (tab: string) => `wh-detail-panel-${tab}`;

function isDetailTab(value: string | null): value is DetailTab {
	return value != null && (DETAIL_TABS as readonly string[]).includes(value);
}

function formatMs(ms: number | null): string {
	if (ms == null) return '—';
	if (ms < 1000) return `${Math.round(ms)} ms`;
	return `${(ms / 1000).toFixed(2)} s`;
}

/** Order-sensitive content equality for two string lists. */
function arraysEqual(a: readonly string[], b: readonly string[]): boolean {
	return a.length === b.length && a.every((v, i) => v === b[i]);
}

/**
 * The Svix-style "Delivery Stats (last 24h)" bar: a single stacked bar that
 * splits the last-24h volume into succeeded / failed segments, with a legend
 * beneath. Purely presentational — it reads the aggregate counts the KPI strip
 * already loaded. When nothing has been sent in the window it degrades to a
 * muted empty track rather than a zero-width bar.
 */
function DeliveryStatsBar({ stats }: { stats: WebhookEndpointStats | undefined }) {
	const total = stats?.recentTotal ?? 0;
	const failed = stats?.recentFailed ?? 0;
	const succeeded = Math.max(total - failed, 0);
	const pct = (n: number) => (total > 0 ? `${((n / total) * 100).toFixed(1)}%` : '0%');

	return (
		<div className="space-y-3">
			<div
				className="border-border bg-muted flex h-3 w-full overflow-hidden rounded-full"
				role="img"
				aria-label={
					total > 0
						? `${succeeded} succeeded, ${failed} failed of ${total} in the last 24 hours`
						: 'No deliveries in the last 24 hours'
				}
			>
				{total > 0 && (
					<>
						<span
							className="bg-accent-green h-full"
							style={{ width: pct(succeeded) }}
						/>
						<span className="bg-danger h-full" style={{ width: pct(failed) }} />
					</>
				)}
			</div>
			<div className="flex flex-wrap gap-x-4 gap-y-1 text-xs">
				<span className="flex items-center gap-1.5">
					<span className="bg-accent-green h-2 w-2 rounded-full" aria-hidden="true" />
					<span className="text-muted-foreground">Succeeded</span>
					<span className="text-foreground font-mono">{succeeded}</span>
				</span>
				<span className="flex items-center gap-1.5">
					<span className="bg-danger h-2 w-2 rounded-full" aria-hidden="true" />
					<span className="text-muted-foreground">Failed</span>
					<span className="text-foreground font-mono">{failed}</span>
				</span>
			</div>
		</div>
	);
}

/** The Overview KPI strip — aggregate delivery health, from the stats endpoint. */
function OverviewTab({ endpointId }: { endpointId: string }) {
	const { data, isLoading, error } = useWebhookEndpointStats(endpointId);
	const stats: WebhookEndpointStats | undefined = data;
	const successRate =
		stats && stats.total > 0
			? `${Math.round(((stats.total - (stats.countsByStatus.dead ?? 0) - (stats.countsByStatus.failed ?? 0)) / stats.total) * 100)}%`
			: '—';
	const recentHealthy = !stats || stats.recentFailed === 0;

	return (
		<div className="space-y-4">
			<div
				role="group"
				aria-label="Delivery health"
				className="grid grid-cols-2 gap-3 lg:grid-cols-4"
			>
				<StatCard
					label="Total deliveries"
					value={stats ? stats.total.toLocaleString() : '—'}
					icon={<Send className="h-4 w-4" />}
					accent="blue"
					isLoading={isLoading}
					error={error ? 'Unavailable' : null}
				/>
				<StatCard
					label="Last 24h"
					value={stats ? stats.recentTotal.toLocaleString() : '—'}
					caption={
						stats && stats.recentFailed > 0 ? `${stats.recentFailed} failed` : undefined
					}
					icon={<Clock className="h-4 w-4" />}
					accent={recentHealthy ? 'green' : 'danger'}
					isLoading={isLoading}
					error={error ? 'Unavailable' : null}
				/>
				<StatCard
					label="Success rate"
					value={successRate}
					icon={<CheckCircle2 className="h-4 w-4" />}
					accent="green"
					isLoading={isLoading}
					error={error ? 'Unavailable' : null}
				/>
				<StatCard
					label="Avg response"
					value={formatMs(stats?.avgDurationMs ?? null)}
					caption={
						stats?.lastDurationMs != null
							? `last ${formatMs(stats.lastDurationMs)}`
							: undefined
					}
					icon={<Gauge className="h-4 w-4" />}
					accent="orange"
					isLoading={isLoading}
					error={error ? 'Unavailable' : null}
				/>
			</div>

			<DetailSection
				title="Delivery stats (last 24h)"
				icon={<Activity className="h-4 w-4" />}
			>
				<DeliveryStatsBar stats={stats} />
			</DetailSection>

			<DetailSection title="Latest activity" icon={<Timer className="h-4 w-4" />}>
				<dl className="grid grid-cols-1 gap-x-6 gap-y-3 sm:grid-cols-2">
					<div>
						<dt className="text-muted-foreground text-xs tracking-wider uppercase">
							Last attempt
						</dt>
						<dd className="text-foreground mt-0.5 text-sm">
							{stats?.lastAttemptAt ? timeAgo(stats.lastAttemptAt) : '—'}
						</dd>
					</div>
					<div>
						<dt className="text-muted-foreground text-xs tracking-wider uppercase">
							Next attempt
						</dt>
						<dd className="text-foreground mt-0.5 text-sm">
							{stats?.nextAttemptAt ? timeAgo(stats.nextAttemptAt) : '—'}
						</dd>
					</div>
					<div>
						<dt className="text-muted-foreground text-xs tracking-wider uppercase">
							Last response
						</dt>
						<dd className="text-foreground mt-0.5 text-sm">
							{stats?.lastStatusCode != null ? (
								<code className="font-mono">{stats.lastStatusCode}</code>
							) : (
								'—'
							)}
						</dd>
					</div>
					<div>
						<dt className="text-muted-foreground text-xs tracking-wider uppercase">
							Dead-lettered
						</dt>
						<dd className="text-foreground mt-0.5 text-sm">
							{stats ? (stats.countsByStatus.dead ?? 0).toLocaleString() : '—'}
						</dd>
					</div>
				</dl>
			</DetailSection>
		</div>
	);
}

/**
 * The inline "Subscribed events" editor (the reference's Save/Cancel events
 * panel). It edits *only* the event subscription — the highest-frequency change
 * an operator makes — via the grouped `EventTypePicker`, and commits with the
 * update mutation. Name, target URL, and the active flag live in the sibling
 * {@link ConfigurationEditor}. The draft is local and dirty-tracked so
 * Save/Cancel only appear once something changed.
 */
function SubscribedEventsEditor({ endpoint }: { endpoint: WebhookEndpointEntity }) {
	const update = useUpdateWebhookEndpoint();
	const [draft, setDraft] = useState<string[]>(endpoint.eventTypes);

	// Re-seed whenever the endpoint's own subscription changes (a save landing,
	// or navigating to a different endpoint). Guard on content equality so a
	// background refetch of an unchanged list doesn't clobber an in-progress
	// draft (rather than relying on React Query structural sharing to do it).
	useEffect(() => {
		setDraft((prev) => (arraysEqual(prev, endpoint.eventTypes) ? prev : endpoint.eventTypes));
	}, [endpoint.id, endpoint.eventTypes]);

	const dirty = !arraysEqual(draft, endpoint.eventTypes);

	async function save() {
		await update.mutateAsync({
			endpointId: endpoint.id,
			changes: { eventTypes: draft },
		});
		// The list invalidation re-seeds `draft` from the fresh value via the
		// effect above; nothing else to reset.
	}

	return (
		<div className="space-y-3">
			<EventTypePicker value={draft} onChange={setDraft} labelId="wh-detail-events-label" />
			{dirty && (
				<div className="flex justify-end gap-2">
					<Button
						variant="secondary"
						size="sm"
						onClick={() => setDraft(endpoint.eventTypes)}
						disabled={update.isPending}
					>
						Cancel
					</Button>
					<Button variant="primary" size="sm" onClick={save} loading={update.isPending}>
						Save events
					</Button>
				</div>
			)}
		</div>
	);
}

/**
 * The Advanced IP/CIDR allowlist editor — a dirty-tracked Save/Cancel around the
 * {@link CidrListField}, matching the sibling {@link SubscribedEventsEditor}.
 *
 * Chips are edited in a *local* draft; the whole set commits in a **single**
 * update on Save. That fixes the earlier per-chip behaviour, where every add or
 * remove fired its own PATCH (and its own "updated" toast), so building a
 * three-entry list meant three round-trips and three toasts. While the save is
 * in flight the field is disabled so a second edit can't race it.
 */
function AllowlistEditor({ endpoint }: { endpoint: WebhookEndpointEntity }) {
	const update = useUpdateWebhookEndpoint();
	const [draft, setDraft] = useState<string[]>(endpoint.allowedCidrs);

	// Re-seed whenever the endpoint's own allowlist changes (a save landing, or
	// navigating to a different endpoint). Guard on content equality so a
	// background refetch of an unchanged list doesn't clobber an in-progress
	// draft (rather than relying on React Query structural sharing to do it).
	useEffect(() => {
		setDraft((prev) =>
			arraysEqual(prev, endpoint.allowedCidrs) ? prev : endpoint.allowedCidrs,
		);
	}, [endpoint.id, endpoint.allowedCidrs]);

	const dirty = !arraysEqual(draft, endpoint.allowedCidrs);

	async function save() {
		await update.mutateAsync({
			endpointId: endpoint.id,
			changes: { allowedCidrs: draft },
		});
		// The list/endpoint invalidation re-seeds `draft` via the effect above.
	}

	return (
		<div className="space-y-3">
			<CidrListField
				value={draft}
				onChange={setDraft}
				labelId="wh-detail-cidrs-label"
				disabled={update.isPending}
			/>
			{dirty && (
				<div className="flex justify-end gap-2">
					<Button
						variant="secondary"
						size="sm"
						onClick={() => setDraft(endpoint.allowedCidrs)}
						disabled={update.isPending}
					>
						Cancel
					</Button>
					<Button variant="primary" size="sm" onClick={save} loading={update.isPending}>
						Save allowlist
					</Button>
				</div>
			)}
		</div>
	);
}

/**
 * The inline "Configuration" editor — name, target URL, and the active flag,
 * edited in place on the Settings tab (matching the Agents/Toolkits
 * `IdentitySettingsCard` grammar): fields seeded from the endpoint, a single
 * bottom-right "Save changes" that stays disabled until something changed, and a
 * "Reset" to discard the draft. Dirtiness is measured against the last value the
 * server acknowledged (`saved`) rather than live props, so a background refetch
 * can't clobber an in-progress draft and the form reads clean the instant a save
 * resolves. The target URL keeps the create sheet's validation: a cheap
 * client-side check before submit, and the server's rejection reason pinned to
 * the field (not lost in a toast) when the backend refuses a disallowed URL.
 */
function ConfigurationEditor({ endpoint }: { endpoint: WebhookEndpointEntity }) {
	const update = useUpdateWebhookEndpoint();
	const [name, setName] = useState(endpoint.name);
	const [targetUrl, setTargetUrl] = useState(endpoint.targetUrl ?? '');
	const [active, setActive] = useState(endpoint.active);
	const [nameError, setNameError] = useState<string | null>(null);
	const [targetUrlError, setTargetUrlError] = useState<string | null>(null);
	// What the server last acknowledged — dirtiness compares against THIS, not
	// live props, so the form reads clean the instant a save resolves (before a
	// cache refetch) and a background refetch can't flip a clean form dirty.
	const [saved, setSaved] = useState({
		name: endpoint.name,
		targetUrl: endpoint.targetUrl ?? '',
		active: endpoint.active,
	});

	// Re-seed when the endpoint itself changes (a save landing, or navigating to
	// a different endpoint). Keyed on id + the fields so a concurrent update
	// repaints the editor rather than stranding a stale draft.
	useEffect(() => {
		const next = {
			name: endpoint.name,
			targetUrl: endpoint.targetUrl ?? '',
			active: endpoint.active,
		};
		setSaved(next);
		setName(next.name);
		setTargetUrl(next.targetUrl);
		setActive(next.active);
		setNameError(null);
		setTargetUrlError(null);
	}, [endpoint.id, endpoint.name, endpoint.targetUrl, endpoint.active]);

	const trimmedName = name.trim();
	const trimmedUrl = targetUrl.trim();
	const dirty =
		trimmedName !== saved.name || trimmedUrl !== saved.targetUrl || active !== saved.active;

	function reset() {
		setName(saved.name);
		setTargetUrl(saved.targetUrl);
		setActive(saved.active);
		setNameError(null);
		setTargetUrlError(null);
	}

	async function save() {
		if (!trimmedName) {
			setNameError('A name is required.');
			return;
		}
		setNameError(null);
		const urlError = validateTargetUrl(targetUrl);
		if (urlError) {
			setTargetUrlError(urlError);
			return;
		}
		setTargetUrlError(null);
		try {
			const updated = await update.mutateAsync({
				endpointId: endpoint.id,
				changes: { name: trimmedName, targetUrl: trimmedUrl, active },
			});
			// Re-seed from the acknowledged value so the form returns to clean even
			// before the cache refetch lands.
			setSaved({
				name: updated.name,
				targetUrl: updated.targetUrl ?? '',
				active: updated.active,
			});
		} catch (err) {
			// A rejected target URL is pinned to the field; anything else keeps its
			// toast (from the hook) and the draft so the user can correct it.
			const fieldError = targetUrlServerError(err);
			if (fieldError) setTargetUrlError(fieldError);
		}
	}

	return (
		<form
			className="space-y-4"
			onSubmit={(e) => {
				e.preventDefault();
				void save();
			}}
		>
			<div className="space-y-1.5">
				<Label htmlFor="wh-detail-name">Name</Label>
				<Input
					id="wh-detail-name"
					value={name}
					onChange={(e) => {
						setName(e.target.value);
						if (nameError) setNameError(null);
					}}
					error={nameError ?? undefined}
					maxLength={255}
				/>
			</div>

			<div className="space-y-1.5">
				<Label htmlFor="wh-detail-target">Target URL</Label>
				<Input
					id="wh-detail-target"
					value={targetUrl}
					onChange={(e) => {
						setTargetUrl(e.target.value);
						if (targetUrlError) setTargetUrlError(null);
					}}
					placeholder="https://example.com/hooks/jentic"
					error={targetUrlError ?? undefined}
					className="font-mono"
				/>
				<Disclosure summary="Which URLs are allowed?">
					The URL must be http(s). It&apos;s re-validated at send time by the egress
					guard, so an internal or private address is refused then even if it looks fine
					now.
				</Disclosure>
			</div>

			<div className="border-border bg-muted/30 flex items-start gap-3 rounded-lg border p-3">
				<Checkbox checked={active} onChange={setActive} ariaLabel="Endpoint active">
					<span className="text-foreground font-medium">Active</span>
					<span className="text-muted-foreground mt-0.5 block text-xs leading-relaxed">
						When off, the endpoint is paused: matching events are not fanned out to it
						until you re-enable it.
					</span>
				</Checkbox>
			</div>

			{dirty && (
				<div className="flex justify-end gap-2">
					<Button
						type="button"
						variant="secondary"
						size="sm"
						onClick={reset}
						disabled={update.isPending}
					>
						Reset
					</Button>
					<Button type="submit" variant="primary" size="sm" loading={update.isPending}>
						Save changes
					</Button>
				</div>
			)}
		</form>
	);
}

/** The Settings tab — config, inline event editor, secret rotation, advanced. */
function SettingsTab({
	endpoint,
	canWrite,
	onRotate,
	onDelete,
}: {
	endpoint: WebhookEndpointEntity;
	canWrite: boolean;
	onRotate: (e: WebhookEndpointEntity) => void;
	onDelete: (e: WebhookEndpointEntity) => void;
}) {
	return (
		<div className="space-y-4">
			<DetailSection title="Configuration" icon={<Settings className="h-4 w-4" />}>
				{canWrite ? (
					<ConfigurationEditor endpoint={endpoint} />
				) : (
					<dl className="space-y-3 text-sm">
						<div>
							<dt className="text-muted-foreground text-xs tracking-wider uppercase">
								Target URL
							</dt>
							<dd className="text-foreground mt-0.5 font-mono break-words">
								{endpoint.targetUrl ?? '—'}
							</dd>
						</div>
						<div>
							<dt className="text-muted-foreground text-xs tracking-wider uppercase">
								Status
							</dt>
							<dd className="mt-1">
								{endpoint.active ? (
									<Badge variant="success" dot>
										active
									</Badge>
								) : (
									<Badge variant="danger" dot>
										paused
									</Badge>
								)}
							</dd>
						</div>
					</dl>
				)}
			</DetailSection>

			<DetailSection title="Subscribed events" icon={<ListChecks className="h-4 w-4" />}>
				{canWrite ? (
					<SubscribedEventsEditor endpoint={endpoint} />
				) : (
					<dd className="flex flex-wrap gap-1.5">
						{endpoint.eventTypes.length > 0 ? (
							endpoint.eventTypes.map((t) => (
								<Badge key={t} variant="default">
									{t}
								</Badge>
							))
						) : (
							<span className="text-muted-foreground text-sm">
								All relayable types
							</span>
						)}
					</dd>
				)}
			</DetailSection>

			{canWrite && (
				<DetailSection title="Signing secret" icon={<ShieldCheck className="h-4 w-4" />}>
					<div className="flex flex-wrap items-center justify-between gap-3">
						<p className="text-muted-foreground max-w-prose text-xs leading-relaxed">
							Rotating issues a new secret and keeps the previous one valid for a
							grace window, so both sides update without dropping events. Shown once.
						</p>
						<Button
							variant="secondary"
							size="sm"
							onClick={() => onRotate(endpoint)}
							className="shrink-0"
						>
							Rotate secret
						</Button>
					</div>
				</DetailSection>
			)}

			{canWrite && (
				<DetailSection title="Advanced" icon={<Settings className="h-4 w-4" />}>
					<Disclosure summary="IP / CIDR allowlist">
						<div className="mt-1">
							<AllowlistEditor endpoint={endpoint} />
						</div>
					</Disclosure>
				</DetailSection>
			)}

			{canWrite && (
				<DangerZone
					actions={[
						{
							key: 'delete',
							title: 'Delete this endpoint',
							description:
								'Stops all delivery and removes the endpoint and its delivery history. This cannot be undone.',
							buttonLabel: 'Delete endpoint',
							ariaLabel: `Delete ${endpoint.name}`,
							emphasis: 'solid',
						},
					]}
					onAction={() => onDelete(endpoint)}
				/>
			)}
		</div>
	);
}

export interface WebhookEndpointDetailBodyProps {
	endpointId: string;
	canWrite: boolean;
	/** Host "Back" (not-found, post-delete). Page → navigate to the list. */
	onRequestClose: () => void;
	onRotate: (endpoint: WebhookEndpointEntity) => void;
	onDelete: (endpoint: WebhookEndpointEntity) => void;
}

export function WebhookEndpointDetailBody({
	endpointId,
	canWrite,
	onRequestClose,
	onRotate,
	onDelete,
}: WebhookEndpointDetailBodyProps) {
	const { data: endpoint, isLoading } = useWebhookEndpoint(endpointId);

	const [searchParams, setSearchParams] = useSearchParams();
	const tabParam = searchParams.get('tab');
	const activeTab: DetailTab = isDetailTab(tabParam) ? tabParam : 'overview';

	function setTab(tab: string) {
		// Pushed (not replaced) so the browser back button walks tabs — same
		// contract as the toolkit/agent consoles; the back link on the page is
		// static for exactly that reason.
		setSearchParams(
			(prev) => {
				const next = new URLSearchParams(prev);
				if (tab === 'overview') next.delete('tab');
				else next.set('tab', tab);
				return next;
			},
			{ replace: false },
		);
	}

	if (isLoading) {
		return <LoadingState message="Loading webhook endpoint…" />;
	}

	if (!endpoint) {
		return (
			<EmptyState
				icon={<SearchX className="h-6 w-6" />}
				title="Endpoint not found"
				description="It may have been deleted, or the link is stale."
				action={
					<Button variant="secondary" onClick={onRequestClose}>
						All webhooks
					</Button>
				}
			/>
		);
	}

	return (
		<div className="space-y-6">
			<TabNav<DetailTab>
				options={TAB_OPTIONS}
				value={activeTab}
				onChange={setTab}
				ariaLabel="Endpoint detail sections"
				getTabId={tabId}
				getControls={panelId}
			/>

			<div
				role="tabpanel"
				id={panelId(activeTab)}
				aria-labelledby={tabId(activeTab)}
				tabIndex={0}
				className="focus-visible:outline-none"
			>
				{activeTab === 'overview' && <OverviewTab endpointId={endpoint.id} />}
				{activeTab === 'deliveries' && (
					<DeliveryLogPanel endpointId={endpoint.id} canWrite={canWrite} />
				)}
				{activeTab === 'settings' && (
					<SettingsTab
						endpoint={endpoint}
						canWrite={canWrite}
						onRotate={onRotate}
						onDelete={onDelete}
					/>
				)}
			</div>
		</div>
	);
}
