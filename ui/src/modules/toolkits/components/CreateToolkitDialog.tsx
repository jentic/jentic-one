import { useState } from 'react';
import { ArrowRight } from 'lucide-react';
import { Button, Checkbox, Dialog, ErrorAlert, Input, Label, Textarea } from '@/shared/ui';
import { useBindableCredentials, useCreateToolkit } from '@/modules/toolkits/api';
import { OneTimeKeyDisplay } from '@/modules/toolkits/components/OneTimeKeyDisplay';
import { CREDENTIAL_TYPE_LABELS } from '@/modules/toolkits/api/types';
import type { CreatedToolkit } from '@/modules/toolkits/api/types';

/**
 * Two-step "New toolkit" dialog.
 *
 * Step 1 (form): name + description + an optional credential multi-select —
 * `POST /toolkits` has always accepted `credential_ids[]` for inline binds,
 * the UI just never offered it (phase-4 gap). Inline binds land with zero
 * rules, so the form says the broker will default-deny until rules are added.
 *
 * Step 2 (key): the response's one-time plaintext key, rendered through the
 * same `OneTimeKeyDisplay` contract the Keys tab uses — it must never be
 * silently thrown away on create. The plaintext is wiped from state the
 * moment the dialog closes (sensitive-data rule) and the CTA hands off to the
 * new toolkit's detail page.
 */
export interface CreateToolkitDialogProps {
	open: boolean;
	onClose: () => void;
	/** Navigate to the created toolkit's detail page (host page owns routing). */
	onGoToToolkit: (toolkitId: string) => void;
}

export function CreateToolkitDialog({ open, onClose, onGoToToolkit }: CreateToolkitDialogProps) {
	const createToolkit = useCreateToolkit();
	const { data: credentials = [] } = useBindableCredentials({ enabled: open });

	const [name, setName] = useState('');
	const [description, setDescription] = useState('');
	const [selectedCredIds, setSelectedCredIds] = useState<Set<string>>(new Set());
	const [created, setCreated] = useState<CreatedToolkit | null>(null);

	// Dialog-state lifecycle: the FORM draft survives casual dismissals and is
	// reset only on the success path; the one-time plaintext key is the
	// sensitive-data exception and is wiped on every close.
	const resetForm = () => {
		setName('');
		setDescription('');
		setSelectedCredIds(new Set());
	};

	const close = () => {
		setCreated(null); // wipe the one-time plaintext
		createToolkit.reset();
		onClose();
	};

	const toggleCred = (credentialId: string) => {
		setSelectedCredIds((prev) => {
			const next = new Set(prev);
			if (next.has(credentialId)) next.delete(credentialId);
			else next.add(credentialId);
			return next;
		});
	};

	const submit = () => {
		if (!name.trim()) return;
		createToolkit.mutate(
			{
				name: name.trim(),
				description: description.trim() || null,
				credential_ids: selectedCredIds.size > 0 ? [...selectedCredIds] : null,
			},
			{
				onSuccess: (res) => {
					resetForm();
					setCreated(res);
				},
			},
		);
	};

	const goToToolkit = () => {
		const id = created?.toolkit.toolkit_id;
		close();
		if (id) onGoToToolkit(id);
	};

	return (
		<Dialog
			open={open}
			onClose={close}
			title={created ? 'Toolkit created' : 'New toolkit'}
			size="sm"
			footer={
				created ? (
					<Button onClick={goToToolkit}>
						Open toolkit <ArrowRight className="h-4 w-4" />
					</Button>
				) : (
					<>
						<Button variant="secondary" onClick={close}>
							Cancel
						</Button>
						<Button
							onClick={submit}
							loading={createToolkit.isPending}
							disabled={!name.trim()}
						>
							{createToolkit.isPending ? 'Creating…' : 'Create'}
						</Button>
					</>
				)
			}
		>
			{created ? (
				<div className="space-y-3">
					<p className="text-muted-foreground text-sm">
						<span className="text-foreground font-medium">{created.toolkit.name}</span>{' '}
						is ready. Agents authenticate with this key:
					</p>
					<OneTimeKeyDisplay
						keyValue={created.apiKey}
						title="Toolkit API key"
						onConfirm={goToToolkit}
					/>
					{created.toolkit.credential_count > 0 && (
						<p className="text-muted-foreground text-xs">
							{created.toolkit.credential_count} credential
							{created.toolkit.credential_count === 1 ? '' : 's'} bound with no
							permission rules yet — the broker denies every call until you add allow
							rules on the Access tab.
						</p>
					)}
				</div>
			) : (
				<div className="space-y-4">
					{createToolkit.isError && (
						<ErrorAlert
							message={
								createToolkit.error instanceof Error
									? createToolkit.error.message
									: 'Failed to create toolkit.'
							}
						/>
					)}
					<div>
						<Label
							htmlFor="tk-create-name"
							className="text-muted-foreground mb-1 block text-xs"
						>
							Name
						</Label>
						<Input
							id="tk-create-name"
							type="text"
							value={name}
							onChange={(e) => setName(e.target.value)}
							placeholder="My toolkit"
							autoFocus
							onKeyDown={(e) => {
								if (e.key === 'Enter' && !createToolkit.isPending) submit();
							}}
						/>
					</div>
					<div>
						<Label
							htmlFor="tk-create-description"
							className="text-muted-foreground mb-1 block text-xs"
						>
							Description
						</Label>
						<Textarea
							id="tk-create-description"
							value={description}
							onChange={(e) => setDescription(e.target.value)}
							rows={2}
							placeholder="Optional"
						/>
					</div>
					{credentials.length > 0 && (
						<fieldset>
							<legend className="text-muted-foreground mb-1 block text-xs">
								Bind credentials (optional)
							</legend>
							<div className="border-border max-h-40 space-y-0.5 overflow-y-auto rounded-lg border p-1.5">
								{credentials.map((cred) => (
									<Checkbox
										key={cred.credential_id}
										checked={selectedCredIds.has(cred.credential_id)}
										onChange={() => toggleCred(cred.credential_id)}
										ariaLabel={`Bind ${cred.name}`}
										size="sm"
										className="hover:bg-muted/50 w-full rounded-md px-2 py-1.5"
									>
										<span className="flex min-w-0 flex-1 items-center gap-2">
											<span className="text-foreground min-w-0 flex-1 truncate text-sm">
												{cred.name}
											</span>
											<span className="text-muted-foreground shrink-0 font-mono text-[10px]">
												{cred.vendor ?? cred.provider ?? ''}
												{cred.vendor || cred.provider ? ' · ' : ''}
												{CREDENTIAL_TYPE_LABELS[cred.type] ?? cred.type}
											</span>
										</span>
									</Checkbox>
								))}
							</div>
							<p className="text-muted-foreground mt-1 text-xs">
								Inline binds start blocked (no rules) — grant access from the
								toolkit's Access tab, the same way the bind wizard's "Start blocked"
								option works.
							</p>
						</fieldset>
					)}
				</div>
			)}
		</Dialog>
	);
}
