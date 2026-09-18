import type { ReactNode } from 'react';
import { motion } from 'framer-motion';
import { Key } from 'lucide-react';
import { Button, EmptyState, ErrorAlert } from '@/shared/ui';
import { CredentialCard, CredentialCardSkeleton } from './CredentialCard';
import type { Credential } from '@/shared/credentials/api';

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
	 * Per-credential usage figures for the cards' meta line. A resolver rather
	 * than maps, because each figure is three-state (resolving / unprovable /
	 * exact) and only the host knows which — the truncation gate on a top-N
	 * usage list, say, is the host's to apply. Omit it and the cards carry no
	 * usage clauses at all, which is what a host with no such reads should show.
	 */
	usageFor?: (cred: Credential) => {
		usedByAgentCount?: number | null;
		callsLast7d?: number | null;
	};
}

/** The credentials grid body: skeleton → error → empty → staggered cards. */
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
	const gridClass =
		columns === 2
			? 'grid grid-cols-1 gap-4 sm:grid-cols-2'
			: 'grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3';
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
			{credentials.map((cred) => (
				<motion.div key={cred.credential_id} variants={cardVariants}>
					<CredentialCard
						cred={cred}
						onEdit={onEdit}
						onDelete={onDelete}
						onConnect={onConnect}
						{...usageFor?.(cred)}
					/>
				</motion.div>
			))}
		</motion.div>
	);
}
