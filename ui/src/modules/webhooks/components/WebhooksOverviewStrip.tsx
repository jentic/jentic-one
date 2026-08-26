/**
 * WebhooksOverviewStrip — the at-a-glance health ribbon above the endpoint list.
 *
 * Answers the webhooks console's first question — "is it working?" — before the
 * operator scans a single row. Built from the shared {@link StatCard} grid (the
 * same tile grammar the Dashboard, the Agents KPI strip, and the endpoint detail
 * Overview use) so the list reads as one product.
 *
 * Data sourcing: there is no aggregate endpoint. Totals are rolled up
 * client-side from the per-endpoint `/stats` queries the list already fans out
 * (see {@link summariseEndpoints}). The strip therefore degrades gracefully —
 * the endpoint-count tile is always exact (it comes from the list itself), while
 * the delivery-derived tiles show real *partial* totals with an "N of M loaded"
 * caption until every endpoint's stats settle, and never fabricate a number.
 */
import { AlertTriangle, CheckCircle2, Clock, Webhook } from 'lucide-react';
import { StatCard } from '@/shared/ui';
import type { WebhooksSummary } from '@/modules/webhooks/lib/health';

interface WebhooksOverviewStripProps {
	summary: WebhooksSummary;
	/** True until at least one endpoint's stats have settled. */
	isLoading: boolean;
	/** Every stats query failed — delivery tiles degrade rather than lie. */
	isError: boolean;
}

export function WebhooksOverviewStrip({ summary, isLoading, isError }: WebhooksOverviewStripProps) {
	const {
		totalEndpoints,
		activeEndpoints,
		pausedEndpoints,
		recentTotal,
		recentFailed,
		recentSuccessRate,
		deadLettered,
		retrying,
		statsLoaded,
		partial,
	} = summary;

	// The endpoint tile never depends on stats, so it is exact even while the
	// delivery tiles are still loading or have failed.
	const endpointCaption =
		pausedEndpoints > 0
			? `${activeEndpoints} active · ${pausedEndpoints} paused`
			: totalEndpoints > 0
				? 'all active'
				: undefined;

	// A partial caption is only worth showing while more than one endpoint's
	// stats are still outstanding.
	const partialCaption =
		partial && totalEndpoints > 0 ? `${statsLoaded} of ${totalEndpoints} loaded` : undefined;

	const attention = deadLettered + retrying;
	const statsError = isError ? 'Unavailable' : null;

	// The success-rate tile is degraded when below 100%. Convey that with an
	// icon + text label (not colour alone), mirroring the per-row HealthPill.
	const rateDegraded = recentSuccessRate != null && recentSuccessRate < 100;
	const successRateValue =
		recentSuccessRate != null ? (
			<span className="inline-flex items-center gap-1.5">
				{rateDegraded && (
					<AlertTriangle className="text-danger h-4 w-4 shrink-0" aria-hidden="true" />
				)}
				<span>{`${recentSuccessRate}%`}</span>
			</span>
		) : (
			'—'
		);
	const successRateCaption =
		partialCaption ??
		(rateDegraded ? 'degraded' : recentTotal === 0 ? 'no deliveries' : undefined);

	return (
		<section
			aria-label="Webhooks health overview"
			data-testid="webhooks-overview-strip"
			className="grid grid-cols-2 gap-3 lg:grid-cols-4"
		>
			<StatCard
				label="Endpoints"
				value={totalEndpoints.toLocaleString()}
				caption={endpointCaption}
				icon={<Webhook className="h-4 w-4" />}
				accent="blue"
			/>
			<StatCard
				label="Deliveries · 24h"
				value={recentTotal.toLocaleString()}
				caption={
					partialCaption ?? (recentFailed > 0 ? `${recentFailed} failed` : undefined)
				}
				icon={<Clock className="h-4 w-4" />}
				accent={recentFailed > 0 ? 'danger' : 'green'}
				isLoading={isLoading}
				error={statsError}
			/>
			<StatCard
				label="Success rate · 24h"
				value={successRateValue}
				caption={successRateCaption}
				icon={<CheckCircle2 className="h-4 w-4" />}
				accent={rateDegraded ? 'orange' : 'green'}
				valueClassName={
					recentSuccessRate != null
						? rateDegraded
							? 'text-danger'
							: 'text-success'
						: undefined
				}
				isLoading={isLoading}
				error={statsError}
			/>
			<StatCard
				label="Needs attention"
				value={attention.toLocaleString()}
				caption={
					attention > 0
						? `${deadLettered} dead · ${retrying} retrying`
						: partial
							? partialCaption
							: 'all delivered'
				}
				icon={<AlertTriangle className="h-4 w-4" />}
				accent={attention > 0 ? 'danger' : 'green'}
				valueClassName={attention > 0 ? 'text-danger' : undefined}
				isLoading={isLoading}
				error={statsError}
			/>
		</section>
	);
}
