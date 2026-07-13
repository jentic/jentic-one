import React, { useId } from 'react';
import { CopyButton, Input, Label, Select, ScopePicker } from '@/shared/ui';
import {
	CredentialType,
	KEY_LOCATIONS,
	type ProviderDiscoveryEntryResponse,
} from '@/modules/credentials/api';
import { isManagedProvider, providerOptions } from '@/modules/credentials/config';
import type { EnhancedScope } from '@/shared/lib';
import type { OAuth2FlowDef } from '@/modules/credentials/lib/schemes';

/**
 * The full create form state — a superset of every per-type field. Only the
 * fields relevant to the chosen `type` are read when assembling the request
 * body (see `buildCreateBody`), so unrelated values are simply ignored.
 */
export interface CredentialFormState {
	name: string;
	provider: string;
	apiVendor: string;
	apiName: string;
	apiVersion: string;
	// bearer_token
	token: string;
	// api_key
	key: string;
	fieldName: string;
	location: 'header' | 'query';
	// basic
	username: string;
	password: string;
	// oauth2
	clientId: string;
	clientSecret: string;
	tokenUrl: string;
	authorizeUrl: string;
	grantType: string;
	scopes: string;
	/**
	 * Values for OpenAPI server variables (e.g. Atlassian `{your-domain}`),
	 * keyed by variable name. Collected from the spec's `servers[].variables`
	 * and surfaced via {@link ServerVariablesSection}. See the create-body
	 * builder for how (and how far) these reach the wire today.
	 */
	serverVars: Record<string, string>;
}

export const EMPTY_FORM: CredentialFormState = {
	name: '',
	provider: 'static',
	apiVendor: '',
	apiName: '',
	apiVersion: '',
	token: '',
	key: '',
	fieldName: '',
	location: 'header',
	username: '',
	password: '',
	clientId: '',
	clientSecret: '',
	tokenUrl: '',
	authorizeUrl: '',
	grantType: '',
	scopes: '',
	serverVars: {},
};

interface FieldsProps {
	type: CredentialType;
	state: CredentialFormState;
	onChange: (patch: Partial<CredentialFormState>) => void;
	errors?: Partial<Record<keyof CredentialFormState, string>>;
	/** Edit mode reframes secret fields as optional rotations. */
	mode: 'create' | 'edit';
	/**
	 * When the spec declares OAuth2 scopes, the parent passes the grouped
	 * scope picker wiring here and the OAuth2 form renders {@link ScopePicker}
	 * instead of the free-text input. Omitted → plain text input fallback.
	 */
	scope?: ScopePickerWiring;
	/** Available OAuth2 flows parsed from the spec (drives grant type selector + URL seeding). */
	flows?: OAuth2FlowDef[];
	/**
	 * The `id` of the currently-selected flow (when multiple `(scheme, flow)`
	 * pairs are available). Optional — when omitted the component falls back
	 * to matching `state.grantType` (single-flow specs).
	 */
	activeFlowId?: string | null;
	/** Fired when the user picks a different flow in the grant-type selector. */
	onFlowChange?: (flowId: string) => void;
	/** Callback URL from the provider config, shown as a copyable helper. */
	callbackUrl?: string;
	/**
	 * Discovered providers from `useProviders()`. Drives whether the managed
	 * Pipedream option appears in the OAuth2 connect picker (only when reported
	 * `configured`). Omitted → just the always-available direct options.
	 */
	providers?: ProviderDiscoveryEntryResponse[];
}

/** Scope-picker wiring lifted to the parent (owns selection + auto-select). */
export interface ScopePickerWiring {
	available: EnhancedScope[];
	selected: string[];
	onToggle: (scope: string) => void;
	onSelectAll: (group?: string) => void;
	onDeselectAll: (group?: string) => void;
}

/**
 * Renders the secret/config fields that vary by credential type. The shared
 * fields (name, provider, API reference) live in the parent sheet so they
 * stay put when the type picker changes.
 */
export function CredentialTypeFields({
	type,
	state,
	onChange,
	errors = {},
	mode,
	scope,
	flows,
	activeFlowId,
	onFlowChange,
	callbackUrl,
	providers,
}: FieldsProps) {
	const secretHint = mode === 'edit' ? 'Leave blank to keep the current value.' : undefined;

	if (type === CredentialType.BEARER_TOKEN) {
		return (
			<Field
				label="Token"
				required={mode === 'create'}
				hint={secretHint}
				error={errors.token}
			>
				<Input
					type="password"
					showPasswordToggle
					autoComplete="off"
					value={state.token}
					onChange={(e): void => onChange({ token: e.target.value })}
					placeholder="sk_live_…"
				/>
			</Field>
		);
	}

	if (type === CredentialType.API_KEY) {
		return (
			<>
				<Field
					label="API key"
					required={mode === 'create'}
					hint={secretHint}
					error={errors.key}
				>
					<Input
						type="password"
						showPasswordToggle
						autoComplete="off"
						value={state.key}
						onChange={(e): void => onChange({ key: e.target.value })}
					/>
				</Field>
				<Field label="Field name" required={mode === 'create'} error={errors.fieldName}>
					<Input
						value={state.fieldName}
						onChange={(e): void => onChange({ fieldName: e.target.value })}
						placeholder="X-Api-Key"
					/>
				</Field>
				<Field label="Location" required={mode === 'create'}>
					<Select
						value={state.location}
						onChange={(e): void =>
							onChange({ location: e.target.value as 'header' | 'query' })
						}
					>
						{KEY_LOCATIONS.map((loc) => (
							<option key={loc} value={loc}>
								{loc === 'header' ? 'Request header' : 'Query parameter'}
							</option>
						))}
					</Select>
				</Field>
			</>
		);
	}

	if (type === CredentialType.BASIC) {
		return (
			<>
				<Field label="Username" required={mode === 'create'} error={errors.username}>
					<Input
						autoComplete="off"
						value={state.username}
						onChange={(e): void => onChange({ username: e.target.value })}
					/>
				</Field>
				<Field
					label="Password"
					required={mode === 'create'}
					hint={secretHint}
					error={errors.password}
				>
					<Input
						type="password"
						showPasswordToggle
						autoComplete="off"
						value={state.password}
						onChange={(e): void => onChange({ password: e.target.value })}
					/>
				</Field>
			</>
		);
	}

	// oauth2
	const options = providerOptions(CredentialType.OAUTH2, providers);
	const managed = isManagedProvider(state.provider);
	const selected = options.find((o) => o.id === state.provider) ?? options[0];
	// Resolve which flow is "active" for the purpose of hiding spec-supplied
	// URL fields. Prefer the explicit `activeFlowId` (the parent's single
	// source of truth when multiple `(scheme, flow)` pairs are available);
	// otherwise fall back to matching by `grantType` for single-flow callers.
	const activeFlow =
		(activeFlowId && flows?.find((f) => f.id === activeFlowId)) ||
		flows?.find((f) => f.grantType === state.grantType) ||
		flows?.[0];
	const tokenUrlFromSpec = activeFlow?.tokenUrl;
	const authorizeUrlFromSpec = activeFlow?.authorizationUrl;
	return (
		<>
			{options.length > 1 && (
				<Field label="Connect via" hint={selected.description}>
					<Select
						value={state.provider}
						onChange={(e): void => onChange({ provider: e.target.value })}
					>
						{options.map((o) => (
							<option key={o.id} value={o.id}>
								{o.label}
							</option>
						))}
					</Select>
				</Field>
			)}

			{managed && (
				<p
					className="border-border bg-muted/40 text-muted-foreground rounded-lg border p-3 text-xs leading-snug"
					role="note"
				>
					Pipedream manages the vendor sign-in. After creating this credential, use{' '}
					<strong>Connect</strong> to open the Pipedream-hosted link — the broker
					configuration (project, client) is set on the server, so the client fields below
					may be placeholders for managed connections.
				</p>
			)}

			{flows && flows.length > 1 && (
				<Field label="Grant type">
					<Select
						value={activeFlow?.id ?? ''}
						onChange={(e): void => {
							const id = e.target.value;
							if (onFlowChange) {
								onFlowChange(id);
								return;
							}
							const flow = flows.find((f) => f.id === id);
							if (flow) onChange({ grantType: flow.grantType });
						}}
					>
						{flows.map((f) => (
							<option key={f.id} value={f.id}>
								{f.label}
							</option>
						))}
					</Select>
				</Field>
			)}

			<Field label="Client ID" required={mode === 'create'} error={errors.clientId}>
				<Input
					value={state.clientId}
					onChange={(e): void => onChange({ clientId: e.target.value })}
				/>
			</Field>
			<Field
				label="Client secret"
				required={mode === 'create'}
				hint={secretHint}
				error={errors.clientSecret}
			>
				<Input
					type="password"
					showPasswordToggle
					autoComplete="off"
					value={state.clientSecret}
					onChange={(e): void => onChange({ clientSecret: e.target.value })}
				/>
			</Field>
			{!tokenUrlFromSpec && (
				<Field label="Token URL" required={mode === 'create'} error={errors.tokenUrl}>
					<Input
						type="url"
						value={state.tokenUrl}
						onChange={(e): void => onChange({ tokenUrl: e.target.value })}
						placeholder="https://provider.com/oauth/token"
					/>
				</Field>
			)}
			{!authorizeUrlFromSpec && (
				<Field
					label="Authorize URL"
					required={mode === 'create' && state.grantType.trim() === 'authorization_code'}
					error={errors.authorizeUrl}
				>
					<Input
						type="url"
						value={state.authorizeUrl}
						onChange={(e): void => onChange({ authorizeUrl: e.target.value })}
						placeholder="https://provider.com/oauth/authorize"
					/>
				</Field>
			)}
			{callbackUrl && (
				<Field
					label="Callback URL"
					hint="Add this URL to your OAuth app's allowed redirect URIs."
				>
					<CopyableInput value={callbackUrl} />
				</Field>
			)}
			{scope ? (
				<ScopePicker
					scopes={scope.available}
					selectedScopes={scope.selected}
					onScopeToggle={scope.onToggle}
					onSelectAll={scope.onSelectAll}
					onDeselectAll={scope.onDeselectAll}
				/>
			) : (
				<Field label="Scopes" hint="Space-separated.">
					<Input
						value={state.scopes}
						onChange={(e): void => onChange({ scopes: e.target.value })}
						placeholder="read write"
					/>
				</Field>
			)}
		</>
	);
}

function CopyableInput({ value, id }: { value: string; id?: string }) {
	return (
		<div className="flex gap-1.5">
			<Input id={id} value={value} readOnly className="flex-1 font-mono text-xs" />
			<CopyButton
				value={value}
				ariaLabel="Copy callback URL"
				toastMessage="Callback URL copied"
			/>
		</div>
	);
}

function Field({
	label,
	required,
	hint,
	error,
	children,
}: {
	label: string;
	required?: boolean;
	hint?: string;
	error?: string;
	children: React.ReactElement<{ id?: string }>;
}) {
	const fieldId = useId();
	// Inject the generated id into the single control child so the <label> can
	// point at it (unless the caller already set an explicit id).
	const control = React.cloneElement(children, {
		id: children.props.id ?? fieldId,
	});
	return (
		<div className="space-y-1.5">
			<Label htmlFor={fieldId} required={required}>
				{label}
			</Label>
			{control}
			{error ? (
				<p className="text-danger text-xs" role="alert">
					{error}
				</p>
			) : (
				hint && <p className="text-muted-foreground text-xs">{hint}</p>
			)}
		</div>
	);
}
