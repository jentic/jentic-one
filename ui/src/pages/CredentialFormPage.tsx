import React, { useState, useEffect, useRef } from 'react';
import { useParams, useNavigate, useSearchParams } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { AlertTriangle, Search, Check, ChevronRight, Loader2 } from 'lucide-react';
import { api, oauthBrokers } from '@/api/client';
import type { CredentialCreate, CredentialPatch, ApiOut } from '@/api/types';
import { AppLink } from '@/components/ui/AppLink';
import { BackButton } from '@/components/ui/BackButton';
import { PageHeader } from '@/components/ui/PageHeader';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Select } from '@/components/ui/Select';
import { Textarea } from '@/components/ui/Textarea';
import { Label } from '@/components/ui/Label';
import { ErrorAlert } from '@/components/ui/ErrorAlert';
import { emitCredentialImported } from '@/lib/events/credentialImported';
import { LoadingState } from '@/components/ui/LoadingState';
import { PageShell } from '@/components/layout/PageShell';

// ── Helpers ────────────────────────────────────────────────────────────────

function useDebounce<T>(value: T, ms: number): T {
	const [debounced, setDebounced] = useState(value);
	useEffect(() => {
		const t = setTimeout(() => setDebounced(value), ms);
		return () => clearTimeout(t);
	}, [value, ms]);
	return debounced;
}

type SchemeType = 'bearer' | 'basic' | 'apiKey' | 'oauth2' | 'unknown';

type RawSchemes =
	| Record<string, { type?: string; scheme?: string; in?: string; name?: string }>
	| null
	| undefined;

/** Returns true when the scheme map uses the canonical compound pattern: a 'Secret' + 'Identity' apiKey pair. */
function isCompoundApiKey(schemes: RawSchemes): boolean {
	if (!schemes) return false;
	return 'Secret' in schemes && 'Identity' in schemes;
}

/** For compound schemes, derive human-friendly labels from the header names defined in the overlay. */
function compoundLabels(schemes: RawSchemes): { secretLabel: string; identityLabel: string } {
	const secret = schemes?.Secret;
	const identity = schemes?.Identity;
	return {
		secretLabel: secret?.name ?? 'API Key',
		identityLabel: identity?.name ?? 'Username',
	};
}

interface SchemeOption {
	name: string; // key from securitySchemes (e.g. "bearerAuth")
	type: SchemeType;
	label: string; // human label (e.g. "Bearer Token")
}

const SCHEME_TYPE_PRIORITY: SchemeType[] = ['bearer', 'apiKey', 'basic', 'oauth2', 'unknown'];
const SCHEME_TYPE_LABELS: Record<SchemeType, string> = {
	bearer: 'Bearer Token',
	apiKey: 'API Key',
	basic: 'Basic Auth',
	oauth2: 'OAuth 2.0',
	unknown: 'Credential',
};

function schemeTypeFromRaw(s: { type?: string; scheme?: string }): SchemeType {
	if (s.type === 'oauth2') return 'oauth2';
	if (s.type === 'http' && s.scheme?.toLowerCase() === 'bearer') return 'bearer';
	if (s.type === 'http' && s.scheme?.toLowerCase() === 'basic') return 'basic';
	if (s.type === 'apiKey') return 'apiKey';
	return 'unknown';
}

/** Returns all scheme options from a spec, sorted by priority. */
function parseSchemeOptions(schemes: RawSchemes): SchemeOption[] {
	if (!schemes || Object.keys(schemes).length === 0) return [];
	const options: SchemeOption[] = Object.entries(schemes).map(([name, s]) => {
		const type = schemeTypeFromRaw(s);
		return { name, type, label: SCHEME_TYPE_LABELS[type] };
	});
	// Sort by priority, dedup labels (keep first of each type)
	const seen = new Set<SchemeType>();
	return options
		.sort((a, b) => SCHEME_TYPE_PRIORITY.indexOf(a.type) - SCHEME_TYPE_PRIORITY.indexOf(b.type))
		.filter((o) => {
			if (seen.has(o.type)) return false;
			seen.add(o.type);
			return true;
		});
}

function inferSchemeTypeFromSchemes(schemes: RawSchemes): SchemeType {
	const options = parseSchemeOptions(schemes);
	return options[0]?.type ?? 'unknown';
}

function firstSchemeNameFromSchemes(schemes: RawSchemes): string | null {
	if (!schemes) return null;
	return Object.keys(schemes)[0] ?? null;
}

// ── Server variable definitions ───────────────────────────────────────────

export interface ServerVarDef {
	name: string;
	default?: string | null;
	description?: string | null;
	enum?: string[] | null;
	required: boolean;
}

/** Extract server variable definitions from local API detail or catalog spec. */
function useApiServerVarDefs(
	selectedApi: ApiOut | null,
	localDetail: ApiOut | null,
	spec: any,
): ServerVarDef[] {
	// Local API: server_variables comes from backend GET /apis/{id}
	if (localDetail && (localDetail as any).server_variables) {
		const raw = (localDetail as any).server_variables as Record<string, any>;
		return Object.entries(raw).map(([name, def]) => ({
			name,
			default: def?.default ?? null,
			description: def?.description ?? null,
			enum: def?.enum ?? null,
			required: def?.required ?? false,
		}));
	}
	// Catalog API: parse from raw spec servers array
	if (spec?.servers) {
		const server = spec.servers[0];
		if (server?.variables) {
			return Object.entries(server.variables as Record<string, any>).map(([name, def]) => ({
				name,
				default: def?.default ?? null,
				description: def?.description ?? null,
				enum: def?.enum ?? null,
				required: !def?.default, // no default = required
			}));
		}
	}
	return [];
}

/** Fetch security schemes for a selected API.
 *  - local API: use already-fetched detail (has security_schemes)
 *  - catalog API: fetch catalog entry → get spec_url → fetch spec → parse securitySchemes
 *  Returns { schemes, loading }
 */
function useApiSchemes(selectedApi: ApiOut | null): {
	schemes: RawSchemes;
	loading: boolean;
	localDetail: ApiOut | null;
	spec: any;
} {
	const isCatalog = selectedApi?.source === 'catalog';
	const isLocal = selectedApi?.source === 'local' || (!!selectedApi && !selectedApi.source);

	// Local: fetch full API detail
	const { data: localDetail, isLoading: localLoading } = useQuery({
		queryKey: ['api-detail', selectedApi?.id],
		queryFn: () => api.getApi(selectedApi!.id),
		enabled: !!selectedApi && isLocal,
	});

	// Catalog step 1: get catalog entry to find spec_url
	const { data: catalogEntry, isLoading: entryLoading } = useQuery({
		queryKey: ['catalog-entry', selectedApi?.id],
		queryFn: () => api.getCatalogEntry(selectedApi!.id),
		enabled: !!selectedApi && isCatalog,
	});

	const specUrl: string | null = (catalogEntry as any)?.spec_url ?? null;

	// Catalog step 2: fetch the raw spec from GitHub (public, no auth)
	const { data: spec, isLoading: specLoading } = useQuery({
		queryKey: ['spec', specUrl],
		queryFn: async () => {
			const res = await fetch(specUrl!);
			if (!res.ok) throw new Error(`Failed to fetch spec: ${res.status}`);
			return res.json();
		},
		enabled: !!specUrl,
		staleTime: 5 * 60 * 1000, // cache for 5 min
	});

	if (isLocal) {
		const schemes = (localDetail as any)?.security_schemes as RawSchemes;
		return {
			schemes,
			loading: localLoading,
			localDetail: (localDetail as ApiOut) ?? null,
			spec: null,
		};
	}

	if (isCatalog) {
		const schemes = (spec as any)?.components?.securitySchemes as RawSchemes;
		return {
			schemes,
			loading: entryLoading || specLoading,
			localDetail: null,
			spec: spec ?? null,
		};
	}

	return { schemes: null, loading: false, localDetail: null, spec: null };
}

// ── Step 1 — API Picker ────────────────────────────────────────────────────

function ApiPicker({ onSelect }: { onSelect: (api: ApiOut) => void }) {
	const [query, setQuery] = useState('');
	const debouncedQuery = useDebounce(query, 250);
	const inputRef = useRef<HTMLInputElement>(null);

	useEffect(() => {
		inputRef.current?.focus();
	}, []);

	const { data, isLoading } = useQuery({
		queryKey: ['apis-search', debouncedQuery],
		queryFn: () => api.listApis(1, 30, undefined, debouncedQuery),
		enabled: debouncedQuery.length > 0,
		placeholderData: (prev) => prev,
	});

	const items = (data?.items ?? (data as any)?.data ?? []) as ApiOut[];
	const local = items.filter((a: ApiOut) => a.source === 'local');
	const catalog = items.filter((a: ApiOut) => a.source === 'catalog');

	return (
		<div className="space-y-3">
			<div className="relative">
				<Input
					ref={inputRef}
					type="text"
					value={query}
					onChange={(e) => setQuery(e.target.value)}
					placeholder="Search APIs (GitHub, Gmail, Stripe…)"
					aria-label="Search APIs"
					startIcon={<Search className="h-4 w-4" />}
					className="bg-background py-2.5 pr-3"
				/>
				{isLoading && (
					<Loader2 className="text-muted-foreground absolute top-1/2 right-3 h-4 w-4 -translate-y-1/2 animate-spin" />
				)}
			</div>

			{items.length === 0 && !isLoading && debouncedQuery && (
				<p className="text-muted-foreground py-4 text-center text-sm">
					No APIs found for "{debouncedQuery}"
				</p>
			)}

			{local.length > 0 && (
				<div>
					<p className="text-muted-foreground mb-1.5 px-1 font-mono text-[10px] tracking-widest uppercase">
						Available locally
					</p>
					<div className="space-y-1">
						{local.map((a: ApiOut) => (
							<ApiRow key={a.id} api={a} onSelect={onSelect} />
						))}
					</div>
				</div>
			)}

			{catalog.length > 0 && (
				<div>
					<p className="text-muted-foreground mb-1.5 px-1 font-mono text-[10px] tracking-widest uppercase">
						From the Jentic public catalog
					</p>
					<div className="space-y-1">
						{catalog.map((a: ApiOut) => (
							<ApiRow key={a.id} api={a} onSelect={onSelect} />
						))}
					</div>
				</div>
			)}

			{!debouncedQuery && items.length === 0 && !isLoading && (
				<p className="text-muted-foreground py-6 text-center text-sm">
					Start typing to search 10,000+ APIs
				</p>
			)}
		</div>
	);
}

function ApiRow({ api: a, onSelect }: { api: ApiOut; onSelect: (api: ApiOut) => void }) {
	const hasCreds = !!a.has_credentials;
	return (
		<Button
			variant="ghost"
			onClick={() => onSelect(a)}
			className="bg-background hover:bg-muted/60 border-border group flex w-full items-center justify-between gap-3 rounded-lg border px-3 py-2.5 text-left transition-colors"
		>
			<div className="min-w-0">
				<div className="flex items-center gap-2">
					<span className="text-foreground truncate text-sm font-medium">
						{a.name ?? a.id}
					</span>
					{hasCreds && (
						<span className="bg-success/15 text-success border-success/30 shrink-0 rounded border px-1.5 py-0.5 font-mono text-[10px]">
							configured
						</span>
					)}
				</div>
				{a.description && (
					<p className="text-muted-foreground mt-0.5 truncate text-xs">
						{a.description as string}
					</p>
				)}
			</div>
			<ChevronRight className="text-muted-foreground group-hover:text-foreground h-4 w-4 shrink-0 transition-colors" />
		</Button>
	);
}

// ── Step 2 — Credential Fields ─────────────────────────────────────────────

interface CredFieldsProps {
	selectedApi: ApiOut;
	onBack: () => void;
	onSaved: () => void;
	editId?: string;
	existing?: any;
	prefill?: {
		label?: string;
		value?: string;
		identity?: string;
		serverVars?: Record<string, string>;
	};
}

function CredentialFields({
	selectedApi,
	onBack,
	onSaved,
	editId,
	existing,
	prefill,
}: CredFieldsProps) {
	const queryClient = useQueryClient();
	const isEdit = !!editId;

	// Fetch security schemes from spec (local: API detail, catalog: raw spec via GitHub)
	const { schemes, loading: schemesLoading, localDetail, spec } = useApiSchemes(selectedApi);
	const serverVarDefs = useApiServerVarDefs(selectedApi, localDetail, spec);
	const schemeOptions = parseSchemeOptions(schemes);
	const defaultScheme = schemeOptions[0] ?? null;
	const [selectedScheme, setSelectedScheme] = useState<SchemeOption | null>(null);

	// Server variable values keyed by variable name
	const [serverVars, setServerVars] = useState<Record<string, string>>({});

	// Reset scheme selection and fields when API changes
	const resetFields = () => {
		setSelectedScheme(null);
		setLabel(prefill?.label ?? selectedApi.name ?? selectedApi.id);
		setValue(prefill?.value ?? '');
		setIdentity(prefill?.identity ?? '');
		setServerVars(prefill?.serverVars ?? {});
		setError(null);
	};

	useEffect(resetFields, [selectedApi.id]);

	// Pre-populate server vars defaults when defs load — don't overwrite prefilled values
	useEffect(() => {
		if (serverVarDefs.length > 0 && Object.keys(serverVars).length === 0) {
			const defaults: Record<string, string> = {};
			serverVarDefs.forEach((v) => {
				if (v.default) defaults[v.name] = v.default;
			});
			if (Object.keys(defaults).length > 0)
				setServerVars((prev) =>
					Object.keys(prev).length > 0
						? prev
						: { ...defaults, ...(prefill?.serverVars ?? {}) },
				);
		}
	}, [serverVarDefs, prefill?.serverVars]);

	// Prefill from existing credential in edit mode
	useEffect(() => {
		if (existing) {
			setLabel(existing.label ?? '');
			setIdentity(existing.identity ?? '');
			if (existing.server_variables && Object.keys(existing.server_variables).length > 0) {
				setServerVars(existing.server_variables as Record<string, string>);
			}
			setSchemeJson(existing.scheme ? JSON.stringify(existing.scheme, null, 2) : '');
			setRoutesText(existing.routes ? (existing.routes as string[]).join('\n') : '');
			setShowAdvanced(!!(existing.scheme || existing.routes));
			// value is write-only — leave blank
		}
	}, [existing]);

	const activeScheme = selectedScheme ?? defaultScheme;
	const schemeType = activeScheme?.type ?? 'unknown';
	const schemeName = activeScheme?.name ?? firstSchemeNameFromSchemes(schemes);
	const compound = isCompoundApiKey(schemes);
	const { secretLabel, identityLabel } = compoundLabels(schemes);

	const [label, setLabel] = useState(
		prefill?.label ?? existing?.label ?? selectedApi.name ?? selectedApi.id,
	);
	const [value, setValue] = useState(prefill?.value ?? '');
	const [identity, setIdentity] = useState(prefill?.identity ?? existing?.identity ?? '');
	const [error, setError] = useState<string | Error | null>(null);

	// Advanced broker fields
	const [schemeJson, setSchemeJson] = useState(
		existing?.scheme ? JSON.stringify(existing.scheme, null, 2) : '',
	);
	const [routesText, setRoutesText] = useState(
		existing?.routes ? (existing.routes as string[]).join('\n') : '',
	);
	const [showAdvanced, setShowAdvanced] = useState(!!(existing?.scheme || existing?.routes));

	// For OAuth, check if any broker is configured
	const { data: brokers, isLoading: brokersLoading } = useQuery({
		queryKey: ['oauth-brokers'],
		queryFn: () => oauthBrokers.list(),
		staleTime: 60 * 1000,
	});
	const activeBroker = brokers?.[0] ?? null;
	const hasOAuthBroker = !!activeBroker;

	// On-demand connect link generation
	const connectLinkMutation = useMutation({
		mutationFn: () => {
			if (!label.trim()) {
				throw new Error('Label is required for OAuth connections');
			}
			const parts = selectedApi.id.split('/');
			const appSlug = (selectedApi as any).app_slug ?? parts[parts.length - 1];
			return oauthBrokers.connectLink(activeBroker!.id, {
				app: appSlug,
				label: label.trim(),
				api_id: selectedApi.id,
			});
		},
	});

	const createMutation = useMutation({
		mutationFn: (d: CredentialCreate) => api.createCredential(d),
		onSuccess: (created) => {
			queryClient.invalidateQueries({ queryKey: ['credentials'] });
			// P8: tell any open Discover tab that a credential just landed.
			// `created` may carry counts in the future; for now the api_id
			// is enough to trigger the close-the-loop toast.
			emitCredentialImported({
				api_id:
					selectedApi?.id ?? (created as { api_id?: string } | undefined)?.api_id ?? '',
			});
			onSaved();
		},
		onError: (e: Error) => setError(e),
	});

	const updateMutation = useMutation({
		mutationFn: (d: CredentialPatch) => api.updateCredential(editId!, d),
		onSuccess: () => {
			queryClient.invalidateQueries({ queryKey: ['credentials'] });
			onSaved();
		},
		onError: (e: Error) => setError(e),
	});

	const handleSubmit = (e: React.FormEvent) => {
		e.preventDefault();
		if (schemeType === 'oauth2') return;
		setError(null);

		// Validate required server variables
		const missingVars = serverVarDefs.filter((v) => v.required && !serverVars[v.name]?.trim());
		if (missingVars.length > 0) {
			setError(
				`Required server variables missing: ${missingVars.map((v) => v.name).join(', ')}`,
			);
			return;
		}

		const cleanedVars =
			Object.keys(serverVars).length > 0
				? Object.fromEntries(Object.entries(serverVars).filter(([, v]) => v.trim()))
				: null;

		// Derive auth_type from scheme
		const authTypeMap: Record<SchemeType, CredentialCreate['auth_type']> = {
			bearer: 'bearer',
			apiKey: 'apiKey',
			basic: 'basic',
			oauth2: undefined,
			unknown: undefined,
		};

		if (isEdit) {
			// Parse advanced fields
			let parsedScheme: Record<string, unknown> | null = null;
			if (schemeJson.trim()) {
				try {
					parsedScheme = JSON.parse(schemeJson.trim());
				} catch {
					setError('Scheme JSON is invalid');
					return;
				}
			}
			const parsedRoutes = routesText.trim()
				? routesText
						.split('\n')
						.map((r) => r.trim())
						.filter(Boolean)
				: null;

			updateMutation.mutate({
				label: label || null,
				api_id: selectedApi.id,
				auth_type: authTypeMap[schemeType],
				value: value || null,
				identity: identity || null,
				server_variables: cleanedVars,
				scheme: parsedScheme,
				routes: parsedRoutes,
			});
		} else {
			if (!value) {
				setError('Credential value is required');
				return;
			}
			createMutation.mutate({
				label,
				api_id: selectedApi.id,
				auth_type: authTypeMap[schemeType],
				value,
				identity: identity || undefined,
				server_variables: cleanedVars,
			});
		}
	};

	const isPending = createMutation.isPending || updateMutation.isPending;

	if (schemesLoading) {
		return (
			<LoadingState
				message="Reading API spec…"
				icon={<Loader2 className="h-5 w-5 animate-spin" />}
			/>
		);
	}

	return (
		<form onSubmit={handleSubmit} className="space-y-5">
			{/* Selected API summary */}
			<div className="bg-muted/50 border-border flex items-center gap-2 rounded-lg border px-3 py-2.5">
				<div className="min-w-0 flex-1">
					<p className="text-foreground text-sm font-medium">
						{selectedApi.name ?? selectedApi.id}
					</p>
					<p className="text-muted-foreground truncate font-mono text-xs">
						{selectedApi.id}
					</p>
				</div>
				<Button
					variant="ghost"
					size="sm"
					onClick={onBack}
					className="text-muted-foreground hover:text-foreground shrink-0 text-xs transition-colors"
				>
					Change
				</Button>
			</div>

			{/* Scheme picker — only shown when multiple auth types available */}
			{schemeOptions.length > 1 && (
				<fieldset>
					<legend className="text-muted-foreground mb-1.5 block text-xs">
						Auth method
					</legend>
					<div className="flex flex-wrap gap-1.5">
						{schemeOptions.map((opt) => (
							<Button
								key={opt.name}
								variant={activeScheme?.name === opt.name ? 'primary' : 'outline'}
								size="sm"
								onClick={() => setSelectedScheme(opt)}
								className={`rounded-lg px-3 py-1.5 text-xs transition-colors ${
									activeScheme?.name === opt.name
										? 'font-medium'
										: 'bg-background text-muted-foreground border-border hover:text-foreground'
								}`}
							>
								{opt.label}
							</Button>
						))}
					</div>
				</fieldset>
			)}

			{/* Server variables — shown when the API has templated base URLs */}
			{serverVarDefs.length > 0 && (
				<div className="bg-muted/30 border-border space-y-3 rounded-lg border p-4">
					<div>
						<p className="text-foreground text-sm font-medium">Server configuration</p>
						<p className="text-muted-foreground mt-0.5 text-xs">
							This API uses a templated base URL. Fill in the values for your
							instance.
						</p>
					</div>
					{serverVarDefs.map((varDef) => (
						<div key={varDef.name}>
							<Label
								htmlFor={`svar-${varDef.name}`}
								className="text-muted-foreground mb-1 block text-xs"
							>
								<span className="font-mono">{varDef.name}</span>
								{varDef.required && (
									<span className="text-destructive ml-1">*</span>
								)}
							</Label>
							{varDef.description && (
								<p className="text-muted-foreground mb-1 text-xs">
									{varDef.description}
								</p>
							)}
							{varDef.enum && varDef.enum.length > 0 ? (
								<Select
									id={`svar-${varDef.name}`}
									value={serverVars[varDef.name] ?? varDef.default ?? ''}
									onChange={(e) =>
										setServerVars((prev) => ({
											...prev,
											[varDef.name]: e.target.value,
										}))
									}
									className="bg-background border-border text-foreground w-full rounded-md border px-3 py-2 text-sm"
								>
									{varDef.enum.map((opt) => (
										<option key={opt} value={opt}>
											{opt}
										</option>
									))}
								</Select>
							) : (
								<Input
									id={`svar-${varDef.name}`}
									type="text"
									value={serverVars[varDef.name] ?? ''}
									onChange={(e) =>
										setServerVars((prev) => ({
											...prev,
											[varDef.name]: e.target.value,
										}))
									}
									placeholder={varDef.default ?? `Enter ${varDef.name}`}
									className="bg-background font-mono"
								/>
							)}
						</div>
					))}
				</div>
			)}

			{/* Label */}
			<div>
				<Label htmlFor="cred-label" className="text-muted-foreground mb-1 block text-xs">
					Label
				</Label>
				<Input
					id="cred-label"
					type="text"
					value={label}
					onChange={(e) => setLabel(e.target.value)}
					required
					className="bg-background"
				/>
			</div>

			{/* OAuth flow */}
			{schemeType === 'oauth2' &&
				(() => {
					const apiName = selectedApi.name ?? selectedApi.id;
					if (brokersLoading) {
						// Still checking for broker — don't flash the wrong state
						return (
							<div className="text-muted-foreground flex items-center gap-2 text-xs">
								<Loader2 className="h-3 w-3 animate-spin" />
								Checking OAuth configuration…
							</div>
						);
					}
					if (hasOAuthBroker) {
						const connectUrl = connectLinkMutation.data?.connect_link_url;
						return (
							<div className="bg-muted/50 border-border space-y-3 rounded-lg border p-4">
								<p className="text-foreground text-sm font-medium">
									Connect via OAuth
								</p>
								<p className="text-muted-foreground text-xs">
									{apiName} uses OAuth 2.0. Generate a connect link to authorise
									access.
								</p>
								{connectLinkMutation.isError && (
									<p className="text-destructive text-xs">
										Failed to generate connect link. Check your Pipedream broker
										config.
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
								) : (
									<div className="flex items-center gap-2">
										<Button
											variant="primary"
											size="sm"
											onClick={() =>
												window.open(
													connectUrl,
													'_blank',
													'noopener,noreferrer',
												)
											}
										>
											Open Connect Link →
										</Button>
										<Button
											type="button"
											variant="ghost"
											size="sm"
											className="text-muted-foreground text-xs hover:underline"
											onClick={() => connectLinkMutation.reset()}
										>
											new link
										</Button>
									</div>
								)}
							</div>
						);
					}
					// No broker — guide user to set up Pipedream first
					return (
						<div className="bg-muted/50 border-border space-y-3 rounded-lg border p-4">
							<p className="text-foreground text-sm font-medium">OAuth required</p>
							<p className="text-muted-foreground text-xs">
								{apiName} uses OAuth 2.0. Set up Pipedream Connect first:
							</p>
							<ol className="text-muted-foreground list-decimal space-y-1 pl-5 text-xs">
								<li>
									Go to <AppLink href="/credentials">Credentials</AppLink>
								</li>
								<li>
									Click <strong>Enable OAuth via Pipedream</strong> and enter your
									Pipedream client ID, secret, and project ID
								</li>
								<li>Return here to connect {apiName}</li>
							</ol>
						</div>
					);
				})()}

			{/* Basic auth: username + password */}
			{schemeType === 'basic' && (
				<>
					<div>
						<Label
							htmlFor="cred-username"
							className="text-muted-foreground mb-1 block text-xs"
						>
							Username
						</Label>
						<Input
							id="cred-username"
							type="text"
							value={identity}
							onChange={(e) => setIdentity(e.target.value)}
							placeholder="Your username"
							className="bg-background"
						/>
					</div>
					<div>
						<Label
							htmlFor="cred-password"
							className="text-muted-foreground mb-1 block text-xs"
							required={!isEdit}
						>
							Password
						</Label>
						<Input
							id="cred-password"
							type="password"
							value={value}
							onChange={(e) => setValue(e.target.value)}
							required={!isEdit}
							placeholder={isEdit ? 'Leave blank to keep existing' : 'Your password'}
							className="bg-background"
						/>
					</div>
				</>
			)}

			{/* Bearer / apiKey / unknown: single token field — or compound (Secret + Identity) */}
			{(schemeType === 'bearer' || schemeType === 'apiKey' || schemeType === 'unknown') && (
				<>
					{/* Compound apiKey: separate fields for key and username */}
					{compound && (
						<div>
							<Label
								htmlFor="cred-identity"
								className="text-muted-foreground mb-1 block text-xs"
								required
							>
								{identityLabel}
							</Label>
							<Input
								id="cred-identity"
								type="text"
								value={identity}
								onChange={(e) => setIdentity(e.target.value)}
								placeholder={`Your ${identityLabel.toLowerCase()}`}
								required
								className="bg-background"
							/>
						</div>
					)}
					<div>
						<Label
							htmlFor="cred-token"
							className="text-muted-foreground mb-1 block text-xs"
							required={!isEdit}
						>
							{compound
								? secretLabel
								: schemeType === 'bearer'
									? 'Bearer Token'
									: schemeType === 'apiKey'
										? 'API Key'
										: 'Credential Value'}
							{isEdit && (
								<span className="text-muted-foreground/60">
									{' '}
									(leave blank to keep existing)
								</span>
							)}
						</Label>
						<Textarea
							id="cred-token"
							value={value}
							onChange={(e) => setValue(e.target.value)}
							rows={3}
							required={!isEdit}
							placeholder="Paste your token or API key…"
							resizable="none"
							className="bg-background font-mono"
						/>
						<p className="text-muted-foreground mt-1 text-xs">
							<AlertTriangle className="-mt-0.5 inline h-3 w-3" /> Stored encrypted.
							Never shown again after saving.
						</p>
					</div>
				</>
			)}

			{error && <ErrorAlert message={error} />}

			{/* Advanced: scheme / routes (hidden for OAuth2 — Pipedream manages these) */}
			{schemeType !== 'oauth2' && (
				<div className="border-border rounded-lg border">
					<Button
						variant="ghost"
						type="button"
						className="text-muted-foreground hover:text-foreground flex w-full items-center justify-between px-4 py-3 text-xs font-medium transition-colors"
						onClick={() => setShowAdvanced((v) => !v)}
					>
						<span>Advanced broker settings</span>
						<ChevronRight
							className={`h-3.5 w-3.5 transition-transform ${showAdvanced ? 'rotate-90' : ''}`}
						/>
					</Button>
					{showAdvanced && (
						<div className="border-border space-y-4 border-t px-4 pt-3 pb-4">
							<p className="text-muted-foreground text-xs">
								Override how the broker injects this credential. Leave blank to use
								spec-based inference.
							</p>
							<div>
								<Label
									htmlFor="cred-scheme"
									className="text-muted-foreground mb-1 block text-xs"
								>
									Scheme (JSON)
								</Label>
								<Textarea
									id="cred-scheme"
									value={schemeJson}
									onChange={(e) => setSchemeJson(e.target.value)}
									rows={4}
									placeholder='{"in":"header","name":"X-API-KEY"}'
									resizable="vertical"
									className="bg-background font-mono text-xs"
								/>
							</div>
							<div>
								<Label
									htmlFor="cred-routes"
									className="text-muted-foreground mb-1 block text-xs"
								>
									Routes (one per line)
								</Label>
								<Textarea
									id="cred-routes"
									value={routesText}
									onChange={(e) => setRoutesText(e.target.value)}
									rows={3}
									placeholder="10.0.0.2:9443"
									resizable="vertical"
									className="bg-background font-mono text-xs"
								/>
							</div>
						</div>
					)}
				</div>
			)}

			{schemeType !== 'oauth2' && (
				<div className="flex gap-2 pt-2">
					<Button type="submit" loading={isPending} fullWidth>
						{isEdit ? 'Update Credential' : 'Save Credential'}
					</Button>
					<Button type="button" variant="secondary" onClick={onBack}>
						Cancel
					</Button>
				</div>
			)}
		</form>
	);
}

// ── Main page ──────────────────────────────────────────────────────────────

export default function CredentialFormPage() {
	const { id } = useParams<{ id: string }>();
	const navigate = useNavigate();
	const [searchParams] = useSearchParams();
	const isEdit = !!id;

	// Query param deeplink support:
	//   ?api_id=discourse.org
	//   &label=My+Discourse
	//   &identity=seanblanchfield
	//   &server_vars[defaultHost]=techpreneurs.ie
	// The `value` param (the secret) is intentionally supported so the UI renders
	// ready for the user to paste — but the agent can omit it and only prefill
	// the non-sensitive fields.
	const paramApiId = searchParams.get('api_id');
	const prefill = paramApiId
		? {
				label: searchParams.get('label') ?? undefined,
				value: searchParams.get('value') ?? undefined,
				identity: searchParams.get('identity') ?? undefined,
				serverVars: Array.from(searchParams.entries())
					.filter(([k]) => k.startsWith('server_vars[') && k.endsWith(']'))
					.reduce<Record<string, string>>((acc, [k, v]) => {
						acc[k.slice('server_vars['.length, -1)] = v;
						return acc;
					}, {}),
			}
		: undefined;

	const [selectedApi, setSelectedApi] = useState<ApiOut | null>(null);
	const [step, setStep] = useState<'pick' | 'fill'>(isEdit || !!paramApiId ? 'fill' : 'pick');

	// For edit mode, load the existing credential to pre-select its API
	const { data: existing } = useQuery({
		queryKey: ['credential', id],
		queryFn: () => api.getCredential(id!),
		enabled: isEdit,
	});

	// When editing, fetch the API for the existing credential
	const { data: existingApi } = useQuery({
		queryKey: ['api', existing?.api_id],
		queryFn: () => api.getApi(existing!.api_id!),
		enabled: isEdit && !!existing?.api_id,
	});

	// For deeplink mode (?api_id=...), fetch the preselected API
	const { data: paramApi } = useQuery({
		queryKey: ['api', paramApiId],
		queryFn: () => api.getApi(paramApiId!),
		enabled: !isEdit && !!paramApiId,
	});

	useEffect(() => {
		if (existingApi) {
			setSelectedApi(existingApi as ApiOut);
			setStep('fill');
		} else if (isEdit && existing && !existing.api_id) {
			setStep('pick');
		}
	}, [existingApi, existing, isEdit]);

	useEffect(() => {
		if (paramApi) {
			setSelectedApi(paramApi as ApiOut);
			setStep('fill');
		}
	}, [paramApi]);

	const handleApiSelect = (a: ApiOut) => {
		setSelectedApi(a);
		setStep('fill');
	};

	return (
		<PageShell width="form">
			<BackButton to="/credentials" label="Back to Credentials" />

			{/*
			 * Title varies by intent:
			 *   - Edit existing credential → "Edit Credential".
			 *   - New credential, target API is in workspace already
			 *       → "Add Credential" (the credential is the only new thing).
			 *   - New credential, target API is a catalog row not yet imported
			 *       → "Import to Workspace" — the credential POST is the
			 *         server-side trigger for `ensure_catalog_api_imported`,
			 *         so what the user *experiences* is an import. Calling it
			 *         "Add credential" before the API is local was confusing
			 *         (May 2026 review).
			 *
			 * `selectedApi.source === 'catalog'` is the only durable signal
			 * the form has for "this is a directory row" — it's set both in
			 * the deeplink path (`?api_id=...`) and in the picker step.
			 */}
			<PageHeader
				title={
					isEdit
						? 'Edit Credential'
						: selectedApi?.source === 'catalog'
							? 'Import to Workspace'
							: 'Add Credential'
				}
			/>

			{/* Step indicator */}
			{!isEdit && (
				<div className="text-muted-foreground flex items-center gap-2 text-xs">
					<span
						className={`flex items-center gap-1 ${step === 'pick' ? 'text-foreground font-medium' : 'text-success'}`}
					>
						{step === 'fill' ? (
							<Check className="h-3 w-3" />
						) : (
							<span className="flex h-4 w-4 items-center justify-center rounded-full border border-current text-[10px]">
								1
							</span>
						)}
						Choose API
					</span>
					<ChevronRight className="h-3 w-3" />
					<span
						className={`flex items-center gap-1 ${step === 'fill' ? 'text-foreground font-medium' : ''}`}
					>
						<span className="flex h-4 w-4 items-center justify-center rounded-full border border-current text-[10px]">
							2
						</span>
						Enter credentials
					</span>
				</div>
			)}

			<div className="bg-muted border-border rounded-xl border p-6">
				{step === 'pick' && <ApiPicker onSelect={handleApiSelect} />}
				{step === 'fill' && selectedApi && (
					<CredentialFields
						selectedApi={selectedApi}
						onBack={() => setStep('pick')}
						onSaved={() => navigate('/credentials')}
						editId={id}
						existing={existing}
						prefill={prefill}
					/>
				)}
				{step === 'fill' && !selectedApi && (isEdit || !!paramApiId) && (
					<LoadingState
						message={paramApiId ? 'Loading API…' : 'Loading credential…'}
						icon={<Loader2 className="h-5 w-5 animate-spin" />}
					/>
				)}
			</div>
		</PageShell>
	);
}
