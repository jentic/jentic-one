/**
 * Workspace repository tier.
 *
 * The ONLY place in the Workspace module that talks to `@/shared/api` (the
 * Bearer-JWT HTTP facade). Views and hooks never import the facade directly —
 * ESLint enforces this (see ui/eslint.config.js "Layering"). Mirrors the
 * backend's Repository layer: thin wrappers that turn generated service calls
 * into UI-shaped data and normalize errors into one sentinel type.
 *
 * Builds against the regenerated per-tag client: `/apis` CRUD lives on
 * `ApIsService`, spec downloads on `ApiSpecService`, operation listing on
 * `ApiOperationsService`, and `GET /jobs/{id}` on `JobsService` (the coarse
 * `AdminService`/`ApisService` were split by the codegen retag). Several list
 * methods are still typed `any`, so the adapters cast them into the module's
 * typed envelopes.
 */
import {
	JobsService,
	ApiError,
	ApIsService,
	ApiSpecService,
	ApiOperationsService,
} from '@/shared/api';
import {
	toApiOperation,
	toApiRevision,
	toCursorPage,
	toWorkspaceApi,
} from '@/modules/workspace/api/adapters';
import type { ApiKey } from '@/modules/workspace/api/apiId';
import type {
	ApiOperation,
	ApiRevision,
	CursorPage,
	ImportJob,
	ImportSource,
	JobStatus,
	WorkspaceApi,
} from '@/modules/workspace/api/types';

/**
 * Sentinel error for Workspace repository calls. Hooks/components branch on
 * `error instanceof WorkspaceApiError` without importing the generated
 * `ApiError` (which lives behind the facade). `status` is null for
 * network/parse failures that never reached the server.
 */
export class WorkspaceApiError extends Error {
	readonly status: number | null;
	/** The backend's ProblemDetail `type`, when present (e.g. `no_current_revision`). */
	readonly problemType: string | null;
	readonly cause?: unknown;

	constructor(
		message: string,
		status: number | null,
		problemType: string | null,
		cause?: unknown,
	) {
		super(message);
		this.name = 'WorkspaceApiError';
		this.status = status;
		this.problemType = problemType;
		this.cause = cause;
	}

	/** True when the API has no live revision (draft-only) — a first-class UX state. */
	get isNoCurrentRevision(): boolean {
		return this.status === 404 && this.problemType === 'no_current_revision';
	}
}

function toWorkspaceError(error: unknown, fallback: string): WorkspaceApiError {
	if (error instanceof ApiError) {
		const body = error.body as { detail?: string; type?: string } | undefined;
		return new WorkspaceApiError(
			body?.detail || error.message || fallback,
			error.status,
			body?.type ?? null,
			error,
		);
	}
	if (error instanceof Error) {
		return new WorkspaceApiError(error.message || fallback, null, null, error);
	}
	return new WorkspaceApiError(fallback, null, null, error);
}

/**
 * List the workspace's APIs via `GET /apis`.
 *
 * jentic-one's registry is local-ingest only on this branch's committed
 * contract (no `source` param yet), so a plain list IS the owned lens. When the
 * catalog rebase lands, this call can gain `source=local`; until then the
 * server returns local rows and `WorkspaceApi.source` is simply `undefined`.
 */
export async function listApis(
	params: {
		cursor?: string | null;
		limit?: number;
	} = {},
): Promise<CursorPage<WorkspaceApi>> {
	try {
		const res = await ApIsService.listApis({
			cursor: params.cursor ?? null,
			limit: params.limit ?? 50,
		});
		return toCursorPage(res, toWorkspaceApi);
	} catch (error) {
		throw toWorkspaceError(error, 'Failed to load your APIs.');
	}
}

/** Fetch a single API by its triple. */
export async function getApi(key: ApiKey): Promise<WorkspaceApi> {
	try {
		const res = await ApIsService.getApi(key);
		return toWorkspaceApi(res);
	} catch (error) {
		throw toWorkspaceError(error, 'Failed to load this API.');
	}
}

/**
 * Fetch the resolved OpenAPI document for an API's live revision
 * (`GET /apis/{v}/{n}/{ver}/openapi`, overlays applied). Returns the raw
 * parsed spec object so the viewer can pretty-print / download it; the
 * generated method is typed `any` on this branch.
 */
export async function getApiSpec(key: ApiKey): Promise<unknown> {
	try {
		return await ApiSpecService.getApiSpec({ ...key });
	} catch (error) {
		throw toWorkspaceError(error, 'Failed to load the API spec.');
	}
}

/**
 * Fetch the resolved OpenAPI document for a *specific* revision
 * (`GET /apis/{…}/revisions/{revision_id}/openapi`). Unlike {@link getApiSpec}
 * (which only ever returns the live revision) this works for any revision —
 * archived/old or draft/pending — so the viewer can show specs that aren't
 * currently promoted.
 */
export async function getRevisionSpec(key: ApiKey, revisionId: string): Promise<unknown> {
	try {
		return await ApiSpecService.getApiRevisionSpec({ ...key, revisionId });
	} catch (error) {
		throw toWorkspaceError(error, 'Failed to load the revision spec.');
	}
}

/**
 * List operations for an API's current (live) revision. A draft-only API has
 * no live revision → the backend returns 404 `no_current_revision`, which we
 * surface as a typed sentinel so the UI can show "promote a revision" rather
 * than a generic error.
 */
export async function listOperations(params: {
	key: ApiKey;
	cursor?: string | null;
	limit?: number;
}): Promise<CursorPage<ApiOperation>> {
	try {
		const res = await ApiOperationsService.listApiOperations({
			...params.key,
			cursor: params.cursor ?? null,
			limit: params.limit ?? 25,
		});
		return toCursorPage(res, toApiOperation);
	} catch (error) {
		throw toWorkspaceError(error, 'Failed to load operations.');
	}
}

/** List revisions for an API (newest first, as the backend orders them). */
export async function listRevisions(params: {
	key: ApiKey;
	cursor?: string | null;
	limit?: number;
}): Promise<CursorPage<ApiRevision>> {
	try {
		const res = await ApIsService.listApiRevisions({
			...params.key,
			cursor: params.cursor ?? null,
			limit: params.limit ?? 50,
		});
		return toCursorPage(res, toApiRevision);
	} catch (error) {
		throw toWorkspaceError(error, 'Failed to load revisions.');
	}
}

/** Promote a draft revision to live (archives the current one). */
export async function promoteRevision(key: ApiKey, revisionId: string): Promise<void> {
	try {
		await ApIsService.promoteRevision({
			...key,
			revisionId,
		});
	} catch (error) {
		throw toWorkspaceError(error, 'Failed to promote the revision.');
	}
}

/** Archive a draft revision. */
export async function archiveRevision(key: ApiKey, revisionId: string): Promise<void> {
	try {
		await ApIsService.archiveRevision({
			...key,
			revisionId,
		});
	} catch (error) {
		throw toWorkspaceError(error, 'Failed to archive the revision.');
	}
}

/**
 * Hard-delete an API and every revision under it (`DELETE
 * /apis/{vendor}/{name}/{version}`). The backend cascades to operations and
 * release pointers; irreversible — gate UI behind `CascadeDeleteDialog`.
 */
export async function deleteApi(key: ApiKey): Promise<void> {
	try {
		await ApIsService.deleteApi(key);
	} catch (error) {
		throw toWorkspaceError(error, 'Failed to remove the API.');
	}
}

/**
 * Enqueue an import of one or more spec sources via `POST /apis`.
 *
 * Async: the backend resolves + ingests server-side and returns 202 with a job
 * id. The caller polls `getJob` until terminal. Maps the UI `ImportSource`
 * union onto the generated `ApiSourceUrl | ApiSourceInline` wire shapes.
 */
export async function importSources(sources: ImportSource[]): Promise<ImportJob> {
	try {
		const res = await ApIsService.importApis({
			requestBody: {
				sources: sources.map((s) =>
					s.type === 'url'
						? {
								type: 'url',
								url: s.url,
								vendor: s.vendor ?? null,
								api_name: s.apiName ?? null,
								version: s.version ?? null,
							}
						: { type: 'inline', content: s.content, filename: s.filename },
				),
			},
		});
		const body = (res ?? {}) as { job_id?: string; status?: string };
		return { jobId: String(body.job_id ?? ''), status: String(body.status ?? 'queued') };
	} catch (error) {
		throw toWorkspaceError(error, 'Failed to start the import.');
	}
}

/** Poll an import job's status via `GET /jobs/{id}` (tagged `admin`). */
export async function getJob(jobId: string): Promise<JobStatus> {
	try {
		const res = await JobsService.getJob({ jobId });
		return {
			jobId: res.job_id,
			status: res.status,
			error: res.error ?? null,
		};
	} catch (error) {
		throw toWorkspaceError(error, 'Failed to read the import job.');
	}
}
