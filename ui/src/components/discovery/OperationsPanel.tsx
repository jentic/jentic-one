import { Loader2 } from 'lucide-react';
import { useQuery } from '@tanstack/react-query';
import { MethodBadge } from '@/components/ui/Badge';
import { api } from '@/api/client';

export function OperationsPanel({ apiId }: { apiId: string }) {
	const { data: opsPage, isLoading } = useQuery({
		queryKey: ['ops', apiId],
		queryFn: () => api.listOperations(apiId, 1, 50),
		staleTime: 60000,
	});
	const ops = (opsPage as any)?.data ?? [];
	const total = (opsPage as any)?.total ?? 0;

	if (isLoading)
		return (
			<div className="text-muted-foreground flex items-center gap-2 px-5 py-4 text-sm">
				<Loader2 className="h-4 w-4 animate-spin" /> Loading operations...
			</div>
		);

	if (ops.length === 0)
		return (
			<div className="text-muted-foreground px-5 py-4 text-sm">
				No operations indexed for this API.
			</div>
		);

	return (
		<div className="border-border bg-background/40 border-t">
			<div className="border-border/50 flex items-center justify-between border-b px-5 py-2">
				<span className="text-muted-foreground text-xs">
					{total} operation{total !== 1 ? 's' : ''}
				</span>
			</div>
			<div className="divide-border/50 max-h-72 divide-y overflow-y-auto">
				{ops.map((op: any) => (
					<div
						key={op.id ?? op.operation_id}
						className="flex items-start gap-3 px-5 py-2.5"
					>
						<MethodBadge method={op.method} />
						<div className="min-w-0 flex-1">
							<p className="text-foreground truncate text-sm font-medium">
								{op.summary ?? op.operation_id}
							</p>
							<code className="text-muted-foreground block truncate font-mono text-xs">
								{op.path ?? op.id}
							</code>
						</div>
					</div>
				))}
				{total > 50 && (
					<div className="text-muted-foreground px-5 py-2 text-center text-xs">
						+ {total - 50} more — search to find specific operations
					</div>
				)}
			</div>
		</div>
	);
}
