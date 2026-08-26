/**
 * `/webhooks/:endpointId` (→ `/app/webhooks/:endpointId`) — full-page host for a
 * single webhook endpoint's detail.
 *
 * This is the routed successor to `WebhookEndpointDrawer`: clicking an endpoint
 * row now navigates here instead of opening a slide-over, so the endpoint detail
 * is consistent with the Agents/Toolkits detail pages. The layout mirrors
 * `/toolkits/:toolkitId` and `/agents/:agentId`: a shared `PageHeader` band
 * (endpoint name as title, its target URL as subtitle, with the Send test action
 * and the rotation grace badge in the header action slot), a `BackButton` row
 * beneath it, then the KPI strip + tabbed content (Overview / Deliveries /
 * Settings) in `WebhookEndpointDetailBody`, whose active tab is deep-linked
 * through `?tab=`.
 *
 * The page owns the mutation dialogs the drawer deferred to its host — the
 * rotate/delete confirms and the one-time secret reveal — so the body stays the
 * read + inline-edit surface. Editing the endpoint's configuration now happens
 * inline in the Settings tab (matching the Agents/Toolkits consoles), so there
 * is no Edit affordance in the header and no edit drawer. Everything is gated on
 * `webhooks:write`; the backend enforces the same scope regardless.
 */
import { useState } from 'react';
import { useNavigate, useParams } from 'react-router';
import { Send } from 'lucide-react';
import { Badge, BackButton, Button, LoadingState, PageHeader, PageShell } from '@/shared/ui';
import { usePermission } from '@/shared/auth';
import { timeAgo } from '@/shared/lib/utils';
import { useSendTestEvent, useWebhookEndpoint, WebhooksApiError } from '@/modules/webhooks/api';
import type { RotatedSecret, WebhookEndpointEntity } from '@/modules/webhooks/api';
import { WebhookEndpointDetailBody } from '@/modules/webhooks/components/WebhookEndpointDetailBody';
import { DeleteEndpointDialog } from '@/modules/webhooks/components/DeleteEndpointDialog';
import { RotateSecretDialog } from '@/modules/webhooks/components/RotateSecretDialog';
import { SecretRevealDialog } from '@/modules/webhooks/components/SecretRevealDialog';
import { ROUTES } from '@/shared/app/routes';

/** The secret currently being revealed after a rotation, plus its context. */
interface RevealState {
	secret: string;
	endpointName: string;
	previousSecretExpiresAt: string | null;
}

export default function WebhookEndpointDetailPage() {
	const { endpointId } = useParams<{ endpointId: string }>();
	const id = endpointId ?? null;
	const navigate = useNavigate();
	const canWrite = usePermission('webhooks:write');
	const sendTest = useSendTestEvent();

	const { data: endpoint, isPending, error } = useWebhookEndpoint(id);

	const [rotateTarget, setRotateTarget] = useState<WebhookEndpointEntity | null>(null);
	const [deleteTarget, setDeleteTarget] = useState<WebhookEndpointEntity | null>(null);
	// Conditionally mounted (see SecretRevealDialog) so the plaintext leaves
	// React state entirely once dismissed.
	const [reveal, setReveal] = useState<RevealState | null>(null);

	function handleRotated(rotated: RotatedSecret, rotatedEndpoint: WebhookEndpointEntity) {
		setReveal({
			secret: rotated.secret,
			endpointName: rotatedEndpoint.name,
			previousSecretExpiresAt: rotated.previousSecretExpiresAt,
		});
	}

	if (!id) {
		return (
			<PageShell>
				<PageHeader title="Webhook endpoint" subtitle="No endpoint selected." />
				<div className="-mt-2">
					<BackButton to={ROUTES.webhooks} label="All webhooks" useHistory={false} />
				</div>
			</PageShell>
		);
	}

	if (isPending) {
		return (
			<PageShell>
				<LoadingState message="Loading webhook endpoint…" />
			</PageShell>
		);
	}

	// Only a 404 means "no such endpoint" — anything else (403, 500, network) is
	// a load failure and must not masquerade as not-found.
	const errorStatus = error instanceof WebhooksApiError ? error.status : null;
	if (error && errorStatus !== 404) {
		return (
			<PageShell>
				<PageHeader
					title="Couldn't load endpoint"
					subtitle={
						errorStatus === 403
							? 'You do not have permission to view this endpoint.'
							: 'Something went wrong while loading this endpoint. Try again.'
					}
				/>
				<div className="-mt-2">
					<BackButton to={ROUTES.webhooks} label="All webhooks" useHistory={false} />
				</div>
			</PageShell>
		);
	}

	if (error || !endpoint) {
		return (
			<PageShell>
				<PageHeader
					title="Endpoint not found"
					subtitle={`No webhook endpoint with id ${id}.`}
				/>
				<div className="-mt-2">
					<BackButton to={ROUTES.webhooks} label="All webhooks" useHistory={false} />
				</div>
			</PageShell>
		);
	}

	// Show a rotation grace badge while a previous secret is still valid.
	const graceActive =
		endpoint.previousSecretExpiresAt != null &&
		new Date(endpoint.previousSecretExpiresAt).getTime() > Date.now();

	return (
		<PageShell>
			<PageHeader
				title={endpoint.name}
				subtitle={endpoint.targetUrl ?? undefined}
				actions={
					<div className="flex flex-wrap items-center justify-end gap-2">
						{graceActive && (
							<Badge variant="pending" dot>
								rotating · old key valid {timeAgo(endpoint.previousSecretExpiresAt)}
							</Badge>
						)}
						{!endpoint.active && <Badge variant="danger">disabled</Badge>}
						{canWrite && (
							<Button
								variant="secondary"
								size="sm"
								onClick={() => sendTest.mutate(endpoint.id)}
							>
								<Send className="h-4 w-4" />
								<span className="hidden sm:inline">Send test</span>
							</Button>
						)}
					</div>
				}
			/>

			<div className="-mt-2">
				{/* Static link (not history-back): tab switches push history
				    entries, so popping would step through tabs instead of leaving
				    the page. */}
				<BackButton to={ROUTES.webhooks} label="All webhooks" useHistory={false} />
			</div>

			<WebhookEndpointDetailBody
				endpointId={id}
				canWrite={canWrite}
				onRequestClose={() => navigate(ROUTES.webhooks)}
				onRotate={setRotateTarget}
				onDelete={setDeleteTarget}
			/>

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
				onDeleted={() => navigate(ROUTES.webhooks)}
			/>

			{reveal && (
				<SecretRevealDialog
					open
					onClose={() => setReveal(null)}
					secret={reveal.secret}
					endpointName={reveal.endpointName}
					mode="rotated"
					previousSecretExpiresAt={reveal.previousSecretExpiresAt}
				/>
			)}
		</PageShell>
	);
}
