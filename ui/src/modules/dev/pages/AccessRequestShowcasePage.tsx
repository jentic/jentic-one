/**
 * Access-request showcase (DEV ONLY) — a visual map of every access-request
 * shape the system produces, each openable in the real decision UI, all backed
 * by MSW. Mounted at `/app/dev/access-requests` behind an `import.meta.env.DEV`
 * guard so it never ships to production.
 *
 * Run it: `VITE_ENABLE_MSW=1 npm run dev -- --port 5199`, then visit
 * `http://localhost:5199/app/dev/access-requests`.
 */
import { useEffect, useState } from 'react';
import { Card, CardBody, Badge, Button, PageShell, PageHeader } from '@/shared/ui';
import { AccessRequestDecisionDialog } from '@/shared/app';
import type { AccessRequest } from '@/shared/lib';
import { SHOWCASE_CASES, type RoutedTo } from '@/modules/dev/fixtures';
import { installDevShowcaseHandlers } from '@/modules/dev/mocks/handlers';

const ROUTE_LABEL: Record<RoutedTo, string> = {
	wizard: 'Setup wizard',
	plain: 'Approve / deny dialog',
	'rail-handoff': 'Rail hand-off notice',
};

const ROUTE_VARIANT: Record<RoutedTo, 'success' | 'default' | 'warning'> = {
	wizard: 'success',
	plain: 'default',
	'rail-handoff': 'warning',
};

function statusVariant(status: string): 'success' | 'danger' | 'pending' | 'default' {
	if (status === 'approved') return 'success';
	if (status === 'denied' || status === 'expired' || status === 'withdrawn') return 'danger';
	if (status === 'pending') return 'pending';
	return 'default';
}

export default function AccessRequestShowcasePage() {
	const [active, setActive] = useState<AccessRequest | null>(null);

	// Install the showcase MSW handlers (scoped to this page via worker.use) and
	// reset demo state on mount, so this dev tool is fully backend-free without
	// leaking its /access-requests fixtures into the rest of the app or tests.
	useEffect(() => {
		installDevShowcaseHandlers();
	}, []);

	return (
		<PageShell>
			<PageHeader
				title="Access-request possibilities"
				subtitle="Every access-request shape the system can produce — open each in the real decision UI. Dev-only; MSW-backed."
			/>
			<div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
				{SHOWCASE_CASES.map((c) => (
					<Card key={c.key} data-testid={`case-${c.key}`}>
						<CardBody className="flex h-full flex-col gap-3">
							<div className="flex items-start justify-between gap-2">
								<h3 className="text-foreground text-sm font-semibold">{c.title}</h3>
								<Badge variant={statusVariant(c.request.status)}>
									{c.request.status}
								</Badge>
							</div>
							<p className="text-muted-foreground flex-1 text-sm">{c.summary}</p>
							<div className="flex flex-wrap gap-1.5">
								<Badge variant={ROUTE_VARIANT[c.routedTo]}>
									→ {ROUTE_LABEL[c.routedTo]}
								</Badge>
								<Badge variant="default">{c.request.items.length} item(s)</Badge>
							</div>
							<div className="text-muted-foreground flex flex-wrap gap-1 font-mono text-xs">
								{c.request.items.map((it) => (
									<span key={it.id} className="bg-muted rounded px-1.5 py-0.5">
										{it.resource_type}:{it.action}
									</span>
								))}
							</div>
							<Button
								variant="secondary"
								size="sm"
								onClick={() => setActive(c.request)}
							>
								Open
							</Button>
						</CardBody>
					</Card>
				))}
			</div>

			<AccessRequestDecisionDialog
				request={active}
				onClose={() => setActive(null)}
				onDecided={() => setActive(null)}
			/>
		</PageShell>
	);
}
