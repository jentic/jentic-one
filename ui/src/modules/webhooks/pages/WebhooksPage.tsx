/**
 * `/app/webhooks` — the webhooks console.
 *
 * Jentic One POSTs signed platform events to a URL you own, so something
 * *outside* learns that (say) a credential expired or an execution failed
 * without polling. Detail — the payload shape, the signing scheme, how to run a
 * relay — lives behind the help popover and the Relay guide, not on the page.
 *
 * The list is a directory: each row links to the endpoint's detail page
 * (`/webhooks/:endpointId`), where Overview / Deliveries / Settings live — the
 * same routed-detail pattern the Agents and Toolkits consoles use. This page
 * owns only the list-level flows: "New endpoint" (the create sheet) and the
 * one-time secret reveal that follows a create.
 *
 * Everything on the page is gated on `webhooks:write` for mutations, with
 * `webhooks:read` sufficient to look. The gate is a UX nicety only — the backend
 * enforces the same scopes and would 403 regardless.
 */
import { useState } from 'react';
import { BookOpen, Plus } from 'lucide-react';
import { Button, ErrorAlert, LoadingState, PageHeader, PageHelp, PageShell } from '@/shared/ui';
import { usePermission } from '@/shared/auth';
import { useWebhookEndpoints } from '@/modules/webhooks/api';
import type { CreatedEndpoint } from '@/modules/webhooks/api';
import { RelayGuideDialog } from '@/modules/webhooks/components/RelayGuideDialog';
import { SecretRevealDialog } from '@/modules/webhooks/components/SecretRevealDialog';
import { WebhookEndpointCreateSheet } from '@/modules/webhooks/components/WebhookEndpointCreateSheet';
import { WebhookEndpointList } from '@/modules/webhooks/components/WebhookEndpointList';

/** The secret currently being revealed after a create, plus its context. */
interface RevealState {
	secret: string;
	endpointName: string;
}

export default function WebhooksPage() {
	const { data, isLoading, isError, error, refetch, isFetching } = useWebhookEndpoints();
	const canWrite = usePermission('webhooks:write');

	const [createOpen, setCreateOpen] = useState(false);
	const [relayGuideOpen, setRelayGuideOpen] = useState(false);
	// Conditionally mounted (see SecretRevealDialog) so the plaintext leaves React
	// state entirely once dismissed.
	const [reveal, setReveal] = useState<RevealState | null>(null);

	function handleCreated(created: CreatedEndpoint) {
		setReveal({
			secret: created.secret,
			endpointName: created.endpoint.name,
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
									Jentic One POSTs signed platform events to a URL you own — so an
									external system learns something happened without polling.
								</p>
							}
							sections={[
								{
									heading: 'You usually need a relay',
									body: (
										<p>
											Jentic sends one fixed, signed payload — it doesn&apos;t
											speak Slack or PagerDuty. A small relay you run verifies
											the signature and forwards each event on. The{' '}
											<strong>Relay guide</strong> has the signature scheme,
											payload shape, and a copy-pasteable example.
										</p>
									),
								},
								{
									heading: 'Secrets are shown once; delivery is at-least-once',
									body: (
										<p>
											A signing secret is visible only at creation or rotation
											(rotation keeps the old one valid for a grace window). A
											failed send is retried and eventually dead-lettered
											rather than dropped, so key on the event id to ignore
											repeats.
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
					<WebhookEndpointList
						endpoints={data ?? []}
						canWrite={canWrite}
						onCreate={() => setCreateOpen(true)}
					/>
				)
			)}

			<WebhookEndpointCreateSheet
				open={createOpen}
				onClose={() => setCreateOpen(false)}
				onCreated={handleCreated}
				onOpenRelayGuide={() => setRelayGuideOpen(true)}
			/>

			<RelayGuideDialog open={relayGuideOpen} onClose={() => setRelayGuideOpen(false)} />

			{reveal && (
				<SecretRevealDialog
					open
					onClose={() => setReveal(null)}
					secret={reveal.secret}
					endpointName={reveal.endpointName}
					mode="created"
					previousSecretExpiresAt={null}
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
