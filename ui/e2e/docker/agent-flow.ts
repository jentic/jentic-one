import { createPrivateKey, generateKeyPairSync, sign as cryptoSign } from 'crypto';
import { type APIRequestContext, expect } from '@playwright/test';
import { authHeaders, getAdminUserId, uniqueSuffix } from './helpers';
import { setAgentOwner } from './db';

/**
 * Real agent lifecycle for the access-request approve flow.
 *
 * The access-request `:decide` (approve) verb is gated so the reviewer must own
 * the FILING agent. To exercise that for real we need a request filed by an
 * agent the admin owns — which means walking the genuine agent path end to end:
 *
 *   1. POST /register        — Dynamic Client Registration with a self-generated
 *                              Ed25519 public JWKS (no private material leaves
 *                              the test). Agent starts `pending`.
 *   2. POST /agents/{id}:approve (admin)  — flips the agent to `active`.
 *   3. setAgentOwner(...)     — the ONE non-API seam: assign owner_id = admin so
 *                              the admin satisfies the `owns_filer` rule. See db.ts.
 *   4. POST /oauth/token (jwt-bearer)     — the agent signs a JWT assertion with
 *                              its Ed25519 key and exchanges it for an access
 *                              token (EdDSA, `aud` = the canonical token endpoint
 *                              from discovery).
 *   5. POST /access-requests (agent token) — files as the agent, so
 *                              created_by = agent and filer_owner_id = admin.
 *
 * All five steps were captured live against :8000 on a clean fixtures DB.
 */

const JWT_BEARER_GRANT = 'urn:ietf:params:oauth:grant-type:jwt-bearer';

function b64url(input: Buffer): string {
	return input.toString('base64url');
}

/** RFC 7638-style Ed25519 public JWK from a generated key pair. */
function ed25519Jwk(): { jwk: Record<string, string>; privatePem: string; kid: string } {
	const { publicKey, privateKey } = generateKeyPairSync('ed25519');
	// The raw 32-byte Ed25519 public key is the last 32 bytes of the DER SPKI.
	const spki = publicKey.export({ type: 'spki', format: 'der' });
	const rawPublic = spki.subarray(spki.length - 32);
	const kid = `e2e-${uniqueSuffix()}`;
	const jwk = {
		kty: 'OKP',
		crv: 'Ed25519',
		x: b64url(rawPublic),
		kid,
		alg: 'EdDSA',
		use: 'sig',
	};
	const privatePem = privateKey.export({ type: 'pkcs8', format: 'pem' }).toString();
	return { jwk, privatePem, kid };
}

/** Sign a compact EdDSA JWT with the agent's private key. */
function signEdDsaJwt(privatePem: string, kid: string, payload: Record<string, unknown>): string {
	const header = { alg: 'EdDSA', kid, typ: 'JWT' };
	const signingInput = `${b64url(Buffer.from(JSON.stringify(header)))}.${b64url(
		Buffer.from(JSON.stringify(payload)),
	)}`;
	// Ed25519 in Node: pass algorithm `null` (the curve fixes SHA-512 internally).
	const signature = cryptoSign(null, Buffer.from(signingInput), createPrivateKey(privatePem));
	return `${signingInput}.${b64url(signature)}`;
}

export interface AgentIdentity {
	clientId: string;
	accessToken: string;
	/**
	 * The `client_name` this agent registered with. The directory (`GET /actors`)
	 * returns it as the agent's name, so the UI's <ActorLabel> resolves the agent's
	 * `agnt_…` id to this string — locate queue rows by `name`, not `clientId`.
	 */
	name: string;
}

/**
 * Walk the full register → approve → own → mint chain and return an agent
 * identity (client id + access token) that the admin owns. `adminRequest` must
 * carry the admin Bearer token (use a context with authHeaders()).
 */
export async function provisionAdminOwnedAgent(
	request: APIRequestContext,
	opts: { name?: string; ownerId?: string } = {},
): Promise<AgentIdentity> {
	const { jwk, privatePem, kid } = ed25519Jwk();

	// 1. Dynamic Client Registration.
	const clientName = opts.name ?? `e2e-agent-${uniqueSuffix()}`;
	const regRes = await request.post('/register', {
		headers: { 'content-type': 'application/json' },
		data: { client_name: clientName, jwks: { keys: [jwk] } },
	});
	expect(regRes.status(), `register failed: ${await regRes.text()}`).toBe(201);
	const clientId = (await regRes.json()).client_id as string;

	// 2. Admin approves the pending agent.
	const approveRes = await request.post(`/agents/${clientId}:approve`, {
		headers: authHeaders(),
	});
	expect(approveRes.status(), `agent approve failed: ${await approveRes.text()}`).toBe(200);

	// 3. Assign ownership to the admin (the one non-API seam — see db.ts). The
	//    admin id is resolved live from /users/me unless the caller pins one,
	//    because the no-credential first-run flow generates it at runtime.
	const ownerId = opts.ownerId ?? (await getAdminUserId(request));
	await setAgentOwner(clientId, ownerId);

	// 4. Mint an agent access token via jwt-bearer. `aud` must be the canonical
	//    token endpoint, which we read from discovery so it's env-agnostic.
	const discRes = await request.get('/.well-known/oauth-authorization-server');
	expect(discRes.ok(), `discovery failed: ${discRes.status()}`).toBeTruthy();
	const tokenEndpoint = (await discRes.json()).token_endpoint as string;

	const now = Math.floor(Date.now() / 1000);
	const assertion = signEdDsaJwt(privatePem, kid, {
		iss: clientId,
		sub: clientId,
		aud: tokenEndpoint,
		jti: `${clientId}-${uniqueSuffix()}`,
		iat: now,
		exp: now + 120,
	});
	const tokenRes = await request.post('/oauth/token', {
		headers: { 'content-type': 'application/json' },
		data: { grant_type: JWT_BEARER_GRANT, assertion },
	});
	expect(tokenRes.status(), `jwt-bearer mint failed: ${await tokenRes.text()}`).toBe(200);
	const accessToken = (await tokenRes.json()).access_token as string;

	return { clientId, accessToken, name: clientName };
}

/**
 * File an access request AS the given agent (its own Bearer token). Returns the
 * request id and the first item id (needed for the :decide call). Because the
 * agent is admin-owned, the resulting request's filer_owner_id == admin, so the
 * admin can later approve it.
 */
export async function fileAccessRequestAsAgent(
	request: APIRequestContext,
	agent: AgentIdentity,
	opts: { reason?: string; resourceType?: string; action?: string; resourceId?: string } = {},
): Promise<{ requestId: string; itemId: string }> {
	const res = await request.post('/access-requests', {
		headers: {
			authorization: `Bearer ${agent.accessToken}`,
			'content-type': 'application/json',
		},
		data: {
			reason: opts.reason ?? 'e2e agent-filed access request',
			items: [
				{
					resource_type: opts.resourceType ?? 'toolkit',
					action: opts.action ?? 'bind',
					resource_id: opts.resourceId ?? `e2e-res-${uniqueSuffix()}`,
				},
			],
		},
	});
	expect(res.status(), `fileAccessRequestAsAgent failed: ${await res.text()}`).toBe(202);
	const body = await res.json();
	return { requestId: body.id, itemId: body.items[0].id };
}
