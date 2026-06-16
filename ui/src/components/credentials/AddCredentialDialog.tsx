import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ChevronLeft } from 'lucide-react';
import { SearchApiStep } from './add/SearchApiStep';
import { ExistingFoundStep } from './add/ExistingFoundStep';
import { ConfigureStep } from './add/ConfigureStep';
import { ConfirmStep } from './add/ConfirmStep';
import { StepProgress } from './add/StepProgress';
import type { ApiOut, CredentialOut } from '@/api/types';
import { api } from '@/api/client';
import { Dialog } from '@/components/ui/Dialog';
import { Button } from '@/components/ui/Button';
import { ErrorAlert } from '@/components/ui/ErrorAlert';
import { LoadingState } from '@/components/ui/LoadingState';
import { messageFromApiError } from '@/lib/apiError';
import type {
	AddCredentialMode,
	AddCredentialState,
	AddCredentialStep,
} from '@/hooks/useAddCredentialDialog';

/**
 * Multi-step "Add credential" dialog.
 *
 * The dialog is host-driven: the parent owns a `useAddCredentialDialog()`
 * hook and passes the resulting `state` and action handlers in. That
 * gives every host (toolkit page, credentials page, API detail,
 * Discover sheet) the same surface and lets them open the dialog in
 * the right mode (workspace / toolkit / direct-API).
 *
 * Step routing:
 *
 *   open with no API     → 'search'
 *   open with API        → 'existing' (or 'configure' if no rows)
 *   pick existing (tk)   → bind, then 'confirm'
 *   pick add another     → 'configure'
 *   configure save       → if toolkit mode, bind; then 'confirm'
 *
 * The reducer (`useAddCredentialDialog`) covers the pure state
 * transitions; the side effects (`createCredential` is owned by
 * `<CredentialFormFields>` because the same component runs in the
 * full-page form and the edit sheet — we intercept its `onSaved`
 * here for the bind step) and the toolkit bind mutation live in this
 * component where toast/error UI also lives.
 */
export interface AddCredentialDialogProps {
	state: AddCredentialState;
	onClose: () => void;
	onGoToStep: (step: AddCredentialStep) => void;
	onSelectApi: (api: ApiOut | null) => void;
	onSavedCredentialId: (id: string | null) => void;
}

export function AddCredentialDialog({
	state,
	onClose,
	onGoToStep,
	onSelectApi,
	onSavedCredentialId,
}: AddCredentialDialogProps) {
	const { open, step, mode, toolkitId, toolkitName, selectedApi, savedCredentialId } = state;

	const queryClient = useQueryClient();
	const [error, setError] = useState<string | null>(null);
	const [credentialLabel, setCredentialLabel] = useState<string | null>(null);

	// In toolkit mode, fetch what's already bound so the "existing credentials"
	// step can mark them as bound and stop the user from picking one (which would
	// otherwise hit a 409 "Credential already in toolkit").
	const { data: boundCredentialIds } = useQuery({
		queryKey: ['toolkit-credentials', toolkitId],
		queryFn: () => api.listToolkitCredentials(toolkitId!),
		enabled: open && mode === 'toolkit' && !!toolkitId,
		select: (raw: unknown) => {
			const list = (Array.isArray(raw) ? raw : []) as Array<{ credential_id?: string }>;
			return list.map((c) => c.credential_id).filter(Boolean) as string[];
		},
	});

	useEffect(() => {
		if (!open) {
			setError(null);
			setCredentialLabel(null);
		}
	}, [open]);

	const bindMutation = useMutation({
		mutationFn: async ({
			toolkitId: tkId,
			credentialId,
		}: {
			toolkitId: string;
			credentialId: string;
		}) => api.bindCredential(tkId, credentialId),
		onSuccess: (_data, variables) => {
			queryClient.invalidateQueries({ queryKey: ['toolkit', variables.toolkitId] });
			queryClient.invalidateQueries({
				queryKey: ['toolkit-credentials', variables.toolkitId],
			});
			queryClient.invalidateQueries({ queryKey: ['toolkits'] });
			queryClient.invalidateQueries({ queryKey: ['toolkit-api-bindings'] });
			queryClient.invalidateQueries({ queryKey: ['toolkit-card-enrichment'] });
			queryClient.invalidateQueries({ queryKey: ['workspace'] });
			queryClient.invalidateQueries({
				queryKey: ['credential-bindings', variables.credentialId],
			});
			onGoToStep('confirm');
		},
		onError: (e) => setError(messageFromApiError(e)),
	});

	const handleApiSelected = (api: ApiOut) => {
		setError(null);
		onSelectApi(api);
	};

	const handleUseExisting = (cred: CredentialOut) => {
		setError(null);
		setCredentialLabel(cred.label);
		onSavedCredentialId(cred.id);
		if (mode === 'toolkit' && toolkitId) {
			bindMutation.mutate({ toolkitId, credentialId: cred.id });
			return;
		}
		onGoToStep('confirm');
	};

	const handleConfigureSaved = (saved: { id: string; api_id: string }) => {
		setError(null);
		onSavedCredentialId(saved.id);
		queryClient
			.fetchQuery({
				queryKey: ['credential', saved.id],
				queryFn: () => api.getCredential(saved.id),
			})
			.then((cred: any) => {
				setCredentialLabel(cred?.label ?? null);
			})
			.catch(() => {
				setCredentialLabel(null);
			});
		if (mode === 'toolkit' && toolkitId) {
			bindMutation.mutate({ toolkitId, credentialId: saved.id });
			return;
		}
		onGoToStep('confirm');
	};

	const showBack = step !== 'search' && step !== 'confirm';
	const handleBack = () => {
		setError(null);
		if (step === 'existing') {
			onSelectApi(null);
			onGoToStep('search');
		} else if (step === 'configure') {
			if (selectedApi) onGoToStep('existing');
			else onGoToStep('search');
		}
	};

	const titleByStep: Record<AddCredentialStep, string> = {
		search: 'Add credential',
		existing: 'Add credential',
		configure: 'Add credential',
		confirm: 'Credential added',
	};

	return (
		<Dialog
			open={open}
			onClose={onClose}
			title={titleByStep[step]}
			size="lg"
			dismissOnBackdrop={step === 'confirm'}
		>
			{!open ? null : (
				<div className="space-y-4">
					<div className="flex items-center justify-between gap-3">
						<StepProgress step={step} />
						{showBack && (
							<Button
								variant="ghost"
								size="sm"
								onClick={handleBack}
								className="text-muted-foreground hover:text-foreground -mr-2"
							>
								<ChevronLeft className="h-3.5 w-3.5" /> Back
							</Button>
						)}
					</div>

					{toolkitName && step !== 'confirm' && (
						<p className="text-muted-foreground text-xs">
							This credential will be bound to{' '}
							<strong className="text-foreground">{toolkitName}</strong> on save.
						</p>
					)}

					{error && <ErrorAlert message={error} />}

					{bindMutation.isPending && (
						<LoadingState message={`Binding to ${toolkitName ?? 'toolkit'}…`} />
					)}

					{!bindMutation.isPending && step === 'search' && (
						<SearchApiStep onSelect={handleApiSelected} />
					)}

					{!bindMutation.isPending && step === 'existing' && selectedApi && (
						<ExistingFoundStep
							selectedApi={selectedApi}
							mode={mode}
							boundCredentialIds={boundCredentialIds}
							onUseExisting={handleUseExisting}
							onAddAnother={() => onGoToStep('configure')}
							onChangeApi={() => {
								onSelectApi(null);
								onGoToStep('search');
							}}
						/>
					)}

					{!bindMutation.isPending && step === 'configure' && selectedApi && (
						<ConfigureStep
							selectedApi={selectedApi}
							onBack={handleBack}
							onSaved={handleConfigureSaved}
						/>
					)}

					{!bindMutation.isPending && step === 'confirm' && selectedApi && (
						<ConfirmStep
							selectedApi={selectedApi}
							mode={mode}
							toolkitName={toolkitName}
							credentialLabel={credentialLabel}
							onDone={onClose}
						/>
					)}
				</div>
			)}
		</Dialog>
	);
}

/** Re-export hook utilities so hosts only import from this module. */
export type { AddCredentialMode };
