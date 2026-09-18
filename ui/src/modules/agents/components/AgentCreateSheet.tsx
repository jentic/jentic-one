/**
 * AgentCreateSheet — slide-over form to create an agent manually.
 *
 * A Sheet (not a Dialog) keeps the list context visible while filling the
 * form. Fields reset only after a successful create; a dismissal preserves
 * the draft.
 *
 * Creating flows straight into the Add-APIs step: an agent with no credential
 * bound can authenticate but every call it makes fails, so "created" is not a
 * finished state. `Create empty` stays available, de-emphasised, for the
 * operator who is only reserving an identity.
 */
import { useEffect, useRef, useState } from 'react';
import { Button, Input, Label, Textarea, SheetPrimitive } from '@/shared/ui';
import { useCreateAgent, type AgentEntity } from '@/modules/agents/api';
import { InitialScopesField } from '@/modules/agents/components/InitialScopesField';

interface AgentCreateSheetProps {
	open: boolean;
	onClose: () => void;
	/**
	 * The agent that was just created, and whether the operator asked to carry
	 * on into the Add-APIs flow for it.
	 */
	onCreated?: (agent: AgentEntity, opts: { addApis: boolean }) => void;
}

/** Which button is in flight, so only that one spins. */
type Intent = 'add-apis' | 'empty';

export function AgentCreateSheet({ open, onClose, onCreated }: AgentCreateSheetProps) {
	const [name, setName] = useState('');
	const [description, setDescription] = useState('');
	const [scopes, setScopes] = useState<string[]>([]);
	const [error, setError] = useState<string | null>(null);
	const [intent, setIntent] = useState<Intent | null>(null);
	const nameRef = useRef<HTMLInputElement>(null);
	const create = useCreateAgent();

	useEffect(() => {
		if (open) setError(null);
	}, [open]);

	async function handleSubmit(next: Intent) {
		const trimmed = name.trim();
		if (!trimmed) {
			setError('A name is required.');
			return;
		}
		setIntent(next);
		try {
			const agent = await create.mutateAsync({
				name: trimmed,
				description: description.trim() || null,
				scopes,
			});
			setName('');
			setDescription('');
			setScopes([]);
			setError(null);
			onClose();
			// After the close, so the host's tray opens onto a dismissed sheet
			// rather than stacking a second layer over this one.
			onCreated?.(agent, { addApis: next === 'add-apis' });
		} catch {
			// hook surfaces a toast; keep the draft so the user can retry.
		} finally {
			setIntent(null);
		}
	}

	return (
		<SheetPrimitive
			open={open}
			onClose={onClose}
			side="right"
			ariaLabel="Create agent"
			initialFocus={nameRef}
			className="flex flex-col"
		>
			<header className="border-border border-b p-5">
				<h2 className="text-foreground text-lg font-semibold">Create agent</h2>
				<p className="text-muted-foreground mt-1 text-sm">
					Agents represent autonomous actors on the platform. New agents are created as
					active and can authenticate immediately — you pick the APIs they can reach next.
				</p>
			</header>

			<div className="flex-1 space-y-4 overflow-y-auto p-5">
				<div className="space-y-1.5">
					<Label htmlFor="agent-name">Name</Label>
					<Input
						ref={nameRef}
						id="agent-name"
						value={name}
						onChange={(e) => setName(e.target.value)}
						placeholder="e.g. inbox-triage-bot"
						error={error ?? undefined}
						maxLength={255}
					/>
				</div>
				<div className="space-y-1.5">
					<Label htmlFor="agent-description">Description</Label>
					<Textarea
						id="agent-description"
						value={description}
						onChange={(e) => setDescription(e.target.value)}
						placeholder="What does this agent do?"
						rows={3}
						maxLength={1024}
					/>
				</div>
				<InitialScopesField
					selected={scopes}
					onChange={setScopes}
					idPrefix="agent-create"
				/>
			</div>

			<footer className="border-border flex flex-wrap items-center justify-end gap-2 border-t p-5">
				<Button variant="ghost" onClick={onClose} disabled={create.isPending}>
					Cancel
				</Button>
				{/* De-emphasised, not hidden: reserving an identity ahead of the
				    credentials it will need is a real case, and the operator who
				    takes this exit lands on the agent's own screen, where the
				    same Add-APIs step is one click away. */}
				<Button
					variant="secondary"
					onClick={() => void handleSubmit('empty')}
					loading={create.isPending && intent === 'empty'}
					disabled={create.isPending && intent !== 'empty'}
				>
					Create empty
				</Button>
				<Button
					onClick={() => void handleSubmit('add-apis')}
					loading={create.isPending && intent === 'add-apis'}
					disabled={create.isPending && intent !== 'add-apis'}
				>
					Create and add APIs
				</Button>
			</footer>
		</SheetPrimitive>
	);
}
