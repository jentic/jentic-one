/**
 * Compact registrations list — one row per registration: name + client id,
 * a meta line (vendor · flow · dependent credentials · secret rotation), the
 * status badge, and per-row actions. Rows rather than a wide table so the
 * list fits a narrow host pane, where a seven-column table overflowed.
 *
 * Status is a plain badge; activating / deactivating is an explicit row
 * action so it can't be flipped by a stray click on something that reads as
 * static (the section confirms deactivation — it cuts off everyone at once).
 */
import { Edit2, KeyRound, Power, PowerOff, RefreshCw, Trash2 } from 'lucide-react';
import { Badge, Button, Card, EmptyState, ErrorAlert, LoadingState, Tooltip } from '@/shared/ui';
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
	/** Opens the create flow in register-shared-app mode. */
	onRegister: () => void;
	onAction: (registration: OAuthAppRegistration, action: RegistrationRowAction) => void;
	pendingId?: string | null;
}

const FLOW_KIND_LABEL: Record<string, string> = {
	authorization_code: 'Authorization code',
	device_authorization: 'Device authorization',
};

export function OAuthAppRegistrationsTable({
	registrations,
	isLoading,
	error,
	onRegister,
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
				title="No shared OAuth apps"
				description="Register an OAuth app your organization can connect through — each person signs in with their own account."
				action={<Button onClick={onRegister}>Register shared app</Button>}
			/>
		);
	}

	return (
		<Card>
			<ul aria-label="Shared OAuth apps" className="divide-border divide-y">
				{registrations.map((row) => (
					<RegistrationRow
						key={row.id}
						row={row}
						pending={pendingId === row.id}
						onAction={onAction}
					/>
				))}
			</ul>
		</Card>
	);
}

function RegistrationRow({
	row,
	pending,
	onAction,
}: {
	row: OAuthAppRegistration;
	pending: boolean;
	onAction: (registration: OAuthAppRegistration, action: RegistrationRowAction) => void;
}) {
	const isAuthCode = row.flow_kind === ('authorization_code' as OAuthAppRegistrationFlowKind);
	const credentials = `${row.dependent_credential_count} credential${
		row.dependent_credential_count === 1 ? '' : 's'
	}`;
	const rotated = isAuthCode
		? row.secret_last_rotated_at
			? `secret rotated ${timeAgo(row.secret_last_rotated_at)}`
			: 'secret never rotated'
		: null;

	return (
		<li className="flex flex-wrap items-center gap-x-3 gap-y-2 px-4 py-3">
			<div className="min-w-0 flex-1 basis-56">
				<div className="flex min-w-0 items-center gap-2">
					<span className="text-foreground truncate text-sm font-semibold">
						{row.name}
					</span>
					<Badge variant={row.is_active ? 'success' : 'default'}>
						{row.is_active ? 'Active' : 'Inactive'}
					</Badge>
				</div>
				{/* Wraps rather than truncates: at phone widths the tail (credential
				    count, rotation age) is what got cut. Only the id truncates. */}
				<p className="text-muted-foreground mt-0.5 flex min-w-0 flex-wrap items-center gap-x-1.5 text-xs">
					<span className="max-w-full truncate font-mono">{row.client_id}</span>
					{[row.api_vendor, FLOW_KIND_LABEL[row.flow_kind] ?? row.flow_kind, credentials]
						.concat(rotated ? [rotated] : [])
						.map((item) => (
							<span key={item} className="whitespace-nowrap">
								<span aria-hidden="true">· </span>
								{item}
							</span>
						))}
				</p>
			</div>
			<span className="ml-auto flex shrink-0 items-center gap-1">
				<Tooltip content="Edit">
					<Button
						variant="ghost"
						size="icon"
						onClick={(): void => onAction(row, 'edit')}
						aria-label={`Edit ${row.name}`}
						disabled={pending}
					>
						<Edit2 className="h-4 w-4" />
					</Button>
				</Tooltip>
				<Tooltip content={isAuthCode ? 'Rotate secret' : 'Device flow — no secret'}>
					<span>
						<Button
							variant="ghost"
							size="icon"
							onClick={(): void => onAction(row, 'rotate-secret')}
							aria-label={`Rotate secret for ${row.name}`}
							disabled={!isAuthCode || pending}
						>
							<RefreshCw className="h-4 w-4" />
						</Button>
					</span>
				</Tooltip>
				<Tooltip content={row.is_active ? 'Deactivate' : 'Activate'}>
					<Button
						variant="ghost"
						size="icon"
						onClick={(): void => onAction(row, 'toggle-active')}
						aria-label={`${row.is_active ? 'Deactivate' : 'Activate'} ${row.name}`}
						disabled={pending}
					>
						{row.is_active ? (
							<PowerOff className="h-4 w-4" />
						) : (
							<Power className="h-4 w-4" />
						)}
					</Button>
				</Tooltip>
				<Tooltip content="Delete">
					<Button
						variant="ghost"
						size="icon"
						onClick={(): void => onAction(row, 'delete')}
						aria-label={`Delete ${row.name}`}
						disabled={pending}
					>
						<Trash2 className="h-4 w-4" />
					</Button>
				</Tooltip>
			</span>
		</li>
	);
}
