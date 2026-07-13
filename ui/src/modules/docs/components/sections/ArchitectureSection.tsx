/**
 * ArchitectureSection — the conceptual model: the components that make up the
 * platform and the actor types that call it. Content adapted from the README
 * components table and the actor vocabulary (ActorType).
 */
import { Boxes, Database, Cpu, KeyRound, ClipboardList, Layers } from 'lucide-react';
import { DocsSectionBlock } from '@/modules/docs/components/DocsSectionBlock';
import { FlowDiagram } from '@/modules/docs/components/FlowDiagram';
import { ActorsDiagram } from '@/modules/docs/components/ActorsDiagram';

const COMPONENTS = [
	{
		icon: Cpu,
		name: 'Broker',
		body: 'Stateless execution proxy. Takes the upstream URL as its path, injects the caller’s stored credentials, forwards method/headers/body, and returns the response. Secrets never leave the Broker.',
	},
	{
		icon: Database,
		name: 'Registry',
		body: 'API specification catalogue. Stores registered APIs with immutable revisions, operations, security schemes, and servers. Owns what is available and at which version.',
	},
	{
		icon: KeyRound,
		name: 'Control',
		body: 'Credential storage. Manages polymorphic API credentials (API keys, OAuth2 client credentials, bearer tokens, basic auth) used by the Broker at execution time.',
	},
	{
		icon: ClipboardList,
		name: 'Admin',
		body: 'Users, permissions, jobs, audit, and execution telemetry. Owns the human roster, access grants, async job lifecycle, and the append-only audit log.',
	},
	{
		icon: Layers,
		name: 'Shared',
		body: 'Internal infrastructure: configuration, async database sessions, structured logging, metrics, and the multi-surface application factory.',
	},
];

const ACTORS = [
	{
		name: 'user',
		body: 'A human, authenticated by login; operates the platform via the dashboard or CLI.',
	},
	{
		name: 'agent',
		body: 'An autonomous identity (Ed25519 keypair) that brokers API calls on a human’s behalf.',
	},
	{
		name: 'service_account',
		body: 'A non-human programmatic identity for backend integrations; mints task tokens.',
	},
	{
		name: 'toolkit',
		body: 'A grouping that binds credentials and rides the agent token flow at execution time.',
	},
];

export function ArchitectureSection() {
	return (
		<DocsSectionBlock
			id="architecture"
			title="Architecture"
			icon={Boxes}
			intro="Two peer units over a shared database: the App (control plane) decides and records; the Broker (data plane) executes."
		>
			<FlowDiagram />

			<div className="grid gap-3 sm:grid-cols-2">
				{COMPONENTS.map((c) => (
					<div key={c.name} className="border-border bg-card/50 rounded-lg border p-4">
						<div className="flex items-center gap-2">
							<c.icon className="text-primary h-5 w-5" aria-hidden="true" />
							<p className="font-heading text-foreground font-semibold">{c.name}</p>
						</div>
						<p className="text-foreground/65 mt-1.5 text-sm leading-relaxed">
							{c.body}
						</p>
					</div>
				))}
			</div>

			<div>
				<h3 className="font-heading text-foreground mb-2 text-base font-semibold">
					Actors
				</h3>
				<p className="text-foreground/65 mb-2 max-w-2xl text-sm">
					Every authenticated call belongs to one of four actor types. What an actor may
					do is decided by its scopes and ownership — not its type (see{' '}
					<a href="#permissions" className="text-primary underline">
						Permissions
					</a>
					).
				</p>
				<ActorsDiagram />
				<div className="border-border mt-3 overflow-hidden rounded-lg border">
					<table className="w-full text-left text-sm">
						<tbody className="divide-border/50 divide-y">
							{ACTORS.map((a) => (
								<tr key={a.name} className="align-top">
									<td className="px-3 py-2 font-mono text-xs whitespace-nowrap">
										{a.name}
									</td>
									<td className="text-foreground/70 px-3 py-2">{a.body}</td>
								</tr>
							))}
						</tbody>
					</table>
				</div>
			</div>
		</DocsSectionBlock>
	);
}
