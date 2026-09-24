/**
 * Repository tier for the OAuth App Registrations module — a thin wrapper
 * over the generated `OAuthAppRegistrationsService` facade in `@/shared/api`.
 *
 * The wrappers exist so the module's api/hooks and any future callers can
 * import a single stable module surface (parameter names, return shapes)
 * without leaking the generator's `requestBody` / `id` boilerplate into
 * every call site. Sentinel errors live here too (see `isInUseConflict`)
 * so views can react to the 409 without instanceof-sniffing `ApiError`.
 */
import {
	ApiError,
	OAuthAppRegistrationsService,
	type AuthorizationCodeRegistrationCreateRequest,
	type DeviceAuthorizationRegistrationCreateRequest,
	type OAuthAppRegistrationFlowKind,
	type OAuthAppRegistrationResponse,
	type OAuthAppRegistrationRotateSecretRequest,
	type OAuthAppRegistrationUpdateRequest,
} from '@/shared/api';

export type OAuthAppRegistration = OAuthAppRegistrationResponse;
export type CreateRegistrationInput =
	AuthorizationCodeRegistrationCreateRequest | DeviceAuthorizationRegistrationCreateRequest;

export interface ListRegistrationsParams {
	apiVendor?: string | null;
	includeInactive?: boolean;
	flowKind?: OAuthAppRegistrationFlowKind | null;
}

/**
 * The 409 slug the backend emits when a delete is refused because credentials
 * still reference the registration. Kept as a module constant so callers
 * derive the "some credentials still use this" branch from a single fact.
 */
export const IN_USE_CONFLICT_CODE = 'oauth_app_registration_in_use';

/**
 * `true` when a caught error is the "credentials still reference this
 * registration" 409 (delete refused). Views use this to swap the generic
 * error banner for the "revoke or deactivate first" guidance.
 */
export function isInUseConflict(error: unknown): error is ApiError {
	if (!(error instanceof ApiError) || error.status !== 409) return false;
	const body = error.body as { error?: string; code?: string } | null | undefined;
	return body?.error === IN_USE_CONFLICT_CODE || body?.code === IN_USE_CONFLICT_CODE;
}

export function listRegistrations(
	params: ListRegistrationsParams = {},
): Promise<OAuthAppRegistrationResponse> {
	// Return shape kept broad — the generator returns the envelope for list()
	// but our repository unwraps it to the .data array for callers. Keeping
	// this file simple: expose the raw call and let hooks unwrap.
	return OAuthAppRegistrationsService.listOauthAppRegistrations({
		apiVendor: params.apiVendor ?? undefined,
		includeInactive: params.includeInactive ?? false,
		flowKind: params.flowKind ?? undefined,
	}).then((r) => r as unknown as OAuthAppRegistrationResponse);
}

export function fetchRegistrations(
	params: ListRegistrationsParams = {},
): Promise<OAuthAppRegistrationResponse[]> {
	return OAuthAppRegistrationsService.listOauthAppRegistrations({
		apiVendor: params.apiVendor ?? undefined,
		includeInactive: params.includeInactive ?? false,
		flowKind: params.flowKind ?? undefined,
	}).then((r) => r.data);
}

export function getRegistration(id: string): Promise<OAuthAppRegistrationResponse> {
	return OAuthAppRegistrationsService.getOauthAppRegistration({ id });
}

export function createRegistration(
	input: CreateRegistrationInput,
): Promise<OAuthAppRegistrationResponse> {
	return OAuthAppRegistrationsService.createOauthAppRegistration({ requestBody: input });
}

export function updateRegistration(
	id: string,
	input: OAuthAppRegistrationUpdateRequest,
): Promise<OAuthAppRegistrationResponse> {
	return OAuthAppRegistrationsService.updateOauthAppRegistration({
		id,
		requestBody: input,
	});
}

export function rotateRegistrationSecret(
	id: string,
	input: OAuthAppRegistrationRotateSecretRequest,
): Promise<OAuthAppRegistrationResponse> {
	return OAuthAppRegistrationsService.rotateOauthAppRegistrationSecret({
		id,
		requestBody: input,
	});
}

export function deleteRegistration(id: string): Promise<void> {
	return OAuthAppRegistrationsService.deleteOauthAppRegistration({ id });
}
