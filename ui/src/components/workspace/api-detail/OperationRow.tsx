import { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { ChevronDown } from 'lucide-react';
import { api } from '@/api/client';
import { MethodBadge } from '@/components/ui/Badge';
import type { OpRow } from '@/components/discovery/OperationsListControls';
import {
	OperationInspectContent,
	flattenInspectParameters,
	normalizeWorkspaceAuth,
} from '@/components/discovery/OperationInspect';
import type { InspectParam, InspectAuthEntry } from '@/components/discovery/OperationInspect';

interface OperationRowProps {
	row: OpRow;
	expanded: boolean;
	onToggle: () => void;
}

/**
 * Single expandable operation row. Only fetches the inspect payload
 * when the row is expanded so the operations list stays cheap to
 * render even for APIs with hundreds of endpoints.
 */
export function OperationRow({ row, expanded, onToggle }: OperationRowProps) {
	const { data: detail } = useQuery({
		queryKey: ['inspect', row.key],
		queryFn: () => api.inspectCapability(row.key),
		enabled: expanded,
		staleTime: 10 * 60_000,
	});

	const params: InspectParam[] = useMemo(() => {
		if (!detail) return [];
		return flattenInspectParameters(
			(detail as { parameters?: Parameters<typeof flattenInspectParameters>[0] }).parameters,
		);
	}, [detail]);

	const auth: InspectAuthEntry[] = useMemo(() => {
		if (!detail) return [];
		return normalizeWorkspaceAuth(
			(detail as { auth?: Parameters<typeof normalizeWorkspaceAuth>[0] }).auth,
		);
	}, [detail]);

	return (
		<li>
			<button
				type="button"
				onClick={onToggle}
				className="hover:bg-muted/50 flex w-full items-start gap-3 rounded-md px-2 py-2 text-left transition-colors"
				aria-expanded={expanded}
			>
				<MethodBadge method={row.method} />
				<div className="min-w-0 flex-1">
					<p className="text-foreground truncate text-sm font-medium">{row.label}</p>
					<code className="text-muted-foreground block truncate font-mono text-xs">
						{row.path}
					</code>
				</div>
				<ChevronDown
					size={14}
					className={`text-muted-foreground mt-1 shrink-0 transition-transform ${expanded ? 'rotate-180' : ''}`}
				/>
			</button>
			{expanded && (
				<div className="border-border/30 mb-2 ml-2 border-l-2 pl-4">
					<OperationInspectContent
						description={
							(detail as any)?.description !== (detail as any)?.summary
								? (detail as any)?.description
								: undefined
						}
						parameters={params}
						auth={auth}
					/>
				</div>
			)}
		</li>
	);
}
