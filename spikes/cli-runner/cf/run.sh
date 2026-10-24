#!/bin/sh
cd /tmp; python3 /spike/mock.py 18801 /tmp/calls.log & sleep 1
E="env -i PATH=/usr/local/bin:/usr/bin:/bin HOME=/tmp CLOUDFLARE_API_BASE_URL=http://127.0.0.1:18801/client/v4 CLOUDFLARE_API_TOKEN=dummy-not-a-secret CLOUDFLARE_ACCOUNT_ID=0123456789abcdef0123456789abcdef CF_SEND_TELEMETRY=false DO_NOT_TRACK=1 CI=1 NO_COLOR=1"
c() { label="$1"; shift; : > /tmp/calls.log; out=$("$@" 2>/tmp/err); rc=$?; sleep 0.3
  echo "### $label  (exit $rc)"; echo "  stdout: $(echo "$out" | tr '\n' ' ' | cut -c1-300)"; echo "  stderr: $(tr '\n' ' ' </tmp/err | cut -c1-200)"
  python3 -c "import json;[print('  call:',d['m'],d['t'][:100],'| auth=',d['auth'],'| body=',d['body'][:80]) for d in map(json.loads,open('/tmp/calls.log'))]"; }
B='{"type":"A","name":"x","content":"1.2.3.4","ttl":1}'
c "dns records create --body --dry-run" $E cf dns records create -z 023e105f4ecef8ad9ca31a8372d0c353 --body "$B" --dry-run
c "dns records create --body (to mock)" $E cf dns records create -z 023e105f4ecef8ad9ca31a8372d0c353 --body "$B"
c "accounts tokens create --dry-run" $E cf accounts tokens create --body '{"name":"t","policies":[]}' --dry-run
