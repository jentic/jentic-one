import { test, expect, type Page } from '@playwright/test';

// Hits the prefix container started by the webServer block in
// playwright.docker.config.ts. Uses absolute URLs so the spec is unaffected
// by the default baseURL (which points at the unprefixed main container).
const PREFIX_BASE = 'http://localhost:8901/foo';
const ADMIN_USER = 'admin';
const ADMIN_PASS = 'admin123';

// Log in via the API endpoints rather than the UI so the new tests don't
// depend on the login form rendering within a tight timeout. The session
// cookie is stored in the browser context by Playwright and is available
// to subsequent page.goto() navigations.
async function loginViaApi(page: Page) {
	const healthRes = await page.request.get(`${PREFIX_BASE}/health`);
	const { status } = await healthRes.json();
	const endpoint = status === 'setup_required' ? '/user/create' : '/user/login';
	const res = await page.request.post(`${PREFIX_BASE}${endpoint}`, {
		data: { username: ADMIN_USER, password: ADMIN_PASS },
	});
	expect(res.ok(), `${endpoint} failed: ${res.status()}`).toBeTruthy();
}

test.describe('Reverse-proxy prefix mount', () => {
	test('serves the SPA shell with a prefixed <base href>', async ({ request }) => {
		const res = await request.get(`${PREFIX_BASE}/`, {
			headers: { Accept: 'text/html' },
		});
		expect(res.ok()).toBeTruthy();
		const body = await res.text();
		expect(body).toContain('<base href="/foo/"');
	});

	test('no failed XHR during initial SPA render at the prefix', async ({ page }) => {
		// Regression guard: SPA fetches must include the mount prefix.
		// Intentionally NOT filtered to PREFIX_BASE — the bug is the SPA issuing
		// /health instead of /foo/health, so those URLs would NOT start with
		// PREFIX_BASE and would be silently dropped. Same-origin filter only.
		const origin = new URL(PREFIX_BASE).origin;
		const failures: { url: string; status: number }[] = [];
		page.on('response', (resp) => {
			const url = resp.url();
			const status = resp.status();
			if (!url.startsWith(origin)) return;
			if (status < 400) return;
			const req = resp.request();
			if (!['fetch', 'xhr'].includes(req.resourceType())) return;
			// /user/me intentionally 401s when logged out (used as a probe).
			if (status === 401 && url.endsWith('/user/me')) return;
			failures.push({ url, status });
		});

		await page.goto(`${PREFIX_BASE}/`);
		// Wait for any setup/login UI to settle — that's the end of the
		// initial render's XHR storm.
		await page.waitForLoadState('networkidle');

		expect(failures, `unexpected failed XHRs: ${JSON.stringify(failures)}`).toEqual([]);
	});

	test('navigates to credentials and survives a reload', async ({ page }) => {
		// 1. Bootstrap auth state — fresh container needs admin creation;
		//    a reused container needs login. Both paths leave us logged in.
		await page.goto(`${PREFIX_BASE}/`);

		const setupVisible = await page
			.getByText(/create admin account/i)
			.isVisible({ timeout: 5_000 })
			.catch(() => false);

		if (setupVisible) {
			await page.getByLabel('Username').fill(ADMIN_USER);
			await page.getByRole('textbox', { name: 'Password' }).fill(ADMIN_PASS);
			await page.getByRole('button', { name: /create account/i }).click();
			await expect(page.getByText(/setup complete/i)).toBeVisible({ timeout: 30_000 });
			// Continue from setup wizard's completion state to the dashboard.
			await page.goto(`${PREFIX_BASE}/`);
		} else {
			const loginVisible = await page
				.getByRole('button', { name: /^log in$/i })
				.isVisible({ timeout: 5_000 })
				.catch(() => false);
			if (loginVisible) {
				await page.getByLabel('Username').fill(ADMIN_USER);
				await page.getByRole('textbox', { name: 'Password' }).fill(ADMIN_PASS);
				await page.getByRole('button', { name: /^log in$/i }).click();
			}
		}

		// 2. Click the Credentials nav link — proves React Router's basename
		//    is reading the backend-injected <base href>.
		await page
			.getByRole('link', { name: /credentials/i })
			.first()
			.click();
		await expect(page).toHaveURL(`${PREFIX_BASE}/credentials`);
		// Exact match on the page title — the credentials list renders section
		// headings like "Credentials 1" that also match /credentials/i, which
		// trips Playwright strict mode.
		await expect(page.getByRole('heading', { name: 'Credentials', exact: true })).toBeVisible({
			timeout: 15_000,
		});

		// 3. Cold-boot deep-link path — reloading must keep the URL and re-render.
		await page.reload();
		await expect(page).toHaveURL(`${PREFIX_BASE}/credentials`);
		await expect(page.getByRole('heading', { name: 'Credentials', exact: true })).toBeVisible({
			timeout: 15_000,
		});
	});

	test('dashboard "Review" link has a single /foo prefix', async ({ page }) => {
		await loginViaApi(page);
		// Navigate to the dashboard so the browser context has the session cookie
		// in a same-site page load before we make additional API calls.
		await page.goto(`${PREFIX_BASE}/`);
		await page.getByRole('heading', { name: /dashboard/i }).waitFor({ timeout: 15_000 });

		// Create a toolkit and a pending access request via fetch() running inside
		// the browser page — guarantees the SameSite=Strict session cookie is sent.
		const { toolkitId, reqId } = await page.evaluate(async (base: string) => {
			const tk = await fetch(`${base}/toolkits`, {
				method: 'POST',
				credentials: 'include',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({ name: `prefix-test-${Date.now()}` }),
			});
			if (!tk.ok) throw new Error(`POST /toolkits → ${tk.status}`);
			const { id: toolkitId } = await tk.json();

			const req = await fetch(`${base}/toolkits/${toolkitId}/access-requests`, {
				method: 'POST',
				credentials: 'include',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({ type: 'grant', credential_id: 'api.example.com' }),
			});
			if (!req.ok) throw new Error(`POST /access-requests → ${req.status}`);
			const { id: reqId } = await req.json();

			return { toolkitId: toolkitId as string, reqId: reqId as string };
		}, PREFIX_BASE);

		// Reload the dashboard so usePendingRequests re-fetches the new request.
		await page.goto(`${PREFIX_BASE}/`);

		// Scope to the specific request so local re-runs (reuseExistingServer) with
		// accumulated pending requests don't hit a strict-mode multi-match violation.
		const reviewLink = page.locator(`a[href*="/foo/approve/${toolkitId}/${reqId}"]`);
		await expect(reviewLink).toBeVisible({ timeout: 15_000 });

		const href = await reviewLink.getAttribute('href');
		expect(href).toBeTruthy();
		// Regression: build_canonical_url (server-side) must bake in the prefix once.
		// A double-prefix (/foo/foo/approve/...) would mean the root path was applied twice.
		expect(href!).toContain(`/foo/approve/${toolkitId}/${reqId}`);
		expect(href!).not.toContain('/foo/foo/');
	});

	test('login redirect from a protected route preserves the prefix', async ({ page }) => {
		// Ensure admin exists without acquiring a session in the browser context.
		// On a fresh container /user/create sets a cookie — clear it immediately.
		// On a reused container the admin already exists and no cookies are touched.
		const healthRes = await page.request.get(`${PREFIX_BASE}/health`);
		const { status } = await healthRes.json();
		if (status === 'setup_required') {
			const res = await page.request.post(`${PREFIX_BASE}/user/create`, {
				data: { username: ADMIN_USER, password: ADMIN_PASS },
			});
			expect(res.ok(), `POST /user/create failed: ${res.status()}`).toBeTruthy();
			await page.context().clearCookies();
		}

		// Navigate to a protected route while logged out.
		// AuthGuard (App.tsx) catches every unauthenticated request and issues a
		// client-side Navigate to /login?next={location.pathname}. It uses React
		// Router's useLocation(), so the ?next= value is the basename-stripped path
		// (/approve/..., not /foo/approve/...). Pre-fix the next value included the
		// prefix → /foo/foo/... double-prefix after login.
		await page.goto(`${PREFIX_BASE}/approve/dummy-toolkit/areq_deadbeef`);

		await expect(page).toHaveURL(
			`${PREFIX_BASE}/login?next=${encodeURIComponent('/approve/dummy-toolkit/areq_deadbeef')}`,
			{ timeout: 10_000 },
		);

		// Log in. LoginPage calls navigate(next, { replace: true }) using React
		// Router's basename-aware navigate(), not window.location.href which
		// bypasses the prefix entirely.
		await page.getByLabel('Username').fill(ADMIN_USER);
		await page.getByRole('textbox', { name: 'Password' }).fill(ADMIN_PASS);
		await page.getByRole('button', { name: /^log in$/i }).click();

		// Post-login the URL must stay within the /foo prefix mount and must not
		// double up (/foo/foo/...). Pre-fix: window.location.href = next navigated
		// to the bare path (http://localhost:8901/approve/... — no /foo prefix).
		await expect(page).toHaveURL(/^http:\/\/localhost:8901\/foo/, { timeout: 10_000 });
		expect(page.url()).not.toContain('/foo/foo/');
	});

	test('logout from inside the app lands on /foo/login', async ({ page }) => {
		await loginViaApi(page);
		await page.goto(`${PREFIX_BASE}/`);
		await page.getByRole('heading', { name: /dashboard/i }).waitFor({ timeout: 15_000 });

		// Log out lives inside the UserMenu dropdown, opened via the avatar
		// button in TopNavbar.
		await page.getByRole('button', { name: /user menu/i }).click();

		// Layout.onSuccess calls navigate('/login') — React Router applies basename
		// so the destination is /foo/login, not bare /login or double /foo/foo/login.
		await page.getByRole('menuitem', { name: /log out/i }).click();

		await expect(page).toHaveURL(`${PREFIX_BASE}/login`);
	});

	test('UserMenu API docs link href is /foo/docs', async ({ page }) => {
		await loginViaApi(page);
		await page.goto(`${PREFIX_BASE}/`);
		await page.getByRole('heading', { name: /dashboard/i }).waitFor({ timeout: 15_000 });

		// The API docs link sits inside the UserMenu dropdown now, not the
		// old sidebar; open the menu first.
		await page.getByRole('button', { name: /user menu/i }).click();

		// apiUrl('/docs') → OpenAPI.BASE + '/docs' → '/foo/docs'.
		// AppLink external= renders a plain <a> so React Router does not re-apply basename.
		const docsLink = page.getByRole('menuitem', { name: /api docs/i });
		await expect(docsLink).toBeVisible({ timeout: 10_000 });

		await expect(docsLink).toHaveAttribute('href', '/foo/docs');
	});
});
