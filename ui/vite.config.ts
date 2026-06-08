import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';
import { copyFileSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));

function copyApiDocsAssets(): import('vite').Plugin {
	return {
		name: 'copy-api-docs-assets',
		closeBundle() {
			const outDir = resolve(__dirname, '../static');
			const nm = resolve(__dirname, 'node_modules');
			copyFileSync(
				resolve(nm, 'swagger-ui-dist/swagger-ui-bundle.js'),
				resolve(outDir, 'swagger-ui-bundle.js'),
			);
			copyFileSync(
				resolve(nm, 'swagger-ui-dist/swagger-ui.css'),
				resolve(outDir, 'swagger-ui.css'),
			);
			copyFileSync(
				resolve(nm, 'redoc/bundles/redoc.standalone.js'),
				resolve(outDir, 'redoc.standalone.js'),
			);
		},
	};
}

// In Docker dev (compose.dev.yml) this is overridden to http://jentic-mini:8900
// so the Vite container can reach the API container by service name.
// When running Vite directly on the host, the default http://localhost:8900 applies.
const apiHost = process.env.VITE_API_HOST || 'http://localhost:8900';

// Paths that are also React Router routes (e.g. /toolkits, /search).
// For these, browser navigations (Accept: text/html) must serve index.html so
// the SPA can render — only JSON/API calls should be proxied to the backend.
// Pure API-only paths (no matching SPA route) can use the simpler string form.
const spaRoute = {
	target: apiHost,
	bypass: (req: import('http').IncomingMessage) =>
		req.headers.accept?.includes('text/html') ? '/index.html' : null,
};

export default defineConfig({
	resolve: {
		alias: {
			'@': resolve(__dirname, 'src'),
		},
	},
	plugins: [react(), tailwindcss(), copyApiDocsAssets()],
	// Relative base — bundled assets resolve against the served index.html's
	// <base href>, so the same build can be served at any path prefix.
	base: './',
	build: { outDir: '../static', emptyOutDir: true },
	server: {
		host: '0.0.0.0',
		allowedHosts: true,
		proxy: {
			// Pure API routes — no conflicting SPA page
			'/api': apiHost,
			'/user': apiHost,
			'/apis': apiHost,
			'/health': apiHost,
			'/version': apiHost,
			'/import': apiHost,
			'/inspect': apiHost,
			'/notes': apiHost,
			'/default-api-key': apiHost,
			'/docs': apiHost,
			'/openapi.json': apiHost,
			// Backend-only callback — must be proxied unconditionally (browser redirect from Pipedream)
			'/oauth-brokers': { target: apiHost, bypass: (req) => {
				if (req.url?.includes('/connect-callback')) return null; // always proxy
				return req.headers.accept?.includes('text/html') ? '/index.html' : null;
			}},
			// SPA + API dual-use routes — serve index.html for browser navigations
			'/search': spaRoute,
			'/toolkits': spaRoute,
			'/credentials': spaRoute,
			'/traces': spaRoute,
			'/jobs': spaRoute,
			'/workflows': spaRoute,
			'/catalog': spaRoute,
			'/agents': spaRoute,
		},
	},
});
