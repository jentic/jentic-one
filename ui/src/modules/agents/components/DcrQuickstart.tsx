/**
 * DcrQuickstart — a self-registration card.
 *
 * Most agents should arrive via dynamic client registration (POST /register →
 * pending → approve), not manual creation, so the card teaches exactly that
 * with the same `jentic register` command the zero-agents landing offers.
 */
import { Terminal } from 'lucide-react';
import { Card, CardBody, CodeSnippet } from '@/shared/ui';
import { DEFAULT_REGISTER_NAME, registerCommand } from '@/modules/agents/lib/registerCommand';

export function DcrQuickstart() {
	const code = registerCommand({ url: window.location.origin, name: DEFAULT_REGISTER_NAME });
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
