#!/bin/sh
# No network in this container; only the in-container mock on 127.0.0.1:18800 is reachable (via proxy env).
cd /tmp; python3 /spike/mock.py 18800 /tmp/calls.log & sleep 1
PX="HTTP_PROXY=http://127.0.0.1:18800 http_proxy=http://127.0.0.1:18800"
GHE="env -i PATH=/usr/local/bin:/usr/bin:/bin HOME=/tmp GH_CONFIG_DIR=/tmp/gh GH_HOST=github.localhost GH_PROMPT_DISABLED=1 GH_NO_UPDATE_NOTIFIER=1 $PX"
AWE="env -i PATH=/usr/local/bin:/usr/bin:/bin HOME=/tmp AWS_CONFIG_FILE=/tmp/none AWS_SHARED_CREDENTIALS_FILE=/tmp/none AWS_ACCESS_KEY_ID=AKIADUMMY AWS_SECRET_ACCESS_KEY=dummy AWS_REGION=eu-west-1 AWS_EC2_METADATA_DISABLED=true AWS_PAGER= $PX AWS_ENDPOINT_URL_S3=http://s3.eu-west-1.amazonaws.com AWS_ENDPOINT_URL_STS=http://sts.eu-west-1.amazonaws.com"
c() { label="$1"; shift; : > /tmp/calls.log; out=$("$@" 2>&1); rc=$?; sleep 0.2
  echo "### $label  (exit $rc)"; echo "  out: $(echo "$out" | tr '\n' ' ' | cut -c1-170)"
  python3 -c "import json;[print('  call:',d['m'],d['t'][:90],'| sha256=',d['x-amz-content-sha256'],'| enc=',d['content-encoding'],'| len=',d['len']) for d in map(json.loads,open('/tmp/calls.log'))]"; }
c "gh pr list -R o/r (baseline)"            $GHE gh pr list -R o/r
c "gh pr view <absolute github.com URL>"    $GHE gh pr view https://github.com/o/r/pull/1
c "gh api --paginate (absolute Link next)"  $GHE gh api --paginate /repos/o/r/issues
c "gh run view 1 --log (302 to blob host)"  $GHE gh run view 1 --log -R o/r
c "gh release download v1 (asset redirect)" $GHE gh release download v1 -R o/r -D /tmp/dl
c "aws s3 ls (list buckets)"                $AWE aws s3 ls
c "aws s3 ls s3://my-bucket (bucket host?)" $AWE aws s3 ls s3://my-bucket
c "aws s3 ls --region us-east-1 (pinned eu-west-1 endpoint)" $AWE aws s3 ls s3://my-bucket --region us-east-1
c "aws ec2 describe-regions (no endpoint var)" $AWE aws ec2 describe-regions
head -c 2000000 /dev/urandom > /tmp/f.bin
c "aws s3 cp 2MB file (payload signing?)"   $AWE aws s3 cp /tmp/f.bin s3://my-bucket/f.bin
c "aws s3api put-object (payload signing?)" $AWE aws s3api put-object --bucket my-bucket --key g.bin --body /tmp/f.bin
