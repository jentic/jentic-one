import { useState } from 'react';
import { Activity, CheckCircle2, FlaskConical, Gauge, Link2, XCircle } from 'lucide-react';
import { Badge, Button, DetailSection, Select, StatCard } from '@/shared/ui';
import {
	useEventTypeCatalog,
	useSendTestEvent,
	type WebhookEndpointEntity,
} from '@/modules/webhooks/api';
import { formatDateTime, formatRate, timeAgo } from '@/modules/webhooks/lib/format';

/** Overview tab — KPI strip, configuration summary, and the test-event bench. */
export function OverviewTab({ endpoint }: { endpoint: WebhookEndpointEntity }) {
	const { data: catalog = [] } = useEventTypeCatalog();
	const sendTest = useSendTestEvent(endpoint.id);
	const testableTypes = endpoint.subscribesToAll
		? catalog.map((e) => e.type)
		: endpoint.eventTypes;
	const [testType, setTestType] = useState('');
	const effectiveTestType = testType || testableTypes[0] || '';

	return (
		<div className="space-y-4">
			<div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
				<StatCard
					label="Deliveries (24h)"
					value={endpoint.deliveries24h}
					icon={<Activity className="h-4 w-4" />}
					accent="blue"
				/>
				<StatCard
					label="Success rate (24h)"
					value={formatRate(endpoint.successRate24h)}
					icon={<CheckCircle2 className="h-4 w-4" />}
					accent="green"
				/>
				<StatCard
					label="Consecutive failures"
					value={endpoint.consecutiveFailures}
					icon={<Gauge className="h-4 w-4" />}
					accent={endpoint.consecutiveFailures > 0 ? 'danger' : 'neutral'}
					valueClassName={endpoint.consecutiveFailures > 0 ? 'text-danger' : undefined}
				/>
				<StatCard
					label="Last delivery"
					value={timeAgo(endpoint.lastDeliveryAt)}
					icon={
						endpoint.lastDeliveryOk === false ? (
							<XCircle className="h-4 w-4" />
						) : (
							<CheckCircle2 className="h-4 w-4" />
						)
					}
					accent={endpoint.lastDeliveryOk === false ? 'danger' : 'neutral'}
				/>
			</div>

			<DetailSection title="Configuration" icon={<Link2 className="h-4 w-4" />}>
				<dl className="grid gap-x-6 gap-y-3 sm:grid-cols-2">
					<div>
						<dt className="text-muted-foreground text-xs tracking-wider uppercase">
							{endpoint.destinationType === 'slack'
								? 'Slack incoming webhook URL'
								: 'URL'}
						</dt>
						<dd className="text-foreground mt-0.5 font-mono text-xs break-all">
							{endpoint.url}
						</dd>
					</div>
					{endpoint.destinationType === 'slack' ? (
						<div>
							<dt className="text-muted-foreground text-xs tracking-wider uppercase">
								Message format
							</dt>
							<dd className="text-foreground mt-0.5 text-xs">
								Slack Block Kit — one formatted message per event.
								<span className="text-muted-foreground">
									{' '}
									Unsigned; the URL is the credential.
								</span>
							</dd>
						</div>
					) : (
						<div>
							<dt className="text-muted-foreground text-xs tracking-wider uppercase">
								Signing secret
							</dt>
							<dd className="text-foreground mt-0.5 font-mono text-xs">
								{endpoint.secretPreview}
								<span className="text-muted-foreground">
									{' '}
									— rotate it from Settings
								</span>
							</dd>
						</div>
					)}
					<div>
						<dt className="text-muted-foreground text-xs tracking-wider uppercase">
							Created
						</dt>
						<dd className="text-foreground mt-0.5 text-xs">
							{formatDateTime(endpoint.createdAt)}
						</dd>
					</div>
					<div>
						<dt className="text-muted-foreground text-xs tracking-wider uppercase">
							Updated
						</dt>
						<dd className="text-foreground mt-0.5 text-xs">
							{formatDateTime(endpoint.updatedAt)}
						</dd>
					</div>
					<div className="sm:col-span-2">
						<dt className="text-muted-foreground text-xs tracking-wider uppercase">
							Subscribed events
						</dt>
						<dd className="mt-1.5 flex flex-wrap gap-1.5">
							{endpoint.subscribesToAll ? (
								<Badge variant="default">All events</Badge>
							) : (
								endpoint.eventTypes.map((t) => (
									<Badge key={t} variant="default">
										{t}
									</Badge>
								))
							)}
						</dd>
					</div>
					{endpoint.description && (
						<div className="sm:col-span-2">
							<dt className="text-muted-foreground text-xs tracking-wider uppercase">
								Description
							</dt>
							<dd className="text-foreground mt-0.5 text-sm">
								{endpoint.description}
							</dd>
						</div>
					)}
				</dl>
			</DetailSection>

			<DetailSection title="Send a test event" icon={<FlaskConical className="h-4 w-4" />}>
				<p className="text-muted-foreground text-xs">
					{endpoint.destinationType === 'slack'
						? 'Posts a synthetic message of the chosen event type to the Slack channel, formatted like a real notification. The result lands in the Deliveries tab flagged as a test.'
						: 'Sends a synthetic payload of the chosen type to the endpoint, signed like a real delivery. The result lands in the Deliveries tab flagged as a test.'}
				</p>
				<div className="flex flex-wrap items-center gap-2">
					<Select
						value={effectiveTestType}
						onChange={(e) => setTestType(e.target.value)}
						aria-label="Test event type"
						className="max-w-xs"
						disabled={testableTypes.length === 0}
					>
						{testableTypes.map((t) => (
							<option key={t} value={t}>
								{t}
							</option>
						))}
					</Select>
					<Button
						variant="outline"
						onClick={() => sendTest.mutate(effectiveTestType)}
						loading={sendTest.isPending}
						disabled={!effectiveTestType || endpoint.wireStatus !== 'active'}
					>
						<FlaskConical className="h-4 w-4" /> Send test event
					</Button>
					{endpoint.wireStatus !== 'active' && (
						<span className="text-muted-foreground text-xs">
							Resume deliveries to send test events.
						</span>
					)}
				</div>
			</DetailSection>
		</div>
	);
}
