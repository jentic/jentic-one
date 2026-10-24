/**
 * Manage access header: the API, its status, host · auth type, the
 * not-serving / API-deleted state, Pause / Resume (the reversible cut-off,
 * `agents:write`), close — and, while an OAuth sign-in is unfinished, the way
 * to finish it.
 */
import { ExternalLink, LogIn, PauseCircle, PlayCircle, X } from 'lucide-react';
import {
	Button,
	SheetHeader,
	Tooltip,
	TruncateWithTooltip,
	UserText,
	VendorIcon,
} from '@/shared/ui';
import { vendorIconPropsFor } from '@/shared/lib';
import { credentialTypeIcon } from '@/shared/credentials/lib/authTypeIcons';
import { TileStatusChip } from '@/modules/agents/components/flat/TileStatusMarker';
import type { ApiTileModel } from '@/modules/agents/lib/apiTiles';
import type { TileStatus } from '@/modules/agents/lib/tileStatus';

export interface AccessHeaderProps {
	tile: ApiTileModel;
	headingId: string;
	agentName: string;
	status: TileStatus;
	/** Pause / Resume offered (`agents:write`, agent not archived). */
	canPause: boolean;
	pausing: boolean;
	resuming: boolean;
	onPause: () => void;
	onResume: () => void;
	onClose: () => void;
	/** Finish connecting offered (`credentials:write`). */
	canConnect: boolean;
	connecting: boolean;
	onConnect: () => void;
}

export function AccessHeader({
	tile,
	headingId,
	agentName,
	status,
	canPause,
	pausing,
	resuming,
	onPause,
	onResume,
	onClose,
	canConnect,
	connecting,
	onConnect,
}: AccessHeaderProps) {
	const AuthIcon = tile.authType ? credentialTypeIcon(tile.authType) : null;
	return (
		<SheetHeader className="justify-between">
			<div className="flex min-w-0 items-start gap-3">
				<VendorIcon {...vendorIconPropsFor(tile)} size="sm" />
				<div className="min-w-0 space-y-0.5">
					<div className="flex min-w-0 items-center gap-2">
						<h2
							id={headingId}
							dir="auto"
							className="font-heading text-foreground-name min-w-0 text-base font-semibold"
						>
							<TruncateWithTooltip>{tile.title}</TruncateWithTooltip>
						</h2>
						<TileStatusChip status={status} className="shrink-0" />
					</div>
					<p className="text-muted-foreground truncate text-xs">
						<UserText>{tile.host}</UserText>
						{tile.authLabel && (
							<>
								{' · '}
								{AuthIcon && (
									<AuthIcon
										aria-hidden="true"
										className="mr-0.5 inline-block h-3 w-3 align-[-2px]"
									/>
								)}
								{tile.authLabel}
							</>
						)}{' '}
						— access for <UserText>{agentName}</UserText>
						{tile.suspended ? ' · not serving calls' : ''}
						{tile.suspended && tile.suspendedReason === 'api_deleted'
							? ' because its API was deleted — review the rules before resuming'
							: ''}
					</p>
					{tile.awaitingConsent && (
						<div
							data-testid="sidebar-connect-affordance"
							className="flex flex-wrap items-center gap-x-3 gap-y-1.5 pt-1"
						>
							<p className="text-foreground flex items-center gap-1.5 text-xs">
								<LogIn
									className="text-warning h-3.5 w-3.5 shrink-0"
									aria-hidden="true"
								/>
								Waiting for a sign-in at {tile.vendor}. Calls fail until it
								completes.
							</p>
							{canConnect && (
								<Button size="xs" loading={connecting} onClick={onConnect}>
									<ExternalLink className="h-3.5 w-3.5" />
									Finish connecting
								</Button>
							)}
						</div>
					)}
				</div>
			</div>
			<div className="flex shrink-0 items-center gap-1.5">
				{canPause &&
					(tile.suspended ? (
						<Tooltip
							content="Resume this binding — rules survived; access is restored."
							interactiveChild
							placement="bottom"
						>
							<Button
								size="sm"
								variant="secondary"
								loading={resuming}
								onClick={onResume}
								aria-label={`Resume binding for ${tile.credentialName}`}
							>
								<PlayCircle className="h-4 w-4" /> Resume
							</Button>
						</Tooltip>
					) : (
						<Tooltip
							content="Pause this binding — reversible; rules survive and resume restores access."
							interactiveChild
							placement="bottom"
						>
							<Button
								size="sm"
								variant="secondary"
								loading={pausing}
								onClick={onPause}
								aria-label={`Suspend binding for ${tile.credentialName}`}
							>
								<PauseCircle className="h-4 w-4" /> Pause
							</Button>
						</Tooltip>
					))}
				<Button
					variant="ghost"
					size="icon-xs"
					aria-label="Close"
					onClick={onClose}
					className="shrink-0"
				>
					<X className="h-4 w-4" />
				</Button>
			</div>
		</SheetHeader>
	);
}
