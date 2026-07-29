/**
 * DcrQuickstart — first-run helper shown under the agents tab's empty state.
 *
 * Most agents should arrive via dynamic client registration (POST /register →
 * pending → approve), not manual creation, so the empty fleet teaches exactly
 * that: a copy-pasteable curl against THIS instance. Rendered only when the
 * roster is genuinely empty (phase 5 of the agents-rebuild plan).
 */
import { Terminal } from 'lucide-react';
import { Card, CardBody, CopyButton } from '@/shared/ui';

/** The curl the operator can paste — targets the current origin's API. */
function snippet(): string {
	return [
		`curl -X POST "${window.location.origin}/register" \\`,
		`  -H "Content-Type: application/json" \\`,
		`  -d '{"client_name": "my-first-agent"}'`,
	].join('\n');
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
					<strong>pending</strong> for you to approve.
				</p>
				<div className="bg-muted/60 border-border/60 relative rounded-lg border p-3">
					<pre className="text-foreground/90 overflow-x-auto pr-8 font-mono text-xs leading-relaxed">
						{code}
					</pre>
					<div className="absolute top-2 right-2">
						<CopyButton value={code} />
					</div>
				</div>
			</CardBody>
		</Card>
	);
}
