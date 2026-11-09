/**
 * Approvals repository tier.
 *
 * The ONLY place in the Approvals surfaces that talks to `@/shared/api` (the
 * HTTP facade). Views and hooks never import the facade directly — ESLint
 * enforces this. Mirrors the backend Repository layer: thin wrappers that turn
 * typed service calls into UI-shaped data and normalize errors into a single
 * sentinel.
 *
 * Response-code contract (the held-executions surface):
 *   GET  /executions/holds/{id}         → 200 + HeldExecutionDetailResponse
 *   POST /executions/holds/{id}:decide  → 200 + HeldExecutionResponse
 *   404 on detail/decide means the held execution is outside the caller's
 *   visibility (the filing agent, the agent's owner, or an org admin) — or it
 *   does not exist. 403 `held_execution_decide_forbidden` on decide means the
 *   caller sees it but may not decide it (only the owner or an org admin may).
 *   409 `held_execution_already_settled` means it is no longer pending.
 *
 * The review pop-up also reads the org's admins (`GET /users`, which only an
 * org admin may list in full) to say how many people can decide a call.
 */
import {
	ApiError,
	HeldExecutionsService,
	UsersService,
	type DecideHeldExecutionRequest,
	type HeldExecutionDetailResponse,
	type HeldExecutionResponse,
} from '@/shared/api';
import { ORG_ADMIN } from '@/shared/auth/usePermission';
import type { HeldCallDetail } from '@/shared/approvals/heldCall';

// Re-export types so module pages never need to reach into @/shared/api directly.
export type { DecideHeldExecutionRequest, HeldExecutionDetailResponse, HeldExecutionResponse };

/** The held-executions problem `type`s the Approvals surfaces tell apart. */
export const HELD_EXECUTION_ERRORS = {
	notFound: 'held_execution_not_found',
	alreadySettled: 'held_execution_already_settled',
	notHeld: 'held_execution_not_held',
	decideForbidden: 'held_execution_decide_forbidden',
} as const;

/** Sentinel error for Approvals repository calls. */
export class ApprovalsApiError extends Error {
	readonly status: number | null;
	/** The problem body's `type` (e.g. `held_execution_decide_forbidden`), when it has one. */
	readonly code: string | null;
	readonly cause?: unknown;

	constructor(message: string, status: number | null, cause?: unknown, code?: string | null) {
		super(message);
		this.name = 'ApprovalsApiError';
		this.status = status;
		this.code = code ?? null;
		this.cause = cause;
	}

	/** Whether the problem `type` is `code`, bare or as the last segment of a type URI. */
	is(code: string): boolean {
		return this.code === code || (this.code?.endsWith(`/${code}`) ?? false);
	}
}

/** A decision that lost the race: the call was decided, expired or withdrawn first. */
export function isAlreadySettled(error: unknown): boolean {
	return (
		error instanceof ApprovalsApiError &&
		(error.status === 409 || error.is(HELD_EXECUTION_ERRORS.alreadySettled))
	);
}

/** The server let the viewer see the call but refused their decision. */
export function isDecideForbidden(error: unknown): boolean {
	return (
		error instanceof ApprovalsApiError &&
		(error.status === 403 || error.is(HELD_EXECUTION_ERRORS.decideForbidden))
	);
}

/** Status lines a server sends when it has nothing more to say; the UI says it better. */
const BARE_STATUS_TEXT =
	/^(not found|internal server error|bad gateway|service unavailable|gateway timeout|bad request|unprocessable entity|forbidden|conflict|unauthorized)\.?$/i;

/** The server's own detail when it says something, else a plain line for the status. */
function messageFor(status: number | null, detail: string | null, fallback: string): string {
	if (detail && !BARE_STATUS_TEXT.test(detail.trim())) return detail;
	if (status === 404) return "This call wasn't found.";
	if (status != null && status >= 500)
		return "The server couldn't handle this just now. Try again.";
	return fallback;
}

function toApprovalsError(error: unknown, fallback: string): ApprovalsApiError {
	if (error instanceof ApprovalsApiError) return error;
	if (error instanceof ApiError) {
		const body = error.body as { detail?: unknown; type?: unknown } | undefined;
		const detail = typeof body?.detail === 'string' ? body.detail : null;
		const code = typeof body?.type === 'string' ? body.type : null;
		return new ApprovalsApiError(
			messageFor(error.status, detail, fallback),
			error.status,
			error,
			code,
		);
	}
	// `fetch` rejects with a TypeError when the server can't be reached at all.
	if (error instanceof TypeError) {
		return new ApprovalsApiError(
			"Couldn't reach the server. Check your connection and try again.",
			null,
			error,
		);
	}
	if (error instanceof Error) {
		return new ApprovalsApiError(error.message || fallback, null, error);
	}
	return new ApprovalsApiError(fallback, null, error);
}

const str = (value: unknown): string => (typeof value === 'string' ? value : '');

/**
 * A held call as the surfaces can show it: the method one upper-case token,
 * every name a string. The server sends them so; a row that doesn't is made
 * safe here rather than crashing a surface.
 */
export function normaliseHeldCall<T extends HeldExecutionResponse>(row: T): T {
	return {
		...row,
		method: str(row.method).trim().toUpperCase(),
		path: str(row.path),
		agent_id: str(row.agent_id),
		api_vendor: str(row.api_vendor),
		api_name: str(row.api_name),
		api_version: str(row.api_version),
	};
}

/** The detail response as a held call, or an error when it is some other shape. */
function asHeldCallDetail(value: unknown): HeldCallDetail {
	const row = value as Partial<HeldCallDetail> | null;
	if (
		row == null ||
		typeof row !== 'object' ||
		Array.isArray(row) ||
		typeof row.id !== 'string' ||
		typeof row.state !== 'string' ||
		typeof row.method !== 'string'
	) {
		throw new ApprovalsApiError("The server didn't return a held call.", null, value);
	}
	const detail = normaliseHeldCall(row as HeldCallDetail);
	const held = detail.request;
	const request =
		held != null && typeof held === 'object' && typeof held.url === 'string'
			? {
					...held,
					method: str(held.method).trim().toUpperCase() || detail.method,
					body:
						held.body == null
							? null
							: typeof held.body === 'string'
								? held.body
								: JSON.stringify(held.body, null, 2),
				}
			: null;
	return {
		...detail,
		request,
		agent_reason: typeof detail.agent_reason === 'string' ? detail.agent_reason : null,
		_links:
			detail._links != null && typeof detail._links === 'object'
				? detail._links
				: { self: '' },
	};
}

export async function getApproval(heldExecutionId: string): Promise<HeldCallDetail> {
	try {
		return asHeldCallDetail(await HeldExecutionsService.getHeldExecution({ heldExecutionId }));
	} catch (error) {
		throw toApprovalsError(error, 'Failed to load approval');
	}
}

export async function decideApproval(
	heldExecutionId: string,
	body: DecideHeldExecutionRequest,
): Promise<HeldExecutionResponse> {
	try {
		return await HeldExecutionsService.decideHeldExecution({
			heldExecutionId,
			requestBody: body,
		});
	} catch (error) {
		throw toApprovalsError(error, 'Failed to submit decision');
	}
}

/** An org admin who can decide any held call. */
export interface OrgAdmin {
	id: string;
	name: string;
}

export interface OrgAdmins {
	admins: OrgAdmin[];
	/** False when the org has more users than were read: the list is a floor. */
	complete: boolean;
}

type UserRow = Awaited<ReturnType<typeof UsersService.listUsers>>['data'][number];

/** The list endpoint's largest page. */
const USERS_PAGE = 200;
/** Pages read before the admin list counts as a floor. */
const USERS_MAX_PAGES = 5;

/**
 * An enabled user holding `org:admin` who has signed in at least once: an
 * invite nobody redeemed yet can't decide anything.
 */
function canDecideAsAdmin(user: UserRow): boolean {
	if (!user.active || user.invite_state === 'pending' || user.invite_state === 'expired') {
		return false;
	}
	return (
		user.permissions.assigned.includes(ORG_ADMIN) ||
		user.permissions.effective.some((permission) => permission.name === ORG_ADMIN)
	);
}

/** Every org admin who can decide a held call. Org admins only: anyone else sees themselves. */
export async function listOrgAdmins(): Promise<OrgAdmins> {
	const admins: OrgAdmin[] = [];
	let cursor: string | null = null;
	try {
		for (let page = 0; page < USERS_MAX_PAGES; page++) {
			const res = await UsersService.listUsers({ cursor, limit: USERS_PAGE });
			for (const user of res.data) {
				if (canDecideAsAdmin(user)) admins.push({ id: user.id, name: user.name });
			}
			if (!res.has_more || !res.next_cursor) return { admins, complete: true };
			cursor = res.next_cursor;
		}
	} catch (error) {
		throw toApprovalsError(error, 'Failed to list org admins');
	}
	return { admins, complete: false };
}
