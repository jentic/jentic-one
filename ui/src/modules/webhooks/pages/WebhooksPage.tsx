/**
 * `/app/webhooks` — the webhooks console.
 *
 * - **Notification (outbound)** — Jentic One POSTs signed platform events to a URL
 *   you own. This is how something *outside* learns that a credential expired or
 *   an execution failed, without polling.
 *
 * Everything on the page is gated on `webhooks:write` for mutations, with
 * `webhooks:read` sufficient to look. The gate is a UX nicety only — the backend
 * enforces the same scopes and would 403 regardless.
 */
import { useState } from 'react';
import { BookOpen, Plus, ShieldCheck, Send, Waypoints } from 'lucide-react';
import {
	Button,
	Card,
	ErrorAlert,
	LoadingState,
	PageHeader,
	PageHelp,
	PageShell,
} from '@/shared/ui';
import { usePermission } from '@/shared/auth';
import { useWebhookEndpoints } from '@/modules/webhooks/api';
import type { CreatedEndpoint, RotatedSecret, WebhookEndpointEntity } from '@/modules/webhooks/api';
import { DeleteEndpointDialog } from '@/modules/webhooks/components/DeleteEndpointDialog';
import { RelayGuideDialog } from '@/modules/webhooks/components/RelayGuideDialog';
import { RotateSecretDialog } from '@/modules/webhooks/components/RotateSecretDialog';
import { SecretRevealDialog } from '@/modules/webhooks/components/SecretRevealDialog';
import { WebhookEndpointCreateSheet } from '@/modules/webhooks/components/WebhookEndpointCreateSheet';
import { WebhookEndpointTable } from '@/modules/webhooks/components/WebhookEndpointTable';

/** The secret currently being revealed, plus the context to explain it. */
interface RevealState {
	secret: string;
	endpointName: string;
	mode: 'created' | 'rotated';
	previousSecretExpiresAt: string | null;
}

export default function WebhooksPage() {
	const { data, isLoading, isError, error, refetch, isFetching } = useWebhookEndpoints();
	const canWrite = usePermission('webhooks:write');

	const [createOpen, setCreateOpen] = useState(false);
	const [editTarget, setEditTarget] = useState<WebhookEndpointEntity | null>(null);
	const [relayGuideOpen, setRelayGuideOpen] = useState(false);
	const [rotateTarget, setRotateTarget] = useState<WebhookEndpointEntity | null>(null);
	const [deleteTarget, setDeleteTarget] = useState<WebhookEndpointEntity | null>(null);
	// Conditionally mounted (see SecretRevealDialog) so the plaintext leaves React
	// state entirely once dismissed.
	const [reveal, setReveal] = useState<RevealState | null>(null);

	function handleCreated(created: CreatedEndpoint) {
		setReveal({
			secret: created.secret,
			endpointName: created.endpoint.name,
			mode: 'created',
			previousSecretExpiresAt: null,
		});
	}

	function handleRotated(rotated: RotatedSecret, endpoint: WebhookEndpointEntity) {
		setReveal({
			secret: rotated.secret,
			endpointName: endpoint.name,
			mode: 'rotated',
			previousSecretExpiresAt: rotated.previousSecretExpiresAt,
		});
	}

	return (
		<PageShell>
			<PageHeader
				title="Webhooks"
				subtitle="Push platform events out to a URL you own."
				actions={
					<div className="flex flex-wrap items-center justify-end gap-2">
						<Button variant="secondary" onClick={() => setRelayGuideOpen(true)}>
							<BookOpen className="h-4 w-4" />
							<span className="hidden sm:inline">Relay guide</span>
							<span className="sm:hidden">Guide</span>
						</Button>
						{canWrite && (
							<Button variant="primary" onClick={() => setCreateOpen(true)}>
								<Plus className="h-4 w-4" />
								<span className="hidden sm:inline">New endpoint</span>
								<span className="sm:hidden">New</span>
							</Button>
						)}
						<PageHelp
							title="About Webhooks"
							intro={
								<p>
									A webhook replaces polling with a phone call: instead of asking
									&ldquo;anything new?&rdquo; on a timer, whichever side knows
									something happened says so immediately.
								</p>
							}
							sections={[
								{
									heading: 'Notifications are outbound',
									body: (
										<p>
											A <strong>notification</strong> endpoint is outbound —
											Jentic One POSTs signed events to a URL you own, so an
											external system learns something happened without
											polling.
										</p>
									),
								},
								{
									heading: 'You usually need a relay',
									body: (
										<p>
											Jentic sends one fixed, signed payload — it doesn&apos;t
											speak Slack or PagerDuty. A small relay you run verifies
											the signature and forwards each event to the real
											destination. Open the <strong>Relay guide</strong> for
											the signature scheme, payload shape, and a
											copy-pasteable example.
										</p>
									),
								},
								{
									heading: 'Pick the events you care about',
									body: (
										<p>
											Subscribe to specific platform events (a credential
											expired, an execution failed, an access request filed,
											and more). Leaving the selection empty subscribes you to
											every relayable event type.
										</p>
									),
								},
								{
									heading: 'Signing secrets are shown once',
									body: (
										<p>
											Jentic One stores an encrypted copy it cannot reverse
											for display, so a secret is visible only at creation or
											rotation. Lose it and the only remedy is another
											rotation. Rotation keeps the previous secret alive for a
											grace period so both sides can be updated without
											dropping events.
										</p>
									),
								},
								{
									heading: 'Delivery is at-least-once',
									body: (
										<p>
											A failed send is retried with exponential backoff and
											eventually dead-lettered rather than deleted, so you can
											diagnose it and resend. Because a receiver can be told
											the same thing twice, it should key on the event id and
											ignore repeats.
										</p>
									),
								},
							]}
						/>
					</div>
				}
			/>

			{isError && (
				<ErrorAlert
					message={error instanceof Error ? error : 'Failed to load webhook endpoints.'}
					onRetry={() => void refetch()}
					retrying={isFetching}
				/>
			)}

			{isLoading ? (
				<LoadingState message="Loading webhook endpoints…" />
			) : (
				!isError && (
					<>
						{(data ?? []).length === 0 && (
							<Card className="p-5">
								<h2 className="font-heading text-foreground mb-1 font-semibold">
									What are outbound webhooks?
								</h2>
								<p className="text-muted-foreground mb-4 text-sm leading-relaxed">
									When something happens, Jentic One POSTs a signed event to a URL
									you own — so you can route it into Slack, PagerDuty or anywhere
									else without polling.
								</p>
								<ol className="grid gap-3 sm:grid-cols-3">
									<li className="border-border bg-muted/30 rounded-lg border p-3">
										<Send className="text-accent-teal mb-1.5 h-4 w-4" />
										<p className="text-foreground text-sm font-medium">
											1. Jentic notifies you
										</p>
										<p className="text-muted-foreground mt-0.5 text-xs leading-relaxed">
											A signed event is POSTed to your target URL.
										</p>
									</li>
									<li className="border-border bg-muted/30 rounded-lg border p-3">
										<ShieldCheck className="text-accent-teal mb-1.5 h-4 w-4" />
										<p className="text-foreground text-sm font-medium">
											2. Your relay verifies it
										</p>
										<p className="text-muted-foreground mt-0.5 text-xs leading-relaxed">
											A small service you run checks the signature.
										</p>
									</li>
									<li className="border-border bg-muted/30 rounded-lg border p-3">
										<Waypoints className="text-accent-teal mb-1.5 h-4 w-4" />
										<p className="text-foreground text-sm font-medium">
											3. It forwards on
										</p>
										<p className="text-muted-foreground mt-0.5 text-xs leading-relaxed">
											Your relay reshapes it and sends it to the destination.
										</p>
									</li>
								</ol>
								<Button
									variant="ghost"
									size="sm"
									onClick={() => setRelayGuideOpen(true)}
									className="text-accent-teal mt-3 -ml-2"
								>
									<BookOpen className="h-4 w-4" />
									Read the full relay guide
								</Button>
							</Card>
						)}
						<WebhookEndpointTable
							endpoints={data ?? []}
							canWrite={canWrite}
							onEdit={setEditTarget}
							onRotate={setRotateTarget}
							onDelete={setDeleteTarget}
							onCreate={() => setCreateOpen(true)}
						/>
					</>
				)
			)}

			<WebhookEndpointCreateSheet
				open={createOpen || editTarget !== null}
				endpoint={editTarget}
				onClose={() => {
					setCreateOpen(false);
					setEditTarget(null);
				}}
				onCreated={handleCreated}
				onUpdated={() => setEditTarget(null)}
				onOpenRelayGuide={() => setRelayGuideOpen(true)}
			/>

			<RelayGuideDialog open={relayGuideOpen} onClose={() => setRelayGuideOpen(false)} />

			<RotateSecretDialog
				open={rotateTarget !== null}
				onClose={() => setRotateTarget(null)}
				endpoint={rotateTarget}
				onRotated={handleRotated}
			/>

			<DeleteEndpointDialog
				open={deleteTarget !== null}
				onClose={() => setDeleteTarget(null)}
				endpoint={deleteTarget}
			/>

			{reveal && (
				<SecretRevealDialog
					open
					onClose={() => setReveal(null)}
					secret={reveal.secret}
					endpointName={reveal.endpointName}
					mode={reveal.mode}
					previousSecretExpiresAt={reveal.previousSecretExpiresAt}
					onOpenRelayGuide={() => setRelayGuideOpen(true)}
				/>
			)}

			{!canWrite && (
				<p className="text-muted-foreground text-sm">
					You have read-only access. Creating, editing, rotating, or deleting an endpoint
					requires <code className="font-mono">webhooks:write</code>.
				</p>
			)}
		</PageShell>
	);
}
