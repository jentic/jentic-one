import { useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Briefcase, X } from 'lucide-react';
import { api } from '@/api/client';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { PageHeader } from '@/components/ui/PageHeader';
import { LoadingState } from '@/components/ui/LoadingState';
import { EmptyState } from '@/components/ui/EmptyState';
import { ErrorAlert } from '@/components/ui/ErrorAlert';
import { Pagination } from '@/components/ui/Pagination';
import { PageShell } from '@/components/layout/PageShell';
import { timeAgo } from '@/lib/time';
import { statusVariant } from '@/lib/status';

export default function JobsPage() {
	const navigate = useNavigate();
	const queryClient = useQueryClient();
	const [searchParams, setSearchParams] = useSearchParams();
	const [page, setPage] = useState(1);
	const statusFilter = searchParams.get('status') || undefined;

	const {
		data: jobsPage,
		isLoading,
		isError,
	} = useQuery({
		queryKey: ['jobs', page, statusFilter],
		queryFn: () => api.listJobs({ page, status: statusFilter }),
	});

	const cancelMutation = useMutation({
		mutationFn: (id: string) => api.cancelJob(id),
		onSuccess: () => queryClient.invalidateQueries({ queryKey: ['jobs'] }),
	});

	const jobs: any[] = (jobsPage as any)?.items ?? [];
	const total = (jobsPage as any)?.total ?? 0;
	const totalPages = Math.ceil(total / 20);

	return (
		<PageShell spacing="space-y-5">
			<PageHeader
				title="Background jobs"
				subtitle="Async workflow executions you've dispatched."
			/>

			<div className="flex flex-wrap items-center gap-2">
				<span className="text-muted-foreground text-xs">Status:</span>
				{([null, 'pending', 'running', 'complete', 'failed'] as (string | null)[]).map(
					(s) => (
						<Button
							key={s ?? 'all'}
							variant={
								statusFilter === s || (s === null && !statusFilter)
									? 'outline'
									: 'ghost'
							}
							size="sm"
							onClick={() => {
								const p = new URLSearchParams(searchParams);
								if (s) {
									p.set('status', s);
								} else {
									p.delete('status');
								}
								setSearchParams(p);
								setPage(1);
							}}
							className="rounded-full font-mono text-xs"
						>
							{s ?? 'all'}
						</Button>
					),
				)}
			</div>

			{isLoading ? (
				<LoadingState message="Loading jobs..." />
			) : isError ? (
				<div className="bg-muted border-border rounded-xl border p-12 text-center">
					<ErrorAlert message="Failed to load jobs" />
					<p className="text-muted-foreground mt-2 text-sm">
						Please try refreshing the page.
					</p>
				</div>
			) : jobs.length === 0 ? (
				<EmptyState
					icon={<Briefcase className="h-10 w-10 opacity-30" />}
					title="No jobs found"
					description={
						statusFilter
							? `No ${statusFilter} jobs.`
							: 'Background jobs appear here when agents trigger async work.'
					}
				/>
			) : (
				<>
					<div className="bg-muted border-border overflow-hidden rounded-xl border">
						<div className="overflow-x-auto">
							<table className="w-full text-sm">
								<thead>
									<tr className="border-border border-b">
										{[
											'ID',
											'Kind',
											'Status',
											'Toolkit',
											'Created',
											'Actions',
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
									{jobs.map((job: any) => (
										<tr
											key={job.id}
											className="border-border/50 hover:bg-background/50 cursor-pointer border-b transition-colors"
											onClick={() => navigate(`/jobs/${job.id}`)}
										>
											<td className="text-muted-foreground max-w-[120px] truncate px-4 py-3 font-mono text-xs">
												{job.id}
											</td>
											<td className="text-foreground px-4 py-3">
												{job.kind ?? '—'}
											</td>
											<td className="px-4 py-3">
												<Badge variant={statusVariant(job.status)}>
													{job.status ?? 'unknown'}
												</Badge>
											</td>
											<td className="text-muted-foreground px-4 py-3">
												{job.toolkit_id ?? '—'}
											</td>
											<td className="text-muted-foreground px-4 py-3 text-xs">
												{timeAgo(job.created_at)}
											</td>
											<td className="px-4 py-3">
												{(job.status === 'pending' ||
													job.status === 'running') && (
													<Button
														variant="ghost"
														size="icon"
														onClick={(e) => {
															e.stopPropagation();
															cancelMutation.mutate(job.id);
														}}
														className="text-danger hover:text-danger/80 transition-colors"
														title="Cancel"
													>
														<X className="h-4 w-4" />
													</Button>
												)}
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
