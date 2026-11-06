/**
 * The read-only body of the review pop-up for a call that is still waiting,
 * top to bottom: who is asking for what (the agent, the API and credential,
 * the call's headline, a "Deletes data" tag on a DELETE), when it was asked
 * and when it expires, who can decide it, the agent's own reason, and the
 * exact request that runs if approved. Every value is the one the backend
 * holds; only the labels are made readable.
 */
import type { ReactNode } from 'react';
import { ArrowUpRight, Clock, MessageSquareQuote, Timer, Trash2, Users } from 'lucide-react';
import {
	AgentBadge,
	AppLink,
	CopyButton,
	MethodBadge,
	SectionLabel,
	StatusChip,
	Tooltip,
	UserText,
	VendorIcon,
} from '@/shared/ui';
import { cn, formatTimestamp } from '@/shared/lib/utils';
import { ROUTE_PATHS } from '@/shared/app/routes';
import {
	agentReasonOf,
	isDeleteCall,
	isRouteHeadline,
	type HeldCallDetail,
} from '@/shared/approvals/heldCall';
import { askPhrase, expiresIn, waitingSince } from '@/shared/approvals/heldCallCopy';
import type { DecidersLine } from '@/shared/approvals/review/people';
import {
	expiresSoon,
	formatBody,
	humanKey,
	splitUrl,
} from '@/shared/approvals/review/reviewFormat';

/** "expires in 23 h" as a line of its own: "Expires in 23 h". */
const sentence = (text: string) => text.charAt(0).toUpperCase() + text.slice(1);
const isDate = (iso: string) => !Number.isNaN(Date.parse(iso));

/** A route with a break opportunity after each slash, so a long one wraps between segments. */
function breakAtSlashes(route: string): ReactNode[] {
	return route
		.split('/')
		.flatMap((part, i) => (i === 0 ? [part] : [<wbr key={i} />, `/${part}`]));
}

export function CallHeader({
	call,
	agentName,
	headline,
	credentialName,
	deciders,
}: {
	call: HeldCallDetail;
	agentName: string;
	headline: string;
	credentialName: string | null;
	deciders: DecidersLine | null;
}) {
	const isRoute = isRouteHeadline(headline);
	return (
		<div className="space-y-2.5">
			<div className="text-foreground-sub flex min-w-0 flex-wrap items-center gap-x-1.5 gap-y-1 text-[12.5px]">
				<AgentBadge id={call.agent_id} name={agentName} size="xs" shape="circle" />
				<span
					className="text-foreground-name min-w-0 font-semibold [overflow-wrap:anywhere]"
					dir="auto"
				>
					{agentName}
				</span>
				<span aria-hidden="true" className="text-foreground-faint">
					→
				</span>
				<VendorIcon name={call.api_name} vendor={call.api_vendor} size="xs" />
				<span
					className="text-foreground-name min-w-0 font-semibold [overflow-wrap:anywhere]"
					dir="auto"
				>
					{call.api_name}
				</span>
				{credentialName && (
					<span className="text-foreground-sub">
						· <UserText>{credentialName}</UserText>
					</span>
				)}
			</div>
			<p className="font-heading text-foreground-name text-[19px] leading-7 font-semibold tracking-[-0.01em] [overflow-wrap:anywhere]">
				<UserText>{agentName}</UserText>{' '}
				<span className="text-foreground-sub font-normal">wants to run</span>{' '}
				<span className={cn(isRoute && 'font-mono text-[0.85em] break-words')}>
					<UserText>
						{isRoute ? breakAtSlashes(askPhrase(headline)) : askPhrase(headline)}
					</UserText>
				</span>
			</p>
			{isDeleteCall(call) && (
				<StatusChip tone="danger" icon={Trash2}>
					Deletes data
				</StatusChip>
			)}
			<div className="text-foreground-sub flex flex-wrap items-center gap-x-4 gap-y-1 text-[12px]">
				{/* A time the row doesn't state is left out, not shown as a dash. */}
				{isDate(call.created_at) && (
					<span className="inline-flex items-center gap-1.5">
						<Clock className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
						<Tooltip content={formatTimestamp(call.created_at)}>
							Asked {waitingSince(call.created_at)}
						</Tooltip>
					</span>
				)}
				{isDate(call.expires_at) && (
					<span className="inline-flex items-center gap-1.5">
						<Timer
							className={cn(
								'h-3.5 w-3.5 shrink-0',
								expiresSoon(call.expires_at) && 'text-warning',
							)}
							aria-hidden="true"
						/>
						<Tooltip content={formatTimestamp(call.expires_at)}>
							{sentence(expiresIn(call.expires_at))}
						</Tooltip>
					</span>
				)}
				{deciders && (
					<span className="inline-flex basis-full items-center gap-1.5">
						<Users className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
						{deciders.others && deciders.others.length > 0 ? (
							<Tooltip content={deciders.others.join(', ')}>{deciders.text}</Tooltip>
						) : (
							deciders.text
						)}
					</span>
				)}
			</div>
		</div>
	);
}

/**
 * The agent's own account of why it made the call, quoted so it reads as the
 * agent's words, not the platform's. Rendered only when the call carries one:
 * a call without a reason shows no box at all.
 */
export function AgentReason({ call }: { call: HeldCallDetail }) {
	const reason = agentReasonOf(call);
	if (!reason) return null;
	return (
		<section
			aria-label="Why the agent is asking"
			className="border-hairline-field rounded-lg border border-dashed px-3.5 py-3"
		>
			<p className="text-foreground-faint mb-1.5 flex items-center gap-1.5 text-[10.5px] font-bold tracking-[0.08em] uppercase">
				<MessageSquareQuote className="h-3.5 w-3.5" aria-hidden="true" />
				Why the agent is asking
			</p>
			<p
				className="text-foreground max-h-40 overflow-y-auto text-[13.5px] leading-relaxed [overflow-wrap:anywhere] whitespace-pre-line"
				dir="auto"
			>
				“{reason}”
			</p>
		</section>
	);
}

function DetailRow({ label, children }: { label: string; children: ReactNode }) {
	return (
		<div className="grid grid-cols-[minmax(0,1fr)_minmax(0,1.8fr)] items-baseline gap-4 py-2 text-[13px]">
			<dt className="text-foreground-name min-w-0 font-semibold">{label}</dt>
			<dd className="text-foreground-name min-w-0 text-right font-mono text-[12px] [overflow-wrap:anywhere]">
				{children}
			</dd>
		</div>
	);
}

/**
 * The exact request that runs if approved: its method and URL on one line, then
 * each query parameter by a readable name, then the body. A call without query
 * parameters or a body shows neither; nothing stands in for what isn't there.
 */
export function RequestDetails({ call }: { call: HeldCallDetail }) {
	const request = call.request ?? null;
	const { base, params } = request
		? splitUrl(request.url)
		: { base: call.path, params: [] as [string, string][] };
	return (
		<section aria-label="Request details" className="space-y-1.5">
			<SectionLabel as="h3">Request details</SectionLabel>
			<p className="bg-surface-field flex items-start gap-2.5 rounded-lg px-3.5 py-2.5">
				<MethodBadge method={request?.method ?? call.method} />
				<span className="text-foreground min-w-0 flex-1 font-mono text-[12px] leading-5 [overflow-wrap:anywhere]">
					{base}
				</span>
			</p>
			{params.length > 0 && (
				<dl className="divide-hairline-row divide-y">
					{params.map(([key, value], i) => (
						<DetailRow key={`${key}-${i}`} label={humanKey(key)}>
							{value}
						</DetailRow>
					))}
				</dl>
			)}
			{!request ? (
				<p className="text-foreground-sub pt-1 text-xs">
					The held request is no longer available.
				</p>
			) : request.body ? (
				<div className="space-y-1.5 pt-1">
					<pre
						aria-label="Request body"
						className="bg-code text-foreground max-h-72 overflow-auto rounded-md px-3 py-2.5 font-mono text-[12px] leading-5"
					>
						{formatBody(request.body)}
					</pre>
					{request.body_truncated && (
						<p className="text-foreground-sub text-[11.5px]">
							The body is shortened for display; the full body runs if approved.
						</p>
					)}
				</div>
			) : null}
		</section>
	);
}

/**
 * Under the request: where the hold comes from (an Ask rule on the agent's
 * access to the API) with the way to the agent's rules, and the call's
 * reference for a ticket or a chat. The id stays out of the copy otherwise.
 */
export function ReviewFooter({ call }: { call: HeldCallDetail }) {
	return (
		<footer className="text-foreground-faint flex flex-wrap items-center justify-between gap-x-4 gap-y-1 text-[11.5px]">
			<p>
				An Ask rule on this agent&apos;s access to <UserText>{call.api_name}</UserText>{' '}
				holds calls like this one.{' '}
				<AppLink
					href={ROUTE_PATHS.agentTab(call.agent_id)}
					className="text-primary inline-flex items-center gap-0.5 font-medium hover:underline"
				>
					Review the agent&apos;s rules
					<ArrowUpRight className="h-3 w-3" aria-hidden="true" />
				</AppLink>
			</p>
			<span className="inline-flex items-center gap-1">
				Copy reference
				<CopyButton
					value={call.id}
					size="icon"
					variant="ghost"
					className="h-6 w-6 shrink-0"
					ariaLabel="Copy held call reference"
					toastMessage="Reference copied"
				/>
			</span>
		</footer>
	);
}
