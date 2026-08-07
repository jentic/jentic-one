/**
 * Faux-Slack rendering of the notification a `slack` destination receives.
 * Purely illustrative: shows operators what lands in their channel before
 * they commit, mirroring the "message preview" pattern of Grafana contact
 * points and Sentry Slack integrations.
 */
export function SlackMessagePreview({ eventType }: { eventType?: string }) {
	const type = eventType || 'agent.self_registered';
	const bad = type.includes('failed') || type.includes('expired') || type.startsWith('security.');
	const emoji = bad ? '🔴' : '🟢';
	const summary = bad
		? 'Something needs attention — open jentic-one for details.'
		: 'New activity on your jentic-one instance.';
	return (
		<div
			className="border-border bg-surface rounded-lg border p-3"
			aria-label="Slack message preview"
		>
			<div className="flex items-start gap-2.5">
				<div
					className="bg-primary/15 text-primary flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-sm font-bold"
					aria-hidden="true"
				>
					j1
				</div>
				<div className="min-w-0 space-y-0.5">
					<p className="text-foreground text-xs">
						<span className="font-semibold">jentic-one</span>{' '}
						<span className="bg-muted text-muted-foreground rounded px-1 py-px text-[10px] font-medium tracking-wide uppercase">
							App
						</span>{' '}
						<span className="text-muted-foreground">just now</span>
					</p>
					<p className="text-foreground text-sm">
						{emoji} <span className="font-semibold">{type}</span>
					</p>
					<p className="text-foreground text-sm">{summary}</p>
					<p className="text-muted-foreground text-xs">
						<span className="text-primary underline decoration-dotted">
							View in jentic-one
						</span>{' '}
						· evt_00000000000000000042
					</p>
				</div>
			</div>
		</div>
	);
}
