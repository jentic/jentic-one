/**
 * What happens when an agent holds several credentials for one API. The broker
 * cannot guess which account a call means, so the agent names one with the
 * `Jentic-Credential-Id` header; a call without it gets the accounts back to pick
 * from. The tile's accounts badge and the access sidebar share this copy.
 */
import { cn } from '@/shared/lib/utils';

export function multiAccountExplanation(
	agentName: string,
	apiTitle: string,
	count: number,
): string {
	return `${agentName} has ${count} accounts for ${apiTitle}. It chooses one per call with the Jentic-Credential-Id header; without it, calls return the accounts to pick from.`;
}

/** The explanation as one quiet line, for the access sidebar. */
export function MultiAccountNote({
	agentName,
	apiTitle,
	count,
	className,
}: {
	agentName: string;
	apiTitle: string;
	count: number;
	className?: string;
}) {
	return (
		<p
			data-testid="multi-account-note"
			className={cn('text-muted-foreground text-xs leading-relaxed', className)}
		>
			{multiAccountExplanation(agentName, apiTitle, count)}
		</p>
	);
}
