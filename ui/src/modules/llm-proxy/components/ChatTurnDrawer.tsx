/**
 * ChatTurnDrawer — deep-dive for a single proxy round-trip (a "chat turn").
 *
 * Secondary to tool calls but always reachable: shows the model, message count,
 * the first user message, the assistant's text, the tool_uses it emitted, and
 * latency + token usage. Slides in from the right via SheetPrimitive.
 */
import { MessageSquare, X } from 'lucide-react';
import { SheetPrimitive, Badge } from '@/shared/ui';
import type { ChatTurn } from '@/modules/llm-proxy/api';
import { formatDuration, formatTimestamp } from '@/modules/llm-proxy/lib/format';

interface ChatTurnDrawerProps {
	turn: ChatTurn | null;
	open: boolean;
	onClose: () => void;
	/** Portal target — the fullscreen element when the canvas is fullscreen. */
	container?: HTMLElement | null;
}

/** ISO string or epoch-seconds float → display timestamp. */
function turnTime(ts: string | number): string {
	if (typeof ts === 'number') return formatTimestamp(new Date(ts * 1000).toISOString());
	return formatTimestamp(ts);
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
	return (
		<div>
			<div className="text-muted-foreground/70 mb-1 text-[10px] font-semibold tracking-wide uppercase">
				{label}
			</div>
			<div className="text-foreground text-sm">{children}</div>
		</div>
	);
}

export function ChatTurnDrawer({ turn, open, onClose, container }: ChatTurnDrawerProps) {
	return (
		<SheetPrimitive
			open={open}
			onClose={onClose}
			side="right"
			ariaLabel="Chat turn detail"
			className="sm:w-[540px]"
			container={container}
		>
			{turn && (
				<div className="flex h-full flex-col">
					<div className="border-border/60 flex items-start justify-between gap-3 border-b px-5 py-4">
						<div className="flex items-start gap-2">
							<MessageSquare className="text-accent-blue mt-0.5 h-5 w-5 shrink-0" />
							<div>
								<h2 className="text-foreground text-base font-semibold">
									Chat turn
								</h2>
								<p className="text-muted-foreground font-mono text-xs">
									{turn.turn_id}
								</p>
							</div>
						</div>
						<div className="flex items-center gap-2">
							{turn.status && (
								<Badge variant={turn.status === 'success' ? 'success' : 'warning'}>
									{turn.status}
								</Badge>
							)}
							<button
								type="button"
								onClick={onClose}
								aria-label="Close"
								className="text-muted-foreground hover:text-foreground rounded p-1 transition-colors"
							>
								<X className="h-4 w-4" />
							</button>
						</div>
					</div>

					<div className="flex-1 space-y-5 overflow-y-auto px-5 py-4">
						<div className="grid grid-cols-2 gap-4">
							<Field label="Model">
								<span className="font-mono text-xs break-all">
									{turn.model ?? '—'}
								</span>
							</Field>
							<Field label="Messages">
								<span className="tabular-nums">{turn.n_messages ?? '—'}</span>
							</Field>
							<Field label="Latency">{formatDuration(turn.latency_ms)}</Field>
							<Field label="Time">{turnTime(turn.ts)}</Field>
						</div>

						<Field label="First user message">
							<pre className="bg-muted/40 border-border/40 text-muted-foreground max-h-40 overflow-auto rounded-lg border p-3 font-mono text-[11px] leading-relaxed whitespace-pre-wrap">
								{turn.first_user_msg || '—'}
							</pre>
						</Field>

						<Field label="Assistant">
							<div className="bg-accent-blue/[0.06] border-accent-blue/20 rounded-lg border p-3 text-[13px] leading-relaxed">
								{turn.assistant_text || '—'}
							</div>
						</Field>

						{turn.tool_uses.length > 0 && (
							<div>
								<div className="text-muted-foreground/70 mb-2 text-[10px] font-semibold tracking-wide uppercase">
									Tool uses ({turn.tool_uses.length})
								</div>
								<div className="space-y-2">
									{turn.tool_uses.map((tu, i) => (
										<div
											key={`${tu.name}-${i}`}
											className="border-border/50 bg-card/60 rounded-lg border p-2.5"
										>
											<div className="mb-1 flex items-center gap-2">
												<Badge variant="default">{tu.name}</Badge>
											</div>
											<pre className="text-muted-foreground max-h-32 overflow-auto font-mono text-[11px] leading-relaxed whitespace-pre-wrap">
												{tu.preview}
											</pre>
										</div>
									))}
								</div>
							</div>
						)}

						{turn.usage && (
							<Field label="Token usage">
								<dl className="border-border/50 divide-border/40 divide-y rounded-lg border">
									{Object.entries(turn.usage).map(([k, v]) => (
										<div
											key={k}
											className="flex items-center justify-between px-3 py-1.5 text-xs"
										>
											<dt className="text-muted-foreground capitalize">
												{k.replace(/_/g, ' ')}
											</dt>
											<dd className="text-foreground font-mono tabular-nums">
												{typeof v === 'number'
													? v.toLocaleString()
													: String(v)}
											</dd>
										</div>
									))}
								</dl>
							</Field>
						)}
					</div>
				</div>
			)}
		</SheetPrimitive>
	);
}
