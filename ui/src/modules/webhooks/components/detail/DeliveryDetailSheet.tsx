import { CheckCircle2, Clock3, RotateCcw, X, XCircle } from 'lucide-react';
import { Button, CopyButton, SheetPrimitive, StatusBadge } from '@/shared/ui';
import { useRedeliverDelivery, type WebhookDeliveryEntity } from '@/modules/webhooks/api';
import { DeliveryStatusBadge } from '@/modules/webhooks/components/badges';
import { formatDateTime, prettyJson, timeUntil } from '@/modules/webhooks/lib/format';

/**
 * Delivery inspector — attempt timeline, signed request headers, payload and
 * response, plus the redeliver action (the single most support-ticket-saving
 * affordance per the UX research — kept prominent).
 */
export function DeliveryDetailSheet({
	endpointId,
	delivery,
	onClose,
}: {
	endpointId: string;
	delivery: WebhookDeliveryEntity | null;
	onClose: () => void;
}) {
	const redeliver = useRedeliverDelivery(endpointId);

	return (
		<SheetPrimitive open={delivery != null} onClose={onClose} ariaLabel="Delivery details">
			{delivery && (
				<div className="flex h-full flex-col">
					<div className="border-border flex items-start justify-between gap-3 border-b px-5 py-4">
						<div className="min-w-0">
							<div className="flex flex-wrap items-center gap-2">
								<h2 className="text-foreground truncate font-mono text-sm font-semibold">
									{delivery.eventType}
								</h2>
								<DeliveryStatusBadge status={delivery.status} />
							</div>
							<p className="text-muted-foreground mt-0.5 font-mono text-xs">
								{delivery.id}
								{delivery.isTest ? ' · test event' : ''}
								{delivery.isRedelivery ? ' · redelivery' : ''}
							</p>
						</div>
						<Button variant="ghost" size="icon" onClick={onClose} aria-label="Close">
							<X className="h-5 w-5" />
						</Button>
					</div>

					<div className="flex-1 space-y-5 overflow-y-auto px-5 py-4">
						<section className="space-y-2">
							<h3 className="font-heading text-foreground text-sm font-semibold">
								Attempts
							</h3>
							<ol className="space-y-2">
								{delivery.attempts.map((attempt, i) => (
									<li
										key={attempt.attempted_at + String(i)}
										className="border-border/60 bg-muted/30 flex flex-wrap items-center gap-2 rounded-lg border p-2.5"
									>
										{attempt.ok ? (
											<CheckCircle2
												className="text-success h-4 w-4 shrink-0"
												aria-hidden="true"
											/>
										) : (
											<XCircle
												className="text-danger h-4 w-4 shrink-0"
												aria-hidden="true"
											/>
										)}
										<span className="text-foreground text-xs font-medium">
											Attempt {i + 1}
										</span>
										<StatusBadge status={attempt.http_status} />
										<span className="text-muted-foreground font-mono text-xs tabular-nums">
											{attempt.latency_ms} ms
										</span>
										<span className="text-muted-foreground ml-auto text-xs">
											{formatDateTime(attempt.attempted_at)}
										</span>
										{attempt.error && (
											<p className="text-danger w-full text-xs">
												{attempt.error}
											</p>
										)}
									</li>
								))}
								{delivery.status === 'pending' && (
									<li className="border-border/60 text-muted-foreground flex items-center gap-2 rounded-lg border border-dashed p-2.5 text-xs">
										<Clock3 className="h-4 w-4 shrink-0" aria-hidden="true" />
										Next retry {timeUntil(delivery.nextAttemptAt)}
									</li>
								)}
							</ol>
						</section>

						<section className="space-y-2">
							<h3 className="font-heading text-foreground text-sm font-semibold">
								Request headers
							</h3>
							<dl className="border-border/60 bg-muted/30 space-y-1 rounded-lg border p-3">
								{Object.entries(delivery.requestHeaders).map(([k, v]) => (
									<div
										key={k}
										className="flex flex-wrap gap-x-2 font-mono text-xs"
									>
										<dt className="text-muted-foreground">{k}:</dt>
										<dd className="text-foreground min-w-0 break-all">{v}</dd>
									</div>
								))}
							</dl>
						</section>

						<section className="space-y-2">
							<div className="flex items-center justify-between gap-2">
								<h3 className="font-heading text-foreground text-sm font-semibold">
									Payload
								</h3>
								<CopyButton
									value={prettyJson(delivery.payload)}
									size="sm"
									variant="ghost"
									ariaLabel="Copy payload"
								/>
							</div>
							<pre className="border-border/60 bg-muted/30 max-h-64 overflow-auto rounded-lg border p-3 font-mono text-xs leading-relaxed">
								{prettyJson(delivery.payload)}
							</pre>
						</section>

						<section className="space-y-2">
							<h3 className="font-heading text-foreground text-sm font-semibold">
								Response
							</h3>
							<pre className="border-border/60 bg-muted/30 max-h-40 overflow-auto rounded-lg border p-3 font-mono text-xs leading-relaxed">
								{delivery.responseBody ?? 'No response body recorded.'}
							</pre>
						</section>
					</div>

					<div className="border-border flex items-center justify-between gap-2 border-t px-5 py-4">
						<p className="text-muted-foreground text-xs">
							Redelivery re-sends the original payload with a fresh timestamp and
							signature.
						</p>
						<Button
							variant={delivery.status === 'delivered' ? 'secondary' : 'primary'}
							onClick={() => redeliver.mutate(delivery.id)}
							loading={redeliver.isPending}
						>
							<RotateCcw className="h-4 w-4" /> Redeliver
						</Button>
					</div>
				</div>
			)}
		</SheetPrimitive>
	);
}
