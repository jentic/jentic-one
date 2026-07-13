import { useNavigate } from 'react-router-dom';
import { Users, CheckCircle2 } from 'lucide-react';
import {
	Card,
	CardHeader,
	CardBody,
	CardTitle,
	Badge,
	EmptyState,
	ErrorAlert,
	SkeletonRows,
	AgentBadge,
} from '@/shared/ui';
import { usePendingAgents, formatApproxCount } from '@/modules/dashboard/api';
import { ROUTES } from '@/shared/app/routes';
import { timeAgo } from '@/shared/lib/utils';
import { CardRow, CardHeaderIcon } from '@/modules/dashboard/components/CardRow';

/**
 * Agents awaiting approval. Composed from `GET /agents?status=pending`. Links
 * into the Agents surface (`ROUTES.agents`) to act on a request — Dashboard only
 * surfaces the queue, it doesn't own the approve/deny flow.
 */
export function PendingAgentsCard() {
	const { data, isLoading, isError, error } = usePendingAgents();
	const navigate = useNavigate();

	return (
		<Card className="flex h-full flex-col">
			<CardHeader className="flex items-center justify-between gap-3">
				<CardTitle as="h2" className="flex items-center gap-2.5">
					<CardHeaderIcon>
						<Users className="h-4 w-4" aria-hidden="true" />
					</CardHeaderIcon>
					Agents awaiting approval
				</CardTitle>
				{data && data.count.value > 0 && (
					<Badge variant="pending" dot>
						{formatApproxCount(data.count)}
					</Badge>
				)}
			</CardHeader>
			<CardBody className="flex-1 px-0 py-2">
				{isLoading ? (
					<div className="px-5 py-2">
						<SkeletonRows rows={3} />
					</div>
				) : isError ? (
					<div className="px-5 py-2">
						<ErrorAlert message={error ?? 'Failed to load agents.'} />
					</div>
				) : !data || data.agents.length === 0 ? (
					<div className="px-5 py-2">
						<EmptyState
							icon={<CheckCircle2 className="h-7 w-7" aria-hidden="true" />}
							title="No agents waiting"
							description="New agent registrations that need approval will appear here."
						/>
					</div>
				) : (
					<ul className="divide-border/70 divide-y">
						{data.agents.map((agent) => (
							<li key={agent.id}>
								<CardRow
									onClick={() => navigate(ROUTES.agents)}
									aria-label={`Review agent ${agent.name}`}
									leading={
										<AgentBadge
											id={agent.id}
											name={agent.name}
											kind="Agent"
											size="sm"
										/>
									}
									title={agent.name}
									subtitle={`registered ${timeAgo(agent.created_at)}${
										agent.description ? ` · ${agent.description}` : ''
									}`}
								/>
							</li>
						))}
					</ul>
				)}
			</CardBody>
		</Card>
	);
}
