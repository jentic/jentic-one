/**
 * What happens when an agent holds several credentials for one API. The broker
 * picks the credential per call: the `Jentic-Credential-Id` header first, then
 * `Jentic-Credential-Name`, then the most narrowly scoped credential (a version pin beats a whole API, which
 * beats a whole vendor). If that still leaves a tie, the call is refused and
 * lists the candidates. The id header is the reliable choice because
 * credential names are often identical. The tile's credentials chip carries the
 * short form; the access sidebar carries the longer one, beside the
 * credential's full, copyable id and the header that names it.
 */
import { cn } from '@/shared/lib/utils';

/** The header line that makes the broker use `credentialId` for a call. */
export function credentialIdHeader(credentialId: string): string {
	return `Jentic-Credential-Id: ${credentialId}`;
}

/** Short form, for the tile's credentials-chip tooltip. */
export function multiCredentialExplanation(apiTitle: string, count: number): string {
	return `This agent has ${count} credentials for ${apiTitle}. Unless one is scoped more narrowly, each call must name one with the Jentic-Credential-Id header; without it, the call is refused and lists the credentials to choose from. Open a tile to copy its credential's ID.`;
}

/** Long form, for the access sidebar: also says the rules follow the chosen credential. */
export function multiCredentialSidebarExplanation(apiTitle: string, count: number): string {
	return `This agent has ${count} credentials for ${apiTitle}. A narrower one (for example, pinned to a version) is used automatically; otherwise each call must name one with the Jentic-Credential-Id header — copy this credential's ID or header below — or it is refused and lists the options. These rules apply only when this credential is the one chosen.`;
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
			{multiCredentialSidebarExplanation(apiTitle, count)}
		</p>
	);
}
