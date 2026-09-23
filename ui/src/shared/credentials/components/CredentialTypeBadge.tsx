import { Cloud, KeyRound, LockOpen, LogIn, Ticket, UserRound, type LucideIcon } from 'lucide-react';
import { Tag } from '@/shared/ui';
import { CredentialType, CREDENTIAL_TYPE_LABELS } from '@/shared/credentials/api';

const ICON: Record<CredentialType, LucideIcon> = {
	[CredentialType.BEARER_TOKEN]: Ticket,
	[CredentialType.API_KEY]: KeyRound,
	[CredentialType.BASIC]: UserRound,
	[CredentialType.OAUTH2]: LogIn,
	[CredentialType.NO_AUTH]: LockOpen,
	[CredentialType.SIGV4]: Cloud,
};

/** Neutral tag that names a credential's auth type; the icon tells the types apart. */
export function CredentialTypeBadge({ type }: { type: CredentialType }) {
	return <Tag icon={ICON[type]}>{CREDENTIAL_TYPE_LABELS[type] ?? type}</Tag>;
}
