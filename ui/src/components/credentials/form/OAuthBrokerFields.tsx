import { useEffect, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ArrowUpRight, CheckCircle2, Link2, Loader2, RotateCcw } from 'lucide-react';
import { oauthBrokers } from '@/api/client';
import { AppLink } from '@/components/ui/AppLink';
import { Button } from '@/components/ui/Button';
import { toast } from '@/components/ui/toastStore';
import { emitCredentialImported } from '@/lib/events/credentialImported';
import type { ApiOut } from '@/api/types';

/**
 * The OAuth-via-Pipedream sub-block. States:
 *
 *  1. Brokers query is in-flight — show a card-shaped skeleton matching
 *     the connect card's layout. We intentionally don't render the "no
 *     broker configured" path until we know, because it would flash for
 *     the legitimate case where a broker exists but the request is just
 *     slow.
 *  2. A broker exists — show "Create Connect Link" → on success
 *     swap to "Open Connect Link →" + a small "new link" reset action.
 *     Opening the link starts a watch (poll + window-refocus re-check):
 *     when a healthy account for this API appears we flip to a
 *     "✓ Connected" confirmation so the user knows the round trip
 *     through Pipedream's hosted flow actually landed.
 *  3. No broker — show inline setup instructions linking to
 *     `/credentials` where the Pipedream broker can be configured.
 *
 * Owns the `connectLinkMutation` because it's specific to this UI;
 * extracting it would force callers to thread the mutation manually.
 *
 * `disabled` is set by the parent when the label field is empty —
 * the mutation needs a label so the resulting credential row is
 * identifiable in the list.
 */
export interface OAuthBrokerFieldsProps {
	selectedApi: ApiOut;
	label: string;
}

/** How often we poll the accounts list while waiting for the connection. */
const POLL_INTERVAL_MS = 2500;
/** Give up the active poll after this long (refocus re-check still fires). */
const POLL_TIMEOUT_MS = 3 * 60 * 1000;

export function OAuthBrokerFields({ selectedApi, label }: OAuthBrokerFieldsProps) {
	const apiName = selectedApi.name ?? selectedApi.id;
	const queryClient = useQueryClient();

	const { data: brokers, isLoading: brokersLoading } = useQuery({
		queryKey: ['oauth-brokers'],
		queryFn: () => oauthBrokers.list(),
		staleTime: 60 * 1000,
	});
	const activeBroker = brokers?.[0] ?? null;
	const hasOAuthBroker = !!activeBroker;

	// `watching` is on between opening the Pipedream tab and detecting the
	// new account. `connected` latches once we see it. `baselineRef` records
	// the account_ids that existed *before* we opened the link, so a brand
	// new account is unambiguous (rather than matching a pre-existing one).
	const [watching, setWatching] = useState(false);
	const [connected, setConnected] = useState(false);
	const baselineRef = useRef<Set<string> | null>(null);

	const connectLinkMutation = useMutation({
		mutationFn: () => {
			if (!label.trim()) {
				throw new Error('Label is required for OAuth connections');
			}
			const parts = selectedApi.id.split('/');
			// eslint-disable-next-line @typescript-eslint/no-explicit-any -- app_slug is a Pipedream-only hint absent from the generated ApiOut type
			const appSlug = (selectedApi as any).app_slug ?? parts[parts.length - 1];
			return oauthBrokers.connectLink(activeBroker!.id, {
				app: appSlug,
				label: label.trim(),
				api_id: selectedApi.id,
			});
		},
	});

	// Snapshot existing accounts so we can tell the *new* one apart.
	const snapshotBaseline = async () => {
		if (!activeBroker) return;
		try {
			const existing = await oauthBrokers.accounts(activeBroker.id);
			baselineRef.current = new Set(existing.map((a) => a.account_id));
		} catch {
			baselineRef.current = new Set();
		}
	};

	// Watch loop: poll the accounts list and re-check whenever the user
	// returns to this tab. Resolves the moment a healthy, previously-unseen
	// account for this API shows up. Cleans up its timer + listener on unmount
	// or once connected.
	const apiId = selectedApi.id;
	useEffect(() => {
		if (!watching || !activeBroker || connected) return;

		let cancelled = false;
		const startedAt = Date.now();

		// A connected account's api_host can be a prefix/suffix of the API id
		// (e.g. `googleapis.com/gmail` vs `gmail.googleapis.com`), so match
		// loosely in both directions rather than on strict equality.
		const matchesThisApi = (apiHost: string) =>
			apiHost === apiId || apiId.includes(apiHost) || apiHost.includes(apiId);

		const check = async () => {
			if (cancelled) return;
			try {
				const accounts = await oauthBrokers.accounts(activeBroker.id);
				const baseline = baselineRef.current ?? new Set<string>();
				const fresh = accounts.find(
					(a) => a.healthy && !baseline.has(a.account_id) && matchesThisApi(a.api_host),
				);
				if (fresh && !cancelled) {
					setConnected(true);
					setWatching(false);
					// Close the loop everywhere the new credential might show.
					queryClient.invalidateQueries({ queryKey: ['credentials'] });
					queryClient.invalidateQueries({ queryKey: ['oauth-broker-accounts'] });
					emitCredentialImported({ api_id: apiId });
					toast({
						title: `${apiName} connected`,
						description: `"${fresh.label || label.trim()}" is ready to use.`,
						variant: 'success',
					});
				}
			} catch {
				// Transient — keep polling until the timeout.
			}
		};

		const interval = setInterval(() => {
			if (Date.now() - startedAt > POLL_TIMEOUT_MS) {
				setWatching(false);
				return;
			}
			void check();
		}, POLL_INTERVAL_MS);

		// A refocus is the strongest signal the user finished in the other
		// tab — check immediately rather than waiting for the next poll tick.
		const onFocus = () => void check();
		window.addEventListener('focus', onFocus);
		void check();

		return () => {
			cancelled = true;
			clearInterval(interval);
			window.removeEventListener('focus', onFocus);
		};
	}, [watching, connected, activeBroker, apiId, apiName, label, queryClient]);

	if (brokersLoading) {
		// Mirror the connect-card layout (icon + two text lines + button row)
		// so the dialog holds its shape while we find out whether a broker
		// exists — no tiny floating spinner, no jump when the real card lands.
		return (
			<div
				className="bg-muted/40 border-border space-y-3 rounded-lg border p-4"
				aria-hidden="true"
			>
				<div className="flex items-center gap-3">
					<div className="bg-muted h-9 w-9 shrink-0 animate-pulse rounded-lg" />
					<div className="min-w-0 flex-1 space-y-2">
						<div className="bg-muted h-3.5 w-32 animate-pulse rounded" />
						<div className="bg-muted h-3 w-48 animate-pulse rounded" />
					</div>
				</div>
				<div className="bg-muted h-8 w-40 animate-pulse rounded-md" />
			</div>
		);
	}

	if (hasOAuthBroker) {
		const connectUrl = connectLinkMutation.data?.connect_link_url;

		if (connected) {
			return (
				<div className="border-success/40 bg-success/5 flex items-center gap-3 rounded-lg border p-4">
					<div className="bg-success/15 text-success flex h-9 w-9 shrink-0 items-center justify-center rounded-lg">
						<CheckCircle2 className="h-4 w-4" />
					</div>
					<div className="min-w-0">
						<p className="text-foreground text-sm font-medium">{apiName} connected</p>
						<p className="text-muted-foreground text-xs">
							The credential is ready — you can close this dialog.
						</p>
					</div>
				</div>
			);
		}

		return (
			<div className="bg-muted/40 border-border space-y-3 rounded-lg border p-4">
				<div className="flex items-center gap-3">
					<div className="bg-primary/10 text-primary flex h-9 w-9 shrink-0 items-center justify-center rounded-lg">
						<Link2 className="h-4 w-4" />
					</div>
					<div className="min-w-0 flex-1">
						<p className="text-foreground text-sm font-medium">Connect via OAuth</p>
						<p className="text-muted-foreground text-xs leading-snug">
							{apiName} uses OAuth 2.0. Generate a connect link to authorise access.
						</p>
					</div>
				</div>

				{connectLinkMutation.isError && (
					<p className="text-danger text-xs">
						Failed to generate connect link. Check your Pipedream broker config.
					</p>
				)}

				{!connectUrl ? (
					<Button
						variant="primary"
						size="sm"
						disabled={connectLinkMutation.isPending || !label.trim()}
						onClick={() => connectLinkMutation.mutate()}
					>
						{connectLinkMutation.isPending ? (
							<>
								<Loader2 className="mr-1 h-3 w-3 animate-spin" />
								Generating…
							</>
						) : (
							'Create Connect Link'
						)}
					</Button>
				) : watching ? (
					// Once the link is open we own the whole row with a single
					// "waiting" affordance — a second primary button competing
					// with the spinner read as visual noise. "open again" stays
					// available as a quiet text action in case the tab was lost.
					<div className="border-border/60 bg-background flex items-center gap-3 rounded-lg border p-3">
						<Loader2 className="text-primary h-4 w-4 shrink-0 animate-spin" />
						<div className="min-w-0 flex-1">
							<p className="text-foreground text-xs font-medium">
								Waiting for authorisation…
							</p>
							<p className="text-muted-foreground text-[11px] leading-snug">
								Finish connecting {apiName} in the new tab — this updates
								automatically when you're done.
							</p>
						</div>
						<Button
							type="button"
							variant="ghost"
							size="sm"
							className="text-muted-foreground shrink-0 text-xs hover:underline"
							onClick={() => window.open(connectUrl, '_blank', 'noopener,noreferrer')}
						>
							open again
						</Button>
					</div>
				) : (
					<div className="flex items-center gap-2">
						<Button
							variant="primary"
							size="sm"
							onClick={async () => {
								await snapshotBaseline();
								setWatching(true);
								window.open(connectUrl, '_blank', 'noopener,noreferrer');
							}}
						>
							Open Connect Link
							<ArrowUpRight className="ml-0.5 h-3.5 w-3.5" />
						</Button>
						<Button
							type="button"
							variant="ghost"
							size="sm"
							className="text-muted-foreground text-xs hover:underline"
							onClick={() => {
								setWatching(false);
								connectLinkMutation.reset();
							}}
						>
							<RotateCcw className="mr-1 h-3 w-3" />
							new link
						</Button>
					</div>
				)}
			</div>
		);
	}

	return (
		<div className="bg-muted/40 border-border space-y-3 rounded-lg border p-4">
			<div className="flex items-center gap-3">
				<div className="bg-primary/10 text-primary flex h-9 w-9 shrink-0 items-center justify-center rounded-lg">
					<Link2 className="h-4 w-4" />
				</div>
				<div className="min-w-0 flex-1">
					<p className="text-foreground text-sm font-medium">OAuth required</p>
					<p className="text-muted-foreground text-xs leading-snug">
						{apiName} uses OAuth 2.0. Set up Pipedream Connect first:
					</p>
				</div>
			</div>
			<ol className="text-muted-foreground list-decimal space-y-1 pl-5 text-xs">
				<li>
					Go to <AppLink href="/credentials">Credentials</AppLink>
				</li>
				<li>
					Click <strong>Enable OAuth</strong> and enter your Pipedream client ID, secret,
					and project ID
				</li>
				<li>Return here to connect {apiName}</li>
			</ol>
		</div>
	);
}
