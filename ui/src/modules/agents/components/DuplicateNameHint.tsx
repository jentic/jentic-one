import { duplicateAgentNameHint } from '@/modules/agents/lib/agentName';

/**
 * The muted note under an agent-name field whose name another agent already
 * has. Advisory only: the backend accepts the duplicate, so nothing is blocked.
 */
export function DuplicateNameHint({ id, existing }: { id: string; existing: string }) {
	return (
		<p
			id={id}
			data-testid="agent-name-duplicate"
			className="text-muted-foreground mt-1 text-xs"
		>
			{duplicateAgentNameHint(existing)}
		</p>
	);
}
