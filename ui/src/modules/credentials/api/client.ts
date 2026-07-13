// Credentials data layer (≈ backend repository). The ONLY place in this module
// that touches the generated services, and it does so through the `@/shared/api`
// facade so the Bearer-JWT client config is always applied. No React, no UI.
//
// Each function narrows the generated contract to a typed envelope and forwards
// the typed request/response shapes from `./types`. Hooks (`./index.ts`) are the
// only callers.
import { CredentialsService } from '@/shared/api';
import type { ProviderDiscoveryResponse } from '@/shared/api';
import type {
	ConnectChallengeResponse,
	ConnectRequestBody,
	CredentialCreateRequest,
	CredentialCreateResponse,
	CredentialListResponse,
	CredentialRedactedResponse,
	CredentialUpdateRequest,
} from './types';

export interface ListCredentialsParams {
	cursor?: string | null;
	limit?: number;
	vendor?: string | null;
}

/** GET /credentials — cursor-paginated, optional vendor filter. */
export function listCredentials(
	params: ListCredentialsParams = {},
): Promise<CredentialListResponse> {
	return CredentialsService.listCredentials({
		cursor: params.cursor ?? undefined,
		limit: params.limit,
		vendor: params.vendor ?? undefined,
	});
}

/** GET /credentials/{id} — redacted secrets. */
export function getCredential(credentialId: string): Promise<CredentialRedactedResponse> {
	return CredentialsService.getCredential({ credentialId });
}

/** POST /credentials — secret is returned ONCE in the response. */
export function createCredential(body: CredentialCreateRequest): Promise<CredentialCreateResponse> {
	return CredentialsService.createCredential({ requestBody: body });
}

/** PATCH /credentials/{id} — update metadata or rotate the secret. */
export function updateCredential(
	credentialId: string,
	body: CredentialUpdateRequest,
): Promise<CredentialRedactedResponse> {
	return CredentialsService.updateCredential({
		credentialId,
		requestBody: body,
	});
}

/** DELETE /credentials/{id}. */
export function deleteCredential(credentialId: string): Promise<void> {
	return CredentialsService.deleteCredential({ credentialId });
}

/** POST /credentials/{id}/connect — begin the OAuth redirect flow. */
export function connectCredential(
	credentialId: string,
	body: ConnectRequestBody = {},
): Promise<ConnectChallengeResponse> {
	return CredentialsService.connectCredential({
		credentialId,
		requestBody: body,
	});
}

/** GET /credentials/providers — discovery metadata for configured providers. */
export function getProviders(): Promise<ProviderDiscoveryResponse> {
	return CredentialsService.listProviders();
}
