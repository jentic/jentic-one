# Spike image: pinned Cloudflare `cf` CLI (npm, integrity-checked by npm) + bubblewrap.
FROM node:22.20.0-bookworm-slim
ARG CF_VERSION=1.0.0-beta.18
RUN apt-get update && apt-get install -y --no-install-recommends bubblewrap python3 ca-certificates && rm -rf /var/lib/apt/lists/* \
 && npm install -g --no-fund --no-audit cf@${CF_VERSION} && npm ls -g cf
COPY mock.py run.sh /spike/
USER node
ENTRYPOINT ["/bin/sh", "/spike/run.sh"]
