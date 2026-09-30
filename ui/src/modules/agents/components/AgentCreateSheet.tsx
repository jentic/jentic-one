/**
 * AgentCreateSheet — slide-over form to create an agent manually. The draft
 * lives in `useAgentCreateForm`, above the sheet's content, so a dismissal
 * preserves it.
 */
import { SheetPrimitive } from '@/shared/ui';
import {
	AgentCreateActions,
	AgentCreateFields,
	useAgentCreateForm,
	type AgentCreateFormOptions,
} from '@/modules/agents/components/AgentCreateForm';

export function AgentCreateSheet(props: AgentCreateFormOptions) {
	const { open, onClose } = props;
	const form = useAgentCreateForm(props);

	return (
		<SheetPrimitive
			open={open}
			onClose={onClose}
			side="right"
			ariaLabel="Create agent"
			initialFocus={form.nameRef}
			className="flex flex-col"
		>
			<header className="border-border border-b p-5">
				<h2 className="text-foreground text-lg font-semibold">Create agent</h2>
			</header>

			<div className="flex-1 overflow-y-auto p-5">
				<AgentCreateFields form={form} />
			</div>

			<footer className="border-border flex flex-wrap items-center justify-end gap-2 border-t p-5">
				<AgentCreateActions form={form} onCancel={onClose} />
			</footer>
		</SheetPrimitive>
	);
}
