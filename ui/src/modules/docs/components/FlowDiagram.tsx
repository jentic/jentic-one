/**
 * FlowDiagram — a high-level "how it fits together" picture for the Architecture
 * section. It is deliberately conceptual (not an exhaustive component graph):
 * an actor authenticates, the App (control plane) decides & records against the
 * shared database, and the Broker (data plane) injects credentials and forwards
 * the call to the upstream API. Secrets stay inside the Broker.
 *
 * Built from layout primitives + a couple of SVG arrows so it inherits the
 * theme tokens and stays legible in dark mode without a binary asset.
 */
import { UserRound, Server, Cpu, Database, KeyRound, ArrowRight, ArrowDown } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';

function Node({
	icon: Icon,
	title,
	subtitle,
	accent = 'text-primary',
	className,
}: {
	icon: LucideIcon;
	title: string;
	subtitle: string;
	accent?: string;
	className?: string;
}) {
	return (
		<div
			className={`border-border bg-card/60 flex min-w-[8.5rem] flex-col items-center rounded-lg border px-4 py-3 text-center ${className ?? ''}`}
		>
			<Icon className={`mb-1 h-6 w-6 ${accent}`} aria-hidden="true" />
			<p className="text-foreground text-sm font-semibold">{title}</p>
			<p className="text-foreground/55 text-[11px] leading-snug">{subtitle}</p>
		</div>
	);
}

function ArrowX({ label }: { label: string }) {
	return (
		<div className="flex flex-col items-center px-1">
			<span className="text-foreground/50 mb-0.5 text-[10px] whitespace-nowrap">{label}</span>
			<ArrowRight className="text-foreground/40 h-5 w-5" aria-hidden="true" />
		</div>
	);
}

export function FlowDiagram() {
	return (
		<figure className="border-border bg-background/30 rounded-xl border p-4 sm:p-6">
			<figcaption className="text-foreground/55 mb-4 text-xs">
				High-level request flow — the control plane decides &amp; records; the data plane
				executes.
			</figcaption>

			{/* Top row: actor → app → broker → upstream (wraps to vertical on mobile). */}
			<div className="flex flex-col items-stretch gap-3 sm:flex-row sm:items-center sm:justify-center">
				<Node
					icon={UserRound}
					title="Actor"
					subtitle="user · agent"
					accent="text-accent-blue"
				/>
				<div className="flex justify-center sm:block">
					<div className="hidden sm:block">
						<ArrowX label="authenticated call" />
					</div>
					<ArrowDown
						className="text-foreground/40 h-5 w-5 sm:hidden"
						aria-hidden="true"
					/>
				</div>

				<Node
					icon={Server}
					title="App"
					subtitle="control plane — Registry · Control · Admin"
					accent="text-accent-green"
					className="sm:min-w-[12rem]"
				/>
				<div className="flex justify-center sm:block">
					<div className="hidden sm:block">
						<ArrowX label="execute via" />
					</div>
					<ArrowDown
						className="text-foreground/40 h-5 w-5 sm:hidden"
						aria-hidden="true"
					/>
				</div>

				<Node
					icon={Cpu}
					title="Broker"
					subtitle="data plane — injects credentials"
					accent="text-accent-orange"
				/>
				<div className="flex justify-center sm:block">
					<div className="hidden sm:block">
						<ArrowX label="forwards request" />
					</div>
					<ArrowDown
						className="text-foreground/40 h-5 w-5 sm:hidden"
						aria-hidden="true"
					/>
				</div>

				<Node
					icon={KeyRound}
					title="Upstream API"
					subtitle="third-party service"
					accent="text-accent-pink"
				/>
			</div>

			{/* Shared substrate underneath App + Broker. */}
			<div className="mt-4 flex flex-col items-center">
				<ArrowDown className="text-foreground/30 h-4 w-4" aria-hidden="true" />
				<div className="border-border/70 bg-muted/30 mt-1 flex items-center gap-2 rounded-lg border px-4 py-2">
					<Database className="text-foreground/55 h-4 w-4" aria-hidden="true" />
					<span className="text-foreground/70 text-xs">
						Shared PostgreSQL — catalogue, identities, grants, audit &amp; executions
					</span>
				</div>
			</div>

			<p className="text-foreground/50 mt-4 text-[11px] leading-relaxed">
				Credentials are stored encrypted and only ever decrypted inside the Broker at
				execution time — they never reach the actor or leave the data plane.
			</p>
		</figure>
	);
}
