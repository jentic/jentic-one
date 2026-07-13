/**
 * Workspace adapters — raw server payload → UI types.
 *
 * Pure, unit-testable mappers kept separate from the repository (`client.ts`)
 * so the HTTP wrapper stays thin. These are the single boundary where the
 * generated `any` (untyped `/apis` list/operation/revision responses on this
 * branch's committed client) becomes the module's typed shapes.
 */
import type {
	ApiOperation,
	ApiRef,
	ApiRevision,
	CursorPage,
	WorkspaceApi,
} from '@/modules/workspace/api/types';

type Raw = Record<string, unknown>;

function asRecord(value: unknown): Raw {
	return value && typeof value === 'object' ? (value as Raw) : {};
}

function str(value: unknown): string {
	return typeof value === 'string' ? value : '';
}

function strOrNull(value: unknown): string | null {
	return typeof value === 'string' ? value : null;
}

function num(value: unknown): number {
	return typeof value === 'number' ? value : 0;
}

function bool(value: unknown): boolean {
	return value === true;
}

function strArray(value: unknown): string[] {
	return Array.isArray(value) ? value.filter((v): v is string => typeof v === 'string') : [];
}

function toApiRef(value: unknown): ApiRef {
	const r = asRecord(value);
	return {
		vendor: str(r.vendor),
		name: str(r.name),
		version: str(r.version),
		host: strOrNull(r.host),
	};
}

/** `GET /apis` / `GET /apis/{…}` row → `WorkspaceApi`. */
export function toWorkspaceApi(value: unknown): WorkspaceApi {
	const r = asRecord(value);
	return {
		api: toApiRef(r.api),
		displayName: strOrNull(r.display_name),
		description: strOrNull(r.description),
		iconUrl: strOrNull(r.icon_url),
		currentRevisionId: strOrNull(r.current_revision_id),
		revisionCount: num(r.revision_count),
		operationCount: num(r.operation_count),
		securitySchemes: strArray(r.security_schemes),
		source: typeof r.source === 'string' ? r.source : undefined,
		registered: typeof r.registered === 'boolean' ? r.registered : undefined,
		createdAt: str(r.created_at),
		updatedAt: str(r.updated_at),
	};
}

/** Generic `{data, has_more, next_cursor}` → `CursorPage<T>`. */
export function toCursorPage<T>(value: unknown, mapItem: (raw: unknown) => T): CursorPage<T> {
	const r = asRecord(value);
	const data = Array.isArray(r.data) ? r.data : [];
	return {
		items: data.map(mapItem),
		hasMore: bool(r.has_more),
		nextCursor: strOrNull(r.next_cursor),
	};
}

/** Operation summary row → `ApiOperation`. */
export function toApiOperation(value: unknown): ApiOperation {
	const r = asRecord(value);
	return {
		operationId: str(r.operation_id),
		method: str(r.method),
		path: str(r.path),
		name: strOrNull(r.name),
		description: strOrNull(r.description),
		tags: strArray(r.tags),
		deprecated: bool(r.deprecated),
		revisionId: str(r.revision_id),
	};
}

/** Revision row → `ApiRevision`, lifting `_links` action hrefs to the surface. */
export function toApiRevision(value: unknown): ApiRevision {
	const r = asRecord(value);
	const source = asRecord(r.source);
	const links = asRecord(r._links);
	return {
		revisionId: str(r.revision_id),
		api: toApiRef(r.api),
		sourceType: str(source.type),
		sourceUrl: strOrNull(source.url),
		specDigest: str(r.spec_digest),
		operationCount: num(r.operation_count),
		state: str(r.state),
		isCurrent: bool(r.is_current),
		promotedAt: strOrNull(r.promoted_at),
		archivedAt: strOrNull(r.archived_at),
		createdAt: str(r.created_at),
		promoteHref: strOrNull(links.promote),
		archiveHref: strOrNull(links.archive),
	};
}
