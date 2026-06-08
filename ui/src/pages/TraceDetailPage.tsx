import { useParams, useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Clock, Zap } from 'lucide-react';
import { api } from '@/api/client';
import { Badge, StatusBadge } from '@/components/ui/Badge';
import { BackButton } from '@/components/ui/BackButton';
import { Button } from '@/components/ui/Button';
import { LoadingState } from '@/components/ui/LoadingState';
import { Card, CardHeader, CardBody } from '@/components/ui/Card';
import { ErrorAlert } from '@/components/ui/ErrorAlert';
import { PageShell } from '@/components/layout/PageShell';
import { formatTimestamp } from '@/lib/time';

export default function TraceDetailPage() {
	const { id } = useParams<{ id: string }>();
	const navigate = useNavigate();

	const { data: trace, isLoading } = useQuery({
		queryKey: ['trace', id],
		queryFn: () => api.getTrace(id!),
		enabled: !!id,
	});

	if (isLoading) return <LoadingState message="Loading trace..." />;
	if (!trace)
		return (
			<div className="text-muted-foreground py-16 text-center">
				<p>Trace not found.</p>
				<Button
					variant="secondary"
					size="sm"
					onClick={() => navigate('/traces')}
					className="mt-4"
				>
					Back to Traces
				</Button>
			</div>
		);

	return (
		<PageShell width="reading">
			<BackButton to="/traces" label="Back to Traces" />

			<div>
				<p className="text-primary/75 font-mono text-[10px] tracking-widest uppercase">
					Trace Detail
				</p>
				<h1 className="font-heading text-foreground mt-1 font-mono text-xl font-bold break-all">
					{trace.id}
				</h1>
			</div>

			<Card>
				<CardHeader>
					<h2 className="font-heading text-foreground font-semibold">Summary</h2>
				</CardHeader>
				<CardBody>
					<div className="grid grid-cols-2 gap-4">
						<div>
							<p className="text-muted-foreground mb-1 text-xs">Toolkit</p>
							<p className="text-foreground font-medium">{trace.toolkit_id ?? '—'}</p>
						</div>
						<div>
							<p className="text-muted-foreground mb-1 text-xs">Status</p>
							{trace.http_status ? (
								<StatusBadge status={trace.http_status} />
							) : (
								<Badge variant={trace.status === 'error' ? 'danger' : 'success'}>
									{trace.status ?? '—'}
								</Badge>
							)}
						</div>
						{trace.operation_id && (
							<div className="col-span-2">
								<p className="text-muted-foreground mb-1 text-xs">Operation</p>
								<code className="text-accent-teal font-mono text-sm break-all">
									{trace.operation_id}
								</code>
							</div>
						)}
						{trace.workflow_id && (
							<div className="col-span-2">
								<p className="text-muted-foreground mb-1 text-xs">Workflow</p>
								<code className="text-accent-pink font-mono text-sm break-all">
									{trace.workflow_id}
								</code>
							</div>
						)}
						{trace.spec_path && (
							<div className="col-span-2">
								<p className="text-muted-foreground mb-1 text-xs">Spec Path</p>
								<code className="text-muted-foreground font-mono text-xs">
									{trace.spec_path}
								</code>
							</div>
						)}
						<div>
							<p className="text-muted-foreground mb-1 text-xs">Duration</p>
							<div className="flex items-center gap-1.5">
								<Zap className="text-accent-yellow h-4 w-4" />
								<span className="text-foreground font-mono">
									{trace.duration_ms != null ? `${trace.duration_ms}ms` : '—'}
								</span>
							</div>
						</div>
						<div>
							<p className="text-muted-foreground mb-1 text-xs">Execution Time</p>
							<div className="flex items-center gap-1.5">
								<Clock className="text-muted-foreground h-4 w-4" />
								<span className="text-foreground text-sm">
									{trace.created_at ? formatTimestamp(trace.created_at) : '—'}
								</span>
							</div>
						</div>
						{trace.completed_at && trace.completed_at !== trace.created_at && (
							<div className="col-span-2">
								<p className="text-muted-foreground mb-1 text-xs">Completed</p>
								<span className="text-foreground text-sm">
									{formatTimestamp(trace.completed_at)}
								</span>
							</div>
						)}
					</div>
					{trace.error && (
						<div className="mt-4">
							<ErrorAlert message={trace.error} />
						</div>
					)}
				</CardBody>
			</Card>

			{trace.steps && trace.steps.length > 0 && (
				<Card>
					<CardHeader>
						<h2 className="font-heading text-foreground font-semibold">
							Steps ({trace.steps.length})
						</h2>
					</CardHeader>
					<CardBody className="space-y-2">
						{trace.steps.map((step: any, i: number) => (
							<div
								key={i}
								className="bg-background border-border flex gap-3 rounded-lg border p-3"
							>
								<div className="bg-primary/10 border-primary/30 text-primary flex h-6 w-6 shrink-0 items-center justify-center rounded-full border font-mono text-xs">
									{i + 1}
								</div>
								<div className="flex-1">
									<div className="flex flex-wrap items-center gap-2">
										{step.step_id && (
											<code className="text-muted-foreground mr-2 font-mono text-xs">
												{step.step_id}
											</code>
										)}
										{step.operation && (
											<code className="text-foreground font-mono text-sm">
												{step.operation}
											</code>
										)}
										{step.http_status && (
											<StatusBadge status={step.http_status} />
										)}
										{step.status && !step.http_status && (
											<Badge
												variant={
													step.status === 'error' ? 'danger' : 'success'
												}
											>
												{step.status}
											</Badge>
										)}
									</div>
									{step.error && (
										<pre className="text-danger mt-1 font-mono text-xs break-words whitespace-pre-wrap">
											{String(step.error)}
										</pre>
									)}
								</div>
							</div>
						))}
					</CardBody>
				</Card>
			)}

			{trace.inputs && Object.keys(trace.inputs).length > 0 && (
				<Card>
					<CardHeader>
						<h2 className="font-heading text-foreground font-semibold">Inputs</h2>
					</CardHeader>
					<CardBody>
						<pre className="bg-background border-border text-foreground max-h-64 overflow-auto rounded-lg border p-4 font-mono text-xs">
							{JSON.stringify(trace.inputs, null, 2)}
						</pre>
					</CardBody>
				</Card>
			)}
			{trace.outputs && Object.keys(trace.outputs).length > 0 && (
				<Card>
					<CardHeader>
						<h2 className="font-heading text-foreground font-semibold">Outputs</h2>
					</CardHeader>
					<CardBody>
						<pre className="bg-background border-border text-foreground max-h-64 overflow-auto rounded-lg border p-4 font-mono text-xs">
							{JSON.stringify(trace.outputs, null, 2)}
						</pre>
					</CardBody>
				</Card>
			)}
		</PageShell>
	);
}
