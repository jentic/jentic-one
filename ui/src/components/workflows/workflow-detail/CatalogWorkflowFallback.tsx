import { useState } from 'react';
import type { ReactNode } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { ExternalLink, Workflow, Zap } from 'lucide-react';
import { apiUrl } from '@/api/client';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { BackButton } from '@/components/ui/BackButton';
import { AppLink } from '@/components/ui/AppLink';
import { PageHeader } from '@/components/ui/PageHeader';
import { PageShell } from '@/components/layout/PageShell';

interface CatalogWorkflowFallbackProps {
	slug: string;
	navigate: (path: string) => void;
	/**
	 * Optional `<PageHelp>` (or any other) trailing actions slot from
	 * the page so the fallback header matches the imported view.
	 */
	helpSlot?: ReactNode;
}

/**
 * Shown when the user lands on `/workspace/workflows/:slug` for a
 * workflow that lives in the public Jentic catalog but hasn't been
 * imported into the workspace yet. Provides:
 *
 *   - A summary of "this is from the catalog"
 *   - A one-click import that resolves the catalog spec URL, kicks
 *     off the regular `/import` flow, and navigates to the workspace
 *     once the workflow is available locally
 *   - An "Open in Arazzo UI" escape hatch so the user can still
 *     visualise the workflow without importing
 *   - A link to the GitHub source for power users
 */
export function CatalogWorkflowFallback({
	slug,
	navigate,
	helpSlot,
}: CatalogWorkflowFallbackProps) {
	const queryClient = useQueryClient();
	const [importing, setImporting] = useState(false);
	const [error, setError] = useState<string | null>(null);

	// Catalog slugs use `~` as a separator because `/` would clash
	// with route segments. Reverse the substitution before talking to
	// the catalog endpoint.
	const apiId = slug.replace('~', '/');
	const githubUrl = `https://github.com/jentic/jentic-public-apis/tree/main/workflows/${slug}`;
	const encodedSlug = encodeURIComponent(slug);
	const rawArazzoUrl = `https://raw.githubusercontent.com/jentic/jentic-public-apis/refs/heads/main/workflows/${encodedSlug}/workflows.arazzo.json`;
	const arazzoUIUrl = `https://arazzo-ui.jentic.com?document=${encodeURIComponent(rawArazzoUrl)}`;

	const handleImport = async () => {
		setImporting(true);
		setError(null);
		try {
			const catalogRes = await fetch(apiUrl(`/catalog/${apiId}`), { credentials: 'include' });
			if (!catalogRes.ok) {
				const body = await catalogRes.json().catch(() => ({}));
				throw new Error(body.detail || `Catalog lookup failed (${catalogRes.status})`);
			}
			const catalogEntry = await catalogRes.json();
			if (!catalogEntry.spec_url) {
				throw new Error('No spec URL found for this API in the catalog');
			}
			const importRes = await fetch(apiUrl('/import'), {
				method: 'POST',
				credentials: 'include',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({
					sources: [{ type: 'url', url: catalogEntry.spec_url, force_api_id: apiId }],
				}),
			});
			if (!importRes.ok) {
				const body = await importRes.json().catch(() => ({}));
				throw new Error(body.detail || `Import failed (${importRes.status})`);
			}
			const importResult = await importRes.json();
			if (importResult.failed > 0) {
				const err = importResult.results?.[0]?.error || 'Unknown error';
				throw new Error(`Import failed: ${err}`);
			}
			queryClient.invalidateQueries({ queryKey: ['workflows'] });
			navigate('/workspace');
		} catch (e: any) {
			setError(e.message);
		} finally {
			setImporting(false);
		}
	};

	const headerActions = (
		<>
			<Button
				variant="primary"
				size="sm"
				onClick={handleImport}
				loading={importing}
				data-testid="workflow-catalog-import"
			>
				<Zap size={14} aria-hidden="true" />
				{importing ? 'Importing…' : 'Import this workflow'}
			</Button>
			<AppLink
				href={arazzoUIUrl}
				className="border-border text-muted-foreground hover:text-foreground hover:bg-muted/40 inline-flex h-8 items-center gap-1.5 rounded-md border px-2.5 text-xs font-medium transition-colors"
			>
				<ExternalLink size={12} aria-hidden="true" /> Open in Arazzo UI
			</AppLink>
			{helpSlot}
		</>
	);

	return (
		<PageShell width="wide">
			<PageHeader
				title={apiId}
				subtitle="From the Jentic public catalog. Import to inspect and execute."
				actions={headerActions}
			/>

			<BackButton to="/workspace" label="Back" />

			<div className="flex items-center gap-3">
				<Badge
					variant="default"
					className="bg-accent-yellow/10 text-accent-yellow border-accent-yellow/20"
				>
					<Workflow size={11} aria-hidden="true" /> Catalog workflow
				</Badge>
				<p className="text-muted-foreground font-mono text-[11px]">{slug}</p>
			</div>

			{error ? (
				<div
					className="border-danger/30 bg-danger/10 text-danger rounded-lg border p-3 text-xs"
					data-testid="workflow-catalog-import-error"
				>
					{error}
				</div>
			) : null}

			<section className="border-border/60 bg-card grid gap-3 rounded-xl border p-5 text-sm">
				<p className="text-muted-foreground">
					This workflow lives in the Jentic public catalog. Import it to bring its steps
					into your workspace; once imported you'll see the diagram, docs, and run
					controls here.
				</p>
				<ul className="text-muted-foreground space-y-2 text-xs">
					<li>
						<AppLink
							href={githubUrl}
							className="text-primary hover:text-primary/80 inline-flex items-center gap-1.5"
						>
							<ExternalLink size={12} aria-hidden="true" /> View source on GitHub
						</AppLink>
					</li>
				</ul>
			</section>
		</PageShell>
	);
}
