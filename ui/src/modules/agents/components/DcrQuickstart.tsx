/**
 * DcrQuickstart — first-run helper shown under the agents tab's empty state.
 *
 * Most agents should arrive via dynamic client registration (POST /register →
 * pending → approve), not manual creation, so the empty fleet teaches exactly
 * that. Raw POST /register requires an Ed25519 JWKS, which no one can type by
 * hand — the CLI generates the keypair and performs DCR, so the snippet points
 * at `jentic register` targeting THIS instance (phase 5 of the agents-rebuild
 * plan).
 */
import { Terminal } from 'lucide-react';
import { Card, CardBody, CodeSnippet } from '@/shared/ui';

/** The command the operator can paste — targets the current origin's API. */
function snippet(): string {
	return `jentic register --url "${window.location.origin}" --name my-first-agent`;
}

export function DcrQuickstart() {
	const code = snippet();
	return (
		<Card>
			<CardBody className="space-y-3">
				<div className="flex items-center gap-2">
					<Terminal className="text-muted-foreground h-4 w-4" aria-hidden />
					<h3 className="text-foreground text-sm font-semibold">
						Register an agent from the command line
					</h3>
				</div>
				<p className="text-muted-foreground text-sm">
					Agents self-register via dynamic client registration and land here as{' '}
					<strong>pending</strong> for you to approve. The CLI generates the agent's
					Ed25519 keypair and registers it in one step.
				</p>
				<CodeSnippet code={code} />
			</CardBody>
		</Card>
	);
}
