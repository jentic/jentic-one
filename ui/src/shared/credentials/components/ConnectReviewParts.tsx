/**
 * Approve-dialog pieces for connect sessions beyond the vendor OAuth flow:
 * the API target's provenance / scheme / pinned hosts, the "how to connect"
 * chooser (the session's own flow, an existing credential, a shared app),
 * and the secret / own-OAuth-client entry steps.
 *
 * Presentational: the owning flow (`VendorConnectFlow`, approve mode) holds
 * the session, calls `:confirm`, and decides which step shows.
 */
import { useId, useState, type FormEvent, type ReactNode } from 'react';
import { ArrowLeft, Globe, KeyRound, ShieldAlert } from 'lucide-react';
import {
	ActorLabel,
	Badge,
	Button,
	CopyButton,
	ErrorAlert,
	Input,
	Label,
	RadioCardGroup,
} from '@/shared/ui';
import type { RadioCardOption } from '@/shared/ui';
import type { ExistingCredential, ReviewSession } from '@/shared/credentials/api/vendors-types';
import {
	isCatalogProvenance,
	schemeSummary,
	secretNoun,
	sharedWithCount,
	type SecretKind,
} from '@/shared/credentials/lib/connectReview';

const FOOTER_CLASS =
	'bg-surface-sheet-foot border-hairline-field -mx-5 -mb-4 flex items-center justify-between border-t px-5 py-3.5';

const EYEBROW_CLASS = 'text-foreground-faint text-[10.5px] font-bold tracking-[0.08em] uppercase';

// ---------------------------------------------------------------------------
// API target details
// ---------------------------------------------------------------------------

/**
 * What an API-target session would hand over and where it may go: where the
 * API's spec came from, the scheme it declares, and the hosts the credential
 * is pinned to. All server data — the agent chose none of it.
 */
export function ApiTargetDetails({ session }: { session: ReviewSession }) {
	const { provenance, scheme, pinned_hosts: hosts } = session;
	const catalog = isCatalogProvenance(session);
	return (
		<div className="bg-surface-inset space-y-3 rounded-lg px-3 py-2.5">
			<div>
				<p className={EYEBROW_CLASS}>API</p>
				<div className="mt-1 flex flex-wrap items-center gap-2">
					<code className="text-foreground text-sm">
						{session.api_reference.vendor}/{session.api_reference.name}
						{session.api_reference.version ? `@${session.api_reference.version}` : ''}
					</code>
					{provenance && (
						<Badge variant={catalog ? 'neutral' : 'warning'}>
							{catalog ? 'Public catalog' : 'Agent- or user-submitted spec'}
						</Badge>
					)}
				</div>
				{provenance && !catalog && (
					<p className="text-muted-foreground mt-1 text-xs">
						This API&apos;s spec didn&apos;t come from the public catalog.
						{provenance.submitted_by && (
							<>
								{' '}
								Submitted by <ActorLabel actorId={provenance.submitted_by} />.
							</>
						)}{' '}
						Check the hosts below before you hand over a credential.
					</p>
				)}
				{provenance?.source_url && (
					<p className="text-muted-foreground mt-1 truncate text-xs">
						Source: <span className="font-mono">{provenance.source_url}</span>
					</p>
				)}
			</div>
			{scheme && (
				<div>
					<p className={EYEBROW_CLASS}>Authentication</p>
					<p className="text-foreground mt-1 flex items-center gap-1.5 text-sm">
						<KeyRound className="text-muted-foreground h-3.5 w-3.5 shrink-0" />
						{schemeSummary(scheme)}
					</p>
				</div>
			)}
			{hosts && hosts.length > 0 && (
				<div>
					<p className={EYEBROW_CLASS}>Sent only to</p>
					<ul className="mt-1 space-y-0.5" aria-label="Pinned server hosts">
						{hosts.map((host) => (
							<li
								key={host}
								className="text-foreground flex items-center gap-1.5 font-mono text-xs"
							>
								<Globe className="text-muted-foreground h-3.5 w-3.5 shrink-0" />
								{host}
							</li>
						))}
					</ul>
				</div>
			)}
		</div>
	);
}

/** Shown instead of the approve controls when this viewer cannot confirm. */
export function ConfirmBlockedNotice({ reason }: { reason: string }) {
	return (
		<div
			role="note"
			className="bg-surface-inset text-muted-foreground flex items-start gap-2 rounded-lg px-3 py-2.5 text-xs"
		>
			<ShieldAlert className="mt-0.5 h-4 w-4 shrink-0" />
			<p>{reason}</p>
		</div>
	);
}

// ---------------------------------------------------------------------------
// How to connect
// ---------------------------------------------------------------------------

/**
 * How the approver resolves the session:
 *
 * * `primary` — the session's own flow (vendor sign-in, a typed secret, or,
 *   for `awaiting_app`, the approver's own OAuth client);
 * * `shared_app` — register an organisation-wide OAuth app (hosts that have
 *   that surface only);
 * * `existing:<id>` — bind a credential the approver already holds;
 * * `reauthorize:<id>` — bind it and ask the vendor for the missing scopes
 *   (only when no other agent uses it).
 */
export type ConnectMethod =
	| { kind: 'primary' }
	| { kind: 'shared_app' }
	| { kind: 'existing'; credentialId: string }
	| { kind: 'reauthorize'; credentialId: string };

export function methodKey(method: ConnectMethod): string {
	if (method.kind === 'existing') return `existing:${method.credentialId}`;
	if (method.kind === 'reauthorize') return `reauthorize:${method.credentialId}`;
	return method.kind;
}

export function parseMethodKey(key: string): ConnectMethod {
	if (key.startsWith('existing:')) {
		return { kind: 'existing', credentialId: key.slice('existing:'.length) };
	}
	if (key.startsWith('reauthorize:')) {
		return { kind: 'reauthorize', credentialId: key.slice('reauthorize:'.length) };
	}
	return key === 'shared_app' ? { kind: 'shared_app' } : { kind: 'primary' };
}

function isOAuthType(type: string): boolean {
	return type.startsWith('oauth2');
}

function scopeList(scopes: readonly string[]): string {
	return scopes.length ? scopes.join(', ') : 'none';
}

/** The description under an existing credential's option. */
function candidateDescription(credential: ExistingCredential, requested: string[]): ReactNode {
	if (!isOAuthType(credential.type)) {
		return "Already connected. Its upstream permissions aren't visible to Jentic, so check it can do what the agent needs.";
	}
	const granted = credential.granted_scopes ?? [];
	if (credential.can_bind) {
		return requested.length
			? `Its sign-in covers the requested scopes (${scopeList(requested)}).`
			: 'Already connected.';
	}
	const missing = credential.missing_scopes ?? [];
	return (
		<span className="block space-y-1">
			<span className="block">
				Granted {scopeList(granted)}; the agent asked for {scopeList(requested)}. Missing{' '}
				{scopeList(missing)}, so binding it as is would fail upstream.
			</span>
			{!credential.can_reauthorize && credential.other_bound_agent_ids.length > 0 && (
				<span className="block">
					{sharedWithCount(credential)} (
					{credential.other_bound_agent_ids.map((id, i) => (
						<span key={id}>
							{i > 0 && ', '}
							<ActorLabel actorId={id} />
						</span>
					))}
					), so it can&apos;t be widened: that would widen theirs too. Connect a new
					credential instead.
				</span>
			)}
		</span>
	);
}

/**
 * The radio options for "how to connect". `primaryLabel` / `primaryDescription`
 * name the session's own flow.
 */
export function buildMethodOptions({
	primaryLabel,
	primaryDescription,
	sharedApp,
	candidates,
	requestedScopes,
}: {
	primaryLabel: string;
	primaryDescription: string;
	sharedApp: boolean;
	candidates: readonly ExistingCredential[];
	requestedScopes: string[];
}): RadioCardOption<string>[] {
	const options: RadioCardOption<string>[] = [
		{ value: 'primary', label: primaryLabel, description: primaryDescription },
	];
	if (sharedApp) {
		options.push({
			value: 'shared_app',
			label: 'Register as a shared app',
			description:
				'Register an OAuth app for your organization. This request and any later ones for this API connect through it.',
		});
	}
	for (const credential of candidates) {
		options.push({
			value: `existing:${credential.credential_id}`,
			label: `Use ${credential.name}`,
			description: candidateDescription(credential, requestedScopes),
			disabled: !credential.can_bind,
		});
		if (!credential.can_bind && credential.can_reauthorize) {
			options.push({
				value: `reauthorize:${credential.credential_id}`,
				label: `Re-authorize ${credential.name} with more scopes`,
				description: `No other agent uses it. You'll sign in again to grant ${scopeList(
					credential.missing_scopes ?? [],
				)}.`,
			});
		}
	}
	return options;
}

export function ConnectMethodField({
	options,
	value,
	onChange,
}: {
	options: RadioCardOption<string>[];
	value: string;
	onChange: (value: string) => void;
}) {
	const labelId = useId();
	return (
		<div className="space-y-2">
			<Label id={labelId}>How do you want to connect?</Label>
			<RadioCardGroup
				options={options}
				value={value}
				onChange={onChange}
				ariaLabelledBy={labelId}
				maxHeightClass="max-h-80"
			/>
		</div>
	);
}

// ---------------------------------------------------------------------------
// Secret entry (manual_* flows)
// ---------------------------------------------------------------------------

export type EnteredSecret =
	| { kind: 'api_key'; key: string }
	| { kind: 'bearer'; token: string }
	| { kind: 'basic'; username: string; password: string };

/**
 * The secret the approver types for a `manual_*` session. The values live in
 * this component's own state only, so leaving the step (Back, closing the
 * dialog) drops them — a deliberate exception to "persist drafts between
 * dismissals" for sensitive input. Nothing here is logged or stored.
 */
export function SecretEntryStep({
	header,
	kind,
	fieldHint,
	submitting,
	error,
	onBack,
	onSubmit,
}: {
	header: ReactNode;
	kind: SecretKind;
	/** Where the secret goes (from the declared scheme), shown under the field. */
	fieldHint: string | null;
	submitting: boolean;
	error: string | null;
	onBack: () => void;
	onSubmit: (secret: EnteredSecret) => void;
}) {
	const [secret, setSecret] = useState('');
	const [username, setUsername] = useState('');
	const secretId = useId();
	const usernameId = useId();
	const ready = secret.length > 0 && (kind !== 'basic' || username.trim().length > 0);

	const submit = (e: FormEvent): void => {
		e.preventDefault();
		if (!ready || submitting) return;
		if (kind === 'api_key') onSubmit({ kind, key: secret });
		else if (kind === 'bearer') onSubmit({ kind, token: secret });
		else onSubmit({ kind, username: username.trim(), password: secret });
	};

	return (
		<form className="space-y-5" autoComplete="off" onSubmit={submit}>
			{header}
			<p className="text-muted-foreground text-xs">
				It&apos;s encrypted when stored and never shown again, to you or the agent.
			</p>
			{kind === 'basic' && (
				<div className="space-y-1.5">
					<Label htmlFor={usernameId} required>
						Username
					</Label>
					<Input
						id={usernameId}
						autoComplete="off"
						spellCheck={false}
						value={username}
						onChange={(e): void => setUsername(e.target.value)}
					/>
				</div>
			)}
			<div className="space-y-1.5">
				<Label htmlFor={secretId} required>
					{kind === 'api_key'
						? 'API key'
						: kind === 'bearer'
							? 'Bearer token'
							: 'Password'}
				</Label>
				<Input
					id={secretId}
					type="password"
					showPasswordToggle
					autoComplete="off"
					spellCheck={false}
					value={secret}
					onChange={(e): void => setSecret(e.target.value)}
				/>
				{fieldHint && <p className="text-muted-foreground text-xs">{fieldHint}</p>}
			</div>

			{error && <ErrorAlert message={error} />}

			<div className={FOOTER_CLASS}>
				<Button
					type="button"
					variant="ghost"
					size="sm"
					onClick={onBack}
					disabled={submitting}
				>
					<ArrowLeft className="h-4 w-4" />
					Back
				</Button>
				<Button type="submit" variant="primary" disabled={!ready} loading={submitting}>
					Connect
				</Button>
			</div>
		</form>
	);
}

/** "Paste the API key for Acme." — the subtitle of the secret step. */
export function secretStepSubtitle(kind: SecretKind, displayName: string): string {
	return `Enter the ${secretNoun(kind)} for ${displayName}.`;
}

// ---------------------------------------------------------------------------
// Own OAuth client (awaiting_app)
// ---------------------------------------------------------------------------

function isHttpsUrl(value: string): boolean {
	try {
		return new URL(value.trim()).protocol === 'https:';
	} catch {
		return false;
	}
}

export interface EnteredOAuthClient {
	clientId: string;
	clientSecret: string;
	authorizeUrl: string;
	tokenUrl: string;
}

/**
 * The approver's own OAuth client for an `awaiting_app` session — the same
 * fields as a direct OAuth 2.0 credential. The endpoints are optional: left
 * blank, the API's declared authorization-code endpoints apply. The client
 * secret follows the sensitive-input rule of {@link SecretEntryStep}.
 */
export function OwnOAuthClientStep({
	header,
	callbackUrl,
	submitting,
	error,
	onBack,
	onSubmit,
}: {
	header: ReactNode;
	/** The redirect URI to allow on the OAuth app, when the server reports one. */
	callbackUrl: string | null;
	submitting: boolean;
	error: string | null;
	onBack: () => void;
	onSubmit: (client: EnteredOAuthClient) => void;
}) {
	const [clientId, setClientId] = useState('');
	const [clientSecret, setClientSecret] = useState('');
	const [authorizeUrl, setAuthorizeUrl] = useState('');
	const [tokenUrl, setTokenUrl] = useState('');
	const ids = {
		clientId: useId(),
		clientSecret: useId(),
		authorizeUrl: useId(),
		tokenUrl: useId(),
		callback: useId(),
	};
	// Both endpoints are the approver's own: the API's declared ones are never
	// used, since an agent may have submitted the spec and the token URL
	// receives the client secret.
	const ready =
		clientId.trim().length > 0 &&
		clientSecret.length > 0 &&
		isHttpsUrl(authorizeUrl) &&
		isHttpsUrl(tokenUrl);

	const submit = (e: FormEvent): void => {
		e.preventDefault();
		if (!ready || submitting) return;
		onSubmit({
			clientId: clientId.trim(),
			clientSecret,
			authorizeUrl: authorizeUrl.trim(),
			tokenUrl: tokenUrl.trim(),
		});
	};

	return (
		<form className="space-y-5" autoComplete="off" onSubmit={submit}>
			{header}
			<div className="space-y-1.5">
				<Label htmlFor={ids.clientId} required>
					Client ID
				</Label>
				<Input
					id={ids.clientId}
					autoComplete="off"
					spellCheck={false}
					value={clientId}
					onChange={(e): void => setClientId(e.target.value)}
				/>
			</div>
			<div className="space-y-1.5">
				<Label htmlFor={ids.clientSecret} required>
					Client secret
				</Label>
				<Input
					id={ids.clientSecret}
					type="password"
					showPasswordToggle
					autoComplete="off"
					spellCheck={false}
					value={clientSecret}
					onChange={(e): void => setClientSecret(e.target.value)}
				/>
			</div>
			<div className="space-y-1.5">
				<Label htmlFor={ids.authorizeUrl} required>
					Authorize URL
				</Label>
				<Input
					id={ids.authorizeUrl}
					type="url"
					autoComplete="off"
					value={authorizeUrl}
					onChange={(e): void => setAuthorizeUrl(e.target.value)}
					placeholder="https://provider.com/oauth/authorize"
				/>
			</div>
			<div className="space-y-1.5">
				<Label htmlFor={ids.tokenUrl} required>
					Token URL
				</Label>
				<Input
					id={ids.tokenUrl}
					type="url"
					autoComplete="off"
					value={tokenUrl}
					onChange={(e): void => setTokenUrl(e.target.value)}
					placeholder="https://provider.com/oauth/token"
				/>
				<p className="text-muted-foreground text-xs">
					Your OAuth provider&apos;s https endpoints. The token URL receives your client
					secret, so copy both from your provider, not from the API&apos;s spec.
				</p>
			</div>
			{callbackUrl && (
				<div className="space-y-1.5">
					<Label htmlFor={ids.callback}>Callback URL</Label>
					<div className="flex gap-1.5">
						<Input
							id={ids.callback}
							value={callbackUrl}
							readOnly
							className="flex-1 font-mono text-xs"
						/>
						<CopyButton
							value={callbackUrl}
							ariaLabel="Copy callback URL"
							toastMessage="Callback URL copied"
						/>
					</div>
					<p className="text-muted-foreground text-xs">
						Add this URL to your OAuth app&apos;s allowed redirect URIs.
					</p>
				</div>
			)}

			{error && <ErrorAlert message={error} />}

			<div className={FOOTER_CLASS}>
				<Button
					type="button"
					variant="ghost"
					size="sm"
					onClick={onBack}
					disabled={submitting}
				>
					<ArrowLeft className="h-4 w-4" />
					Back
				</Button>
				<Button type="submit" variant="primary" disabled={!ready} loading={submitting}>
					Continue to sign-in
				</Button>
			</div>
		</form>
	);
}
