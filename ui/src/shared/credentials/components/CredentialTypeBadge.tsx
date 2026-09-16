import { Badge } from '@/shared/ui';
import {
	CredentialType,
	CREDENTIAL_TYPE_LABELS,
	credentialDetails,
	type Credential,
} from '@/shared/credentials/api';

const VARIANT: Record<CredentialType, 'default' | 'success' | 'warning' | 'pending'> = {
	[CredentialType.BEARER_TOKEN]: 'default',
	[CredentialType.API_KEY]: 'success',
	[CredentialType.BASIC]: 'warning',
	[CredentialType.OAUTH2]: 'pending',
	[CredentialType.NO_AUTH]: 'default',
	[CredentialType.SIGV4]: 'success',
};

const OAUTH_GRANT_LABEL: Record<string, string> = {
	authorization_code: 'Authorization Code',
	client_credentials: 'Client Credentials',
	device_code: 'Device Code',
};

/**
 * Small pill labelling a credential's auth type with a stable colour. For
 * OAuth 2.0 credentials, appends the grant variant (Device Code / Auth Code
 * / Client Credentials) so the picker/list disambiguates them at a glance.
 */
export function CredentialTypeBadge({ credential }: { credential: Credential }) {
	const { type } = credential;
	let label = CREDENTIAL_TYPE_LABELS[type] ?? type;
	if (type === CredentialType.OAUTH2) {
		const grant = credentialDetails(credential).grant_type;
		const suffix = grant ? OAUTH_GRANT_LABEL[grant] : undefined;
		if (suffix) label = `${label} · ${suffix}`;
	}
	return <Badge variant={VARIANT[type] ?? 'default'}>{label}</Badge>;
}
