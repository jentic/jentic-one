/**
 * CallDetailDrawer — the Level-3 deep dive for a single tool call.
 *
 * Surfaces every field we have: method + path, operation_id, verdict (with the
 * `error` string as the rule reason on a deny), credential used (provider +
 * wire type + opaque id), http status, duration, tokens + est. cost, api
 * vendor/name/version, started_at, trace_id (often "unknown"), and origin.
 *
 * If the session has chat, a "View requesting chat" button hands off to the
 * ChatTurnDrawer (the page owns which turn opens). Ids are copyable.
 */
import { AlertTriangle, ArrowRight, MessageSquare, ShieldCheck, X } from 'lucide-react';
import { SheetPrimitive, Badge, StatusBadge, MethodBadge, CopyButton, Button } from '@/shared/ui';
import type { ProxyCall } from '@/modules/llm-proxy/api';
import {
	formatCost,
	formatDuration,
	formatTimestamp,
	formatTokens,
} from '@/modules/llm-proxy/lib/format';
import { cn } from '@/shared/lib/utils';
import { callTone } from '@/modules/llm-proxy/components/CallBlock';
import { CollapsibleSection } from '@/modules/llm-proxy/components/CollapsibleSection';

interface CallDetailDrawerProps {
	call: ProxyCall | null;
	open: boolean;
	onClose: () => void;
	/** When set, renders a "View requesting chat" button. */
	onViewChat?: (call: ProxyCall) => void;
	/** Portal target — the fullscreen element when the canvas is fullscreen. */
	container?: HTMLElement | null;
}

function Field({
	label,
	children,
	className,
}: {
	label: string;
	children: React.ReactNode;
	className?: string;
}) {
	return (
		<div className={className}>
			<div className="text-muted-foreground/70 mb-1 text-[10px] font-semibold tracking-wide uppercase">
				{label}
			</div>
			<div className="text-foreground text-sm">{children}</div>
		</div>
	);
}

function Mono({ children }: { children: React.ReactNode }) {
	return <span className="font-mono text-xs break-all">{children}</span>;
}

/** A JSON/text block rendered like ChatTurnDrawer's message previews. */
function CodeBlock({ children }: { children: React.ReactNode }) {
	return (
		<pre className="bg-muted/40 border-border/40 text-muted-foreground max-h-56 overflow-auto rounded-lg border p-3 font-mono text-[11px] leading-relaxed whitespace-pre-wrap">
			{children}
		</pre>
	);
}

/** Pretty-print an arbitrary value as JSON (falls back to String on cycles). */
function toJson(value: unknown): string {
	try {
		return JSON.stringify(value, null, 2);
	} catch {
		return String(value);
	}
}

/** One row in the lifecycle timeline: a stage label + its duration bar. */
function TimelineStage({
	label,
	ms,
	total,
	tone = 'bg-accent-blue',
	last = false,
}: {
	label: string;
	ms: number | null;
	total: number;
	tone?: string;
	last?: boolean;
}) {
	const pct = ms != null && total > 0 ? Math.max(2, Math.round((ms / total) * 100)) : 0;
	return (
		<div className="flex items-stretch gap-3">
			<div className="flex flex-col items-center">
				<span
					className={cn('mt-1 h-2 w-2 shrink-0 rounded-full', tone)}
					aria-hidden="true"
				/>
				{!last && <span className="bg-border/60 mt-0.5 w-px flex-1" aria-hidden="true" />}
			</div>
			<div className="flex-1 pb-3">
				<div className="flex items-center justify-between gap-2">
					<span className="text-foreground text-xs">{label}</span>
					<span className="text-muted-foreground shrink-0 font-mono text-[11px] tabular-nums">
						{ms != null ? `${ms}ms` : '—'}
					</span>
				</div>
				{ms != null && (
					<div className="bg-muted mt-1 h-1 w-full overflow-hidden rounded-full">
						<span
							className={cn('block h-full rounded-full', tone)}
							style={{ width: `${pct}%` }}
						/>
					</div>
				)}
			</div>
		</div>
	);
}

/** A scope pill; `granted` decides success vs muted styling. */
function ScopePill({ scope, granted }: { scope: string; granted: boolean }) {
	return (
		<span
			className={cn(
				'inline-flex items-center rounded border px-1.5 py-0.5 font-mono text-[10px]',
				granted
					? 'border-success/30 bg-success/[0.08] text-success'
					: 'border-danger/30 bg-danger/[0.08] text-danger',
			)}
		>
			{scope}
		</span>
	);
}

const VERDICT_BADGE = {
	allow: { variant: 'success' as const, label: 'Allowed' },
	deny: { variant: 'danger' as const, label: 'Denied' },
	error: { variant: 'warning' as const, label: 'Error' },
};

export function CallDetailDrawer({
	call,
	open,
	onClose,
	onViewChat,
	container,
}: CallDetailDrawerProps) {
	const tone = call ? callTone(call) : 'allow';
	const verdict = VERDICT_BADGE[tone];

	return (
		<SheetPrimitive
			open={open}
			onClose={onClose}
			side="right"
			ariaLabel="Tool call detail"
			className="sm:w-[560px]"
			container={container}
		>
			{call && (
				<div className="flex h-full flex-col">
					<div className="border-border/60 flex items-start justify-between gap-3 border-b px-5 py-4">
						<div className="min-w-0">
							<div className="mb-1.5 flex items-center gap-2">
								<MethodBadge method={call.method} />
								<Badge variant={verdict.variant} dot>
									{verdict.label}
								</Badge>
								{call.destructive && (
									<Badge variant="warning">
										<AlertTriangle className="mr-0.5 h-3 w-3" />
										destructive
									</Badge>
								)}
							</div>
							<h2 className="text-foreground font-mono text-sm font-semibold break-all">
								{call.path}
							</h2>
							<p className="text-muted-foreground mt-0.5 text-xs">{call.summary}</p>
						</div>
						<button
							type="button"
							onClick={onClose}
							aria-label="Close"
							className="text-muted-foreground hover:text-foreground shrink-0 rounded p-1 transition-colors"
						>
							<X className="h-4 w-4" />
						</button>
					</div>

					<div className="flex-1 space-y-5 overflow-y-auto px-5 py-4">
						{call.synthesised && (
							<div className="border-accent-orange/30 bg-accent-orange/[0.07] text-accent-orange rounded-lg border px-3 py-2 text-xs">
								Synthesised demo row — not from the real run (added so every
								verdict/outcome state renders).
							</div>
						)}

						{/* Governance verdict + rule reason */}
						<div
							className={cn(
								'rounded-lg border px-3 py-2.5',
								tone === 'deny'
									? 'border-danger/40 bg-danger/[0.06]'
									: tone === 'error'
										? 'border-warning/40 bg-warning/[0.06]'
										: 'border-success/30 bg-success/[0.06]',
							)}
						>
							<div className="text-muted-foreground/70 mb-1 text-[10px] font-semibold tracking-wide uppercase">
								Governance
							</div>
							<div className="flex items-center gap-2">
								<Badge variant={verdict.variant} dot>
									{verdict.label}
								</Badge>
								<StatusBadge status={call.http_status} />
							</div>
							{call.error && (
								<p className="text-foreground mt-2 text-xs leading-relaxed">
									<span className="text-muted-foreground">Reason: </span>
									{call.error}
								</p>
							)}
						</div>

						<div className="grid grid-cols-2 gap-4">
							<Field label="Duration">{formatDuration(call.duration_ms)}</Field>
							<Field label="Started">{formatTimestamp(call.started_at)}</Field>
							<Field label="Tokens in / out">
								<span className="tabular-nums">
									{formatTokens(call.tokens_in)} / {formatTokens(call.tokens_out)}
								</span>
							</Field>
							<Field label="Est. cost">{formatCost(call.cost_usd)}</Field>
							<Field label="Origin">
								<Mono>{call.origin}</Mono>
							</Field>
							<Field label="HTTP status">
								{call.http_status ?? (
									<span className="text-muted-foreground">—</span>
								)}
							</Field>
						</div>

						{/* API */}
						<div className="border-border/50 rounded-lg border p-3">
							<div className="text-muted-foreground/70 mb-2 text-[10px] font-semibold tracking-wide uppercase">
								API
							</div>
							<div className="grid grid-cols-2 gap-3 text-sm">
								<Field label="Vendor">
									<Mono>{call.api_vendor}</Mono>
								</Field>
								<Field label="Name">
									<Mono>{call.api_name}</Mono>
								</Field>
								<Field label="Version">
									<Mono>{call.api_version}</Mono>
								</Field>
								<Field label="Operation">
									<div className="flex items-center gap-1">
										<Mono>{call.operation_id}</Mono>
										<CopyButton
											value={call.operation_id}
											variant="ghost"
											size="icon"
											ariaLabel="Copy operation id"
										/>
									</div>
								</Field>
							</div>
						</div>

						{/* Credential */}
						<div className="border-border/50 rounded-lg border p-3">
							<div className="text-muted-foreground/70 mb-2 text-[10px] font-semibold tracking-wide uppercase">
								Credential
							</div>
							{call.credential_id ? (
								<div className="grid grid-cols-2 gap-3 text-sm">
									<Field label="Provider">
										<Mono>{call.credential_provider}</Mono>
									</Field>
									<Field label="Wire type">
										<Mono>{call.credential_wire_type}</Mono>
									</Field>
									<Field label="Credential id" className="col-span-2">
										<div className="flex items-center gap-1">
											<Mono>{call.credential_id}</Mono>
											<CopyButton
												value={call.credential_id}
												variant="ghost"
												size="icon"
												ariaLabel="Copy credential id"
											/>
										</div>
									</Field>
								</div>
							) : (
								<p className="text-muted-foreground text-xs">
									No credential was injected (denied before credential access).
								</p>
							)}
						</div>

						{/* --- Progressive disclosure: collapsed by default, no added noise. --- */}
						<div className="space-y-2">
							<CollapsibleSection
								title="Request / Response"
								icon={<ArrowRight className="h-4 w-4" />}
							>
								<div className="space-y-3">
									{call.request?.params &&
									Object.keys(call.request.params).length > 0 ? (
										<div>
											<div className="text-muted-foreground/70 mb-1 text-[10px] font-semibold tracking-wide uppercase">
												Request params
											</div>
											<CodeBlock>{toJson(call.request.params)}</CodeBlock>
										</div>
									) : null}
									{call.request?.body !== undefined &&
									call.request?.body !== null ? (
										<div>
											<div className="text-muted-foreground/70 mb-1 text-[10px] font-semibold tracking-wide uppercase">
												Request body
											</div>
											<CodeBlock>{toJson(call.request.body)}</CodeBlock>
										</div>
									) : null}
									<div>
										<div className="text-muted-foreground/70 mb-1 text-[10px] font-semibold tracking-wide uppercase">
											Response
										</div>
										{call.response_snippet ? (
											<CodeBlock>{call.response_snippet}</CodeBlock>
										) : (
											<p className="text-muted-foreground text-xs italic">
												No response body captured.
											</p>
										)}
									</div>
									<p className="text-muted-foreground/70 text-[10px] italic">
										Secrets are redacted to{' '}
										<span className="font-mono">***</span> before display.
									</p>
								</div>
							</CollapsibleSection>

							<CollapsibleSection
								title="Timeline"
								icon={<ArrowRight className="h-4 w-4" />}
								meta={formatDuration(call.duration_ms)}
							>
								{call.timeline ? (
									(() => {
										const t = call.timeline;
										const total =
											t.queued_ms +
											t.policy_ms +
											(t.credential_ms ?? 0) +
											(t.upstream_ms ?? 0);
										return (
											<div>
												<TimelineStage
													label="Queued"
													ms={t.queued_ms}
													total={total}
													tone="bg-muted-foreground/50"
												/>
												<TimelineStage
													label={
														tone === 'deny'
															? 'Policy check → denied'
															: 'Policy check'
													}
													ms={t.policy_ms}
													total={total}
													tone={
														tone === 'deny'
															? 'bg-danger'
															: 'bg-accent-blue'
													}
													last={tone === 'deny'}
												/>
												{tone !== 'deny' && (
													<>
														<TimelineStage
															label="Credential inject"
															ms={t.credential_ms}
															total={total}
															tone="bg-accent-blue"
														/>
														<TimelineStage
															label="Upstream request → response"
															ms={t.upstream_ms}
															total={total}
															tone={
																tone === 'error'
																	? 'bg-warning'
																	: 'bg-success'
															}
															last
														/>
													</>
												)}
											</div>
										);
									})()
								) : (
									<p className="text-muted-foreground text-xs italic">
										No timeline captured for this call.
									</p>
								)}
							</CollapsibleSection>

							<CollapsibleSection
								title="Governance detail"
								icon={<ShieldCheck className="h-4 w-4" />}
								meta={call.rule?.matched ? call.rule.id : undefined}
							>
								<div className="space-y-3">
									<Field label="Matched rule">
										{call.rule ? (
											<span>
												<span className="font-medium">
													{call.rule.name}
												</span>{' '}
												<span className="text-muted-foreground font-mono text-xs">
													({call.rule.id})
												</span>
											</span>
										) : (
											<span className="text-muted-foreground italic">
												No rule matched — default deny.
											</span>
										)}
									</Field>
									<div className="grid grid-cols-2 gap-4">
										<Field label="Scopes required">
											{call.scopes_required &&
											call.scopes_required.length > 0 ? (
												<div className="flex flex-wrap gap-1">
													{call.scopes_required.map((s) => (
														<ScopePill
															key={s}
															scope={s}
															granted={(
																call.scopes_granted ?? []
															).includes(s)}
														/>
													))}
												</div>
											) : (
												<span className="text-muted-foreground">—</span>
											)}
										</Field>
										<Field label="Scopes granted">
											{call.scopes_granted &&
											call.scopes_granted.length > 0 ? (
												<div className="flex flex-wrap gap-1">
													{call.scopes_granted.map((s) => (
														<ScopePill key={s} scope={s} granted />
													))}
												</div>
											) : (
												<span className="text-muted-foreground">—</span>
											)}
										</Field>
									</div>
									{tone === 'deny' && call.grant_hint && (
										<div className="border-danger/30 bg-danger/[0.06] rounded-lg border px-3 py-2">
											<div className="text-danger mb-1 text-[10px] font-semibold tracking-wide uppercase">
												How to grant
											</div>
											<p className="text-foreground text-xs leading-relaxed">
												{call.grant_hint}
											</p>
										</div>
									)}
								</div>
							</CollapsibleSection>
						</div>

						{/* Correlation */}
						<div className="grid grid-cols-2 gap-4">
							<Field label="Call id">
								<div className="flex items-center gap-1">
									<Mono>{call.call_id}</Mono>
									<CopyButton
										value={call.call_id}
										variant="ghost"
										size="icon"
										ariaLabel="Copy call id"
									/>
								</div>
							</Field>
							<Field label="Trace id">
								{call.trace_id === 'unknown' ? (
									<span
										className="text-muted-foreground italic"
										title="No correlation id was recorded — the keystone gap this feature exists to close."
									>
										unknown
									</span>
								) : (
									<Mono>{call.trace_id}</Mono>
								)}
							</Field>
						</div>
					</div>

					{onViewChat && (
						<div className="border-border/60 border-t px-5 py-3">
							<Button variant="secondary" fullWidth onClick={() => onViewChat(call)}>
								<MessageSquare className="h-4 w-4" />
								View requesting chat
							</Button>
						</div>
					)}
				</div>
			)}
		</SheetPrimitive>
	);
}
