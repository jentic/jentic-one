import { useCallback, useEffect, useId, useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { Button, ErrorAlert, Input, Label, Skeleton, toast } from '@/shared/ui';
import {
	invalidateEditSurfaces,
	useBindingAccess,
	useCancelConnectSession,
	usePollConnectSessionStatus,
	useStartEditConnection,
} from '@/shared/credentials/api/vendors-hooks';
import {
	IntegrationsApiError,
	cancelConnectSession,
	cancelConnectSessionOnUnload,
	pollConnectSessionStatus,
} from '@/shared/credentials/api/vendors-client';
import { isHttpsVendorUrl, openVendorUrl } from '@/shared/credentials/lib/safe-navigation';
import type { ConfirmResponse } from '@/shared/credentials/api/vendors-types';
import {
	AwaitingStep,
	ScopeChooseField,
	TerminalStep,
	VendorHeader,
	signInFailure,
} from './VendorConnectSteps';
import type { VendorDisplay } from './vendorFlowTypes';

/** Why a person's change was refused, for the scopes step. */
function describeEditError(err: unknown): string {
	if (err instanceof IntegrationsApiError && err.code === 'connect_session_unknown_scopes') {
		const unknown = err.extensions.unknown_scopes;
		const names = Array.isArray(unknown) ? unknown.join(', ') : '';
		return `The vendor doesn’t offer ${names ? `these scopes: ${names}` : 'those scopes'}. Check the names and try again.`;
	}
	if (err instanceof IntegrationsApiError && err.code === 'nothing_to_change') {
		return 'Those are the scopes it has already.';
	}
	return err instanceof Error ? err.message : 'The change couldn’t be started. Try again.';
}

/**
 * A person changing the scopes on a binding's credential (adding, removing or
 * both): their own change, applied as they make it through an edit session
 * (`apply`). It goes straight to the vendor sign-in for the new set, the same
 * step an approved agent request runs. An agent's open request for the
 * binding is rebased onto it once it lands.
 */
export function EditScopesFlow({
	agentId,
	credentialId,
	credentialName,
	vendorKey,
	onRegisterDismiss,
	onBack,
	onDone,
}: {
	agentId: string;
	credentialId: string;
	credentialName: string;
	vendorKey: string;
	onRegisterDismiss?: (handler: (() => void) | null) => void;
	onBack: () => void;
	onDone: () => void;
}) {
	const queryClient = useQueryClient();
	const access = useBindingAccess(agentId, credentialId);
	const start = useStartEditConnection();
	const cancelMutation = useCancelConnectSession();
	const [typed, setTyped] = useState('');
	// The scopes ticked; untouched, it is the grant as it stands.
	const [picked, setPicked] = useState<Set<string> | null>(null);
	const [reason, setReason] = useState('');
	const [error, setError] = useState<string | null>(null);
	const [consent, setConsent] = useState<{
		sessionId: string;
		pollToken: string;
		challenge: ConfirmResponse;
	} | null>(null);
	const [cancelError, setCancelError] = useState<string | null>(null);
	const [cancelChecking, setCancelChecking] = useState(false);
	const [outcome, setOutcome] = useState<{ failed: string | null } | null>(null);
	const consentRef = useRef<{ sessionId: string; pollToken: string } | null>(null);
	const terminalRef = useRef(false);
	const mountedRef = useRef(true);
	const startingRef = useRef(false);
	const scopesId = useId();
	const reasonId = useId();
	const display: VendorDisplay = { displayName: credentialName, iconKey: vendorKey };

	const polling = usePollConnectSessionStatus(consent?.sessionId ?? '', consent?.pollToken, {
		enabled: consent != null && outcome == null,
		intervalMs:
			consent?.challenge.kind === 'device_authorization'
				? (consent.challenge.poll_interval_seconds ?? 5) * 1000
				: undefined,
	});
	const showApplied = useCallback((): void => {
		if (terminalRef.current) return;
		terminalRef.current = true;
		setOutcome({ failed: null });
		void queryClient.invalidateQueries({ queryKey: ['credentials'] });
		void invalidateEditSurfaces(queryClient, {
			binding: { agent_id: agentId, credential_id: credentialId },
			other_bound_agents: [],
		});
		toast({ title: 'Access updated', variant: 'success' });
	}, [agentId, credentialId, queryClient]);

	useEffect(() => {
		if (!consent || outcome) return;
		const err = polling.error as { status?: number } | undefined;
		const status = polling.data?.status;
		if (status === 'applied') {
			showApplied();
		} else if (err?.status === 403 || status === 'failed' || status === 'expired') {
			terminalRef.current = true;
			setOutcome({
				failed: signInFailure(polling.data),
			});
		}
	}, [consent, outcome, polling.data, polling.error, showApplied]);

	const cancelLiveConsent = useCallback(async (): Promise<void> => {
		const live = consentRef.current;
		if (!live || terminalRef.current) {
			onBack();
			return;
		}
		if (cancelMutation.isPending || cancelChecking) return;
		setCancelChecking(true);
		setCancelError(null);
		try {
			await cancelMutation.mutateAsync({
				sessionId: live.sessionId,
				pollToken: live.pollToken,
			});
			const authoritative = await pollConnectSessionStatus(live.sessionId, live.pollToken);
			if (authoritative.status === 'applied') {
				showApplied();
				return;
			}
			if (authoritative.status === 'failed' || authoritative.status === 'expired') {
				terminalRef.current = true;
				if (authoritative.error_code === 'cancelled') {
					onBack();
				} else {
					setOutcome({ failed: signInFailure(authoritative) });
				}
				return;
			}
			setCancelError('The server has not confirmed the sign-in outcome. Try again.');
		} catch (err) {
			setCancelError(
				err instanceof Error && err.message
					? err.message
					: 'Could not confirm the sign-in outcome. Try again.',
			);
		} finally {
			setCancelChecking(false);
		}
	}, [cancelChecking, cancelMutation, onBack, showApplied]);

	useEffect(() => {
		if (!onRegisterDismiss) return undefined;
		if (start.isPending) {
			onRegisterDismiss(() => {});
			return () => onRegisterDismiss(null);
		}
		if (consent && !outcome) {
			onRegisterDismiss(() => {
				void cancelLiveConsent();
			});
			return () => onRegisterDismiss(null);
		}
		onRegisterDismiss(null);
		return undefined;
	}, [consent, outcome, onRegisterDismiss, cancelLiveConsent, start.isPending]);

	useEffect(() => {
		mountedRef.current = true;
		return () => {
			mountedRef.current = false;
			if (terminalRef.current) return;
			const live = consentRef.current;
			if (!live) return;
			void cancelConnectSession(live.sessionId, live.pollToken).catch(() => {});
		};
	}, []);

	useEffect(() => {
		const onBeforeUnload = (): void => {
			if (terminalRef.current) return;
			const live = consentRef.current;
			if (!live) return;
			cancelConnectSessionOnUnload(live.sessionId, live.pollToken);
		};
		window.addEventListener('beforeunload', onBeforeUnload);
		return () => window.removeEventListener('beforeunload', onBeforeUnload);
	}, []);

	if (outcome) {
		return (
			<TerminalStep
				display={display}
				status={outcome.failed ? 'failed' : 'applied'}
				connectedAs={null}
				errorCode={null}
				failureMessage={outcome.failed ?? undefined}
				credentialId={null}
				boundAgentId={agentId}
				onDone={onDone}
			/>
		);
	}
	if (consent) {
		return (
			<AwaitingStep
				display={display}
				challenge={consent.challenge}
				status={polling.data?.status ?? 'pending'}
				cancelling={cancelMutation.isPending || cancelChecking}
				cancelError={cancelError}
				onCancel={(): void => {
					void cancelLiveConsent();
				}}
			/>
		);
	}
	if (access.isPending) {
		return (
			<div role="status" aria-live="polite" aria-busy="true">
				<span className="sr-only">Loading access…</span>
				<Skeleton className="h-32 rounded-lg" />
			</div>
		);
	}
	const data = access.data;
	if (!data) {
		return (
			<ErrorAlert
				message="Failed to load this binding's access."
				onRetry={() => void access.refetch()}
			/>
		);
	}

	const granted = data.granted_scopes ?? [];
	const chosen = picked ?? new Set(granted);
	// Every scope to tick or untick: the vendor's catalog, plus any the grant
	// holds that it doesn't list. Without a catalog, the grant's scopes, and
	// more are named as text and passed to the vendor as asked.
	const options = [
		...(data.scope_catalog ?? []).map((scope) => ({ ...scope, default: false })),
		...granted
			.filter((name) => !data.scope_catalog?.some((scope) => scope.name === name))
			.map((name) => ({
				name,
				classification: 'read' as const,
				default: false,
				description: '',
			})),
	];
	const next = [
		...new Set([
			...chosen,
			...(data.scope_catalog ? [] : typed.split(/[\s,]+/).filter(Boolean)),
		]),
	].sort();
	const changed = JSON.stringify(next) !== JSON.stringify([...granted].sort());
	const toggle = (name: string): void =>
		setPicked(() => {
			const updated = new Set(chosen);
			if (updated.has(name)) updated.delete(name);
			else updated.add(name);
			return updated;
		});

	const submit = async (): Promise<void> => {
		if (startingRef.current || consentRef.current) return;
		startingRef.current = true;
		setError(null);
		try {
			const result = await start.mutateAsync({
				agent_id: agentId,
				credential_id: credentialId,
				scopes: { set: next },
				reason: reason.trim() || undefined,
				apply: true,
			});
			if (result.challenge) {
				const live = {
					sessionId: result.session_id,
					pollToken: result.poll_token,
				};
				consentRef.current = live;
				if (!mountedRef.current) {
					terminalRef.current = true;
					void cancelConnectSession(live.sessionId, live.pollToken).catch(() => {});
					return;
				}
				terminalRef.current = false;
				setCancelError(null);
				setConsent({
					sessionId: result.session_id,
					pollToken: result.poll_token,
					challenge: result.challenge,
				});
				if (
					result.challenge.kind === 'authorization_code' &&
					isHttpsVendorUrl(result.challenge.authorize_url)
				) {
					openVendorUrl(result.challenge.authorize_url, '_blank', 'noopener,noreferrer');
				}
			}
		} catch (err) {
			if (mountedRef.current) setError(describeEditError(err));
		} finally {
			startingRef.current = false;
		}
	};

	return (
		<div className="space-y-5">
			<VendorHeader
				display={display}
				subtitle={`Change the OAuth scopes on ${credentialName}.`}
			/>

			{!data.scopes_editable ? (
				<p className="text-muted-foreground text-sm">
					This credential’s scopes can’t be changed here.
				</p>
			) : (
				<>
					{options.length > 0 && (
						<ScopeChooseField
							label="Scopes"
							loading={false}
							error={null}
							scopes={options}
							selected={chosen}
							onToggle={toggle}
						/>
					)}
					{!data.scope_catalog && (
						<div className="space-y-1">
							<Label htmlFor={scopesId}>Scopes to add</Label>
							<Input
								id={scopesId}
								value={typed}
								onChange={(e) => setTyped(e.target.value)}
								placeholder="Separate scopes with spaces or commas"
							/>
						</div>
					)}
					<p className="text-muted-foreground text-xs">
						Granted today: {granted.length > 0 ? granted.join(', ') : 'none'}. Saving
						signs in to the vendor again for the new set; every agent using this
						credential gets the change.
					</p>
				</>
			)}

			<div className="space-y-1">
				<Label htmlFor={reasonId}>Reason (optional)</Label>
				<Input
					id={reasonId}
					value={reason}
					maxLength={1024}
					onChange={(e) => setReason(e.target.value)}
					placeholder="Kept in the change's history"
				/>
			</div>

			{error && <ErrorAlert message={error} />}

			<div className="bg-surface-sheet-foot border-hairline-field sticky -bottom-4 z-10 -mx-5 mt-auto -mb-4 flex items-center justify-between border-t px-5 py-3.5">
				<Button
					type="button"
					variant="ghost"
					size="sm"
					onClick={onBack}
					disabled={start.isPending}
				>
					Cancel
				</Button>
				<Button
					type="button"
					variant="primary"
					loading={start.isPending}
					disabled={!changed || start.isPending}
					onClick={(): void => void submit()}
				>
					Sign in and save
				</Button>
			</div>
		</div>
	);
}
