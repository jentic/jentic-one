/**
 * AgentCreateSheet — slide-over form to create an agent manually.
 *
 * Mirrors ServiceAccountCreateSheet. A Sheet (not a Dialog) keeps the list
 * context visible while filling the form. Fields reset
 * only after a successful create; a dismissal preserves the draft.
 */
import { useEffect, useRef, useState } from 'react';
import { Button, Input, Label, Textarea, SheetPrimitive } from '@/shared/ui';
import { useCreateAgent } from '@/modules/agents/api';

interface AgentCreateSheetProps {
	open: boolean;
	onClose: () => void;
}

export function AgentCreateSheet({ open, onClose }: AgentCreateSheetProps) {
	const [name, setName] = useState('');
	const [description, setDescription] = useState('');
	const [error, setError] = useState<string | null>(null);
	const nameRef = useRef<HTMLInputElement>(null);
	const create = useCreateAgent();

	useEffect(() => {
		if (open) setError(null);
	}, [open]);

	async function handleSubmit() {
		const trimmed = name.trim();
		if (!trimmed) {
			setError('A name is required.');
			return;
		}
		try {
			await create.mutateAsync({
				name: trimmed,
				description: description.trim() || null,
			});
			setName('');
			setDescription('');
			setError(null);
			onClose();
		} catch {
			// hook surfaces a toast; keep the draft so the user can retry.
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
					active and can authenticate immediately.
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
			</div>

			<footer className="border-border flex items-center justify-end gap-2 border-t p-5">
				<Button variant="secondary" onClick={onClose} disabled={create.isPending}>
					Cancel
				</Button>
				<Button onClick={handleSubmit} loading={create.isPending}>
					Create
				</Button>
			</footer>
		</SheetPrimitive>
	);
}
