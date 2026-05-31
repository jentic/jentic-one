import { useEffect, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Download, Key, ExternalLink, Trash2 } from 'lucide-react';
import { PageShell } from '@/components/layout/PageShell';
import { PageHeader } from '@/components/ui/PageHeader';
import { PageHelp } from '@/components/ui/PageHelp';
import { BackButton } from '@/components/ui/BackButton';
import { AppLink } from '@/components/ui/AppLink';
import { Button } from '@/components/ui/Button';
import { ConfirmDeleteDialog } from '@/components/ui/ConfirmDeleteDialog';
import { KeyboardShortcutsBar, MOD_KEY } from '@/components/ui/KeyboardShortcutsBar';
import { ApiDetailView } from '@/components/workspace/api-detail';
import { VendorIcon } from '@/components/discovery/VendorIcon';
import { api, apiUrl } from '@/api/client';
import { isTypingTarget } from '@/lib/keyboard';
import { useScrollRestore } from '@/hooks/useScrollRestore';

export default function ApiDetailPage() {
	const { apiId } = useParams<{ apiId: string }>();
	const navigate = useNavigate();
	const queryClient = useQueryClient();
	useScrollRestore();

	const [deleteOpen, setDeleteOpen] = useState(false);

	useEffect(() => {
		function onKeyDown(e: KeyboardEvent) {
			if (e.metaKey || e.ctrlKey || e.altKey) return;
			if (isTypingTarget(e.target)) return;
			if (e.key === 'Escape' && !deleteOpen && !document.querySelector('dialog[open]')) {
				e.preventDefault();
				navigate('/workspace');
			}
		}
		document.addEventListener('keydown', onKeyDown);
		return () => document.removeEventListener('keydown', onKeyDown);
	}, [navigate, deleteOpen]);

	const {
		data: apiData,
		isLoading,
		error,
	} = useQuery({
		queryKey: ['api', apiId],
		queryFn: () => api.getApi(apiId!),
		enabled: !!apiId,
	});

	const removeMutation = useMutation({
		mutationFn: (opts: { cascade: boolean }) => api.deleteApi(apiId!, opts),
		onSuccess: async () => {
			await Promise.all([
				queryClient.invalidateQueries({ queryKey: ['apis'] }),
				queryClient.invalidateQueries({ queryKey: ['workflows'] }),
				queryClient.invalidateQueries({ queryKey: ['workspace'] }),
				queryClient.invalidateQueries({ queryKey: ['workspace-stats'] }),
				queryClient.invalidateQueries({ queryKey: ['credentials'] }),
				queryClient.invalidateQueries({ queryKey: ['toolkits'] }),
			]);
			navigate('/workspace');
		},
		onError: () => {
			// Keep dialog open — the loading state resets automatically
		},
	});

	const title = apiData?.name || apiData?.info?.title || apiId || 'API';
	const version = apiData?.info?.version;
	const subtitle = version ? `v${version}` : undefined;

	const docsUrl = apiData?.info?.['x-jentic-source-url'] as string | undefined;

	const actions = apiId ? (
		<div className="flex items-center gap-2">
			{docsUrl && (
				<AppLink
					href={docsUrl}
					external
					className="border-border bg-background hover:bg-muted inline-flex h-8 items-center gap-1.5 rounded-lg border px-3 text-xs font-medium transition-colors"
				>
					<ExternalLink className="h-3.5 w-3.5" /> Docs
				</AppLink>
			)}
			<AppLink
				href={`/credentials/new?api_id=${encodeURIComponent(apiId)}`}
				className="border-border bg-background hover:bg-muted inline-flex h-8 items-center gap-1.5 rounded-lg border px-3 text-xs font-medium transition-colors"
			>
				<Key className="h-3.5 w-3.5" /> Add credential
			</AppLink>
			<AppLink
				href={apiUrl(`/apis/${apiId}/openapi.json`)}
				external
				className="border-border bg-background hover:bg-muted inline-flex h-8 items-center gap-1.5 rounded-lg border px-3 text-xs font-medium transition-colors"
			>
				<Download className="h-3.5 w-3.5" /> Spec
			</AppLink>
			<Button variant="danger" size="sm" onClick={() => setDeleteOpen(true)}>
				<Trash2 className="h-3.5 w-3.5" />
			</Button>
			<PageHelp
				title="About this API"
				intro={
					<p>
						This page shows everything about a single API in your workspace — its
						credentials, toolkit bindings, operations, and linked workflows.
					</p>
				}
				sections={[
					{
						heading: 'Credentials & Toolkits',
						body: (
							<p>
								See which credentials are set up, which toolkits bind them, and add
								new credentials directly from the header.
							</p>
						),
					},
					{
						heading: 'Operations',
						body: (
							<p>
								Browse the API's endpoints. Each shows method, path, and a short
								summary extracted from the OpenAPI spec.
							</p>
						),
					},
					{
						heading: 'Removing the API',
						body: (
							<p>
								The trash icon opens a confirmation dialog that explains what will
								be affected — workflows, credentials, and toolkit bindings. You can
								choose a soft or cascade delete.
							</p>
						),
					},
				]}
				shortcuts={[
					{ keys: ['Esc'], label: 'Go back to workspace' },
					{ keys: [MOD_KEY, '/'], chord: true, label: 'Show this help' },
				]}
			/>
		</div>
	) : undefined;

	if (isLoading) {
		return (
			<PageShell width="wide">
				<div className="flex flex-col items-center justify-center py-20">
					<p className="text-muted-foreground animate-pulse text-sm">Loading API…</p>
				</div>
			</PageShell>
		);
	}

	if (error || (!isLoading && !apiData && apiId)) {
		return (
			<PageShell width="wide">
				<div className="flex flex-col items-center justify-center py-20">
					<p className="text-muted-foreground text-sm">
						API not found or failed to load.
					</p>
					<BackButton to="/workspace" label="Back" className="mt-4" />
				</div>
			</PageShell>
		);
	}

	return (
		<>
			<PageShell width="wide">
				<PageHeader
					title={title}
					subtitle={subtitle}
					icon={<VendorIcon name={title} vendor={apiId} size="lg" />}
					actions={actions}
				/>

				<BackButton to="/workspace" label="Back" />

				{apiId ? <ApiDetailView apiId={apiId} /> : null}

				<ConfirmDeleteDialog
					target={apiId ? { kind: 'api', id: apiId, name: title } : null}
					open={deleteOpen}
					onClose={() => setDeleteOpen(false)}
					onConfirm={({ cascade }) => removeMutation.mutate({ cascade })}
					loading={removeMutation.isPending}
				/>
			</PageShell>

			<KeyboardShortcutsBar
				shortcuts={[
					{ keys: ['Esc'], label: 'back' },
					{ keys: [MOD_KEY, '/'], chord: true, label: 'help' },
				]}
			/>
		</>
	);
}
