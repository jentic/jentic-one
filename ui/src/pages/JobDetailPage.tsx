import { useParams, useNavigate } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Clock, ExternalLink, X } from 'lucide-react';
import { api } from '@/api/client';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { BackButton } from '@/components/ui/BackButton';
import { LoadingState } from '@/components/ui/LoadingState';
import { AppLink } from '@/components/ui/AppLink';
import { Card, CardHeader, CardBody } from '@/components/ui/Card';
import { PageShell } from '@/components/layout/PageShell';
import { statusVariant } from '@/lib/status';
import { formatTimestamp } from '@/lib/time';

export default function JobDetailPage() {
	const { id } = useParams<{ id: string }>();
	const navigate = useNavigate();
	const queryClient = useQueryClient();

	const { data: job, isLoading } = useQuery({
		queryKey: ['job', id],
		queryFn: () => api.getJob(id!),
		enabled: !!id,
		refetchInterval: (query) => {
			const data = query.state.data;
			if (data && (data.status === 'running' || data.status === 'pending')) return 3000;
			return false;
		},
	});

	const cancelMutation = useMutation({
		mutationFn: () => api.cancelJob(id!),
		onSuccess: () => queryClient.invalidateQueries({ queryKey: ['job', id] }),
	});

	if (isLoading) return <LoadingState message="Loading job..." />;
	if (!job)
		return (
			<div className="text-muted-foreground py-16 text-center">
				<p>Job not found.</p>
				<Button
					variant="secondary"
					size="sm"
					onClick={() => navigate('/jobs')}
					className="mt-4"
				>
					Back to Jobs
				</Button>
			</div>
		);

	return (
		<PageShell width="reading">
			<BackButton to="/jobs" label="Back to Jobs" />

			<div className="flex items-start justify-between gap-4">
				<div>
					<p className="text-primary/75 font-mono text-[10px] tracking-widest uppercase">
						Job Detail
					</p>
					<h1 className="font-heading text-foreground mt-1 font-mono text-xl font-bold break-all">
						{job.id}
					</h1>
				</div>
				{(job.status === 'pending' || job.status === 'running') && (
					<Button
						variant="danger"
						size="sm"
						onClick={() => cancelMutation.mutate()}
						loading={cancelMutation.isPending}
					>
						<X className="h-4 w-4" />
						{cancelMutation.isPending ? 'Cancelling...' : 'Cancel Job'}
					</Button>
				)}
			</div>

			<Card>
				<CardHeader>
					<h2 className="font-heading text-foreground font-semibold">Summary</h2>
				</CardHeader>
				<CardBody>
					<div className="grid grid-cols-2 gap-4">
						<div>
							<p className="text-muted-foreground mb-1 text-xs">Status</p>
							<Badge variant={statusVariant(job.status)} className="text-sm">
								{job.status ?? 'unknown'}
							</Badge>
						</div>
						<div>
							<p className="text-muted-foreground mb-1 text-xs">Kind</p>
							<p className="text-foreground font-medium">{job.kind ?? '—'}</p>
						</div>
						{job.toolkit_id && (
							<div>
								<p className="text-muted-foreground mb-1 text-xs">Toolkit</p>
								<code className="text-accent-teal font-mono text-sm">
									{job.toolkit_id}
								</code>
							</div>
						)}
						<div>
							<p className="text-muted-foreground mb-1 text-xs">Created</p>
							<div className="flex items-center gap-1.5">
								<Clock className="text-muted-foreground h-4 w-4" />
								<span className="text-foreground text-sm">
									{job.created_at ? formatTimestamp(job.created_at) : '—'}
								</span>
							</div>
						</div>
						{job.upstream_job_url && (
							<div className="col-span-2">
								<p className="text-muted-foreground mb-1 text-xs">Upstream Job</p>
								<AppLink
									href={job.upstream_job_url}
									className="text-primary hover:text-primary/80 flex items-center gap-1 text-sm"
								>
									{job.upstream_job_url}
									<ExternalLink className="h-3 w-3" />
								</AppLink>
							</div>
						)}
					</div>
				</CardBody>
			</Card>

			{job.result && (
				<Card>
					<CardHeader>
						<h2 className="font-heading text-foreground font-semibold">Result</h2>
					</CardHeader>
					<CardBody>
						<pre className="bg-background border-border text-foreground max-h-96 overflow-auto rounded-lg border p-4 font-mono text-xs">
							{typeof job.result === 'string'
								? job.result
								: JSON.stringify(job.result, null, 2)}
						</pre>
					</CardBody>
				</Card>
			)}
			{job.error && (
				<Card className="border-danger/30">
					<CardHeader className="border-danger/30">
						<h2 className="font-heading text-danger font-semibold">Error</h2>
					</CardHeader>
					<CardBody>
						<pre className="bg-danger/10 border-danger/30 text-danger overflow-auto rounded-lg border p-4 font-mono text-xs">
							{job.error}
						</pre>
					</CardBody>
				</Card>
			)}
		</PageShell>
	);
}
