import { Hash, Webhook } from 'lucide-react';
import { Badge, type BadgeVariant } from '@/shared/ui';
import type {
	DeliveryStatus,
	DestinationType,
	EndpointDisplayStatus,
	WebhookEndpointEntity,
} from '@/modules/webhooks/api';

/**
 * Webhook status vocabulary — deliberately module-local. Endpoint status
 * (`active | failing | paused | disabled`) and delivery status
 * (`delivered | pending | failed | exhausted`) are NOT the shared
 * `ActorStatus` union, so they don't reuse `ActorStatusBadge`.
 */

const ENDPOINT_LABEL: Record<EndpointDisplayStatus, string> = {
	active: 'Active',
	failing: 'Failing',
	paused: 'Paused',
	disabled: 'Disabled',
};

const ENDPOINT_VARIANT: Record<EndpointDisplayStatus, BadgeVariant> = {
	active: 'success',
	failing: 'danger',
	paused: 'warning',
	disabled: 'default',
};

export function EndpointStatusBadge({ status }: { status: EndpointDisplayStatus }) {
	return (
		<Badge variant={ENDPOINT_VARIANT[status]} dot>
			{ENDPOINT_LABEL[status]}
		</Badge>
	);
}

const DELIVERY_LABEL: Record<DeliveryStatus, string> = {
	delivered: 'Delivered',
	pending: 'Pending',
	failed: 'Failed',
	exhausted: 'Exhausted',
};

const DELIVERY_VARIANT: Record<DeliveryStatus, BadgeVariant> = {
	delivered: 'success',
	pending: 'pending',
	failed: 'danger',
	exhausted: 'danger',
};

export function DeliveryStatusBadge({ status }: { status: DeliveryStatus }) {
	return <Badge variant={DELIVERY_VARIANT[status]}>{DELIVERY_LABEL[status]}</Badge>;
}

const DESTINATION_META: Record<
	DestinationType,
	{ label: string; Icon: typeof Webhook; title: string }
> = {
	https: {
		label: 'HTTPS',
		Icon: Webhook,
		title: 'Signed HTTPS webhook — for machines that verify and retry.',
	},
	slack: {
		label: 'Slack',
		Icon: Hash,
		title: 'Slack incoming webhook — events rendered as channel messages.',
	},
};

/** Small icon+label chip identifying where an endpoint delivers to. */
export function DestinationBadge({ type }: { type: DestinationType }) {
	const { label, Icon, title } = DESTINATION_META[type];
	return (
		<span
			className="text-muted-foreground inline-flex items-center gap-1 text-xs"
			title={title}
		>
			<Icon className="h-3.5 w-3.5" aria-hidden="true" />
			{label}
		</span>
	);
}

/** Chip summary of an endpoint's subscription: "execution.completed +2". */
export function EventTypeChips({ endpoint }: { endpoint: WebhookEndpointEntity }) {
	if (endpoint.subscribesToAll) {
		return <Badge variant="default">All events</Badge>;
	}
	const [first, ...rest] = endpoint.eventTypes;
	if (!first) return <span className="text-muted-foreground text-xs">—</span>;
	return (
		<span className="inline-flex items-center gap-1.5" title={endpoint.eventTypes.join(', ')}>
			<Badge variant="default">{first}</Badge>
			{rest.length > 0 && (
				<span className="text-muted-foreground font-mono text-xs">+{rest.length}</span>
			)}
		</span>
	);
}
