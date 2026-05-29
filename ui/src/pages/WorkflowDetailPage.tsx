import { useEffect, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useQuery, useQueryClient, useMutation } from '@tanstack/react-query';
import { AlertTriangle, Trash2, Workflow } from 'lucide-react';
import { api } from '@/api/client';
import { Button } from '@/components/ui/Button';
import { BackButton } from '@/components/ui/BackButton';
import { ConfirmDeleteDialog } from '@/components/ui/ConfirmDeleteDialog';
import { KeyboardShortcutsBar, MOD_KEY } from '@/components/ui/KeyboardShortcutsBar';
import { LoadingState } from '@/components/ui/LoadingState';
import { PageHeader } from '@/components/ui/PageHeader';
import { PageHelp } from '@/components/ui/PageHelp';
import { PageShell } from '@/components/layout/PageShell';
import {
	CatalogWorkflowFallback,
	WorkflowDetailView,
} from '@/components/workflows/workflow-detail';
import { useScrollRestore } from '@/hooks/useScrollRestore';
import { isTypingTarget } from '@/lib/keyboard';

const HELP = (
	<PageHelp
		title="About this workflow"
		intro={
			<p>
				An Arazzo workflow is a sequence of API calls choreographed across one or more APIs
				— think "register a customer, charge their card, email a receipt" as a single
				declarative document. Each step calls a real operation on a real API, so the
				workflow is fully executable from this page.
			</p>
		}
		sections={[
			{
				heading: 'Overview',
				body: (
					<p>
						Skim the workflow at a glance: description, the APIs it touches, and an
						ordered list of its steps. Click an API chip to inspect that API directly in
						Discover.
					</p>
				),
			},
			{
				heading: 'Diagram / Docs / Split',
				body: (
					<p>
						The same Arazzo document, three ways. <strong>Diagram</strong> is the visual
						flow, <strong>Docs</strong> is the human-readable spec, and{' '}
						<strong>Split</strong> shows both side-by-side for deep-dive reviews. Your
						choice is preserved in the URL via{' '}
						<code className="text-foreground">?view=&hellip;</code>.
					</p>
				),
			},
			{
				heading: 'Catalog vs Local',
				body: (
					<p>
						Workflows from the Jentic public catalog can be browsed without importing.
						Click <strong>Import</strong> on a catalog workflow to bring it into your
						workspace — the steps then become executable using your credentials.
					</p>
				),
			},
		]}
		links={[
			{ href: 'https://www.openapis.org/arazzo-specification', label: 'What is Arazzo?' },
			{
				href: 'https://github.com/jentic/jentic-public-apis/tree/main/workflows',
				label: 'Browse the catalog on GitHub',
			},
		]}
		shortcuts={[
			{ keys: ['Esc'], label: 'Go back to workspace' },
			{ keys: [MOD_KEY, '/'], chord: true, label: 'Show this help' },
		]}
	/>
);

export default function WorkflowDetailPage() {
	const { slug } = useParams<{ slug: string }>();
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

	const deleteMutation = useMutation({
		mutationFn: () => api.deleteWorkflow(slug!),
		onSuccess: async () => {
			await Promise.all([
				queryClient.invalidateQueries({ queryKey: ['workflows'] }),
				queryClient.invalidateQueries({ queryKey: ['workspace'] }),
				queryClient.invalidateQueries({ queryKey: ['workspace-stats'] }),
				queryClient.invalidateQueries({ queryKey: ['apis'] }),
			]);
			navigate('/workspace');
		},
		// On error: keep the dialog open — its loading state resets
		// automatically and the user can retry.
		onError: () => {},
	});

	const {
		data: workflow,
		isLoading,
		error,
	} = useQuery({
		queryKey: ['workflow', slug],
		queryFn: () => api.getWorkflow(slug!),
		enabled: !!slug,
		retry: (failureCount, err: any) => err?.status !== 404 && failureCount < 2,
	});

	if (isLoading)
		return (
			<PageShell>
				<LoadingState message="Loading workflow..." />
			</PageShell>
		);

	const is404 = (error as any)?.status === 404;
	if (error && !is404) {
		return (
			<PageShell>
				<PageHeader title="Workflow" />
				<BackButton to="/workspace" label="Back" />
				<div className="py-16 text-center">
					<AlertTriangle className="text-danger mx-auto mb-3 h-8 w-8" />
					<p className="text-foreground text-sm font-medium">Failed to load workflow</p>
					<p className="text-muted-foreground mt-1 text-xs">
						{(error as any)?.message || 'Unknown error'}
					</p>
				</div>
			</PageShell>
		);
	}

	// 404 (workflow not in our DB) and "no payload yet" both fall
	// through to the catalog fallback — the user might be looking at
	// a public catalog workflow they haven't imported yet.
	if (!workflow)
		return <CatalogWorkflowFallback slug={slug!} navigate={navigate} helpSlot={HELP} />;

	const resolvedTitle = workflow.name ?? workflow.slug;
	const showDescription =
		workflow.description &&
		workflow.description !== workflow.name &&
		workflow.description !== workflow.slug;

	return (
		<>
			<PageShell width="wide">
				<PageHeader
					title={resolvedTitle}
					subtitle={showDescription ? workflow.description : undefined}
					icon={
						<div className="bg-accent-teal/10 flex h-10 w-10 items-center justify-center rounded-lg">
							<Workflow className="text-accent-teal h-5 w-5" />
						</div>
					}
					actions={
						<div className="flex items-center gap-2">
							<Button variant="danger" size="sm" onClick={() => setDeleteOpen(true)}>
								<Trash2 className="h-3.5 w-3.5" />
							</Button>
							{HELP}
						</div>
					}
				/>

				<BackButton to="/workspace" label="Back" />

				<WorkflowDetailView slug={slug!} workflow={workflow} />

				<ConfirmDeleteDialog
					target={slug ? { kind: 'workflow', slug, name: workflow.name ?? slug } : null}
					open={deleteOpen}
					onClose={() => setDeleteOpen(false)}
					onConfirm={() => deleteMutation.mutate()}
					loading={deleteMutation.isPending}
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
