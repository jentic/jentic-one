/**
 * WebhookEndpointCreateSheet — slide-over form to create a new endpoint.
 *
 * A Sheet (not a Dialog) keeps the endpoint list visible while filling the form.
 * The draft survives a dismissal and resets only on a successful submit, per the
 * project's dialog-state rule.
 *
 * This is the **create** surface only. Editing an existing endpoint lives inline
 * in the endpoint detail page's Settings tab (matching the Agents/Toolkits
 * consoles), so there is no longer a drawer edit mode to duplicate what that tab
 * already shows.
 *
 * This build ships **outbound notifications only**: Jentic One POSTs signed
 * platform events to a URL you own. `event_types` is the subscription filter
 * (empty = every relayable type), chosen through the {@link EventTypePicker}
 * rather than free text so a user can only pick types the platform emits.
 */
import { useEffect, useRef, useState } from 'react';
import { ArrowRight, BookOpen } from 'lucide-react';
import { Button, Disclosure, Input, Label, SheetPrimitive } from '@/shared/ui';
import { useCreateWebhookEndpoint } from '@/modules/webhooks/api';
import type { CreatedEndpoint } from '@/modules/webhooks/api';
import { EventTypePicker } from '@/modules/webhooks/components/EventTypePicker';
import { CidrListField } from '@/modules/webhooks/components/CidrListField';
import { targetUrlServerError, validateTargetUrl } from '@/modules/webhooks/lib/targetUrl';

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
	const [allowedCidrs, setAllowedCidrs] = useState<string[]>([]);
	const [error, setError] = useState<string | null>(null);
	// Field-scoped error for the target URL — from client validation or, more
	// importantly, the server's rejection reason (surfaced at the field, not lost
	// in a toast).
	const [targetUrlError, setTargetUrlError] = useState<string | null>(null);
	const nameRef = useRef<HTMLInputElement>(null);
	const create = useCreateWebhookEndpoint();

	// Clear any leftover draft/error on (re)open so a previous attempt's values
	// don't bleed into a fresh "New endpoint".
	useEffect(() => {
		if (!open) return;
		setError(null);
		setTargetUrlError(null);
	}, [open]);

	function resetDraft() {
		setName('');
		setTargetUrl('');
		setEventTypes([]);
		setAllowedCidrs([]);
		setError(null);
		setTargetUrlError(null);
	}

	async function handleSubmit() {
		const trimmedName = name.trim();
		if (!trimmedName) {
			setError('A name is required.');
			return;
		}
		const urlError = validateTargetUrl(targetUrl);
		if (urlError) {
			setTargetUrlError(urlError);
			return;
		}
		setTargetUrlError(null);

		try {
			const created = await create.mutateAsync({
				name: trimmedName,
				targetUrl: targetUrl.trim(),
				eventTypes,
				allowedCidrs,
			});
			resetDraft();
			onClose();
			// Reveal last: the secret is unrecoverable, so the dialog must open only
			// once the create has actually succeeded.
			onCreated(created);
		} catch (err) {
			const fieldError = targetUrlServerError(err);
			if (fieldError) setTargetUrlError(fieldError);
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
						Jentic POSTs a fixed, signed event to your URL. You&apos;ll usually point
						this at a small <strong>relay</strong> that verifies the signature and
						forwards to the real destination.
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
						onChange={(e) => {
							setTargetUrl(e.target.value);
							if (targetUrlError) setTargetUrlError(null);
						}}
						placeholder="https://example.com/hooks/jentic"
						aria-invalid={targetUrlError ? true : undefined}
						aria-describedby={targetUrlError ? 'wh-target-error' : undefined}
					/>
					{targetUrlError && (
						<p id="wh-target-error" className="text-danger text-sm" role="alert">
							{targetUrlError}
						</p>
					)}
					<Disclosure summary="Which URLs are allowed?">
						The URL must be http(s). It&apos;s re-validated at send time by the egress
						guard, so an internal or private address is refused then even if it looks
						fine now.
					</Disclosure>
				</div>

				<div className="space-y-1.5">
					<Label id="wh-events-label">Event types</Label>
					<EventTypePicker
						value={eventTypes}
						onChange={setEventTypes}
						labelId="wh-events-label"
					/>
				</div>

				<Disclosure summary="Advanced">
					<div className="mt-1 space-y-1.5">
						<Label id="wh-cidrs-label">IP / CIDR allowlist</Label>
						<CidrListField
							value={allowedCidrs}
							onChange={setAllowedCidrs}
							labelId="wh-cidrs-label"
						/>
					</div>
				</Disclosure>

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
