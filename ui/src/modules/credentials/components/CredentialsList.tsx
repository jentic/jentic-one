import { motion } from 'framer-motion';
import { Key } from 'lucide-react';
import { Button, EmptyState, ErrorAlert } from '@/shared/ui';
import { CredentialCard, CredentialCardSkeleton } from './CredentialCard';
import type { Credential } from '@/modules/credentials/api';

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
}: CredentialsListProps) {
	if (isLoading) {
		return (
			<div
				className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3"
				aria-hidden="true"
				data-testid="credentials-skeleton"
			>
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
			className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3"
			data-testid="credentials-grid"
		>
			{credentials.map((cred) => (
				<motion.div key={cred.credential_id} variants={cardVariants}>
					<CredentialCard
						cred={cred}
						onEdit={onEdit}
						onDelete={onDelete}
						onConnect={onConnect}
					/>
				</motion.div>
			))}
		</motion.div>
	);
}
