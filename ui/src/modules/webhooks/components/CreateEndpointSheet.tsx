import { useEffect, useState } from 'react';
import { CheckCircle2, Hash, Webhook, X, XCircle } from 'lucide-react';
import { Button, ErrorAlert, Input, Label, SheetPrimitive, Textarea } from '@/shared/ui';
import {
	useCreateEndpoint,
	useEventTypeCatalog,
	type DestinationType,
	type PingResult,
} from '@/modules/webhooks/api';
import { EventTypePicker } from '@/modules/webhooks/components/EventTypePicker';
import { SecretReveal } from '@/modules/webhooks/components/SecretReveal';
import { SlackMessagePreview } from '@/modules/webhooks/components/SlackMessagePreview';

/**
 * "New destination" slide-over (flow F1): destination type first (Stripe's
 * event-destination pattern), then a form that adapts — signed HTTPS webhooks
 * for machines, Slack incoming webhooks for humans. On success the panel shows
 * the one-time signing secret (https only) and the automatic ping result, so
 * reachability feedback is immediate. Draft persists across dismissals; the
 * secret never does.
 */
const DESTINATION_OPTIONS: Array<{
	type: DestinationType;
	label: string;
	description: string;
	Icon: typeof Webhook;
}> = [
	{
		type: 'https',
		label: 'HTTPS endpoint',
		description: 'Signed JSON POSTs to your own service. Verify the signature, get retries.',
		Icon: Webhook,
	},
	{
		type: 'slack',
		label: 'Slack channel',
		description: 'Readable messages in a channel, via a Slack incoming webhook URL.',
		Icon: Hash,
	},
];

export function CreateEndpointSheet({ open, onClose }: { open: boolean; onClose: () => void }) {
	const { data: catalog = [] } = useEventTypeCatalog();
	const create = useCreateEndpoint();

	const [destination, setDestination] = useState<DestinationType>('https');
	const [name, setName] = useState('');
	const [url, setUrl] = useState('');
	const [description, setDescription] = useState('');
	const [eventTypes, setEventTypes] = useState<string[]>([]);
	const [formError, setFormError] = useState<string | null>(null);
	// Post-create result state — sensitive, wiped on every close.
	const [secret, setSecret] = useState<string | null>(null);
	const [created, setCreated] = useState(false);
	const [ping, setPing] = useState<PingResult | null>(null);

	// Transient flags are not user input — clear on every (re)open.
	useEffect(() => {
		if (!open) return;
		setFormError(null);
	}, [open]);

	// Sensitive-data exception: wipe the one-time secret whenever the sheet closes.
	useEffect(() => {
		if (!open) {
			setSecret(null);
			setCreated(false);
			setPing(null);
		}
	}, [open]);

	const isSlack = destination === 'slack';

	const validate = (): string | null => {
		if (!name.trim()) return 'Give the destination a name.';
		if (isSlack && !url.trim().startsWith('https://hooks.slack.com/')) {
			return 'Paste a Slack incoming webhook URL (it starts with https://hooks.slack.com/).';
		}
		if (!url.trim().startsWith('https://')) return 'The endpoint URL must start with https://.';
		if (eventTypes.length === 0) return 'Subscribe to at least one event type.';
		return null;
	};

	const submit = () => {
		const problem = validate();
		setFormError(problem);
		if (problem) return;
		create.mutate(
			{
				name: name.trim(),
				url: url.trim(),
				description: description.trim() || null,
				destination_type: destination,
				event_types: eventTypes,
			},
			{
				onSuccess: (result) => {
					// Reset the draft only on the success path.
					setName('');
					setUrl('');
					setDescription('');
					setEventTypes([]);
					setSecret(result.secret);
					setCreated(true);
					setPing(result.ping);
				},
			},
		);
	};

	const previewEventType = eventTypes.find((t) => t !== '*');

	return (
		<SheetPrimitive open={open} onClose={onClose} ariaLabel="New webhook destination">
			<div className="flex h-full flex-col">
				<div className="border-border flex items-center justify-between border-b px-5 py-4">
					<div className="flex items-center gap-2">
						<Webhook className="text-primary h-4 w-4" aria-hidden="true" />
						<h2 className="text-foreground text-base font-semibold">New destination</h2>
					</div>
					<Button variant="ghost" size="icon" onClick={onClose} aria-label="Close">
						<X className="h-5 w-5" />
					</Button>
				</div>

				<div className="flex-1 space-y-5 overflow-y-auto px-5 py-4">
					{created ? (
						<div className="space-y-4">
							{secret ? (
								<SecretReveal secret={secret} onConfirm={onClose} />
							) : (
								<div className="border-success/40 bg-success/5 rounded-lg border p-3">
									<p className="text-foreground flex items-center gap-2 text-sm font-medium">
										<CheckCircle2
											className="text-success h-4 w-4"
											aria-hidden="true"
										/>
										Slack destination created
									</p>
									<p className="text-muted-foreground mt-1 text-xs">
										No signing secret is involved — Slack incoming webhooks are
										unsigned, the URL itself is the credential. Keep it private.
									</p>
								</div>
							)}
							{ping && (
								<div
									className={
										ping.ok
											? 'border-success/40 bg-success/5 rounded-lg border p-3'
											: 'border-danger/40 bg-danger/5 rounded-lg border p-3'
									}
								>
									<p className="text-foreground flex items-center gap-2 text-sm font-medium">
										{ping.ok ? (
											<CheckCircle2
												className="text-success h-4 w-4"
												aria-hidden="true"
											/>
										) : (
											<XCircle
												className="text-danger h-4 w-4"
												aria-hidden="true"
											/>
										)}
										{ping.ok
											? secret
												? `Ping delivered — ${ping.http_status} in ${ping.latency_ms} ms`
												: `Test message posted — ${ping.http_status} in ${ping.latency_ms} ms`
											: secret
												? 'Ping failed'
												: 'Test message failed'}
									</p>
									{!ping.ok && (
										<p className="text-muted-foreground mt-1 text-xs">
											{ping.error ?? 'The destination did not answer.'}{' '}
											Deliveries will retry automatically; check the
											Deliveries tab.
										</p>
									)}
								</div>
							)}
							{!secret && (
								<div className="flex justify-end">
									<Button onClick={onClose}>Done</Button>
								</div>
							)}
						</div>
					) : (
						<>
							<div className="space-y-1.5">
								<Label>Destination type</Label>
								<div
									className="grid grid-cols-1 gap-2 sm:grid-cols-2"
									role="radiogroup"
									aria-label="Destination type"
								>
									{DESTINATION_OPTIONS.map(
										({ type, label, description: desc, Icon }) => {
											const selected = destination === type;
											return (
												<button
													key={type}
													type="button"
													role="radio"
													aria-checked={selected}
													onClick={() => setDestination(type)}
													className={`rounded-lg border p-3 text-left transition-colors ${
														selected
															? 'border-primary bg-primary/5'
															: 'border-border hover:border-primary/40'
													}`}
												>
													<span className="text-foreground flex items-center gap-2 text-sm font-medium">
														<Icon
															className={`h-4 w-4 ${selected ? 'text-primary' : 'text-muted-foreground'}`}
															aria-hidden="true"
														/>
														{label}
													</span>
													<span className="text-muted-foreground mt-1 block text-xs">
														{desc}
													</span>
												</button>
											);
										},
									)}
								</div>
							</div>
							<div className="space-y-1.5">
								<Label htmlFor="webhook-name">Name</Label>
								<Input
									id="webhook-name"
									value={name}
									onChange={(e) => setName(e.target.value)}
									placeholder={isSlack ? '#jentic-ops channel' : 'SIEM export'}
									autoFocus
								/>
							</div>
							<div className="space-y-1.5">
								<Label htmlFor="webhook-url">
									{isSlack ? 'Slack incoming webhook URL' : 'Endpoint URL'}
								</Label>
								<Input
									id="webhook-url"
									value={url}
									onChange={(e) => setUrl(e.target.value)}
									placeholder={
										isSlack
											? 'https://hooks.slack.com/services/T…/B…/…'
											: 'https://example.com/hooks/jentic'
									}
									inputMode="url"
								/>
								<p className="text-muted-foreground text-xs">
									{isSlack
										? 'In Slack: create an app → Incoming Webhooks → pick the channel, then paste the URL here. Events arrive as formatted messages — no code needed.'
										: 'HTTPS only. Each delivery is signed (Standard Webhooks headers) so the receiver can verify it came from this instance.'}
								</p>
							</div>
							<div className="space-y-1.5">
								<Label htmlFor="webhook-description">Description (optional)</Label>
								<Textarea
									id="webhook-description"
									value={description}
									onChange={(e) => setDescription(e.target.value)}
									rows={2}
									placeholder={
										isSlack
											? 'Who watches this channel?'
											: 'What consumes these events?'
									}
								/>
							</div>
							<div className="space-y-1.5">
								<Label>Events to send</Label>
								<EventTypePicker
									catalog={catalog}
									value={eventTypes}
									onChange={setEventTypes}
								/>
							</div>
							{isSlack && (
								<div className="space-y-1.5">
									<Label>Message preview</Label>
									<SlackMessagePreview eventType={previewEventType} />
								</div>
							)}
							{formError && <ErrorAlert message={formError} />}
						</>
					)}
				</div>

				{!created && (
					<div className="border-border flex justify-end gap-2 border-t px-5 py-4">
						<Button variant="secondary" onClick={onClose}>
							Cancel
						</Button>
						<Button onClick={submit} loading={create.isPending}>
							{isSlack ? 'Connect Slack channel' : 'Create endpoint'}
						</Button>
					</div>
				)}
			</div>
		</SheetPrimitive>
	);
}
