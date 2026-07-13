/**
 * StreamEventIcon — small status icon that explains *what* happened on a row.
 * Gives every stream row a clear "what" read alongside its severity stripe.
 *
 * Keyed by the real wire `type` (e.g. "execution.failed"), with a per-kind
 * fallback so any event the backend adds later still renders a sensible glyph
 * without a code change.
 */
import {
	AlertTriangle,
	CheckCircle2,
	Clock,
	Database,
	KeyRound,
	PackageCheck,
	PackageX,
	PlayCircle,
	ShieldQuestion,
	XCircle,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import type { StreamEvent, StreamKind } from '@/shared/lib/agentStream';
import { cn } from '@/shared/lib/utils';

const TYPE_ICON_MAP: Record<string, { Icon: LucideIcon; tone: string }> = {
	'import.completed': { Icon: PackageCheck, tone: 'text-success' },
	'import.failed': { Icon: PackageX, tone: 'text-danger' },
	'execution.completed': { Icon: CheckCircle2, tone: 'text-success' },
	'execution.failed': { Icon: XCircle, tone: 'text-danger' },
	'execution.repeated_failure': { Icon: AlertTriangle, tone: 'text-danger' },
	'credential.expiring_soon': { Icon: Clock, tone: 'text-warning' },
	'credential.expired': { Icon: KeyRound, tone: 'text-danger' },
	'access_request.filed': { Icon: ShieldQuestion, tone: 'text-warning' },
	'access_request.approved': { Icon: CheckCircle2, tone: 'text-success' },
	'access_request.denied': { Icon: XCircle, tone: 'text-warning' },
	'access_request.withdrawn': { Icon: XCircle, tone: 'text-muted-foreground' },
};

const KIND_ICON_MAP: Record<StreamKind, { Icon: LucideIcon; tone: string }> = {
	import: { Icon: Database, tone: 'text-muted-foreground' },
	execution: { Icon: PlayCircle, tone: 'text-muted-foreground' },
	credential: { Icon: KeyRound, tone: 'text-muted-foreground' },
	access_request: { Icon: ShieldQuestion, tone: 'text-muted-foreground' },
	other: { Icon: AlertTriangle, tone: 'text-muted-foreground' },
};

export function StreamEventIcon({
	ev,
	className,
}: {
	ev: Pick<StreamEvent, 'type' | 'kind'>;
	className?: string;
}) {
	const entry = TYPE_ICON_MAP[ev.type] ?? KIND_ICON_MAP[ev.kind];
	const Icon = entry.Icon;
	const tone = entry.tone;
	return <Icon className={cn('h-3.5 w-3.5 shrink-0', tone, className)} aria-hidden />;
}
