/**
 * Hand-maintained shim for the REMOVED service-account API (theme-8 Phase 2).
 *
 * The backend deleted every `/service-accounts…` route, so the regenerated
 * client no longer emits `ServiceAccountsService` or its models. The agents
 * module's service-account tab still compiles against them until theme-8
 * Phase 3 deletes that UI; this shim keeps the old call shapes (every call now
 * answers 404 from a Phase-2 backend) so the removal stays a single,
 * UI-owned change. Delete this file with the Phase-3 UI removal.
 */
import { OpenAPI } from '@/shared/api/generated/core/OpenAPI';
import { request } from '@/shared/api/generated/core/request';
import type { CancelablePromise } from '@/shared/api/generated/core/CancelablePromise';
import type { ApiKeyResponse } from '@/shared/api/generated/models/ApiKeyResponse';

export type ServiceAccountResponse = {
	approved_at?: string | null;
	approved_by?: string | null;
	created_at: string;
	denial_reason?: string | null;
	denied_by?: string | null;
	description?: string | null;
	id: string;
	name: string;
	owner_id: string;
	registered_by: string;
	status: string;
};

export type ServiceAccountListResponse = {
	data: Array<ServiceAccountResponse>;
	has_more: boolean;
	next_cursor?: string | null;
};

export type ServiceAccountCreateRequest = {
	description?: string | null;
	name: string;
	scopes?: Array<string> | null;
};

export type ServiceAccountScopesRequest = { scopes: Array<string> };
export type ServiceAccountScopesResponse = { scopes: Array<string> };
export type ServiceAccountDenyRequest = { reason: string };

type ById = { serviceAccountId: string };

const byId = (serviceAccountId: string) => ({ service_account_id: serviceAccountId });

export class ServiceAccountsService {
	public static listServiceAccounts({
		cursor,
		limit = 50,
		status,
	}: {
		cursor?: string | null;
		limit?: number;
		status?: string | null;
	}): CancelablePromise<ServiceAccountListResponse> {
		return request(OpenAPI, {
			method: 'GET',
			url: '/service-accounts',
			query: { cursor, limit, status },
		});
	}

	public static createServiceAccount({
		requestBody,
	}: {
		requestBody: ServiceAccountCreateRequest;
	}): CancelablePromise<ServiceAccountResponse> {
		return request(OpenAPI, {
			method: 'POST',
			url: '/service-accounts',
			body: requestBody,
			mediaType: 'application/json',
		});
	}

	public static archiveServiceAccount({ serviceAccountId }: ById): CancelablePromise<void> {
		return request(OpenAPI, {
			method: 'DELETE',
			url: '/service-accounts/{service_account_id}',
			path: byId(serviceAccountId),
		});
	}

	public static getServiceAccount({
		serviceAccountId,
	}: ById): CancelablePromise<ServiceAccountResponse> {
		return request(OpenAPI, {
			method: 'GET',
			url: '/service-accounts/{service_account_id}',
			path: byId(serviceAccountId),
		});
	}

	public static getServiceAccountScopes({
		serviceAccountId,
	}: ById): CancelablePromise<ServiceAccountScopesResponse> {
		return request(OpenAPI, {
			method: 'GET',
			url: '/service-accounts/{service_account_id}/scopes',
			path: byId(serviceAccountId),
		});
	}

	public static replaceServiceAccountScopes({
		serviceAccountId,
		requestBody,
	}: ById & {
		requestBody: ServiceAccountScopesRequest;
	}): CancelablePromise<ServiceAccountScopesResponse> {
		return request(OpenAPI, {
			method: 'PUT',
			url: '/service-accounts/{service_account_id}/scopes',
			path: byId(serviceAccountId),
			body: requestBody,
			mediaType: 'application/json',
		});
	}

	public static approveServiceAccount({
		serviceAccountId,
	}: ById): CancelablePromise<ServiceAccountResponse> {
		return request(OpenAPI, {
			method: 'POST',
			url: '/service-accounts/{service_account_id}:approve',
			path: byId(serviceAccountId),
		});
	}

	public static denyServiceAccount({
		serviceAccountId,
		requestBody,
	}: ById & {
		requestBody: ServiceAccountDenyRequest;
	}): CancelablePromise<ServiceAccountResponse> {
		return request(OpenAPI, {
			method: 'POST',
			url: '/service-accounts/{service_account_id}:deny',
			path: byId(serviceAccountId),
			body: requestBody,
			mediaType: 'application/json',
		});
	}

	public static disableServiceAccount({ serviceAccountId }: ById): CancelablePromise<void> {
		return request(OpenAPI, {
			method: 'POST',
			url: '/service-accounts/{service_account_id}:disable',
			path: byId(serviceAccountId),
		});
	}

	public static enableServiceAccount({ serviceAccountId }: ById): CancelablePromise<void> {
		return request(OpenAPI, {
			method: 'POST',
			url: '/service-accounts/{service_account_id}:enable',
			path: byId(serviceAccountId),
		});
	}

	public static generateServiceAccountApiKey({
		serviceAccountId,
	}: ById): CancelablePromise<ApiKeyResponse> {
		return request(OpenAPI, {
			method: 'POST',
			url: '/service-accounts/{service_account_id}:generate-api-key',
			path: byId(serviceAccountId),
		});
	}
}
