import { Badge } from '@/shared/ui';
import { CredentialType, CREDENTIAL_TYPE_LABELS } from '@/modules/credentials/api';

const VARIANT: Record<CredentialType, 'default' | 'success' | 'warning' | 'pending'> = {
	[CredentialType.BEARER_TOKEN]: 'default',
	[CredentialType.API_KEY]: 'success',
	[CredentialType.BASIC]: 'warning',
	[CredentialType.OAUTH2]: 'pending',
};

/** Small pill that labels a credential's auth type with a stable color. */
export function CredentialTypeBadge({ type }: { type: CredentialType }) {
	return (
		<Badge variant={VARIANT[type] ?? 'default'}>{CREDENTIAL_TYPE_LABELS[type] ?? type}</Badge>
	);
}
