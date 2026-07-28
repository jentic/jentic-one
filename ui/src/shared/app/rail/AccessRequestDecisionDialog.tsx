/**
 * AccessRequestDecisionDialog — routes an access request to the right decision UI.
 *
 * A *provisioning plan* (a request carrying `toolkit:create` / `credential:provision`
 * intents) must be decided through the fulfilment wizard, which creates the real
 * toolkit + credential and wires them before approving. Approving a plan through
 * the plain approve/deny dialog leaves the bind items unfulfilled and the backend
 * denies them (the setup is a no-op). Any surface that lets an operator decide a
 * request (dashboard queue, pending card, agent card, the rail) should open THIS
 * wrapper so the plan-vs-plain routing happens in one place.
 *
 * Callers that already hold the full request (list pages) pass `request`; the
 * routing is then synchronous. Event-driven callers (the rail) only carry the
 * request ID on the event token — pass `requestId` and this wrapper fetches the
 * request first, showing a brief loading shell, then routes. Without the fetch
 * the rail can't know it's a plan and would strand the operator in the plain
 * dialog's "open it from Access Requests" dead end.
 */
import { useEffect, useState } from 'react';
import { Dialog } from '@/shared/ui/Dialog';
import { LoadingState } from '@/shared/ui/LoadingState';
import { ErrorAlert } from '@/shared/ui/ErrorAlert';
import { AccessRequestDialog } from '@/shared/app/rail/AccessRequestDialog';
import { ProvisioningRequestDialog } from '@/shared/app/rail/ProvisioningRequestDialog';
import { getAccessRequest, isProvisioningPlan, type AccessRequest } from '@/shared/lib';

export interface AccessRequestDecisionDialogProps {
	/** The request to decide; null closes the dialog. Full object so we can route. */
	request?: AccessRequest | null;
	/**
	 * Alternative to `request` for callers that only hold the id (e.g. a rail
	 * event token). The wrapper fetches the request before routing. Ignored when
	 * `request` is provided.
	 */
	requestId?: string | null;
	/**
	 * The filed event id, passed through to `onResolved` after a successful
	 * decision so an event-driven parent (the rail) can settle its row.
	 */
	eventId?: string | null;
	onClose: () => void;
	/** Called after a decision/fulfilment so the caller can refresh its list. */
	onDecided: () => void;
	/** Called with `eventId` after a successful decision (event-driven callers). */
	onResolved?: (eventId: string) => void;
}

export function AccessRequestDecisionDialog({
	request,
	requestId,
	eventId,
	onClose,
	onDecided,
	onResolved,
}: AccessRequestDecisionDialogProps) {
	const [fetched, setFetched] = useState<AccessRequest | null>(null);
	const [fetchError, setFetchError] = useState<string | null>(null);

	// Only fetch when routing has nothing to go on: no full request, id present.
	const needsFetch = !request && typeof requestId === 'string' && requestId !== '';

	useEffect(() => {
		if (!needsFetch) return;
		let cancelled = false;
		setFetched(null);
		setFetchError(null);
		void getAccessRequest(requestId)
			.then((fresh) => {
				if (!cancelled) setFetched(fresh);
			})
			.catch((e: unknown) => {
				if (!cancelled)
					setFetchError(e instanceof Error ? e.message : 'Failed to load the request.');
			});
		return () => {
			cancelled = true;
		};
	}, [needsFetch, requestId]);

	const resolved = request ?? (needsFetch ? fetched : null);
	const open = Boolean(request) || needsFetch;

	if (open && !resolved) {
		// Fetch-by-id in flight (or failed): a neutral shell, so the operator
		// never sees the wrong surface flash while we determine the routing.
		return (
			<Dialog open onClose={onClose} title="Access request" size="lg">
				{fetchError ? (
					<ErrorAlert message={fetchError} />
				) : (
					<LoadingState message="Loading the access request…" />
				)}
			</Dialog>
		);
	}

	if (resolved && isProvisioningPlan(resolved)) {
		return (
			<ProvisioningRequestDialog
				open
				request={resolved}
				onClose={onClose}
				onFulfilled={() => {
					if (eventId) onResolved?.(eventId);
					onDecided();
				}}
			/>
		);
	}
	return (
		<AccessRequestDialog
			open={resolved !== null}
			requestId={resolved?.id ?? null}
			eventId={eventId ?? null}
			onClose={onClose}
			onResolved={onResolved}
			onDecided={onDecided}
		/>
	);
}
