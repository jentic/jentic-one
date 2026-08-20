/**
 * WebhookEndpointCreateSheet — slide-over form to create *or* edit an endpoint.
 *
 * A Sheet (not a Dialog) keeps the endpoint list visible while filling the form.
 * The draft survives a dismissal and resets only on a successful submit, per the
 * project's dialog-state rule.
 *
 * One component serves both create and edit because the fields are identical
 * (name, target URL, event-type subscription) — the only differences are the
 * copy, whether an `active` toggle is offered, and which mutation runs. Passing
 * an `endpoint` puts it in edit mode: fields pre-fill from that endpoint and the
 * submit calls the update mutation. Editing never touches or reveals the signing
 * secret — that is the separate rotation flow.
 *
 * This build ships **outbound notifications only**: Jentic One POSTs signed
 * platform events to a URL you own. `event_types` is the subscription filter
 * (empty = every relayable type), chosen through the {@link EventTypePicker}
 * rather than free text so a user can only pick types the platform emits.
 */
import { useEffect, useRef, useState } from 'react';
import { ArrowRight, BookOpen } from 'lucide-react';
import { Button, Checkbox, Disclosure, Input, Label, SheetPrimitive } from '@/shared/ui';
import { useCreateWebhookEndpoint, useUpdateWebhookEndpoint } from '@/modules/webhooks/api';
import type { CreatedEndpoint, WebhookEndpointEntity } from '@/modules/webhooks/api';
import { EventTypePicker } from '@/modules/webhooks/components/EventTypePicker';

interface WebhookEndpointCreateSheetProps {
	open: boolean;
	onClose: () => void;
	/**
	 * The endpoint being edited. `null`/omitted means the sheet is in create mode.
	 */
	endpoint?: WebhookEndpointEntity | null;
	/** Hands the one-time secret to the parent so it can be revealed (create only). */
	onCreated: (created: CreatedEndpoint) => void;
	/** Fired after a successful edit so the parent can close and refresh. */
	onUpdated?: (endpoint: WebhookEndpointEntity) => void;
	/** Opens the relay guide so a first-time user knows what to build. */
	onOpenRelayGuide: () => void;
}

export function WebhookEndpointCreateSheet({
	open,
	onClose,
	endpoint,
	onCreated,
	onUpdated,
	onOpenRelayGuide,
}: WebhookEndpointCreateSheetProps) {
	const isEdit = Boolean(endpoint);
	const [name, setName] = useState('');
	const [targetUrl, setTargetUrl] = useState('');
	const [eventTypes, setEventTypes] = useState<string[]>([]);
	const [active, setActive] = useState(true);
	const [error, setError] = useState<string | null>(null);
	const nameRef = useRef<HTMLInputElement>(null);
	const create = useCreateWebhookEndpoint();
	const update = useUpdateWebhookEndpoint();
	const pending = isEdit ? update.isPending : create.isPending;

	// Seed the form on (re)open, and clear the transient error either way. In edit
	// mode we pre-fill from the endpoint; in create mode we clear any leftover
	// draft so a previous edit's values don't bleed into a fresh "New endpoint".
	// Keyed on the endpoint id so switching which endpoint is edited re-seeds.
	useEffect(() => {
		if (!open) return;
		setError(null);
		if (endpoint) {
			setName(endpoint.name);
			setTargetUrl(endpoint.targetUrl ?? '');
			setEventTypes(endpoint.eventTypes);
			setActive(endpoint.active);
		} else {
			setName('');
			setTargetUrl('');
			setEventTypes([]);
			setActive(true);
		}
	}, [open, endpoint]);

	function resetDraft() {
		setName('');
		setTargetUrl('');
		setEventTypes([]);
		setActive(true);
		setError(null);
	}

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

		if (isEdit && endpoint) {
			try {
				const updated = await update.mutateAsync({
					endpointId: endpoint.id,
					changes: {
						name: trimmedName,
						targetUrl: targetUrl.trim(),
						eventTypes,
						active,
					},
				});
				setError(null);
				onClose();
				onUpdated?.(updated);
			} catch {
				// The hook surfaces a toast; keep the draft so the user can correct it.
			}
			return;
		}

		try {
			const created = await create.mutateAsync({
				name: trimmedName,
				targetUrl: targetUrl.trim(),
				eventTypes,
			});
			resetDraft();
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
			ariaLabel={isEdit ? 'Edit webhook endpoint' : 'Create webhook endpoint'}
			initialFocus={nameRef}
			className="flex flex-col"
		>
			<header className="border-border border-b p-5">
				<h2 className="font-heading text-foreground text-lg font-semibold">
					{isEdit ? 'Edit webhook endpoint' : 'Create webhook endpoint'}
				</h2>
				<p className="text-muted-foreground mt-1 text-sm">
					{isEdit ? (
						<>
							Change where events go and which ones. The signing secret is not
							affected — rotate it separately if you need a new key.
						</>
					) : (
						<>
							Jentic One POSTs signed events to a URL you own. You&apos;ll get a
							signing secret once, at the end.
						</>
					)}
				</p>
			</header>

			<div className="flex-1 space-y-5 overflow-y-auto p-5">
				{!isEdit && (
					<div className="border-primary/25 bg-primary/5 space-y-2 rounded-lg border p-3">
						<p className="text-foreground text-sm leading-relaxed">
							Jentic POSTs a fixed, signed event to your URL. You&apos;ll usually
							point this at a small <strong>relay</strong> that verifies the signature
							and forwards to the real destination.
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
				)}

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

				{isEdit && (
					<div className="border-border bg-muted/30 flex items-start gap-3 rounded-lg border p-3">
						<Checkbox checked={active} onChange={setActive} ariaLabel="Endpoint active">
							<span className="text-foreground font-medium">Active</span>
							<span className="text-muted-foreground mt-0.5 block text-xs leading-relaxed">
								When off, the endpoint is paused: matching events are not fanned out
								to it until you re-enable it.
							</span>
						</Checkbox>
					</div>
				)}

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
				<Button variant="primary" onClick={handleSubmit} loading={pending}>
					{isEdit ? 'Save changes' : 'Create endpoint'}
				</Button>
			</footer>
		</SheetPrimitive>
	);
}
