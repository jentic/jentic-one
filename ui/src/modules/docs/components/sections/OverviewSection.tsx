/**
 * OverviewSection — the docs landing: what Jentic One is, the three governance
 * questions it answers, and the App-vs-Broker shape of the system. Content is
 * adapted from README.md (tagline, architecture, components) and the product
 * roadmap's three-pillar framing.
 */
import { UserCheck, ShieldCheck, ScrollText, ArrowDown } from 'lucide-react';
import { JenticLogo } from '@/shared/ui';
import { DocsSectionBlock } from '@/modules/docs/components/DocsSectionBlock';

const PILLARS = [
	{
		icon: UserCheck,
		question: 'Who is acting?',
		body: 'Every call is tied to a verified identity — a human user, an autonomous agent, a service account, or a toolkit.',
	},
	{
		icon: ShieldCheck,
		question: 'Are they allowed to?',
		body: 'Coarse JWT scopes plus fine-grained per-binding permission rules decide what each identity may execute.',
	},
	{
		icon: ScrollText,
		question: 'What happened?',
		body: 'Every brokered request is recorded as an execution and an append-only audit event you can read back.',
	},
];

function PlaneBox({ title, sub, items }: { title: string; sub: string; items: string[] }) {
	return (
		<div className="border-border bg-card/60 rounded-lg border p-4">
			<p className="font-heading text-foreground font-semibold">{title}</p>
			<p className="text-foreground/50 mt-0.5 text-xs">{sub}</p>
			<div className="mt-3 flex flex-wrap gap-1.5">
				{items.map((it) => (
					<span
						key={it}
						className="border-border bg-muted/60 text-foreground/75 rounded-md border px-2 py-0.5 text-xs"
					>
						{it}
					</span>
				))}
			</div>
		</div>
	);
}

export function OverviewSection() {
	return (
		<DocsSectionBlock id="overview" title="Overview">
			{/* Hero */}
			<div className="border-border from-primary/10 via-card to-card relative overflow-hidden rounded-xl border bg-gradient-to-br p-6 sm:p-8">
				<JenticLogo width={150} height={46} />
				<h1 className="font-heading text-foreground mt-4 max-w-xl text-2xl font-black sm:text-3xl">
					Secure third-party API execution for AI agents.
				</h1>
				<p className="text-foreground/70 mt-3 max-w-2xl text-[15px] leading-relaxed">
					Jentic One is the execution-and-governance substrate for agent harnesses. A
					stateless <strong className="text-foreground">Broker</strong> injects stored
					credentials into outbound requests so secrets never leave the data plane, while
					the <strong className="text-foreground">Control Plane</strong> manages the
					catalogue of available APIs, identities, access, and credentials.
				</p>
			</div>

			{/* Three pillars */}
			<div>
				<p className="text-foreground/55 mb-2 text-sm font-medium">
					It answers three questions about every action:
				</p>
				<div className="grid gap-3 sm:grid-cols-3">
					{PILLARS.map((p) => (
						<div
							key={p.question}
							className="border-border bg-card/50 rounded-lg border p-4"
						>
							<p.icon className="text-primary h-5 w-5" aria-hidden="true" />
							<p className="font-heading text-foreground mt-2 font-semibold">
								{p.question}
							</p>
							<p className="text-foreground/65 mt-1 text-sm leading-relaxed">
								{p.body}
							</p>
						</div>
					))}
				</div>
			</div>

			{/* Architecture shape */}
			<div>
				<p className="text-foreground/55 mb-2 text-sm font-medium">How it fits together:</p>
				<div className="grid gap-3 lg:grid-cols-2">
					<PlaneBox
						title="App · control plane"
						sub="What's available, who's allowed, what happened"
						items={['Registry', 'Control', 'Admin']}
					/>
					<PlaneBox
						title="Broker · data plane"
						sub="Credential-injecting HTTP proxy"
						items={['Inject secrets', 'Forward request', 'Record execution']}
					/>
				</div>
				<div className="text-foreground/40 my-1 flex justify-center">
					<ArrowDown className="h-4 w-4" aria-hidden="true" />
				</div>
				<div className="border-border bg-muted/40 text-foreground/70 rounded-lg border px-4 py-3 text-center text-sm">
					<span className="font-mono">PostgreSQL</span> · 3 schemas (registry · control ·
					admin)
				</div>
			</div>
		</DocsSectionBlock>
	);
}
