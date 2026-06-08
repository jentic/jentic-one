import { type JSX } from 'react';
import { X, AlertCircle, Clock, Zap, Layers, ArrowRight, User } from 'lucide-react';
import { StatusIcon, getStatusLabel } from '@/components/monitor/shared/StatusIcon';
import { formatDuration } from '@/components/monitor/shared/format';
import { getVendorConfig, getInitials } from '@/components/monitor/shared/vendor-icons';
import type { ExecutionDetail } from '@/components/monitor/types';
import { cn } from '@/lib/utils';
import { LoadingSpinner } from '@/components/ui/LoadingSpinner';
import { SheetPrimitive } from '@/components/ui/SheetPrimitive';

interface ExecutionDetailSheetProps {
	execution: ExecutionDetail | null;
	isOpen: boolean;
	isLoading: boolean;
	onClose: () => void;
	side?: 'left' | 'right';
	/** When set, the parent-trace link is rendered as a clickable button. */
	onOpenTrace?: (traceId: string) => void;
	/** When set, the job link is rendered as a clickable button. */
	onOpenJob?: (jobId: string) => void;
}

function formatTimestamp(dateString: string): string {
	return new Date(dateString).toLocaleString('en-US', {
		month: 'short',
		day: 'numeric',
		hour: '2-digit',
		minute: '2-digit',
	});
}

function VendorLogo({ vendor, size = 36 }: { vendor: string; size?: number }) {
	const config = getVendorConfig(vendor);
	return (
		<div
			className="flex items-center justify-center overflow-hidden rounded-xl"
			style={{ width: size, height: size, backgroundColor: config.bg }}
		>
			{config.iconUrl ? (
				<img
					src={config.iconUrl}
					alt={vendor}
					className="object-contain"
					style={{
						width: size * 0.55,
						height: size * 0.55,
						filter: config.text === '#fff' ? 'invert(1)' : undefined,
					}}
				/>
			) : (
				<span className="font-bold" style={{ fontSize: size * 0.35, color: config.text }}>
					{getInitials(vendor)}
				</span>
			)}
		</div>
	);
}

function MetricPill({
	icon,
	label,
	value,
	mono,
}: {
	icon: JSX.Element;
	label: string;
	value: string;
	mono?: boolean;
}) {
	return (
		<div className="border-border bg-muted/30 flex items-center gap-2.5 rounded-lg border px-3 py-2">
			<div className="bg-muted flex h-7 w-7 shrink-0 items-center justify-center rounded-md">
				{icon}
			</div>
			<div className="min-w-0">
				<p className="text-muted-foreground text-[10px] tracking-wider uppercase">
					{label}
				</p>
				<p
					className={cn(
						'text-foreground truncate text-sm font-medium',
						mono && 'font-mono',
					)}
				>
					{value}
				</p>
			</div>
		</div>
	);
}

export function ExecutionDetailSheet({
	execution,
	isOpen,
	isLoading,
	onClose,
	side = 'right',
	onOpenTrace,
	onOpenJob,
}: ExecutionDetailSheetProps): JSX.Element {
	return (
		<SheetPrimitive
			open={isOpen}
			onClose={onClose}
			side={side}
			ariaLabel="Execution detail"
			className="w-full sm:w-[480px] sm:max-w-[90vw]"
		>
			<div className="flex h-full flex-col">
				<div className="border-border flex items-center justify-between border-b px-4 py-3">
					<h2 className="text-foreground text-sm font-semibold">Execution Detail</h2>
					<button
						type="button"
						onClick={onClose}
						className="text-muted-foreground hover:bg-muted hover:text-foreground cursor-pointer rounded-md p-1.5 transition-colors"
						aria-label="Close"
					>
						<X className="h-4 w-4" />
					</button>
				</div>

				<div className="flex-1 overflow-y-auto">
					{isLoading ? (
						<div className="flex items-center justify-center py-16">
							<LoadingSpinner size="lg" />
						</div>
					) : execution ? (
						<div className="space-y-5 p-4">
							<div className="flex items-start gap-3.5">
								<VendorLogo vendor={execution.apiVendor ?? 'unknown'} size={40} />
								<div className="min-w-0 flex-1">
									<h3 className="text-foreground truncate text-base font-semibold">
										{execution.workflowName ??
											execution.operationName ??
											'Execution'}
									</h3>
									<p className="text-muted-foreground mt-0.5 text-sm">
										{execution.apiName ?? '—'} · {execution.apiVendor ?? '—'}
									</p>
								</div>
								<div
									className={cn(
										'inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium',
										execution.status === 'COMPLETED' &&
											'bg-accent-green/10 text-accent-green',
										execution.status === 'FAILED' &&
											'bg-accent-red/10 text-accent-red',
										execution.status === 'RUNNING' &&
											'bg-accent-blue/10 text-accent-blue',
										execution.status === 'QUEUED' &&
											'bg-muted text-muted-foreground',
										execution.status === 'PRE_CHECK' &&
											'bg-accent-amber/10 text-accent-amber',
									)}
								>
									<StatusIcon status={execution.status} size="sm" />
									{getStatusLabel(execution.status)}
								</div>
							</div>

							{execution.errorMessage && (
								<div className="border-accent-red/20 bg-accent-red/5 flex items-start gap-2.5 rounded-lg border p-3">
									<AlertCircle className="text-accent-red mt-0.5 h-4 w-4 shrink-0" />
									<p className="text-accent-red text-sm leading-relaxed">
										{execution.errorMessage}
									</p>
								</div>
							)}

							{execution.isSeedOnlyRow && (
								<div
									className="border-border bg-muted/30 flex items-center gap-2 rounded-lg border px-3 py-2"
									role="note"
								>
									<AlertCircle className="text-muted-foreground h-3.5 w-3.5 shrink-0" />
									<p className="text-muted-foreground text-xs">
										Limited data available for this execution.
									</p>
								</div>
							)}

							<div className="grid grid-cols-2 gap-2">
								<MetricPill
									icon={<Clock className="text-muted-foreground h-3.5 w-3.5" />}
									label="Started"
									value={formatTimestamp(execution.createdAt)}
								/>
								{execution.durationMs !== undefined && (
									<MetricPill
										icon={<Zap className="text-muted-foreground h-3.5 w-3.5" />}
										label="Duration"
										value={formatDuration(execution.durationMs)}
										mono
									/>
								)}
								<MetricPill
									icon={<Layers className="text-muted-foreground h-3.5 w-3.5" />}
									label="Toolkit"
									value={execution.toolkitName ?? '—'}
								/>
								<MetricPill
									icon={<User className="text-muted-foreground h-3.5 w-3.5" />}
									label="Agent"
									value={execution.agentName ?? '—'}
								/>
							</div>

							<div className="border-border bg-muted/20 rounded-lg border px-3 py-2">
								<p className="text-muted-foreground text-[10px] tracking-wider uppercase">
									Execution ID
								</p>
								<p className="text-muted-foreground mt-0.5 truncate font-mono text-xs">
									{execution.executionId}
								</p>
							</div>

							{(() => {
								const parentTraceId = execution.parentTraceId;
								const jobId = execution.jobId;
								if (!parentTraceId && !jobId) return null;
								return (
									<div className="border-border bg-muted/20 space-y-1.5 rounded-lg border px-3 py-2">
										<p className="text-muted-foreground text-[10px] tracking-wider uppercase">
											Linked Context
										</p>
										{parentTraceId && (
											<div className="flex items-center gap-2">
												<span className="text-muted-foreground text-[10px] uppercase">
													Parent
												</span>
												{onOpenTrace ? (
													<button
														type="button"
														onClick={() => onOpenTrace(parentTraceId)}
														className="text-foreground hover:text-accent-blue truncate font-mono text-xs underline-offset-2 hover:underline"
													>
														{parentTraceId}
													</button>
												) : (
													<span className="text-muted-foreground truncate font-mono text-xs">
														{parentTraceId}
													</span>
												)}
											</div>
										)}
										{jobId && (
											<div className="flex items-center gap-2">
												<span className="text-muted-foreground text-[10px] uppercase">
													Job
												</span>
												{onOpenJob ? (
													<button
														type="button"
														onClick={() => onOpenJob(jobId)}
														className="text-foreground hover:text-accent-blue truncate font-mono text-xs underline-offset-2 hover:underline"
													>
														{jobId}
													</button>
												) : (
													<span className="text-muted-foreground truncate font-mono text-xs">
														{jobId}
													</span>
												)}
											</div>
										)}
									</div>
								);
							})()}

							{execution.stepRows && execution.stepRows.length > 0 && (
								<div className="space-y-2">
									<h4 className="text-muted-foreground text-xs font-semibold tracking-wider uppercase">
										Steps ({execution.stepRows.length})
									</h4>
									<div className="space-y-1.5">
										{execution.stepRows.map((step) => (
											<div
												key={`${step.stepIndex}-${step.stepId}`}
												className="border-border bg-muted/20 rounded-lg border p-2.5"
											>
												<div className="flex items-start gap-2">
													<div className="bg-primary/10 border-primary/30 text-primary flex h-5 w-5 shrink-0 items-center justify-center rounded-full border font-mono text-[10px]">
														{step.stepIndex + 1}
													</div>
													<div className="min-w-0 flex-1">
														<div className="flex flex-wrap items-center gap-1.5">
															<code className="text-muted-foreground font-mono text-[11px]">
																{step.stepId}
															</code>
															{step.operation && (
																<code className="text-foreground truncate font-mono text-xs">
																	{step.operation}
																</code>
															)}
															{step.httpStatus !== null && (
																<span className="text-muted-foreground bg-muted rounded px-1.5 py-0.5 font-mono text-[10px]">
																	HTTP {step.httpStatus}
																</span>
															)}
															{step.status && (
																<span
																	className={cn(
																		'rounded-full px-1.5 py-0.5 text-[10px] font-medium',
																		step.status === 'success' &&
																			'bg-accent-green/10 text-accent-green',
																		step.status === 'error' &&
																			'bg-accent-red/10 text-accent-red',
																		step.status !== 'success' &&
																			step.status !==
																				'error' &&
																			'bg-muted text-muted-foreground',
																	)}
																>
																	{step.status}
																</span>
															)}
														</div>
														{step.error && (
															<p className="text-accent-red mt-1 font-mono text-[11px] break-words whitespace-pre-wrap">
																{step.error}
															</p>
														)}
													</div>
												</div>
											</div>
										))}
									</div>
								</div>
							)}

							{execution.childTraces && execution.childTraces.length > 0 && (
								<div className="space-y-2">
									<h4 className="text-muted-foreground text-xs font-semibold tracking-wider uppercase">
										Child broker calls ({execution.childTraces.length})
									</h4>
									<div className="space-y-1.5">
										{execution.childTraces.map((child) => {
											const label =
												child.apiName ?? child.apiId ?? child.operationId;
											const inner = (
												<div className="flex items-start gap-2 text-left">
													<StatusIcon
														status={
															child.status === 'failed'
																? 'FAILED'
																: child.status === 'pending'
																	? 'RUNNING'
																	: 'COMPLETED'
														}
														size="sm"
													/>
													<div className="min-w-0 flex-1">
														<div className="flex flex-wrap items-center gap-1.5">
															{label && (
																<span className="text-foreground truncate text-xs font-medium">
																	{label}
																</span>
															)}
															{child.httpStatus !== null && (
																<span className="text-muted-foreground bg-muted rounded px-1.5 py-0.5 font-mono text-[10px]">
																	HTTP {child.httpStatus}
																</span>
															)}
															{child.durationMs !== null && (
																<span className="text-muted-foreground font-mono text-[10px]">
																	{formatDuration(
																		child.durationMs,
																	)}
																</span>
															)}
														</div>
														{child.operationId &&
															label !== child.operationId && (
																<code className="text-muted-foreground mt-0.5 block truncate font-mono text-[10px]">
																	{child.operationId}
																</code>
															)}
														<code className="text-muted-foreground/70 mt-0.5 block truncate font-mono text-[10px]">
															{child.id}
														</code>
													</div>
												</div>
											);
											return onOpenTrace ? (
												<button
													key={child.id}
													type="button"
													onClick={() => onOpenTrace(child.id)}
													className="border-border bg-muted/20 hover:bg-muted/40 hover:border-accent-blue/30 w-full cursor-pointer rounded-lg border p-2.5 transition-colors"
												>
													{inner}
												</button>
											) : (
												<div
													key={child.id}
													className="border-border bg-muted/20 rounded-lg border p-2.5"
												>
													{inner}
												</div>
											);
										})}
									</div>
								</div>
							)}

							{execution.inputs && Object.keys(execution.inputs).length > 0 && (
								<div className="space-y-2">
									<h4 className="text-muted-foreground text-xs font-semibold tracking-wider uppercase">
										Inputs
									</h4>
									<pre className="border-border bg-muted/30 text-foreground overflow-x-auto rounded-lg border p-3 font-mono text-xs leading-relaxed">
										{JSON.stringify(execution.inputs, null, 2)}
									</pre>
								</div>
							)}

							{execution.outputs && Object.keys(execution.outputs).length > 0 && (
								<div className="space-y-2">
									<h4 className="text-muted-foreground text-xs font-semibold tracking-wider uppercase">
										Outputs
									</h4>
									<pre className="border-border bg-muted/30 text-foreground overflow-x-auto rounded-lg border p-3 font-mono text-xs leading-relaxed">
										{JSON.stringify(execution.outputs, null, 2)}
									</pre>
								</div>
							)}
						</div>
					) : (
						<div className="flex flex-col items-center justify-center py-16 text-center">
							<div className="bg-muted mb-2 rounded-full p-3">
								<ArrowRight className="text-muted-foreground h-5 w-5" />
							</div>
							<p className="text-muted-foreground text-sm">
								Select an execution to view details
							</p>
						</div>
					)}
				</div>
			</div>
		</SheetPrimitive>
	);
}
