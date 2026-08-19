/**
 * WebhookEndpointCreateSheet — slide-over form to create an endpoint.
 *
 * A Sheet (not a Dialog) keeps the endpoint list visible while filling the form.
 * The draft survives a dismissal and resets only on a successful create, per the
 * project's dialog-state rule.
 *
 * This build ships **outbound notifications only**: Jentic One POSTs signed
 * platform events to a URL you own. `event_types` is the subscription filter
 * (empty = every relayable type), chosen through the {@link EventTypePicker}
 * rather than free text so a user can only pick types the platform emits.
 */
import { useEffect, useRef, useState } from 'react';
import { ArrowRight, BookOpen } from 'lucide-react';
import { Button, Input, Label, SheetPrimitive } from '@/shared/ui';
import { useCreateWebhookEndpoint } from '@/modules/webhooks/api';
import type { CreatedEndpoint } from '@/modules/webhooks/api';
import { EventTypePicker } from '@/modules/webhooks/components/EventTypePicker';

interface WebhookEndpointCreateSheetProps {
	open: boolean;
	onClose: () => void;
	/** Hands the one-time secret to the parent so it can be revealed. */
	onCreated: (created: CreatedEndpoint) => void;
	/** Opens the relay guide so a first-time user knows what to build. */
	onOpenRelayGuide: () => void;
}

export function WebhookEndpointCreateSheet({
	open,
	onClose,
	onCreated,
	onOpenRelayGuide,
}: WebhookEndpointCreateSheetProps) {
	const [name, setName] = useState('');
	const [targetUrl, setTargetUrl] = useState('');
	const [eventTypes, setEventTypes] = useState<string[]>([]);
	const [error, setError] = useState<string | null>(null);
	const nameRef = useRef<HTMLInputElement>(null);
	const create = useCreateWebhookEndpoint();

	// Transient flag, not user input — clear on every (re)open.
	useEffect(() => {
		if (open) setError(null);
	}, [open]);

	async function handleSubmit() {
		const trimmedName = name.trim();
		if (!trimmedName) {
			setError('A name is required.');
			return;
		}
		if (!targetUrl.trim()) {
			setError('A notification endpoint needs a target URL to POST to.');
			return;
		}

		try {
			const created = await create.mutateAsync({
				name: trimmedName,
				targetUrl: targetUrl.trim(),
				eventTypes,
			});
			setName('');
			setTargetUrl('');
			setEventTypes([]);
			setError(null);
			onClose();
			// Reveal last: the secret is unrecoverable, so the dialog must open only
			// once the create has actually succeeded.
			onCreated(created);
		} catch {
			// The hook surfaces a toast; keep the draft so the user can correct it.
		}
	}

	return (
		<SheetPrimitive
			open={open}
			onClose={onClose}
			side="right"
			ariaLabel="Create webhook endpoint"
			initialFocus={nameRef}
			className="flex flex-col"
		>
			<header className="border-border border-b p-5">
				<h2 className="font-heading text-foreground text-lg font-semibold">
					Create webhook endpoint
				</h2>
				<p className="text-muted-foreground mt-1 text-sm">
					Jentic One POSTs signed events to a URL you own. You&apos;ll get a signing
					secret once, at the end.
				</p>
			</header>

			<div className="flex-1 space-y-5 overflow-y-auto p-5">
				<div className="border-primary/25 bg-primary/5 space-y-2 rounded-lg border p-3">
					<p className="text-foreground text-sm leading-relaxed">
						An outbound webhook lets Jentic One tell an external system the moment
						something happens — a credential expired, an execution failed — by POSTing a
						signed event to a URL you own, instead of that system polling for changes.
					</p>
					<p className="text-muted-foreground text-xs leading-relaxed">
						Jentic sends a fixed, signed payload; it doesn&apos;t speak Slack or
						PagerDuty directly. You&apos;ll usually point this at a small{' '}
						<strong>relay</strong> that verifies the signature and forwards to the real
						destination.
					</p>
					<Button
						type="button"
						variant="ghost"
						size="sm"
						onClick={onOpenRelayGuide}
						className="text-accent-teal -ml-2"
					>
						<BookOpen className="h-4 w-4" />
						Read the relay guide
						<ArrowRight className="h-3.5 w-3.5" />
					</Button>
				</div>

				<div className="space-y-1.5">
					<Label htmlFor="wh-name">Name</Label>
					<Input
						ref={nameRef}
						id="wh-name"
						value={name}
						onChange={(e) => setName(e.target.value)}
						placeholder="e.g. incident-relay"
						maxLength={255}
					/>
				</div>

				<div className="space-y-1.5">
					<Label htmlFor="wh-target">Target URL</Label>
					<Input
						id="wh-target"
						value={targetUrl}
						onChange={(e) => setTargetUrl(e.target.value)}
						placeholder="https://example.com/hooks/jentic"
					/>
					<p className="text-muted-foreground text-xs leading-relaxed">
						Must be http(s). The address is re-validated at send time by the egress
						guard, so an internal or private address is refused then even if it looks
						fine now.
					</p>
				</div>

				<div className="space-y-1.5">
					<Label id="wh-events-label">Event types</Label>
					<p className="text-muted-foreground text-xs leading-relaxed">
						Choose which platform events to receive. Each is a real type Jentic emits.
					</p>
					<EventTypePicker
						value={eventTypes}
						onChange={setEventTypes}
						labelId="wh-events-label"
					/>
				</div>

				{error && (
					<p className="text-danger text-sm" role="alert">
						{error}
					</p>
				)}
			</div>

			<footer className="border-border flex justify-end gap-2 border-t p-5">
				<Button variant="secondary" onClick={onClose}>
					Cancel
				</Button>
				<Button variant="primary" onClick={handleSubmit} loading={create.isPending}>
					Create endpoint
				</Button>
			</footer>
		</SheetPrimitive>
	);
}
