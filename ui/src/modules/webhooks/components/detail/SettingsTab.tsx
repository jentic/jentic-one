import { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router';
import { KeyRound, Pencil, Save } from 'lucide-react';
import {
	Button,
	DangerZone,
	DetailSection,
	Dialog,
	ErrorAlert,
	Input,
	Label,
	Textarea,
} from '@/shared/ui';
import {
	useDeleteEndpoint,
	useEventTypeCatalog,
	usePauseEndpoint,
	useResumeEndpoint,
	useRotateEndpointSecret,
	useUpdateEndpoint,
	type WebhookEndpointEntity,
} from '@/modules/webhooks/api';
import { EventTypePicker } from '@/modules/webhooks/components/EventTypePicker';
import { SecretReveal } from '@/modules/webhooks/components/SecretReveal';

/**
 * Settings tab — edit config (F9), pause/resume (F7), rotate the signing
 * secret (F8, one-time reveal), and the delete danger zone.
 */
export function SettingsTab({ endpoint }: { endpoint: WebhookEndpointEntity }) {
	const navigate = useNavigate();
	const { data: catalog = [] } = useEventTypeCatalog();
	const update = useUpdateEndpoint();
	const pause = usePauseEndpoint();
	const resume = useResumeEndpoint();
	const rotate = useRotateEndpointSecret();
	const remove = useDeleteEndpoint();

	const [name, setName] = useState(endpoint.name);
	const [url, setUrl] = useState(endpoint.url);
	const [description, setDescription] = useState(endpoint.description ?? '');
	const [eventTypes, setEventTypes] = useState<string[]>(endpoint.eventTypes);
	const [formError, setFormError] = useState<string | null>(null);

	// Seed-from-props syncs only when the endpoint itself changed — not on
	// every render — so in-progress edits aren't clobbered by refetches.
	const lastIdRef = useRef(endpoint.id);
	useEffect(() => {
		if (lastIdRef.current !== endpoint.id) {
			lastIdRef.current = endpoint.id;
			setName(endpoint.name);
			setUrl(endpoint.url);
			setDescription(endpoint.description ?? '');
			setEventTypes(endpoint.eventTypes);
		}
	}, [endpoint]);

	const [rotateConfirmOpen, setRotateConfirmOpen] = useState(false);
	const [deleteConfirmOpen, setDeleteConfirmOpen] = useState(false);
	// Sensitive: the freshly rotated secret — wiped when its panel is dismissed.
	const [rotatedSecret, setRotatedSecret] = useState<string | null>(null);

	const isSlack = endpoint.destinationType === 'slack';

	const save = () => {
		if (!name.trim()) {
			setFormError('Give the endpoint a name.');
			return;
		}
		if (isSlack && !url.trim().startsWith('https://hooks.slack.com/')) {
			setFormError(
				'Paste a Slack incoming webhook URL (it starts with https://hooks.slack.com/).',
			);
			return;
		}
		if (!url.trim().startsWith('https://')) {
			setFormError('The endpoint URL must start with https://.');
			return;
		}
		if (eventTypes.length === 0) {
			setFormError('Subscribe to at least one event type.');
			return;
		}
		setFormError(null);
		update.mutate({
			id: endpoint.id,
			patch: {
				name: name.trim(),
				url: url.trim(),
				description: description.trim() || null,
				event_types: eventTypes,
			},
		});
	};

	const paused = endpoint.wireStatus === 'paused';

	return (
		<div className="space-y-4">
			<DetailSection
				title="Endpoint settings"
				icon={<Pencil className="h-4 w-4" />}
				action={{
					label: (
						<>
							<Save className="h-4 w-4" /> Save changes
						</>
					),
					onClick: save,
					variant: 'primary',
					ariaLabel: `Save changes to ${endpoint.name}`,
				}}
			>
				<div className="space-y-4">
					<div className="space-y-1.5">
						<Label htmlFor="settings-name">Name</Label>
						<Input
							id="settings-name"
							value={name}
							onChange={(e) => setName(e.target.value)}
						/>
					</div>
					<div className="space-y-1.5">
						<Label htmlFor="settings-url">
							{isSlack ? 'Slack incoming webhook URL' : 'Endpoint URL'}
						</Label>
						<Input
							id="settings-url"
							value={url}
							onChange={(e) => setUrl(e.target.value)}
							inputMode="url"
						/>
					</div>
					<div className="space-y-1.5">
						<Label htmlFor="settings-description">Description</Label>
						<Textarea
							id="settings-description"
							value={description}
							onChange={(e) => setDescription(e.target.value)}
							rows={2}
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
					{formError && <ErrorAlert message={formError} />}
				</div>
			</DetailSection>

			{/* Slack incoming webhooks are unsigned — there is no secret to manage. */}
			{!isSlack && (
				<DetailSection
					title="Signing secret"
					icon={<KeyRound className="h-4 w-4" />}
					action={
						rotatedSecret
							? undefined
							: {
									label: 'Rotate secret',
									onClick: () => setRotateConfirmOpen(true),
									variant: 'secondary',
									ariaLabel: `Rotate the signing secret of ${endpoint.name}`,
								}
					}
				>
					{rotatedSecret ? (
						<SecretReveal
							secret={rotatedSecret}
							title="New signing secret"
							onConfirm={() => setRotatedSecret(null)}
						/>
					) : (
						<div className="space-y-1">
							<code className="text-foreground font-mono text-xs">
								{endpoint.secretPreview}
							</code>
							<p className="text-muted-foreground text-xs">
								Rotation is zero-downtime: for 24 hours deliveries are signed with
								both the old and the new secret (space-delimited in
								<code className="font-mono"> webhook-signature</code>), then the old
								one expires.
							</p>
						</div>
					)}
				</DetailSection>
			)}

			<DangerZone
				actions={[
					{
						key: paused ? 'resume' : 'pause',
						title: paused ? 'Resume deliveries' : 'Pause deliveries',
						description: paused
							? 'Start delivering events to this endpoint again.'
							: 'Stop deliveries without deleting the endpoint. Events fired while paused are NOT queued for later.',
						buttonLabel: paused ? 'Resume' : 'Pause',
						ariaLabel: `${paused ? 'Resume' : 'Pause'} ${endpoint.name}`,
						emphasis: 'outline',
					},
					{
						key: 'delete',
						title: 'Delete endpoint',
						description:
							'Permanently removes the endpoint and stops all deliveries. This cannot be undone.',
						buttonLabel: 'Delete',
						ariaLabel: `Delete ${endpoint.name}`,
						emphasis: 'solid',
					},
				]}
				pending={pause.isPending || resume.isPending || remove.isPending}
				onAction={(key) => {
					if (key === 'pause') pause.mutate(endpoint.id);
					if (key === 'resume') resume.mutate(endpoint.id);
					if (key === 'delete') setDeleteConfirmOpen(true);
				}}
			/>

			<Dialog
				open={rotateConfirmOpen}
				onClose={() => setRotateConfirmOpen(false)}
				title="Rotate signing secret?"
				footer={
					<>
						<Button variant="secondary" onClick={() => setRotateConfirmOpen(false)}>
							Cancel
						</Button>
						<Button
							onClick={() =>
								rotate.mutate(endpoint.id, {
									onSuccess: (result) => {
										setRotateConfirmOpen(false);
										setRotatedSecret(result.secret);
									},
								})
							}
							loading={rotate.isPending}
						>
							Rotate secret
						</Button>
					</>
				}
			>
				<p className="text-muted-foreground text-sm">
					A new secret is generated and shown once. Deliveries carry both signatures for
					24 hours so your receiver can migrate without downtime.
				</p>
			</Dialog>

			<Dialog
				open={deleteConfirmOpen}
				onClose={() => setDeleteConfirmOpen(false)}
				title={`Delete ${endpoint.name}?`}
				footer={
					<>
						<Button variant="secondary" onClick={() => setDeleteConfirmOpen(false)}>
							Cancel
						</Button>
						<Button
							variant="danger"
							onClick={() =>
								remove.mutate(endpoint.id, {
									onSuccess: () => navigate('/webhooks', { replace: true }),
								})
							}
							loading={remove.isPending}
						>
							Delete endpoint
						</Button>
					</>
				}
			>
				<p className="text-muted-foreground text-sm">
					Deliveries stop immediately and the endpoint's configuration is removed. The
					delivery history is kept in the audit trail.
				</p>
			</Dialog>
		</div>
	);
}
