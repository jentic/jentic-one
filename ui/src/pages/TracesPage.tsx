import { useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Activity, Filter, X } from 'lucide-react';
import { api } from '@/api/client';
import type { TraceOut } from '@/api/generated';
import { Badge, StatusBadge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { PageHeader } from '@/components/ui/PageHeader';
import { LoadingState } from '@/components/ui/LoadingState';
import { EmptyState } from '@/components/ui/EmptyState';
import { ErrorAlert } from '@/components/ui/ErrorAlert';
import { Pagination } from '@/components/ui/Pagination';
import { PageShell } from '@/components/layout/PageShell';
import { timeAgo } from '@/lib/time';

export default function TracesPage() {
	const navigate = useNavigate();
	const [searchParams, setSearchParams] = useSearchParams();
	const [page, setPage] = useState(parseInt(searchParams.get('page') || '1', 10));
	const toolkit = searchParams.get('toolkit') || undefined;
	const workflow = searchParams.get('workflow') || undefined;

	const {
		data: tracesPage,
		isLoading,
		isError,
	} = useQuery({
		queryKey: ['traces', page, toolkit, workflow],
		queryFn: () => api.listTraces({ page, limit: 20, toolkit, workflow }),
	});

	const traces = tracesPage?.traces ?? [];
	const total = tracesPage?.total ?? 0;
	const totalPages = Math.ceil(total / 20);

	return (
		<PageShell spacing="space-y-5">
			<PageHeader
				title="Traces"
				subtitle="Every authenticated request, with redaction and latency."
			/>

			{(toolkit || workflow) && (
				<div className="flex flex-wrap items-center gap-2">
					<Filter className="text-muted-foreground h-4 w-4" />
					{toolkit && (
						<span className="bg-primary/10 text-primary border-primary/20 inline-flex items-center gap-1 rounded-full border px-2 py-0.5 font-mono text-xs">
							toolkit: {toolkit}
							<Button
								variant="ghost"
								size="icon"
								aria-label="Clear toolkit filter"
								className="h-4 w-4 p-0"
								onClick={() => {
									const p = new URLSearchParams(searchParams);
									p.delete('toolkit');
									setSearchParams(p);
								}}
							>
								<X className="h-3 w-3" />
							</Button>
						</span>
					)}
					{workflow && (
						<span className="bg-primary/10 text-primary border-primary/20 inline-flex items-center gap-1 rounded-full border px-2 py-0.5 font-mono text-xs">
							workflow: {workflow}
							<Button
								variant="ghost"
								size="icon"
								aria-label="Clear workflow filter"
								className="h-4 w-4 p-0"
								onClick={() => {
									const p = new URLSearchParams(searchParams);
									p.delete('workflow');
									setSearchParams(p);
								}}
							>
								<X className="h-3 w-3" />
							</Button>
						</span>
					)}
				</div>
			)}

			{isLoading ? (
				<LoadingState message="Loading traces..." />
			) : isError ? (
				<div className="bg-muted border-border rounded-xl border p-12 text-center">
					<ErrorAlert message="Failed to load traces" />
					<p className="text-muted-foreground mt-2 text-sm">
						Please try refreshing the page.
					</p>
				</div>
			) : traces.length === 0 ? (
				<EmptyState
					icon={<Activity className="h-10 w-10 opacity-30" />}
					title="No traces found"
					description="Traces appear here when agents call the broker."
				/>
			) : (
				<>
					<div className="bg-muted border-border overflow-hidden rounded-xl border">
						<div className="overflow-x-auto">
							<table className="w-full text-sm">
								<thead>
									<tr className="border-border border-b">
										{[
											'Time',
											'Toolkit',
											'Operation / Workflow',
											'Status',
											'Duration',
										].map((h) => (
											<th
												key={h}
												className="text-muted-foreground px-4 py-3 text-left font-mono text-xs tracking-wider uppercase"
											>
												{h}
											</th>
										))}
									</tr>
								</thead>
								<tbody>
									{traces.map((trace: TraceOut) => (
										<tr
											key={trace.id}
											className="border-border/50 hover:bg-background/50 cursor-pointer border-b transition-colors"
											onClick={() => navigate(`/traces/${trace.id}`)}
										>
											<td className="text-muted-foreground px-4 py-3 font-mono text-xs whitespace-nowrap">
												{timeAgo(trace.created_at)}
											</td>
											<td className="text-foreground px-4 py-3">
												{trace.toolkit_id ?? '—'}
											</td>
											<td className="text-muted-foreground max-w-[300px] truncate px-4 py-3 font-mono text-xs">
												{trace.workflow_id && (
													<span className="bg-primary/10 text-primary mr-2 rounded px-1.5 py-0.5 font-mono text-[10px]">
														workflow
													</span>
												)}
												{trace.operation_id ?? trace.workflow_id ?? '—'}
											</td>
											<td className="px-4 py-3">
												{trace.http_status ? (
													<StatusBadge status={trace.http_status} />
												) : (
													<Badge
														variant={
															trace.status === 'error'
																? 'danger'
																: 'success'
														}
													>
														{trace.status ?? '—'}
													</Badge>
												)}
											</td>
											<td className="text-muted-foreground px-4 py-3 text-xs">
												{trace.duration_ms != null
													? `${trace.duration_ms}ms`
													: '—'}
											</td>
										</tr>
									))}
								</tbody>
							</table>
						</div>
					</div>
					{totalPages > 1 && (
						<Pagination
							page={page}
							totalPages={totalPages}
							onPageChange={setPage}
							className="justify-center"
						/>
					)}
				</>
			)}
		</PageShell>
	);
}
