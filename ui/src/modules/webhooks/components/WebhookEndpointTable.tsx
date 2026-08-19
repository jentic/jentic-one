/**
 * WebhookEndpointTable — the endpoint list, one row per configured endpoint.
 *
 * Rows expand to reveal that endpoint's delivery log rather than navigating to a
 * detail route: an operator's loop here is "send a test, watch what happens,
 * fix, resend", and losing list context on every check would make that worse.
 *
 * Every endpoint is an outbound **notification**: it shows the target URL we
 * POST to and can accept a test event.
 */
import { useState } from 'react';
import { ChevronDown, ChevronRight, Webhook } from 'lucide-react';
import { Badge, Button, EmptyState } from '@/shared/ui';
import { useSendTestEvent } from '@/modules/webhooks/api';
import type { WebhookEndpointEntity } from '@/modules/webhooks/api';
import { DeliveryLogPanel } from '@/modules/webhooks/components/DeliveryLogPanel';

interface WebhookEndpointTableProps {
	endpoints: WebhookEndpointEntity[];
	canWrite: boolean;
	onRotate: (endpoint: WebhookEndpointEntity) => void;
	onDelete: (endpoint: WebhookEndpointEntity) => void;
	onCreate: () => void;
}

export function WebhookEndpointTable({
	endpoints,
	canWrite,
	onRotate,
	onDelete,
	onCreate,
}: WebhookEndpointTableProps) {
	const [expandedId, setExpandedId] = useState<string | null>(null);
	const sendTest = useSendTestEvent();

	if (endpoints.length === 0) {
		return (
			<EmptyState
				icon={<Webhook className="h-6 w-6" />}
				title="No webhook endpoints yet"
				description="Create a notification endpoint to push platform events out to an external URL."
				action={
					canWrite ? (
						<Button variant="primary" onClick={onCreate}>
							New endpoint
						</Button>
					) : undefined
				}
			/>
		);
	}

	return (
		<div className="space-y-3">
			{endpoints.map((endpoint) => {
				const expanded = expandedId === endpoint.id;
				return (
					<div
						key={endpoint.id}
						className="border-border bg-card overflow-hidden rounded-xl border"
					>
						<div className="flex flex-wrap items-start justify-between gap-3 p-4">
							<div className="min-w-0 flex-1">
								<div className="flex flex-wrap items-center gap-2">
									<button
										type="button"
										onClick={() => setExpandedId(expanded ? null : endpoint.id)}
										className="text-foreground hover:text-primary flex items-center gap-1.5 font-medium"
										aria-expanded={expanded}
										aria-label={`${expanded ? 'Hide' : 'Show'} delivery log for ${endpoint.name}`}
									>
										{expanded ? (
											<ChevronDown className="h-4 w-4" />
										) : (
											<ChevronRight className="h-4 w-4" />
										)}
										{endpoint.name}
									</button>
									<Badge variant="success">outbound notification</Badge>
									{!endpoint.active && <Badge variant="danger">disabled</Badge>}
								</div>

								<dl className="mt-2 space-y-1.5 text-xs">
									<div>
										<dt className="text-muted-foreground tracking-wider uppercase">
											Target URL
										</dt>
										<dd className="text-foreground mt-0.5 font-mono break-all">
											{endpoint.targetUrl}
										</dd>
									</div>

									<div>
										<dt className="text-muted-foreground tracking-wider uppercase">
											Event types
										</dt>
										<dd className="text-foreground mt-0.5 font-mono">
											{endpoint.eventTypes.length > 0
												? endpoint.eventTypes.join(', ')
												: 'all relayable types'}
										</dd>
									</div>
								</dl>
							</div>

							{canWrite && (
								<div className="flex shrink-0 flex-wrap gap-2">
									<Button
										variant="secondary"
										size="sm"
										onClick={() => {
											sendTest.mutate(endpoint.id);
											setExpandedId(endpoint.id);
										}}
										loading={sendTest.isPending}
									>
										Send test
									</Button>
									<Button
										variant="secondary"
										size="sm"
										onClick={() => onRotate(endpoint)}
									>
										Rotate secret
									</Button>
									<Button
										variant="danger"
										size="sm"
										onClick={() => onDelete(endpoint)}
									>
										Delete
									</Button>
								</div>
							)}
						</div>

						{expanded && (
							<div className="border-border bg-muted/30 border-t p-4">
								<h3 className="font-heading text-foreground mb-3 text-sm font-semibold">
									Delivery log
								</h3>
								<DeliveryLogPanel endpointId={endpoint.id} canWrite={canWrite} />
							</div>
						)}
					</div>
				);
			})}
		</div>
	);
}
