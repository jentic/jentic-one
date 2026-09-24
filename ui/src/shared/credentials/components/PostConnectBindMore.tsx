import { useMemo, useState } from 'react';
import { Button, Checkbox, ErrorAlert, Label, Skeleton } from '@/shared/ui';
import {
	useAgentsForPicker,
	useBindCredentialToAgents,
} from '@/shared/credentials/api/vendors-hooks';

/**
 * Post-connect CTA rendered on the credentials-dialog terminal step:
 * tick a set of other agents in the workspace and bind the newly-
 * created credential to each. Bindings are created in "start blocked"
 * mode (no rules) — the user has already authored per-binding rules
 * for the primary (agent, credential) pair at ``:confirm``, so
 * additional agents get a suspended row and grant them access from
 * each agent's page.
 *
 * Lives under ``shared/credentials`` (not ``modules/agents``) because
 * both the credentials page and the agent-detail page mount it via
 * ``CreateCredentialDialog``'s ``renderPostConnect`` prop, and the
 * layering rules forbid sibling-module imports as well as
 * ``shared → modules`` ones.
 */
export function PostConnectBindMore({
	credentialId,
	boundAgentId,
}: {
	credentialId: string;
	boundAgentId: string | null;
}) {
	const agents = useAgentsForPicker();
	const bindMany = useBindCredentialToAgents();
	const [selected, setSelected] = useState<Set<string>>(new Set());
	const [done, setDone] = useState(false);

	const candidates = useMemo(() => {
		const all = agents.data?.data ?? [];
		return all.filter((a) => a.id !== boundAgentId);
	}, [agents.data, boundAgentId]);

	if (agents.isLoading) {
		return <Skeleton className="h-24 w-full" />;
	}
	if (candidates.length === 0) {
		// No other agents available — nothing to offer. Silent instead of a
		// dead-end block.
		return null;
	}

	const toggle = (id: string): void => {
		setSelected((prev) => {
			const next = new Set(prev);
			if (next.has(id)) next.delete(id);
			else next.add(id);
			return next;
		});
		setDone(false);
	};

	const handleBind = (): void => {
		if (selected.size === 0) return;
		bindMany.mutate(
			{ credentialId, agentIds: Array.from(selected) },
			{
				onSuccess: () => {
					setDone(true);
					setSelected(new Set());
				},
			},
		);
	};

	return (
		<div className="space-y-2">
			<Label>Bind to more agents</Label>
			<p className="text-muted-foreground text-xs">
				Tick any additional agents that should have access to this credential. New bindings
				start suspended (no rules) — grant them access from each agent&apos;s page.
			</p>
			<div className="border-border bg-muted/20 max-h-40 space-y-1 overflow-y-auto rounded-lg border p-2">
				{candidates.map((a) => {
					const checked = selected.has(a.id);
					return (
						<label
							key={a.id}
							className="hover:bg-muted/40 flex cursor-pointer items-center gap-2 rounded-md px-2 py-1.5"
						>
							<Checkbox checked={checked} onChange={(): void => toggle(a.id)} />
							<span className="text-foreground text-sm">{a.name}</span>
						</label>
					);
				})}
			</div>
			{bindMany.error && <ErrorAlert message={bindMany.error} />}
			{done && !bindMany.error && (
				<p className="text-success text-xs">Bound to selected agents.</p>
			)}
			<Button
				type="button"
				variant="secondary"
				size="sm"
				onClick={handleBind}
				disabled={selected.size === 0 || bindMany.isPending}
				loading={bindMany.isPending}
			>
				Bind{' '}
				{selected.size > 0
					? `to ${selected.size} agent${selected.size === 1 ? '' : 's'}`
					: ''}
			</Button>
		</div>
	);
}
