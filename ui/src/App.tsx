import {
	createBrowserRouter,
	RouterProvider,
	Navigate,
	useLocation,
	useParams,
} from 'react-router-dom';
import { MotionConfig } from 'framer-motion';
import { Layout } from '@/components/layout/Layout';
import { AuthGuard } from '@/components/AuthGuard';
import SetupPage from '@/pages/SetupPage';
import LoginPage from '@/pages/LoginPage';
import DashboardPage from '@/pages/DashboardPage';
import DiscoverPage from '@/pages/DiscoverPage';
import WorkspacePage from '@/pages/WorkspacePage';
import ToolkitsPage from '@/pages/ToolkitsPage';
import ToolkitDetailPage from '@/pages/ToolkitDetailPage';
import CredentialsPage from '@/pages/CredentialsPage';
import CredentialFormPage from '@/pages/CredentialFormPage';
import WorkflowDetailPage from '@/pages/WorkflowDetailPage';
import ApiDetailPage from '@/pages/ApiDetailPage';
import TracesPage from '@/pages/TracesPage';
import JobsPage from '@/pages/JobsPage';
import JobDetailPage from '@/pages/JobDetailPage';
import TraceDetailPage from '@/pages/TraceDetailPage';
import ApprovalPage from '@/pages/ApprovalPage';
import AgentsPage from '@/pages/AgentsPage';

// Read the basename from the backend-injected <base href> so the SPA bundle
// stays prefix-agnostic — works at "/" or any "/foo" mount the operator
// configures via JENTIC_ROOT_PATH / X-Forwarded-Prefix.
const basename = new URL(document.baseURI).pathname.replace(/\/$/, '') || undefined;

/**
 * Redirect /search → /discover, preserving the query string so that
 * bookmarks like /search?q=stripe keep working. /catalog → /discover
 * uses the same component so legacy bookmarks (/catalog?q=stripe&inspect=…)
 * land on the new Discover surface with all params intact.
 *
 * Exported so unit tests can mount it inside a `MemoryRouter` without
 * spinning up the whole `createBrowserRouter` tree.
 */
export function DiscoverRedirect() {
	const { search } = useLocation();
	return <Navigate to={`/discover${search}`} replace />;
}

/**
 * Redirect /workflows → /workspace, preserving the query string. The
 * dedicated Workflows list page was retired when the IA collapsed
 * "what's mine" into a single Workspace surface — your own workflows
 * now live alongside your APIs there, and the catalog of workflows
 * lives in Discover. The detail route `/workflows/:slug` is matched
 * before this redirect (router specificity) and stays untouched.
 */
export function WorkflowsRedirect() {
	const { search } = useLocation();
	return <Navigate to={`/workspace${search}`} replace />;
}

/**
 * Redirect legacy `/workflows/:slug` deep links to the canonical
 * `/workspace/workflows/:slug`. The IA contract is "Workspace owns
 * workflows", so the URL hierarchy now mirrors the breadcrumb. We
 * preserve the slug and the full query string so the
 * `?view=diagram|docs|split` deep-link parameter survives the bounce.
 */
export function WorkflowDetailRedirect() {
	const { search } = useLocation();
	const params = useParams<{ slug: string }>();
	return <Navigate to={`/workspace/workflows/${params.slug}${search}`} replace />;
}

const router = createBrowserRouter(
	[
		{
			element: <AuthGuard />,
			children: [
				{ path: '/setup', element: <SetupPage /> },
				{ path: '/login', element: <LoginPage /> },
				// Approval page has minimal chrome — outside Layout
				{ path: '/approve/:toolkit_id/:req_id', element: <ApprovalPage /> },
				{
					element: <Layout />,
					children: [
						{ path: '/', element: <DashboardPage /> },
						// Both legacy paths (/search, /catalog) redirect into the
						// Discover surface preserving the query string so bookmarks
						// keep working: /search?q=stripe and /catalog?q=stripe both
						// land on /discover?q=stripe with all params intact.
						{ path: '/search', element: <DiscoverRedirect /> },
						{ path: '/catalog', element: <DiscoverRedirect /> },
						{ path: '/discover', element: <DiscoverPage /> },
						{ path: '/workspace', element: <WorkspacePage /> },
						{ path: '/workspace/apis/:apiId', element: <ApiDetailPage /> },
						{ path: '/workspace/workflows/:slug', element: <WorkflowDetailPage /> },
						{ path: '/workflows', element: <WorkflowsRedirect /> },
						{ path: '/workflows/:slug', element: <WorkflowDetailRedirect /> },
						{ path: '/toolkits', element: <ToolkitsPage /> },
						{ path: '/toolkits/new', element: <ToolkitsPage createNew /> },
						{ path: '/toolkits/:id', element: <ToolkitDetailPage /> },
						{ path: '/agents', element: <AgentsPage /> },
						{ path: '/credentials', element: <CredentialsPage /> },
						{ path: '/credentials/new', element: <CredentialFormPage /> },
						{ path: '/credentials/:id/edit', element: <CredentialFormPage /> },
						{ path: '/oauth-brokers', element: <Navigate to="/credentials" replace /> },
						{ path: '/traces', element: <TracesPage /> },
						{ path: '/traces/:id', element: <TraceDetailPage /> },
						{ path: '/jobs', element: <JobsPage /> },
						{ path: '/jobs/:id', element: <JobDetailPage /> },
					],
				},
			],
		},
	],
	{ basename },
);

export default function App() {
	// `reducedMotion="user"` makes every framer-motion animation respect the
	// browser's `prefers-reduced-motion` media query: motion-sensitive users
	// see static UI, and Playwright (browser-mode tests) — which we configure
	// with `reducedMotion: 'reduce'` — skips entrance animations so axe doesn't
	// observe mid-animation opacity values.
	return (
		<MotionConfig reducedMotion="user">
			<RouterProvider router={router} />
		</MotionConfig>
	);
}
