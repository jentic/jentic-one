/**
 * CredentialPicker — searchable list of workspace credentials for the toolkit
 * "Bind credential" dialog.
 *
 * Replaces the old raw `cred_…` id text field: instead of asking the user to
 * paste an opaque identifier, we list the credentials they already have
 * (name + API/vendor + auth-type badge), let them filter by name/vendor, hide
 * the ones already bound to this toolkit, and bind on click. Data comes from
 * the toolkits service tier (`useBindableCredentials`), which reads the
 * org-wide `GET /credentials` surface through the shared API — no sibling
 * Credentials-module import (module-boundary rule).
 */
import { useMemo, useState } from 'react';
import { motion, type Variants } from 'framer-motion';
import { ChevronRight, Filter, KeyRound, Link as LinkIcon, SearchX } from 'lucide-react';
import { AppLink, Badge, EmptyState, ErrorAlert, LoadingState, SearchInput } from '@/shared/ui';
import { ROUTES } from '@/shared/app/routes';
import { useBindableCredentials } from '@/modules/toolkits/api';
import { CREDENTIAL_TYPE_LABELS, type BindableCredential } from '@/modules/toolkits/api/types';

interface CredentialPickerProps {
	/** Credential ids already bound to this toolkit — hidden from the list. */
	boundIds: Set<string>;
	/** Fired with the chosen credential id. */
	onSelect: (credentialId: string) => void;
	/** Disables rows while a bind mutation is in flight. */
	pending?: boolean;
	/** Only fetch when the host dialog is actually open. */
	enabled?: boolean;
}

const LIST_VARIANTS: Variants = {
	hidden: {},
	show: { transition: { staggerChildren: 0.03 } },
};

const ROW_VARIANTS: Variants = {
	hidden: { opacity: 0, y: 6 },
	show: { opacity: 1, y: 0, transition: { duration: 0.16, ease: 'easeOut' } },
};

export function CredentialPicker({
	boundIds,
	onSelect,
	pending,
	enabled = true,
}: CredentialPickerProps) {
	const [query, setQuery] = useState('');
	const { data, isLoading, error } = useBindableCredentials({ enabled });

	const available = useMemo(() => {
		const all = data ?? [];
		const q = query.trim().toLowerCase();
		return all.filter((c) => {
			if (boundIds.has(c.credential_id)) return false;
			if (!q) return true;
			return (
				c.name.toLowerCase().includes(q) ||
				(c.vendor?.toLowerCase().includes(q) ?? false) ||
				(c.provider?.toLowerCase().includes(q) ?? false)
			);
		});
	}, [data, query, boundIds]);

	const total = data?.length ?? 0;
	// Credentials that could ever appear in the list (everything not already
	// bound). When this pool is empty there is nothing to filter, so the filter
	// input is disabled — typing could only ever stack a second empty state on
	// top of the "all bound" / "no credentials" one.
	const candidateCount = useMemo(
		() => (data ?? []).filter((c) => !boundIds.has(c.credential_id)).length,
		[data, boundIds],
	);
	const allBound = total > 0 && candidateCount === 0;

	return (
		<div className="space-y-3">
			<SearchInput
				value={query}
				onValueChange={setQuery}
				placeholder="Filter your credentials by name or vendor…"
				aria-label="Filter credentials"
				icon={<Filter className="h-3.5 w-3.5" />}
				disabled={candidateCount === 0}
				autoFocus
			/>

			{error && <ErrorAlert message={(error as Error).message} />}

			{isLoading && <LoadingState message="Loading credentials…" />}

			{!isLoading && !error && total === 0 && (
				<EmptyState
					icon={<KeyRound className="h-8 w-8" />}
					title="No credentials yet"
					description="Create a credential first, then bind it to this toolkit."
					action={
						<AppLink href={ROUTES.credentials} className="text-primary font-medium">
							Go to Credentials
						</AppLink>
					}
				/>
			)}

			{!isLoading && !error && allBound && (
				<EmptyState
					icon={<LinkIcon className="h-8 w-8" />}
					title="All credentials bound"
					description="Every credential in your workspace is already bound to this toolkit."
				/>
			)}

			{!isLoading && !error && query.trim() && available.length === 0 && !allBound && (
				<EmptyState
					icon={<SearchX className="h-8 w-8" />}
					title="No matches"
					description={`Nothing matched "${query.trim()}".`}
				/>
			)}

			{available.length > 0 && (
				<motion.ul
					className="max-h-72 space-y-1.5 overflow-y-auto"
					variants={LIST_VARIANTS}
					initial="hidden"
					animate="show"
				>
					{available.map((cred) => (
						<motion.li key={cred.credential_id} variants={ROW_VARIANTS}>
							<CredentialRow cred={cred} onSelect={onSelect} disabled={pending} />
						</motion.li>
					))}
				</motion.ul>
			)}
		</div>
	);
}

function CredentialRow({
	cred,
	onSelect,
	disabled,
}: {
	cred: BindableCredential;
	onSelect: (credentialId: string) => void;
	disabled?: boolean;
}) {
	const subtitle = cred.vendor ?? cred.provider ?? cred.credential_id;
	return (
		<button
			type="button"
			disabled={disabled}
			onClick={() => onSelect(cred.credential_id)}
			data-testid="credential-picker-row"
			className="group hover:border-primary/50 bg-background hover:bg-muted/40 border-border flex w-full items-center gap-3 rounded-lg border px-3 py-2.5 text-left transition-all hover:shadow-sm disabled:cursor-not-allowed disabled:opacity-60"
		>
			<div className="bg-accent-yellow/10 text-accent-yellow flex h-8 w-8 shrink-0 items-center justify-center rounded-lg">
				<KeyRound className="h-4 w-4" />
			</div>
			<div className="min-w-0 flex-1">
				<span className="text-foreground block truncate text-sm font-medium">
					{cred.name}
				</span>
				<p className="text-muted-foreground mt-0.5 truncate font-mono text-xs">
					{subtitle}
				</p>
			</div>
			<Badge variant="default" className="shrink-0 text-[10px]">
				{CREDENTIAL_TYPE_LABELS[cred.type] ?? cred.type}
			</Badge>
			<ChevronRight className="text-muted-foreground group-hover:text-foreground h-4 w-4 shrink-0 transition-colors" />
		</button>
	);
}
