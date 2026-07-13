/**
 * InstallationSection — how to get the Jentic CLIs onto your machine. Content
 * adapted from README.md (Quick start) and tools/install-README.md (installer
 * behaviour + env-var configuration). The two binaries are introduced here
 * because every later step uses one or the other.
 */
import { Wrench, Compass } from 'lucide-react';
import { DocsSectionBlock, Prose } from '@/modules/docs/components/DocsSectionBlock';
import { CodeBlock } from '@/modules/docs/components/CodeBlock';

const INSTALL_SH = `curl -fsSL https://raw.githubusercontent.com/jentic/jentic-one/main/tools/install.sh | sh`;

const FROM_SOURCE = `make install   # install dependencies and git hooks
make check     # lint + typecheck + tests
make start-app # run the application locally`;

const VERIFY = `jenticctl --version
jentic --version`;

const ENV_VARS: { name: string; def: string; desc: string }[] = [
	{ name: 'JENTIC_REPO', def: 'jentic/jentic-one', desc: 'owner/name of the source repo' },
	{ name: 'JENTIC_REF', def: 'main', desc: 'branch, tag, or commit to build' },
	{ name: 'JENTIC_INSTALL_DIR', def: '~/.jentic/bin', desc: 'where the binaries are installed' },
	{
		name: 'JENTIC_GO_VERSION',
		def: '1.26.2',
		desc: 'Go version to download if none suitable is found',
	},
	{
		name: 'GITHUB_TOKEN',
		def: '(unset)',
		desc: 'token for cloning a private repo (never written to disk)',
	},
];

export function InstallationSection() {
	return (
		<DocsSectionBlock
			id="installation"
			title="Installation"
			intro="One command builds and installs both CLIs from source, detecting your OS/arch and fetching a Go toolchain if needed."
		>
			<CodeBlock code={INSTALL_SH} caption="Install the CLI" />

			{/* The two binaries */}
			<div className="grid gap-3 sm:grid-cols-2">
				<div className="border-border bg-card/50 rounded-lg border p-4">
					<Wrench className="text-primary h-5 w-5" aria-hidden="true" />
					<p className="font-heading text-foreground mt-2 font-semibold">
						<code className="font-mono text-sm">jenticctl</code>
					</p>
					<p className="text-foreground/65 mt-1 text-sm leading-relaxed">
						Install &amp; lifecycle. Stands up a local deployment, trusts the proxy CA,
						and manages the running app (start/stop, logs, health, updates).
					</p>
				</div>
				<div className="border-border bg-card/50 rounded-lg border p-4">
					<Compass className="text-primary h-5 w-5" aria-hidden="true" />
					<p className="font-heading text-foreground mt-2 font-semibold">
						<code className="font-mono text-sm">jentic</code>
					</p>
					<p className="text-foreground/65 mt-1 text-sm leading-relaxed">
						Catalog &amp; run. Register agent identities, browse and import APIs,
						inspect operations, and wrap an agent so its traffic is brokered.
					</p>
				</div>
			</div>

			<div>
				<h3 className="font-heading text-foreground mb-2 text-base font-semibold">
					From source
				</h3>
				<Prose className="mb-2">
					Working in a checkout of the repo? Use the make targets instead:
				</Prose>
				<CodeBlock code={FROM_SOURCE} caption="From a checkout" prompt />
			</div>

			<div>
				<h3 className="font-heading text-foreground mb-2 text-base font-semibold">
					Configuration
				</h3>
				<Prose className="mb-2">
					The installer is configured entirely through optional environment variables:
				</Prose>
				<div className="border-border overflow-hidden rounded-lg border">
					<table className="w-full text-left text-sm">
						<thead className="bg-muted/50 text-foreground/55">
							<tr>
								<th className="px-3 py-2 font-medium">Variable</th>
								<th className="px-3 py-2 font-medium">Default</th>
								<th className="px-3 py-2 font-medium">Description</th>
							</tr>
						</thead>
						<tbody className="divide-border/50 divide-y">
							{ENV_VARS.map((v) => (
								<tr key={v.name} className="align-top">
									<td className="px-3 py-2 font-mono text-xs whitespace-nowrap">
										{v.name}
									</td>
									<td className="text-foreground/60 px-3 py-2 font-mono text-xs">
										{v.def}
									</td>
									<td className="text-foreground/70 px-3 py-2">{v.desc}</td>
								</tr>
							))}
						</tbody>
					</table>
				</div>
			</div>

			<div>
				<h3 className="font-heading text-foreground mb-2 text-base font-semibold">
					Verify
				</h3>
				<CodeBlock code={VERIFY} prompt />
			</div>
		</DocsSectionBlock>
	);
}
