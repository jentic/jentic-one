import { useParams, useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { PageShell } from '@/components/layout/PageShell';
import { BackButton } from '@/components/ui/BackButton';
import { ToolkitDetailBody } from '@/components/toolkits/ToolkitDetailBody';
import { api } from '@/api/client';

/**
 * Route shell for `/toolkits/:id`.
 *
 * Thin wrapper around the shared `ToolkitDetailBody` — the same component
 * the slide-over `ToolkitDetailSheet` renders. This route is kept so deep
 * links and the browser Back button continue to resolve to a full page;
 * the toolkits list and Workspace open the sheet instead.
 *
 * The `BackButton` + `PageShell` chrome only renders once the toolkit
 * resolves so the not-found state shows a single "Back" affordance (the
 * body owns loading/not-found).
 */
export default function ToolkitDetailPage() {
	const { id } = useParams<{ id: string }>();
	const navigate = useNavigate();

	const { data: toolkit, isLoading } = useQuery({
		queryKey: ['toolkit', id],
		queryFn: () => api.getToolkit(id!),
		enabled: !!id,
		refetchInterval: 30000,
	});

	const close = () => navigate('/toolkits');

	if (isLoading || !toolkit) {
		return <ToolkitDetailBody toolkitId={id!} layout="page" onRequestClose={close} />;
	}

	return (
		<PageShell width="reading">
			<BackButton to="/toolkits" label="Back to Toolkits" />
			<ToolkitDetailBody toolkitId={id!} layout="page" onRequestClose={close} />
		</PageShell>
	);
}
