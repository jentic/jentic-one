/**
 * RelayGuideDialog — the in-product guide to building a relay for an endpoint.
 *
 * Approach 1 sends a **fixed, signed** payload to a URL you own; it does not
 * speak Slack/Discord/PagerDuty. So in almost every real setup you run a small
 * relay that (1) receives Jentic's POST, (2) verifies the HMAC signature, and
 * (3) reshapes and forwards it to the real destination. This dialog documents
 * exactly what a relay must verify and hands over a correct, copy-pasteable
 * starting point.
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
import { CopyButton, Dialog } from '@/shared/ui';

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
						<h3 className="text-foreground font-semibold">Why you need a relay</h3>
					</div>
					<p className="text-muted-foreground leading-relaxed">
						Jentic One always POSTs the <em>same</em> signed JSON to your target URL —
						it doesn&apos;t speak Slack, Discord, PagerDuty or Telegram. A relay is a
						small service you run that verifies the signature and then reshapes and
						forwards each event to wherever it actually needs to go. It also keeps your
						destination credentials on your side, never in Jentic.
					</p>
					<ol className="text-muted-foreground ml-4 list-decimal space-y-1 leading-relaxed">
						<li>Jentic POSTs a signed event to your relay.</li>
						<li>
							Your relay verifies the HMAC signature and rejects anything invalid.
						</li>
						<li>Your relay reshapes the payload and forwards it to the destination.</li>
					</ol>
				</section>

				<section className="space-y-2">
					<div className="text-muted-foreground flex items-center gap-2">
						<ShieldCheck className="h-4 w-4 shrink-0" />
						<h3 className="text-foreground font-semibold">Signature &amp; headers</h3>
					</div>
					<p className="text-muted-foreground leading-relaxed">
						Signing follows the{' '}
						<a
							href="https://www.standardwebhooks.com/"
							target="_blank"
							rel="noreferrer noopener"
							className="text-accent-teal hover:underline"
						>
							Standard Webhooks
						</a>{' '}
						convention, so off-the-shelf libraries can verify us. Each request carries
						three headers:
					</p>
					<div className="border-border divide-border/60 divide-y rounded-lg border px-3 py-1">
						<HeaderRow
							name="webhook-id"
							meaning="The event id — also the first field of the signed content."
						/>
						<HeaderRow
							name="webhook-timestamp"
							meaning="Unix seconds when we signed. Reject a drift over 300s to stop replays."
						/>
						<HeaderRow
							name="webhook-signature"
							meaning="One or more space-separated tokens shaped v1,<hex> — verify any that match."
						/>
					</div>
					<p className="text-muted-foreground leading-relaxed">
						The signed content is exactly{' '}
						<code className="text-foreground font-mono text-xs">
							{'{webhook-id}.{webhook-timestamp}.{raw request body}'}
						</code>
						, HMAC-SHA256 with your signing secret, hex-encoded. Verify against the{' '}
						<strong>raw bytes</strong> you received — do not re-serialise the JSON
						first, or the signature won&apos;t match. Always use a constant-time
						comparison.
					</p>
				</section>

				<section className="space-y-2">
					<div className="text-muted-foreground flex items-center gap-2">
						<Webhook className="h-4 w-4 shrink-0" />
						<h3 className="text-foreground font-semibold">Payload shape</h3>
					</div>
					<p className="text-muted-foreground leading-relaxed">
						The body is compact JSON (no spaces, keys sorted). The envelope has{' '}
						<code className="text-foreground font-mono text-xs">id</code>,{' '}
						<code className="text-foreground font-mono text-xs">type</code> and{' '}
						<code className="text-foreground font-mono text-xs">data</code>; the inner{' '}
						<code className="text-foreground font-mono text-xs">data</code> carries the
						event fields. Key on{' '}
						<code className="text-foreground font-mono text-xs">id</code> and ignore
						repeats — delivery is at-least-once.
					</p>
					<CodeBlock code={SAMPLE_PAYLOAD} ariaLabel="Copy sample payload" />
				</section>

				<section className="space-y-2">
					<div className="text-muted-foreground flex items-center gap-2">
						<Copy className="h-4 w-4 shrink-0" />
						<h3 className="text-foreground font-semibold">
							Minimal relay (Python, standard library)
						</h3>
					</div>
					<p className="text-muted-foreground leading-relaxed">
						Verifies the signature exactly as above, then forwards. Set{' '}
						<code className="text-foreground font-mono text-xs">
							JENTIC_WEBHOOK_SECRET
						</code>{' '}
						to the signing secret and{' '}
						<code className="text-foreground font-mono text-xs">DESTINATION_URL</code>{' '}
						to where events should land.
					</p>
					<CodeBlock code={SAMPLE_RELAY} ariaLabel="Copy relay example" />
				</section>
			</div>
		</Dialog>
	);
}
