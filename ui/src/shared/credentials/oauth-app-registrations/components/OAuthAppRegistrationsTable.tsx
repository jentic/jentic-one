/**
 * Registrations list table — one row per registration with the columns
 * the admin surface calls for (name, api_vendor, flow_kind, is_active,
 * dependent_credential_count, last-rotated timestamp for auth-code rows,
 * per-row actions).
 *
 * The `is_active` cell is a click-to-toggle Badge that fires a PATCH on
 * click — the shortest path to "flip active without opening the edit
 * dialog" — mirroring how other roster surfaces expose a soft-disable.
 */
import { Link } from 'react-router';
import { Edit2, KeyRound, RefreshCw, Trash2 } from 'lucide-react';
import {
	Badge,
	Button,
	Card,
	CardBody,
	DataTable,
	EmptyState,
	ErrorAlert,
	LoadingState,
	Tooltip,
	type Column,
} from '@/shared/ui';
import { timeAgo } from '@/shared/lib/utils';
import type {
	OAuthAppRegistration,
	OAuthAppRegistrationFlowKind,
} from '@/shared/credentials/oauth-app-registrations/api/hooks';

export type RegistrationRowAction = 'edit' | 'rotate-secret' | 'delete' | 'toggle-active';

interface OAuthAppRegistrationsTableProps {
	registrations: OAuthAppRegistration[] | undefined;
	isLoading: boolean;
	error: unknown;
	/**
	 * Where the caller should send an admin who wants to register a new app.
	 * Registration happens from the normal credentials page (with the
	 * "Available to everyone in the org" toggle flipped), not from this
	 * management view.
	 */
	credentialsHref: string;
	onAction: (registration: OAuthAppRegistration, action: RegistrationRowAction) => void;
	pendingId?: string | null;
}

const FLOW_KIND_LABEL: Record<string, string> = {
	authorization_code: 'Authorization code',
	device_authorization: 'Device authorization',
};

function FlowKindBadge({ kind }: { kind: OAuthAppRegistrationFlowKind }) {
	return <Badge variant="default">{FLOW_KIND_LABEL[kind] ?? kind}</Badge>;
}

export function OAuthAppRegistrationsTable({
	registrations,
	isLoading,
	error,
	credentialsHref,
	onAction,
	pendingId,
}: OAuthAppRegistrationsTableProps) {
	if (error) {
		return <ErrorAlert message={error instanceof Error ? error : String(error)} />;
	}

	if (isLoading) {
		return <LoadingState message="Loading OAuth app registrations…" />;
	}

	if (!registrations || registrations.length === 0) {
		return (
			<EmptyState
				icon={<KeyRound className="h-6 w-6" />}
				title="No OAuth app registrations"
				description="Register a shared OAuth application from the credentials page — flip “Available to everyone in the organization” on any OAuth2 create."
				action={
					<Link to={credentialsHref}>
						<Button>Go to credentials</Button>
					</Link>
				}
			/>
		);
	}

	const columns: Column<OAuthAppRegistration>[] = [
		{
			key: 'name',
			header: 'Name',
			className: 'max-w-[240px]',
			render: (row) => (
				<span className="flex min-w-0 flex-col">
					<span className="text-foreground truncate text-sm font-semibold">
						{row.name}
					</span>
					<code className="text-muted-foreground truncate font-mono text-xs">
						{row.client_id}
					</code>
				</span>
			),
		},
		{
			key: 'api_vendor',
			header: 'API vendor',
			className: 'whitespace-nowrap',
			render: (row) => <span className="text-foreground text-sm">{row.api_vendor}</span>,
		},
		{
			key: 'flow_kind',
			header: 'Flow',
			className: 'whitespace-nowrap',
			render: (row) => <FlowKindBadge kind={row.flow_kind} />,
		},
		{
			key: 'is_active',
			header: 'Status',
			className: 'whitespace-nowrap',
			render: (row) => (
				<button
					type="button"
					onClick={(): void => onAction(row, 'toggle-active')}
					disabled={pendingId === row.id}
					className="focus-visible:ring-ring cursor-pointer rounded-sm focus-visible:ring-2 focus-visible:outline-none"
					aria-label={`Toggle active status for ${row.name}`}
				>
					<Badge variant={row.is_active ? 'success' : 'default'}>
						{row.is_active ? 'Active' : 'Inactive'}
					</Badge>
				</button>
			),
		},
		{
			key: 'dependent_credential_count',
			header: 'Credentials',
			className: 'w-24 text-right',
			render: (row) => (
				<span className="text-foreground font-mono text-xs tabular-nums">
					{row.dependent_credential_count}
				</span>
			),
		},
		{
			key: 'secret_last_rotated_at',
			header: 'Secret rotated',
			className: 'w-32 whitespace-nowrap',
			render: (row) => {
				if (row.flow_kind !== ('authorization_code' as OAuthAppRegistrationFlowKind)) {
					return <span className="text-muted-foreground text-xs">—</span>;
				}
				if (!row.secret_last_rotated_at) {
					return <span className="text-muted-foreground text-xs">Never</span>;
				}
				return (
					<span className="text-muted-foreground text-xs">
						{timeAgo(row.secret_last_rotated_at)}
					</span>
				);
			},
		},
		{
			key: 'actions',
			header: '',
			className: 'w-40 text-right',
			render: (row) => {
				const canRotate =
					row.flow_kind === ('authorization_code' as OAuthAppRegistrationFlowKind);
				const rowPending = pendingId === row.id;
				return (
					<span className="flex items-center justify-end gap-1">
						<Tooltip content="Edit">
							<Button
								variant="ghost"
								size="icon"
								onClick={(): void => onAction(row, 'edit')}
								aria-label={`Edit ${row.name}`}
								disabled={rowPending}
							>
								<Edit2 className="h-4 w-4" />
							</Button>
						</Tooltip>
						<Tooltip content={canRotate ? 'Rotate secret' : 'Device flow — no secret'}>
							<span>
								<Button
									variant="ghost"
									size="icon"
									onClick={(): void => onAction(row, 'rotate-secret')}
									aria-label={`Rotate secret for ${row.name}`}
									disabled={!canRotate || rowPending}
								>
									<RefreshCw className="h-4 w-4" />
								</Button>
							</span>
						</Tooltip>
						<Tooltip content="Delete">
							<Button
								variant="ghost"
								size="icon"
								onClick={(): void => onAction(row, 'delete')}
								aria-label={`Delete ${row.name}`}
								disabled={rowPending}
							>
								<Trash2 className="h-4 w-4" />
							</Button>
						</Tooltip>
					</span>
				);
			},
		},
	];

	return (
		<Card>
			<CardBody className="px-0 py-0">
				<DataTable<OAuthAppRegistration>
					columns={columns}
					data={registrations}
					getRowKey={(row) => row.id}
					ariaLabel="OAuth app registrations"
				/>
			</CardBody>
		</Card>
	);
}
