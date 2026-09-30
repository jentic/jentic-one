import type { WorkspaceDigestRow } from '@/modules/discover/api';

/**
 * One `WorkspaceDigestRow` fixture for every discover test: a live API with
 * no attention signals, titled `title`. `key`/`href` follow `ref`, so an
 * override of `ref` keeps them consistent.
 */
export function makeDigestRow(
	title: string,
	extra: Partial<WorkspaceDigestRow> = {},
): WorkspaceDigestRow {
	const ref = extra.ref ?? {
		vendor: title.toLowerCase(),
		name: `${title.toLowerCase()}-api`,
		version: '1',
	};
	return {
		key: `${ref.vendor}/${ref.name}/${ref.version}`,
		title,
		host: null,
		iconUrl: null,
		catalogApiId: null,
		currentRevisionId: 'rev_live',
		updateAvailable: false,
		operationCount: 3,
		needsAuth: false,
		securitySchemes: [],
		credentials: [],
		credentialCount: 0,
		pendingOverlays: null,
		usage: null,
		createdAt: '2026-01-01T00:00:00Z',
		href: `/library/workspace/${ref.vendor}/${ref.name}/${ref.version}`,
		...extra,
		ref,
	};
}
