/**
 * Docs service tier — TanStack Query hooks.
 *
 * The only backend access path for the docs page: the page calls this hook,
 * which calls the repository (`./client`). It fetches the OpenAPI document and
 * the canonical scope reference in parallel and returns them side-by-side. The
 * native API reference renders the spec directly and joins each operation to
 * its scope/actor data from the reference payload.
 */
import { useQuery } from '@tanstack/react-query';
import {
	fetchBrokerSpec,
	fetchCliReference,
	fetchOpenApiDocument,
	fetchReferencePayload,
} from '@/modules/docs/api/client';
import type { CliReference, OpenApiDocument, ReferencePayload } from '@/modules/docs/api/types';

export const docsKeys = {
	all: ['docs'] as const,
	bundle: ['docs', 'bundle'] as const,
	cli: ['docs', 'cli'] as const,
	broker: ['docs', 'broker'] as const,
};

export interface DocsBundle {
	/** The OpenAPI document, rendered natively by the API reference. */
	spec: OpenApiDocument;
	/** The raw scope/actor reference payload that enriches each operation. */
	reference: ReferencePayload;
}

export interface UseDocsResult {
	data: DocsBundle | undefined;
	isPending: boolean;
	error: Error | null;
	refetch: () => void;
}

/**
 * Fetch the spec + scope reference.
 *
 * The reference endpoint may be absent on an older server (it shipped in #602):
 * if it fails, we surface the error so the page can show a graceful notice
 * rather than silently dropping the scope panel. The spec is static for the
 * process lifetime, so a long staleTime is fine.
 */
export function useDocs(): UseDocsResult {
	const query = useQuery<DocsBundle>({
		queryKey: docsKeys.bundle,
		queryFn: async () => {
			const [spec, reference] = await Promise.all([
				fetchOpenApiDocument(),
				fetchReferencePayload(),
			]);
			return { spec, reference };
		},
		staleTime: Infinity,
	});

	return {
		data: query.data,
		isPending: query.isPending,
		error: query.error as Error | null,
		refetch: () => {
			void query.refetch();
		},
	};
}

/**
 * Fetch the CLI command reference (the committed `cli-reference.json` static
 * asset). It's a build-time artifact, so it never changes for the process
 * lifetime — cache it indefinitely.
 */
export function useCliReference() {
	const query = useQuery<CliReference>({
		queryKey: docsKeys.cli,
		queryFn: fetchCliReference,
		staleTime: Infinity,
	});
	return {
		data: query.data,
		isPending: query.isPending,
		error: query.error as Error | null,
		refetch: () => {
			void query.refetch();
		},
	};
}

/**
 * Fetch the Broker (data-plane) OpenAPI document (the committed
 * `broker-openapi.json` static asset). The broker is a standalone service whose
 * spec is never part of this instance's `/openapi.json`, so the docs render this
 * build-time artifact instead. Like the other static assets it never changes
 * for the process lifetime — cache it indefinitely.
 */
export function useBrokerSpec() {
	const query = useQuery<OpenApiDocument>({
		queryKey: docsKeys.broker,
		queryFn: fetchBrokerSpec,
		staleTime: Infinity,
	});
	return {
		data: query.data,
		isPending: query.isPending,
		error: query.error as Error | null,
		refetch: () => {
			void query.refetch();
		},
	};
}
