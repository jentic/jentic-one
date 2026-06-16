import { useEffect, useState } from 'react';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Check, ChevronRight, Loader2 } from 'lucide-react';
import { api } from '@/api/client';
import type { ApiOut } from '@/api/types';
import { BackButton } from '@/components/ui/BackButton';
import { PageHeader } from '@/components/ui/PageHeader';
import { PageHelp } from '@/components/ui/PageHelp';
import { LoadingState } from '@/components/ui/LoadingState';
import { PageShell } from '@/components/layout/PageShell';
import {
	ApiPicker,
	CredentialFormFields,
	type CredentialFormPrefill,
} from '@/components/credentials';

/**
 * Route-shell for `/credentials/new` and `/credentials/:id/edit`.
 *
 * After the v3 split, this file owns three responsibilities and
 * nothing else:
 *
 *  1. Parse the URL (path id, `?api_id=…&label=…&value=…&identity=…
 *     &server_vars[…]=…` deeplink contract used by agent guides).
 *  2. Fan out the queries needed to resolve the route's selected
 *     API (the existing credential's API in edit mode, or the
 *     `?api_id=…` deeplink target in new mode).
 *  3. Render the page chrome (`PageShell`, `BackButton`,
 *     `PageHeader` with the contextual help, the picker→fill step
 *     indicator) and compose `<ApiPicker>` and
 *     `<CredentialFormFields>` for the actual body.
 *
 * Why keep this route-as-page at all (we discussed sheet-only):
 *   - Agent docs and external bookmarks still link here. The
 *     `?api_id=…` deeplink contract is published and we don't want
 *     to break it.
 *   - The redirect approach (`/credentials/:id/edit` →
 *     `/credentials?edit=:id`) landed in Phase 2 and is the
 *     preferred edit surface. This route still resolves edit URLs
 *     for backward compatibility (the redirect handler in `App.tsx`
 *     forwards them) but the canonical edit UI is now the inline
 *     `CredentialEditSheet` mounted on the host pages.
 *   - The new add UI is the `AddCredentialDialog` mounted on each
 *     host. It calls `CredentialFormFields` directly (skipping this
 *     route shell) so the dialog can run on top of the page that
 *     opened it. This route stays as the canonical landing page for
 *     deeplinks, and `?api_id=…` remains the published contract.
 *
 * If you find yourself adding form behaviour HERE, you almost
 * certainly want it in `CredentialFormFields` so the sheet/dialog
 * surfaces inherit it.
 */
export default function CredentialFormPage() {
	const { id } = useParams<{ id: string }>();
	const navigate = useNavigate();
	const [searchParams] = useSearchParams();
	const isEdit = !!id;

	// Deeplink contract (used by agent guides, runbooks, MCP tools):
	//   ?api_id=discourse.org
	//   &label=My+Discourse
	//   &identity=seanblanchfield
	//   &server_vars[defaultHost]=techpreneurs.ie
	// `value` is supported but agents rarely populate it — they leave
	// the secret for the user to paste so we don't smuggle plaintext
	// through query strings.
	const paramApiId = searchParams.get('api_id');
	const prefill: CredentialFormPrefill | undefined = paramApiId
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

	const { data: existing } = useQuery({
		queryKey: ['credential', id],
		queryFn: () => api.getCredential(id!),
		enabled: isEdit,
	});

	const { data: existingApi } = useQuery({
		queryKey: ['api', existing?.api_id],
		queryFn: () => api.getApi(existing!.api_id!),
		enabled: isEdit && !!existing?.api_id,
	});

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
			// Orphaned credential row (no api_id). Drop into picker
			// so the user can point it at one. Rare, mostly seen in
			// older fixtures from before api_id became required.
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

	// "Cancel" semantics:
	//   - In picker step (or anywhere we have history), prefer
	//     `navigate(-1)` so a user who deeplinked in from an API
	//     detail page lands back on that detail page.
	//   - For agents that were sent here via direct URL with no
	//     history (an MCP-driven workflow), fall back to
	//     `/credentials`.
	// `window.history.length` is the conventional cue — React
	// Router doesn't expose a precise count.
	const handleCancel = () => {
		if (window.history.length > 1) {
			navigate(-1);
		} else {
			navigate('/credentials');
		}
	};

	return (
		<PageShell width="form">
			<BackButton to="/credentials" label="Back to Credentials" />

			{/*
			 * Title varies by intent:
			 *   - Edit existing → "Edit Credential".
			 *   - New, target API already in workspace → "Add Credential".
			 *   - New, target API is a catalog row not yet imported
			 *       → "Import to Workspace" — the credential POST is
			 *         the server-side trigger for
			 *         `ensure_catalog_api_imported`, so what the user
			 *         experiences is an import.
			 */}
			<PageHeader
				title={
					isEdit
						? 'Edit Credential'
						: selectedApi?.source === 'catalog'
							? 'Import to Workspace'
							: 'Add Credential'
				}
				actions={
					<PageHelp
						title="About the credential form"
						intro={
							<p>
								Credentials are stored encrypted-at-rest. Once saved, the value
								never leaves the broker — it's injected into upstream calls on your
								behalf. This form covers two flows: <strong>manual</strong> (paste a
								bearer / API key) and <strong>OAuth via Pipedream</strong> (redirect
								through Pipedream Connect, no value to paste).
							</p>
						}
						sections={[
							{
								heading: 'Choosing an API',
								body: (
									<p>
										The picker shows both your workspace APIs (
										<strong>Local</strong> badge) and the public catalog (
										<strong>Available</strong> badge). Picking an available API
										imports it into your workspace as a side effect of saving
										the credential — there's no separate import step.
									</p>
								),
							},
							{
								heading: 'Test connection',
								body: (
									<p>
										After saving, use <strong>Test connection</strong> to issue
										a single 5-second probe with the credential injected. We
										prefer an <code>x-jentic-healthcheck</code>-tagged operation
										if the spec defines one; otherwise we fall back to the
										server root.
									</p>
								),
							},
							{
								heading: 'Description',
								body: (
									<p>
										Free-form bookkeeping note for your team — "rotated
										2025-01", "for the digest agent", etc. Never sent upstream;
										surfaced inline on the credentials list.
									</p>
								),
							},
						]}
					/>
				}
			/>

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
					<CredentialFormFields
						selectedApi={selectedApi}
						onBack={
							// In edit or deeplink mode there's no picker to
							// go back to, so Cancel/Change should leave the
							// form (return to caller).
							isEdit || !!paramApiId ? handleCancel : () => setStep('pick')
						}
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
