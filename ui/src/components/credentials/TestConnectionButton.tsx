import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { CheckCircle2, XCircle, Loader2, AlertCircle, HelpCircle } from 'lucide-react';
import { api } from '@/api/client';
import { Button } from '@/components/ui/Button';

/**
 * Edit-page action that issues a single low-impact probe to the upstream API
 * with the credential injected, then renders an inline status pill below
 * itself with the result.
 *
 * Backed by `POST /credentials/{id}/test`, which is intentionally a one-shot
 * call (no scheduling, no caching) — the user is asking the app to *prove*
 * the credential works right now. We don't memoise the result because the
 * credential is mutable: the user typically clicks Test, sees a 401, edits
 * the value, clicks Test again. Stale-while-revalidating that flow would
 * just confuse them.
 *
 * The endpoint also persists its verdict to `credentials.healthy`, so on a
 * successful response we invalidate the `['credentials']` list and the
 * `['credential', id]` detail queries — that's what makes the StatusDot pill
 * flip to its new colour without a page refresh.
 *
 * Hint mapping:
 *   - `unauthorized`         → red — credential rejected
 *   - `rate_limited`         → amber — couldn't tell, try later
 *   - `upstream_error`       → amber — upstream had an issue, not a cred problem
 *   - `pipedream_unsupported`→ neutral — explain why we can't probe
 *   - `no_probe_url`         → neutral — no spec / route to hit
 *   - `timeout` / `network_error` → amber — network problem, retry
 *   - missing hint with ok=true → green
 */
interface TestConnectionButtonProps {
	credentialId: string;
	disabled?: boolean;
}

interface TestResult {
	ok: boolean;
	status: number | null;
	hint: string | null;
	probe_url: string | null;
	message?: string;
}

export function TestConnectionButton({ credentialId, disabled }: TestConnectionButtonProps) {
	const [result, setResult] = useState<TestResult | null>(null);
	const queryClient = useQueryClient();

	const mutation = useMutation({
		mutationFn: () => api.testCredential(credentialId),
		onSuccess: (data) => {
			setResult(data);
			// The /test endpoint persists the verdict to credentials.healthy +
			// health_checked_at, so refetch the list and this credential to pull
			// the new value through — otherwise the StatusDot pill stays stale
			// until a manual page refresh.
			queryClient.invalidateQueries({ queryKey: ['credentials'] });
			queryClient.invalidateQueries({ queryKey: ['credential', credentialId] });
		},
		onError: (e: Error) =>
			setResult({
				ok: false,
				status: null,
				hint: 'network_error',
				probe_url: null,
				message: e.message,
			}),
	});

	return (
		<div className="space-y-2">
			<Button
				type="button"
				variant="secondary"
				size="sm"
				onClick={() => mutation.mutate()}
				disabled={disabled || mutation.isPending}
			>
				{mutation.isPending ? (
					<>
						<Loader2 className="h-4 w-4 animate-spin" />
						Testing…
					</>
				) : (
					<>
						<CheckCircle2 className="h-4 w-4" />
						Test connection
					</>
				)}
			</Button>

			{result && <TestResultPill result={result} />}
		</div>
	);
}

function TestResultPill({ result }: { result: TestResult }) {
	const tone = pillTone(result);
	const Icon = tone.icon;

	return (
		<div
			className={`${tone.bg} ${tone.text} ${tone.border} rounded-md border px-3 py-2 text-xs`}
			role="status"
		>
			<div className="flex items-start gap-2">
				<Icon className="mt-0.5 h-4 w-4 shrink-0" />
				<div className="min-w-0 space-y-1">
					<p className="font-medium">{tone.title}</p>
					<p className="opacity-90">{tone.summary}</p>
					{result.probe_url && (
						<p className="text-muted-foreground font-mono text-[11px] break-all opacity-70">
							→ {result.probe_url}
							{result.status ? ` · HTTP ${result.status}` : ''}
						</p>
					)}
				</div>
			</div>
		</div>
	);
}

interface ToneInfo {
	bg: string;
	text: string;
	border: string;
	icon: typeof CheckCircle2;
	title: string;
	summary: string;
}

function pillTone(r: TestResult): ToneInfo {
	if (r.ok) {
		return {
			bg: 'bg-success/10',
			text: 'text-success',
			border: 'border-success/30',
			icon: CheckCircle2,
			title: r.status ? `Looks good (HTTP ${r.status})` : 'Looks good',
			summary:
				r.status && r.status >= 400
					? 'The upstream responded — the path may not exist, but the credential itself was accepted.'
					: 'The broker reached the upstream and the credential was accepted.',
		};
	}

	switch (r.hint) {
		case 'unauthorized':
			return {
				bg: 'bg-danger/10',
				text: 'text-danger',
				border: 'border-danger/30',
				icon: XCircle,
				title: `Credential rejected (HTTP ${r.status ?? '4xx'})`,
				summary:
					r.message ??
					'The upstream returned 401/403. Check the value, identity, or — for OAuth — reconnect the grant.',
			};
		case 'rate_limited':
			return {
				bg: 'bg-warning/10',
				text: 'text-warning',
				border: 'border-warning/30',
				icon: AlertCircle,
				title: `Rate limited (HTTP ${r.status})`,
				summary:
					r.message ??
					"Upstream returned 429. We can't tell if the credential is healthy — try again in a minute.",
			};
		case 'upstream_error':
			return {
				bg: 'bg-warning/10',
				text: 'text-warning',
				border: 'border-warning/30',
				icon: AlertCircle,
				title: `Upstream error (HTTP ${r.status})`,
				summary:
					r.message ??
					"The upstream returned 5xx. This isn't conclusive about the credential — it just means the API is unhealthy.",
			};
		case 'timeout':
			return {
				bg: 'bg-warning/10',
				text: 'text-warning',
				border: 'border-warning/30',
				icon: AlertCircle,
				title: 'Probe timed out',
				summary:
					r.message ??
					"We waited 5 seconds and got no response. The credential might be fine — the upstream just isn't answering.",
			};
		case 'network_error':
			return {
				bg: 'bg-warning/10',
				text: 'text-warning',
				border: 'border-warning/30',
				icon: AlertCircle,
				title: 'Network error',
				summary: r.message ?? 'Could not reach the upstream from the broker host.',
			};
		case 'pipedream_unsupported':
			return {
				bg: 'bg-muted',
				text: 'text-foreground',
				border: 'border-border',
				icon: HelpCircle,
				title: 'Pipedream credentials are validated by the broker',
				summary:
					r.message ??
					"Test connection doesn't support Pipedream OAuth — the upstream call is mediated by Pipedream. Run a small workflow to verify the connection.",
			};
		case 'no_probe_url':
		default:
			return {
				bg: 'bg-muted',
				text: 'text-foreground',
				border: 'border-border',
				icon: HelpCircle,
				title: 'Could not pick a probe URL',
				summary:
					r.message ??
					"We couldn't infer a safe URL to call from the spec or routes. Add an `x-jentic-healthcheck: true` to a no-required-params GET to use this feature.",
			};
	}
}
