/**
 * OAuth clients API client — wraps the admin /oauth-clients endpoints.
 * TODO: migrate to generated DefaultService (endpoints now in OpenAPI spec).
 */
import { getToken } from '@/shared/api';

export interface OAuthClient {
	id: string;
	client_id: string;
	name: string;
	description: string | null;
	redirect_uris: string[];
	active: boolean;
	require_consent: boolean;
	created_at: string;
	updated_at: string | null;
	created_by: string | null;
}

export interface OAuthClientCreateInput {
	name: string;
	description?: string;
	redirect_uris: string[];
	require_consent?: boolean;
}

export interface OAuthClientUpdateInput {
	name?: string;
	description?: string;
	redirect_uris?: string[];
	active?: boolean;
	require_consent?: boolean;
}

export interface OAuthClientCreateResponse extends OAuthClient {
	client_secret: string;
}

export interface OAuthClientRotateSecretResponse {
	client_secret: string;
}

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
	const token = getToken();
	const res = await fetch(path, {
		...init,
		headers: {
			...init?.headers,
			'Content-Type': 'application/json',
			...(token ? { Authorization: `Bearer ${token}` } : {}),
		},
	});
	if (!res.ok) {
		const body = (await res.json().catch(() => ({}))) as { detail?: string };
		throw new Error(body.detail ?? `API error: ${res.status}`);
	}
	if (res.status === 204) return undefined as T;
	return res.json() as Promise<T>;
}

export async function listOAuthClients(includeInactive = false): Promise<OAuthClient[]> {
	const params = new URLSearchParams();
	if (includeInactive) params.set('include_inactive', 'true');
	const res = await apiFetch<{ data: OAuthClient[] }>(
		`/admin/oauth-clients${params.toString() ? `?${params}` : ''}`,
	);
	return res.data;
}

export async function getOAuthClient(id: string): Promise<OAuthClient> {
	return apiFetch<OAuthClient>(`/admin/oauth-clients/${encodeURIComponent(id)}`);
}

export async function createOAuthClient(
	input: OAuthClientCreateInput,
): Promise<OAuthClientCreateResponse> {
	return apiFetch<OAuthClientCreateResponse>('/admin/oauth-clients', {
		method: 'POST',
		body: JSON.stringify(input),
	});
}

export async function updateOAuthClient(
	id: string,
	input: OAuthClientUpdateInput,
): Promise<OAuthClient> {
	return apiFetch<OAuthClient>(`/admin/oauth-clients/${encodeURIComponent(id)}`, {
		method: 'PATCH',
		body: JSON.stringify(input),
	});
}

export async function deactivateOAuthClient(id: string): Promise<void> {
	await apiFetch<void>(`/admin/oauth-clients/${encodeURIComponent(id)}`, {
		method: 'DELETE',
	});
}

export async function rotateOAuthClientSecret(
	id: string,
): Promise<OAuthClientRotateSecretResponse> {
	return apiFetch<OAuthClientRotateSecretResponse>(
		`/admin/oauth-clients/${encodeURIComponent(id)}/rotate-secret`,
		{ method: 'POST' },
	);
}
