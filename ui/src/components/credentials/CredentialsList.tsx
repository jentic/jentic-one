import { useMemo, useState } from 'react';
import { Key } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import type { CredentialOut } from '@/api/types';
import { api, oauthBrokers } from '@/api/client';
import { Button } from '@/components/ui/Button';
import { ConfirmDeleteDialog, type DeleteTarget } from '@/components/ui/ConfirmDeleteDialog';
import { EmptyState } from '@/components/ui/EmptyState';
import { ErrorAlert } from '@/components/ui/ErrorAlert';
import { LoadingState } from '@/components/ui/LoadingState';
import { SectionTitle } from '@/components/discovery/SectionTitle';
import { CredentialRow } from './CredentialRow';
import { PipedreamCard } from './PipedreamCard';

interface CredentialsListProps {
	loggedIn: boolean;
}

/**
 * The credentials list — Pipedream status line on top, then a section
 * per kind (manual creds + Pipedream-managed) with rows from
 * `<CredentialRow>`. The page shell is the parent's job; this component
 * is purely the body.
 *
 * Why split kinds into sections?
 * Pipedream creds and manually-uploaded creds are conceptually different:
 * - manual creds are owned by the user (rotate locally),
 * - Pipedream creds are owned by an OAuth grant (rotate by reconnect).
 *
 * Mixing them as one flat list led to repeated user confusion ("why is
 * there no value field on this one?"). The section split lets the row UI
 * stay simple — each row only renders the controls that make sense for its
 * kind, no `if (auth_type === 'pipedream_oauth')` branching at the row
 * level.
 */
export function CredentialsList({ loggedIn }: CredentialsListProps) {
	const navigate = useNavigate();
	const queryClient = useQueryClient();

	const {
		data: credentials = [],
		isLoading,
		isError,
	} = useQuery({
		queryKey: ['credentials'],
		queryFn: () => api.listCredentials(),
		select: (d: unknown) => {
			if (Array.isArray(d)) return d as CredentialOut[];
			const data = (d as { data?: unknown })?.data;
			return Array.isArray(data) ? (data as CredentialOut[]) : [];
		},
		enabled: loggedIn,
	});

	// API list is needed to badge each credential row "Local" vs "Catalog".
	// We grab it once and stitch onto each cred — cheaper than fanning out
	// from the row component.
	const { data: apis } = useQuery({
		queryKey: ['apis-for-cred-badges'],
		queryFn: () => api.listApis(1, 500, 'local'),
		enabled: loggedIn,
		select: (d: unknown) => {
			const arr = Array.isArray(d) ? d : (d as { data?: unknown })?.data;
			return Array.isArray(arr) ? (arr as Array<{ id: string; local?: boolean }>) : [];
		},
	});

	const localApiIds = useMemo(() => {
		const set = new Set<string>();
		(apis ?? []).forEach((a) => {
			if (a.local !== false) set.add(a.id);
		});
		return set;
	}, [apis]);

	const annotated = useMemo(
		() =>
			credentials.map((c) => ({
				...c,
				api_local: c.api_id ? localApiIds.has(c.api_id) : undefined,
			})) as Array<CredentialOut & { api_local?: boolean }>,
		[credentials, localApiIds],
	);

	const [manualCreds, pipedreamCreds] = useMemo(() => {
		const manual: typeof annotated = [];
		const pipedream: typeof annotated = [];
		annotated.forEach((c) => {
			if (c.auth_type === 'pipedream_oauth') pipedream.push(c);
			else manual.push(c);
		});
		return [manual, pipedream];
	}, [annotated]);

	const deleteMutation = useMutation({
		mutationFn: (cred: { id: string; authType: string; accountId?: string }) => {
			if (cred.authType === 'pipedream_oauth' && cred.accountId) {
				return oauthBrokers.deleteAccount('pipedream', cred.accountId);
			}
			return api.deleteCredential(cred.id);
		},
		onSuccess: () => {
			queryClient.invalidateQueries({ queryKey: ['credentials'] });
			setDeleteTarget(null);
		},
	});

	// Lifted dialog state — `ConfirmInline` (the previous prompt) was a
	// 50/50 click target without enough room to show toolkit bindings, so
	// users learned to delete creds and *then* discover (in Slack, after
	// the fact) that two toolkits stopped working. The dialog reads the
	// bindings from `/credentials/{id}/bindings` before the user commits.
	const [deleteTarget, setDeleteTarget] = useState<{
		id: string;
		authType: string;
		accountId?: string;
		name: string;
		isPipedream: boolean;
	} | null>(null);

	const dialogTarget: DeleteTarget | null = deleteTarget
		? {
				kind: 'credential',
				id: deleteTarget.id,
				name: deleteTarget.name,
				isPipedream: deleteTarget.isPipedream,
			}
		: null;

	if (!loggedIn || isLoading) {
		return <LoadingState message="Loading credentials..." />;
	}
	if (isError) {
		return (
			<ErrorAlert message="Failed to load credentials. Please try refreshing the page." />
		);
	}
	if (credentials.length === 0) {
		return (
			<>
				<PipedreamCard />
				<EmptyState
					icon={<Key className="h-10 w-10 opacity-30" />}
					title="No credentials stored"
					description="Add a credential to authenticate agents with external APIs."
					action={
						<Button onClick={() => navigate('/credentials/new')}>
							Add your first credential
						</Button>
					}
				/>
			</>
		);
	}

	return (
		<div className="space-y-5">
			<PipedreamCard />

			{manualCreds.length > 0 && (
				<section>
					<SectionTitle count={manualCreds.length}>Credentials</SectionTitle>
					<div className="mt-2 space-y-2">
						{manualCreds.map((cred) => (
							<CredentialRow
								key={cred.id}
								cred={cred}
								onDelete={() =>
									setDeleteTarget({
										id: cred.id,
										authType: cred.auth_type ?? '',
										name: cred.label,
										isPipedream: false,
									})
								}
							/>
						))}
					</div>
				</section>
			)}

			{pipedreamCreds.length > 0 && (
				<section>
					<SectionTitle count={pipedreamCreds.length}>OAuth connections</SectionTitle>
					<div className="mt-2 space-y-2">
						{pipedreamCreds.map((cred) => (
							<CredentialRow
								key={cred.id}
								cred={cred}
								onDelete={() =>
									setDeleteTarget({
										id: cred.id,
										authType: 'pipedream_oauth',
										accountId: cred.account_id ?? undefined,
										name: cred.label,
										isPipedream: true,
									})
								}
							/>
						))}
					</div>
				</section>
			)}

			<ConfirmDeleteDialog
				target={dialogTarget}
				open={dialogTarget != null}
				onClose={() => {
					if (!deleteMutation.isPending) setDeleteTarget(null);
				}}
				onConfirm={() => {
					if (!deleteTarget) return;
					deleteMutation.mutate({
						id: deleteTarget.id,
						authType: deleteTarget.authType,
						accountId: deleteTarget.accountId,
					});
				}}
				loading={deleteMutation.isPending}
			/>
		</div>
	);
}
