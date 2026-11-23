/**
 * What every surface says about one held call: its headline (the operation's
 * summary from the API's spec), whether it deletes data, and the agent's own
 * reason for asking. The review pop-up, the waiting card, the inbox rail, the
 * bell and the attention toast all read these, so a call is named the same
 * way everywhere.
 */
import { useQueries } from '@tanstack/react-query';
import {
	ApiOperationsService,
	type HeldExecutionDetailResponse,
	type HeldExecutionResponse,
} from '@/shared/api';

/**
 * A held execution as the UI reads it. `agent_reason` is the agent's own
 * account of why it made the call; the API only sends it once the broker
 * stores one, so it is optional here and absent from the generated types.
 */
export type HeldCall = HeldExecutionResponse & { agent_reason?: string | null };
export type HeldCallDetail = HeldExecutionDetailResponse & { agent_reason?: string | null };

/**
 * The agent's reason, trimmed, with runs of blank lines closed up so a reason
 * never pads itself out; null when it sent none.
 */
export function agentReasonOf(call: Pick<HeldCall, 'agent_reason'>): string | null {
	const reason = call.agent_reason?.trim().replace(/(\n[^\S\n]*){3,}/g, '\n\n');
	return reason ? reason : null;
}

/**
 * Deletes are the one kind of call bulk approve leaves out: each is approved
 * on its own, in the pop-up.
 */
export function isDeleteCall(call: Pick<HeldExecutionResponse, 'method'>): boolean {
	return heldCallMethod(call) === 'DELETE';
}

/** The method as one upper-case token, however the row spells it. */
export function heldCallMethod(call: Pick<HeldExecutionResponse, 'method'>): string {
	return String(call.method ?? '')
		.trim()
		.toUpperCase();
}

/** "POST /v1/charges" — a call's method and the path it was made to; the method alone without a path. */
export function heldCallRoute(call: Pick<HeldExecutionResponse, 'method' | 'path'>): string {
	const path = String(call.path ?? '').trim();
	return path ? `${heldCallMethod(call)} ${path}` : heldCallMethod(call);
}

/**
 * Whether a headline is route-shaped ("POST /v1/charges",
 * "PUT /v4/spreadsheets/{spreadsheetId}/values/{range}") rather than an
 * operation's name ("Create a charge"). Route headlines read as code.
 */
export function isRouteHeadline(headline: string): boolean {
	return /^(GET|HEAD|POST|PUT|PATCH|DELETE|OPTIONS|TRACE)( \/|$)/.test(headline);
}

/** The operations endpoint's largest page. */
const OPERATIONS_PAGE = 200;
/** Pages read per API; past them, calls fall back to their route. */
const OPERATIONS_MAX_PAGES = 10;

type ApiKey = Pick<HeldExecutionResponse, 'api_vendor' | 'api_name' | 'api_version'>;

const apiKeyOf = (call: ApiKey) => `${call.api_vendor}/${call.api_name}/${call.api_version}`;

/** What the API's spec says about one operation. */
interface SpecOperation {
	/** The spec's summary ("Create a charge"); null when the spec names nothing. */
	name: string | null;
	/** The spec's path template ("/v4/spreadsheets/{spreadsheetId}/values/{range}"). */
	template: string;
}

/** An API's operations by id (a Map: an id is never read off `Object.prototype`). */
type SpecOperations = Map<string, SpecOperation>;

/**
 * The headline reader for a set of held calls: the operation's summary from
 * the API's live spec ("Create a charge"); when the spec names nothing, its
 * method and path template ("PUT /v4/spreadsheets/{spreadsheetId}/values/{range}"),
 * which keeps the ids in the real URL out of a heading; else the call's own
 * route ("POST /v1/charges"). Reads each distinct API's operation list once
 * (every page, up to `OPERATIONS_MAX_PAGES`) and keeps it for the session, since
 * specs change rarely; an API the viewer can't read falls back quietly.
 */
export function useHeldCallHeadlines(
	calls: readonly HeldExecutionResponse[],
): (call: HeldExecutionResponse) => string {
	const apis = [...new Map(calls.map((call) => [apiKeyOf(call), call])).values()];
	const results = useQueries({
		queries: apis.map((api) => ({
			// Outside the approvals root: a decision invalidates that root, and the
			// names don't change with one.
			queryKey: ['held-call-headlines', 'operations', apiKeyOf(api)],
			queryFn: async (): Promise<SpecOperations> => {
				const operations: SpecOperations = new Map();
				let cursor: string | null = null;
				for (let page = 0; page < OPERATIONS_MAX_PAGES; page++) {
					const res = await ApiOperationsService.listApiOperations({
						vendor: api.api_vendor,
						name: api.api_name,
						version: api.api_version,
						limit: OPERATIONS_PAGE,
						cursor,
					});
					for (const op of res.data) {
						operations.set(op.operation_id, {
							name: op.name?.trim() || null,
							template: op.path,
						});
					}
					if (!res.has_more || !res.next_cursor) break;
					cursor = res.next_cursor;
				}
				return operations;
			},
			staleTime: Infinity,
			retry: false,
		})),
	});
	const names = new Map(apis.map((api, i) => [apiKeyOf(api), results[i]?.data]));
	return (call) => {
		const operation = call.operation_id
			? names.get(apiKeyOf(call))?.get(call.operation_id)
			: undefined;
		// A spec name shaped like a route would read as the real one: it is not used.
		if (operation?.name && !isRouteHeadline(operation.name)) return operation.name;
		if (operation) return `${heldCallMethod(call)} ${operation.template}`;
		return heldCallRoute(call);
	};
}
