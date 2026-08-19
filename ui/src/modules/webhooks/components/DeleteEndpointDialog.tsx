/**
 * DeleteEndpointDialog — confirms deletion of an endpoint.
 *
 * Deletion takes the delivery history with it, so the confirmation names that
 * consequence explicitly: the delivery log is the only record of what was sent
 * and what came back, and a dead-lettered row that hasn't been diagnosed yet
 * disappears with it.
 */
import { Button, Dialog } from '@/shared/ui';
import { useDeleteWebhookEndpoint } from '@/modules/webhooks/api';
import type { WebhookEndpointEntity } from '@/modules/webhooks/api';

interface DeleteEndpointDialogProps {
	open: boolean;
	onClose: () => void;
	endpoint: WebhookEndpointEntity | null;
}

export function DeleteEndpointDialog({ open, onClose, endpoint }: DeleteEndpointDialogProps) {
	const del = useDeleteWebhookEndpoint();

	async function handleDelete() {
		if (!endpoint) return;
		try {
			await del.mutateAsync(endpoint.id);
			onClose();
		} catch {
			// The hook surfaces a toast; leave the dialog open.
		}
	}

	return (
		<Dialog
			open={open}
			onClose={onClose}
			title="Delete webhook endpoint"
			subtitle={endpoint?.name}
			size="md"
			footer={
				<>
					<Button variant="secondary" onClick={onClose}>
						Cancel
					</Button>
					<Button variant="danger" onClick={handleDelete} loading={del.isPending}>
						Delete endpoint
					</Button>
				</>
			}
		>
			<div className="space-y-3">
				<p className="text-foreground text-sm leading-relaxed">
					This removes the endpoint and its entire delivery history. Any dead-lettered
					delivery you haven&apos;t diagnosed yet goes with it.
				</p>
			</div>
		</Dialog>
	);
}
