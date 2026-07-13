/**
 * SpecViewerDialog — view the resolved OpenAPI document for a workspace API.
 *
 * jentic-mini only offered a "Spec" download link; here we surface the spec
 * in-app: the resolved document (overlays applied) is fetched lazily when the
 * dialog opens (`useApiSpec(key, open)`), pretty-printed as JSON, and offered
 * for copy + download. The fetch stays behind the open flag so the potentially
 * large document never loads on the detail page itself.
 */
import { useMemo } from 'react';
import { Download } from 'lucide-react';
import { Dialog, Button, Skeleton, ErrorAlert, CopyButton } from '@/shared/ui';
import { useApiSpec, formatApiKey } from '@/modules/workspace/api';
import type { ApiKey } from '@/modules/workspace/api';

export interface SpecViewerDialogProps {
	apiKey: ApiKey;
	open: boolean;
	onClose: () => void;
	/**
	 * View a specific revision's spec (old/archived or draft/pending). When
	 * omitted, the live revision's spec is shown.
	 */
	revisionId?: string | null;
	/** Short label (e.g. revision id / state) shown beside the api key. */
	revisionLabel?: string;
}

export function SpecViewerDialog({
	apiKey,
	open,
	onClose,
	revisionId,
	revisionLabel,
}: SpecViewerDialogProps) {
	const query = useApiSpec(apiKey, open, revisionId);

	const pretty = useMemo(() => {
		if (query.data == null) return '';
		try {
			return JSON.stringify(query.data, null, 2);
		} catch {
			return String(query.data);
		}
	}, [query.data]);

	function download() {
		const blob = new Blob([pretty], { type: 'application/json' });
		const url = URL.createObjectURL(blob);
		const a = document.createElement('a');
		const revSuffix = revisionId ? `-${revisionId.slice(0, 8)}` : '';
		a.href = url;
		a.download = `${apiKey.vendor}-${apiKey.name}-${apiKey.version}${revSuffix}.openapi.json`;
		a.click();
		URL.revokeObjectURL(url);
	}

	return (
		<Dialog
			open={open}
			onClose={onClose}
			title={revisionLabel ? `OpenAPI spec · ${revisionLabel}` : 'OpenAPI spec'}
			size="lg"
			footer={
				<>
					<Button variant="ghost" size="sm" onClick={onClose}>
						Close
					</Button>
					{pretty ? (
						<>
							<CopyButton value={pretty} label="Copy" />
							<Button variant="secondary" size="sm" onClick={download}>
								<Download size={14} aria-hidden="true" />
								Download
							</Button>
						</>
					) : null}
				</>
			}
		>
			<p className="text-muted-foreground mb-3 font-mono text-xs">{formatApiKey(apiKey)}</p>
			{query.isLoading ? (
				<div className="space-y-2" aria-busy="true">
					{Array.from({ length: 8 }).map((_, i) => (
						<Skeleton key={i} className="h-4 w-full" />
					))}
				</div>
			) : query.isError ? (
				<div className="space-y-3">
					<ErrorAlert
						message={
							query.error instanceof Error ? query.error : 'Failed to load the spec.'
						}
					/>
					<Button variant="secondary" size="sm" onClick={() => query.refetch()}>
						Try again
					</Button>
				</div>
			) : (
				<pre
					className="bg-muted/40 border-border/60 text-foreground max-h-[60vh] overflow-auto rounded-lg border p-3 font-mono text-xs leading-relaxed whitespace-pre"
					data-testid="spec-viewer-content"
				>
					{pretty}
				</pre>
			)}
		</Dialog>
	);
}
