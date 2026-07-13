/**
 * QuickstartSection — the end-to-end "happy path" from a fresh install to a
 * brokered API call, as an ordered list of steps with copyable commands. The
 * sequence is adapted from the README CLI quickstart, the v0-mvp end-to-end
 * narrative, and the cobra command help (install → setup → register → import →
 * bind credential → execute).
 */
import type { ReactNode } from 'react';
import { DocsSectionBlock } from '@/modules/docs/components/DocsSectionBlock';
import { CodeBlock } from '@/modules/docs/components/CodeBlock';

interface Step {
	title: string;
	body: ReactNode;
	code?: string;
	prompt?: boolean;
}

const STEPS: Step[] = [
	{
		title: 'Stand up the platform',
		body: 'An interactive wizard generates your config, builds the app (local venv or Docker), runs migrations, and starts it.',
		code: 'jenticctl install',
		prompt: true,
	},
	{
		title: 'Create the first admin',
		body: 'A one-time first-run step that creates the initial admin account. There is no default password to rotate.',
		code: 'jenticctl setup',
		prompt: true,
	},
	{
		title: 'Register an agent identity',
		body: (
			<>
				Generates an Ed25519 keypair, performs dynamic client registration, waits for an
				operator to approve it, then mints access and refresh tokens for the profile.
			</>
		),
		code: 'jentic register',
		prompt: true,
	},
	{
		title: 'Import an API from the catalog',
		body: 'Browse the public catalog and import an API into your local registry. Imported APIs are auto-promoted to live so they can be executed.',
		code: `jentic catalog search "httpbin"
jentic catalog import <api_id>`,
		prompt: true,
	},
	{
		title: 'Bind a credential',
		body: 'Store a credential and bind it to a toolkit so the Broker can inject it at execution time. Secrets stay in the Control plane — they never reach the agent.',
		code: 'jentic apis operations <vendor/name/version>',
		prompt: true,
	},
	{
		title: 'Execute through the Broker',
		body: (
			<>
				Send a real request through the Broker by <code>METHOD:/path</code> or
				<code> operation_id</code>. The Broker injects your bound credential, forwards the
				call, and records an execution.
			</>
		),
		code: `jentic execute GET:/get --query foo=bar --json`,
		prompt: true,
	},
];

export function QuickstartSection() {
	return (
		<DocsSectionBlock
			id="quickstart"
			title="Quickstart"
			intro="From a fresh machine to a brokered, audited API call. Each step is a single command."
		>
			<ol className="space-y-4">
				{STEPS.map((step, i) => (
					<li key={step.title} className="flex gap-3">
						<span
							className="bg-primary/15 text-primary mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full font-mono text-sm font-semibold"
							aria-hidden="true"
						>
							{i + 1}
						</span>
						<div className="min-w-0 flex-1">
							<p className="font-heading text-foreground font-semibold">
								{step.title}
							</p>
							<p className="text-foreground/65 mt-0.5 text-sm leading-relaxed">
								{step.body}
							</p>
							{step.code && (
								<CodeBlock className="mt-2" code={step.code} prompt={step.prompt} />
							)}
						</div>
					</li>
				))}
			</ol>
			<p className="text-foreground/55 text-sm">
				See the{' '}
				<a href="#cli" className="text-primary underline">
					CLI reference
				</a>{' '}
				for every command and flag, and the{' '}
				<a href="#permissions" className="text-primary underline">
					permissions model
				</a>{' '}
				for what each identity is allowed to do.
			</p>
		</DocsSectionBlock>
	);
}
