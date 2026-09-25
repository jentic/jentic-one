import { useMemo, type ReactNode } from 'react';
import { motion } from 'framer-motion';
import { Key } from 'lucide-react';
import { Button, EmptyState, ErrorAlert } from '@/shared/ui';
import { CredentialCard, CredentialCardSkeleton } from './CredentialCard';
import { CredentialGroupCard } from './CredentialGroupCard';
import type { Credential } from '@/shared/credentials/api';
import { credentialApiGroupKey } from '@/shared/credentials/lib/credentialIdentity';

const gridVariants = {
	hidden: { opacity: 1 },
	visible: { opacity: 1, transition: { staggerChildren: 0.04 } },
} as const;

const cardVariants = {
	hidden: { opacity: 0, y: 8 },
	visible: { opacity: 1, y: 0, transition: { duration: 0.2, ease: 'easeOut' } },
} as const;

interface CredentialsListProps {
	credentials: Credential[];
	isLoading: boolean;
	error?: Error | null;
	onAdd: () => void;
	onEdit: (cred: Credential) => void;
	onDelete: (cred: Credential) => void;
	onConnect: (cred: Credential) => void;
	/**
	 * Empty body for a host whose list is narrowed by a filter, where the
	 * default "No credentials stored" would be a claim about the store rather
	 * than about the filter.
	 */
	emptyState?: ReactNode;
	/**
	 * Widest column count. A page has room for three; a drawer does not, and
	 * three 200px columns are what squeeze a credential's name into an ellipsis.
	 */
	columns?: 2 | 3;
	/**
	 * Per-credential usage figures for the cards' meta line. A resolver rather than
	 * maps, because each figure is three-state (resolving / unprovable / exact) and
	 * only the host knows which. Omit it and the cards carry no usage clauses.
	 */
	usageFor?: (cred: Credential) => CredentialUsage;
}

interface CredentialUsage {
	usedByAgentCount?: number | null;
	callsLast7d?: number | null;
}

/**
 * What a host that makes no usage reads offers: nothing, stated as nothing.
 * `undefined` on those props means "still resolving" and draws a skeleton, so a
 * host with no such read must say `null` — otherwise its cards pulse forever
 * waiting for a figure nobody is fetching.
 */
const NO_USAGE = { usedByAgentCount: null, callsLast7d: null } as const;

/** Credentials bucketed by the API they unlock, each bucket at its first member's
 * position so the host's sort still decides the order. */
function groupByApi(credentials: Credential[]): Credential[][] {
	const groups = new Map<string, Credential[]>();
	for (const cred of credentials) {
		const key = credentialApiGroupKey(cred);
		const group = groups.get(key);
		if (group) group.push(cred);
		else groups.set(key, [cred]);
	}
	return [...groups.values()];
}

/**
 * The credentials grid body: skeleton → error → empty → staggered cards. An API
 * holding several credentials is one full-width group card listing them, so the
 * grid never repeats an API.
 */
export function CredentialsList({
	credentials,
	isLoading,
	error,
	onAdd,
	onEdit,
	onDelete,
	onConnect,
	emptyState,
	columns = 3,
	usageFor,
}: CredentialsListProps) {
	// `dense`, so single cards backfill the gap a full-width group leaves.
	const gridClass =
		columns === 2
			? 'grid grid-flow-row-dense grid-cols-1 gap-4 sm:grid-cols-2'
			: 'grid grid-flow-row-dense grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3';
	const groups = useMemo(() => groupByApi(credentials), [credentials]);
	const usage = (cred: Credential): CredentialUsage => ({
		...NO_USAGE,
		...usageFor?.(cred),
	});
	if (isLoading) {
		return (
			<div className={gridClass} aria-hidden="true" data-testid="credentials-skeleton">
				{Array.from({ length: 6 }).map((_, i) => (
					<div key={`cred-skeleton-${i}`} style={{ animationDelay: `${i * 60}ms` }}>
						<CredentialCardSkeleton />
					</div>
				))}
			</div>
		);
	}

	if (error) {
		return <ErrorAlert message={error} />;
	}

	if (credentials.length === 0) {
		if (emptyState) return <>{emptyState}</>;
		return (
			<EmptyState
				icon={<Key className="h-10 w-10 opacity-30" />}
				title="No credentials stored"
				description="Add a credential to authenticate agents with external APIs."
				action={<Button onClick={onAdd}>Add your first credential</Button>}
			/>
		);
	}

	return (
		<motion.div
			variants={gridVariants}
			initial="hidden"
			animate="visible"
			className={gridClass}
			data-testid="credentials-grid"
		>
			{groups.map((group) =>
				group.length === 1 ? (
					<motion.div key={group[0].credential_id} variants={cardVariants}>
						<CredentialCard
							cred={group[0]}
							onEdit={onEdit}
							onDelete={onDelete}
							onConnect={onConnect}
							{...usage(group[0])}
						/>
					</motion.div>
				) : (
					<motion.div
						key={group[0].credential_id}
						variants={cardVariants}
						className="col-span-full"
					>
						<CredentialGroupCard
							credentials={group}
							onEdit={onEdit}
							onDelete={onDelete}
							onConnect={onConnect}
							usageFor={usage}
						/>
					</motion.div>
				),
			)}
		</motion.div>
	);
}
