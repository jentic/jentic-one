/**
 * Inspect view for a workspace (registered) operation.
 *
 * Fetches `GET /inspect/{capability_id}` and feeds the normalized
 * result into `OperationInspectContent` — the same renderer used by
 * `DirectoryInspectPanel`. Splitting fetch + normalization from
 * rendering is how we guarantee the two surfaces stay visually
 * identical as the design iterates.
 *
 * Historical note: a previous version of this file rendered parameters
 * directly off `detail.parameters` (treating it as an array via
 * `.slice(...)`) and auth off `detail.auth_instructions`. Both fields
 * were wrong — the server returns parameters as a dict keyed by
 * location (`{query, path, body, ...}`) and auth as `detail.auth`.
 * The result was that workspace ops silently showed neither
 * parameters nor auth. The two normalizers below
 * (`flattenInspectParameters`, `normalizeWorkspaceAuth`) translate
 * the actual server shape into the shared `InspectParam[]` and
 * `InspectAuthEntry[]` types.
 *
 * `variant` controls the outer chrome:
 *  - `'sheet'` (used inside `ApiDetailSheet`): no wrapping border / X
 *    close button — the sheet provides its own back+close header.
 *  - `'inline'` (default, used in `DiscoveryCard` endpoint expansion):
 *    wraps the content with a `border-t bg-background/50` strip and a
 *    self-contained X close button.
 */
import { Loader2, X } from 'lucide-react';
import { useQuery } from '@tanstack/react-query';
import {
	OperationInspectContent,
	flattenInspectParameters,
	normalizeWorkspaceAuth,
} from './OperationInspect';
import { Button } from '@/components/ui/Button';
import { api } from '@/api/client';
import { parseCapabilityId } from '@/lib/capabilityId';

export function InspectPanel({
	capabilityId,
	onClose,
	variant = 'inline',
}: {
	capabilityId: string;
	onClose: () => void;
	variant?: 'sheet' | 'inline';
}) {
	const {
		data: detail,
		isLoading,
		error,
	} = useQuery({
		queryKey: ['inspect', capabilityId],
		queryFn: () => api.inspectCapability(capabilityId),
		staleTime: 60000,
	});

	if (isLoading)
		return (
			<div className="flex items-center justify-center p-8">
				<Loader2 className="text-muted-foreground h-5 w-5 animate-spin" />
			</div>
		);

	if (error || !detail)
		return (
			<div className="text-danger p-4 text-sm">
				Failed to load details for this capability.
			</div>
		);

	// The capability id (`METHOD/host/path`) is the authoritative source
	// for method + path — the response also returns `detail.method` but
	// not a separate `path` field (the `url` field bundles host+path).
	// Parsing the id is cheaper and works even when host resolution drifts.
	const parsed = parseCapabilityId(capabilityId);
	const method = (detail as { method?: string }).method ?? parsed?.method;
	const path = parsed?.path;
	const apiContextName =
		(detail as { api?: { name?: string } }).api?.name ??
		(detail as { api_context?: { name?: string } }).api_context?.name;

	const parameters = flattenInspectParameters(
		(detail as { parameters?: Parameters<typeof flattenInspectParameters>[0] }).parameters,
	);
	const auth = normalizeWorkspaceAuth(
		(detail as { auth?: Parameters<typeof normalizeWorkspaceAuth>[0] }).auth,
	);

	const content = (
		<OperationInspectContent
			method={method}
			path={path}
			summary={detail.summary ?? undefined}
			description={detail.description ?? undefined}
			parameters={parameters}
			auth={auth}
			testId="inspect-panel"
		/>
	);

	// `'sheet'` variant: parent (`ApiDetailSheet`) already supplies a back
	// button and close X in its own header — render bare content.
	if (variant === 'sheet') {
		return content;
	}

	// `'inline'` variant: this panel is embedded directly into a
	// `DiscoveryCard` (endpoint search result expansion) with no
	// surrounding chrome. Provide a thin top border, subtle background,
	// and an inline close affordance so the user can collapse the expansion.
	return (
		<div className="border-border bg-background/50 border-t">
			<div className="flex items-start justify-between gap-2 px-5 pt-3">
				{apiContextName && (
					<p className="text-muted-foreground font-mono text-xs">{apiContextName}</p>
				)}
				<Button
					variant="ghost"
					size="icon"
					onClick={onClose}
					className="-mt-1 shrink-0"
					aria-label="Close inspect panel"
				>
					<X className="h-4 w-4" />
				</Button>
			</div>
			{content}
		</div>
	);
}
