/**
 * What happens when an agent holds several credentials for one API. The broker
 * cannot guess which credential a call means, so the agent names one with the
 * `Jentic-Credential-Id` header; a call without it gets the credentials back to
 * pick from. The tile's credentials chip and the access sidebar share this copy.
 */
import { cn } from '@/shared/lib/utils';

export function multiCredentialExplanation(apiTitle: string, count: number): string {
	return `This agent has ${count} credentials for ${apiTitle}. It chooses one per call with the Jentic-Credential-Id header; without it, calls return the credentials to pick from.`;
}

/** The explanation as one quiet line, for the access sidebar. */
export function MultiCredentialNote({
	apiTitle,
	count,
	className,
}: {
	apiTitle: string;
	count: number;
	className?: string;
}) {
	return (
		<p
			data-testid="multi-account-note"
			className={cn('text-muted-foreground text-xs leading-relaxed', className)}
		>
			{multiCredentialExplanation(apiTitle, count)}
		</p>
	);
}
