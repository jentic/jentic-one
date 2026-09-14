/**
 * ActorsDiagram — companion to FlowDiagram, focused on how the actor types
 * *relate* (not the request path). The shape mirrors the platform's identity
 * model: a human user registers autonomous agents; agents (and non-human
 * service accounts) carry directly bound credentials the Broker injects at
 * execution time. Service accounts have no human in the loop.
 *
 * Same layout-primitive + lucide-arrow approach as FlowDiagram so it inherits
 * the theme tokens and reflows on mobile without a binary asset.
 */
import { UserRound, Bot, Server, KeyRound, CornerDownRight } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';

function ActorNode({
	icon: Icon,
	title,
	subtitle,
	accent,
}: {
	icon: LucideIcon;
	title: string;
	subtitle: string;
	accent: string;
}) {
	return (
		<div className="border-border bg-card/60 flex w-full max-w-xs items-start gap-3 rounded-lg border px-4 py-3">
			<Icon className={`mt-0.5 h-6 w-6 shrink-0 ${accent}`} aria-hidden="true" />
			<div className="min-w-0">
				<code className="text-foreground font-mono text-sm font-semibold">{title}</code>
				<p className="text-foreground/55 mt-0.5 text-[11px] leading-snug">{subtitle}</p>
			</div>
		</div>
	);
}

/** A labeled connector that reads as "parent —relation→ child". */
function Edge({ label }: { label: string }) {
	return (
		<div className="flex items-center gap-1 pl-3">
			<span className="border-border/60 h-4 border-l" aria-hidden="true" />
			<span className="text-foreground/65 text-[11px] whitespace-nowrap">{label}</span>
		</div>
	);
}

export function ActorsDiagram() {
	return (
		<figure className="border-border bg-background/30 rounded-xl border p-4 sm:p-6">
			<figcaption className="text-foreground/55 mb-4 text-xs">
				How the actor types relate — who creates whom, and what carries credentials.
			</figcaption>

			<div className="flex flex-col gap-2">
				{/* Human at the root. */}
				<ActorNode
					icon={UserRound}
					title="user"
					subtitle="Human operator. Signs in to the dashboard / CLI and registers agents."
					accent="text-accent-blue"
				/>

				<Edge label="registers / owns" />

				{/* Branch: agent (human-backed) and service account (standalone). */}
				<div className="flex flex-col gap-3 pl-3 sm:flex-row sm:items-start">
					<div className="border-border/50 flex flex-1 flex-col gap-2 rounded-lg border border-dashed p-3">
						<div className="text-foreground/65 flex items-center gap-1.5 text-[11px]">
							<CornerDownRight className="h-3.5 w-3.5" aria-hidden="true" />
							acts on the user’s behalf
						</div>
						<ActorNode
							icon={Bot}
							title="agent"
							subtitle="Autonomous identity (Ed25519 key). Brokers calls for its user."
							accent="text-accent-green"
						/>
					</div>

					<div className="border-border/50 flex flex-1 flex-col gap-2 rounded-lg border border-dashed p-3">
						<div className="text-foreground/65 flex items-center gap-1.5 text-[11px]">
							<CornerDownRight className="h-3.5 w-3.5" aria-hidden="true" />
							no human in the loop
						</div>
						<ActorNode
							icon={Server}
							title="service_account"
							subtitle="Non-human integration identity. Mints its own task tokens."
							accent="text-accent-orange"
						/>
					</div>
				</div>

				<Edge label="both carry" />

				{/* Credentials hang off agents and service accounts via direct
				    bindings (not an actor type — no token is issued for them). */}
				<div className="pl-3">
					<ActorNode
						icon={KeyRound}
						title="credential"
						subtitle="Bound directly to an agent or service account, with per-binding permission rules. The Broker injects its secrets at execution time."
						accent="text-accent-pink"
					/>
				</div>
			</div>

			<p className="text-foreground/50 border-border/50 mt-4 border-t pt-3 text-[11px] leading-relaxed">
				The type says <em>who</em> an identity is. What it may actually do is governed
				separately by scopes and ownership — see{' '}
				<a href="#permissions" className="text-primary underline">
					Permissions &amp; scopes
				</a>
				.
			</p>
		</figure>
	);
}
