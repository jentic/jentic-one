/**
 * RelayGuideDialog — the in-product guide to building a relay for an endpoint.
 *
 * Approach 1 sends a fixed, signed payload to a URL you own. A relay is a small
 * service you run that (1) receives Jentic's POST, (2) verifies the HMAC
 * signature, and (3) reshapes and forwards it to the real destination. This
 * dialog documents what a relay must verify and hands over copy-pasteable code.
 *
 * Everything quoted here is transcribed from this branch's backend so the sample
 * verifier actually matches what we send:
 *   - headers + signature scheme: `src/jentic_one/shared/webhooks/signing.py`
 *   - request assembly: `src/jentic_one/shared/webhooks/delivery.py` (`_send`,
 *     `_serialise`)
 *   - payload fields: `admin/services/webhooks/fanout.py`
 *     (`build_notification_payload`)
 */
import { Copy, ShieldCheck, Waypoints, Webhook } from 'lucide-react';
import { CopyButton, Dialog, Disclosure } from '@/shared/ui';

interface RelayGuideDialogProps {
	open: boolean;
	onClose: () => void;
}

/** The exact wire body Jentic sends, pretty-printed for the guide. */
const SAMPLE_PAYLOAD = `{
  "id": "evt_2a1f…",
  "type": "credential.expired",
  "data": {
    "event_id": "evt_2a1f…",
    "event_type": "credential.expired",
    "severity": "error",
    "summary": "Credential cred_123 has expired",
    "created_at": "2026-08-19T10:30:00+00:00",
    "data": { "credential_id": "cred_123" }
  }
}`;

/**
 * A minimal, correct stdlib relay. Verifies the Standard-Webhooks signature the
 * way `signing.py` computes it, then forwards. No third-party deps so it pastes
 * and runs.
 */
const SAMPLE_RELAY = `import hashlib
import hmac
import json
import os
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib import request as urlrequest

# The signing secret shown once when you created the endpoint.
SIGNING_SECRET = os.environ["JENTIC_WEBHOOK_SECRET"]
# Where you actually want the event to go (e.g. a Slack incoming webhook).
DESTINATION_URL = os.environ["DESTINATION_URL"]

TOLERANCE_SECONDS = 300  # reject timestamps older than 5 minutes (replay guard)


def verify(headers, body: bytes) -> bool:
    webhook_id = headers.get("webhook-id", "")
    timestamp = headers.get("webhook-timestamp", "")
    signature_header = headers.get("webhook-signature", "")
    if not (webhook_id and timestamp and signature_header):
        return False

    # Reject stale requests: the id + timestamp are part of the signed material,
    # so a replayed capture also fails the timestamp check.
    try:
        if abs(time.time() - int(timestamp)) > TOLERANCE_SECONDS:
            return False
    except ValueError:
        return False

    # Signed content is exactly "{id}.{timestamp}.{raw body}".
    signed = webhook_id.encode() + b"." + timestamp.encode() + b"." + body
    expected = hmac.new(SIGNING_SECRET.encode(), signed, hashlib.sha256).hexdigest()

    # The header is one or more space-separated "v1,<hex>" tokens. Compare each
    # in constant time so a mismatch never leaks how many bytes were right.
    for token in signature_header.split(" "):
        scheme, _, sig = token.partition(",")
        if scheme == "v1" and hmac.compare_digest(sig, expected):
            return True
    return False


class Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        body = self.rfile.read(int(self.headers.get("content-length", 0)))
        if not verify(self.headers, body):
            self.send_response(401)
            self.end_headers()
            return

        event = json.loads(body)
        # Reshape to your destination's format. Here: post the summary to Slack.
        summary = event["data"]["summary"]
        payload = json.dumps({"text": f"Jentic: {summary}"}).encode()
        urlrequest.urlopen(
            urlrequest.Request(
                DESTINATION_URL, data=payload, headers={"content-type": "application/json"}
            )
        )

        # 2xx tells Jentic the delivery succeeded. Return 410 to have Jentic stop
        # sending (it will deactivate the endpoint); anything else is retried.
        self.send_response(204)
        self.end_headers()


if __name__ == "__main__":
    HTTPServer(("0.0.0.0", 8080), Handler).serve_forever()`;

function CodeBlock({ code, ariaLabel }: { code: string; ariaLabel: string }) {
	return (
		<div className="border-border bg-muted/60 relative rounded-lg border">
			<div className="absolute top-2 right-2">
				<CopyButton value={code} ariaLabel={ariaLabel} size="icon" variant="ghost" />
			</div>
			<pre className="text-foreground overflow-x-auto p-3 pr-12 font-mono text-xs leading-relaxed">
				<code>{code}</code>
			</pre>
		</div>
	);
}

function HeaderRow({ name, meaning }: { name: string; meaning: string }) {
	return (
		<div className="grid grid-cols-[minmax(0,10rem)_1fr] gap-3 py-1.5">
			<code className="text-foreground font-mono text-xs break-all">{name}</code>
			<span className="text-muted-foreground text-xs leading-relaxed">{meaning}</span>
		</div>
	);
}

export function RelayGuideDialog({ open, onClose }: RelayGuideDialogProps) {
	return (
		<Dialog open={open} onClose={onClose} title="Relay guide" size="xl">
			<div className="space-y-6 text-sm">
				<section className="space-y-2">
					<div className="text-muted-foreground flex items-center gap-2">
						<Waypoints className="h-4 w-4 shrink-0" />
						<h3 className="text-foreground font-semibold">What a relay is</h3>
					</div>
					<p className="text-muted-foreground leading-relaxed">
						A <strong>relay</strong> is a small web service you run between Jentic and
						your destination (Slack, Discord, PagerDuty…). It verifies the request came
						from Jentic, reshapes it for that destination, and forwards it — so your
						destination URL and tokens stay on your side.
					</p>
					<ol className="text-muted-foreground ml-4 list-decimal space-y-1 leading-relaxed">
						<li>
							<strong>Jentic sends</strong> a signed event to your relay&apos;s URL.
						</li>
						<li>
							<strong>Your relay verifies</strong> the signature and rejects anything
							that fails.
						</li>
						<li>
							<strong>Your relay forwards</strong> it in your destination&apos;s
							format.
						</li>
					</ol>
				</section>

				<section className="space-y-2">
					<div className="text-muted-foreground flex items-center gap-2">
						<ShieldCheck className="h-4 w-4 shrink-0" />
						<h3 className="text-foreground font-semibold">
							Step 1 — verify the signature
						</h3>
					</div>
					<p className="text-muted-foreground leading-relaxed">
						Every request carries a signature derived from the body plus a secret only
						you and Jentic share. Recreate it and compare — if it matches, it&apos;s
						genuine. This is the{' '}
						<a
							href="https://www.standardwebhooks.com/"
							target="_blank"
							rel="noreferrer noopener"
							className="text-accent-teal hover:underline"
						>
							Standard Webhooks
						</a>{' '}
						scheme, so most libraries can verify it for you.
					</p>
					<div className="border-border divide-border/60 divide-y rounded-lg border px-3 py-1">
						<HeaderRow
							name="webhook-id"
							meaning="Unique id for this event (also part of the signature)."
						/>
						<HeaderRow
							name="webhook-timestamp"
							meaning="Send time. Reject if older than 5 minutes to block replays."
						/>
						<HeaderRow
							name="webhook-signature"
							meaning="The signature, as v1,<hex>. Recreate it and check it matches."
						/>
					</div>
					<p className="text-muted-foreground leading-relaxed">
						Join the id, timestamp, and body with dots —{' '}
						<code className="text-foreground font-mono text-xs">
							{'{webhook-id}.{webhook-timestamp}.{raw body}'}
						</code>{' '}
						— and run HMAC-SHA256 with your secret. Use the{' '}
						<strong>raw bytes as received</strong> (don&apos;t re-format the JSON) and a{' '}
						<strong>constant-time</strong> compare. The sample below does both.
					</p>
				</section>

				<section className="space-y-2">
					<div className="text-muted-foreground flex items-center gap-2">
						<Webhook className="h-4 w-4 shrink-0" />
						<h3 className="text-foreground font-semibold">
							Step 2 — read and reshape the message
						</h3>
					</div>
					<p className="text-muted-foreground leading-relaxed">
						The body is always an envelope with{' '}
						<code className="text-foreground font-mono text-xs">id</code>,{' '}
						<code className="text-foreground font-mono text-xs">type</code>, and{' '}
						<code className="text-foreground font-mono text-xs">data</code> (the event
						details, including a human-readable{' '}
						<code className="text-foreground font-mono text-xs">summary</code>). Jentic
						may resend an event, so dedupe on{' '}
						<code className="text-foreground font-mono text-xs">id</code>.
					</p>
					<CodeBlock code={SAMPLE_PAYLOAD} ariaLabel="Copy sample payload" />
				</section>

				<section className="space-y-2">
					<div className="text-muted-foreground flex items-center gap-2">
						<Copy className="h-4 w-4 shrink-0" />
						<h3 className="text-foreground font-semibold">Ready-to-run example</h3>
					</div>
					<p className="text-muted-foreground leading-relaxed">
						A complete relay in Python (standard library only). Set{' '}
						<code className="text-foreground font-mono text-xs">
							JENTIC_WEBHOOK_SECRET
						</code>{' '}
						and{' '}
						<code className="text-foreground font-mono text-xs">DESTINATION_URL</code>,
						then run it.
					</p>
					<Disclosure summary="Show the relay code (Python, standard library)">
						<CodeBlock code={SAMPLE_RELAY} ariaLabel="Copy relay example" />
					</Disclosure>
				</section>
			</div>
		</Dialog>
	);
}
