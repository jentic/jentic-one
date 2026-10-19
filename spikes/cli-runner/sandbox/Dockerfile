# Spike image: pinned gh + bubblewrap, to test sandboxing under the chart's pod posture.
FROM debian:bookworm-slim
ARG GH_VERSION=2.89.0
ARG GH_SHA256=9e64a623dfc242990aa5d9b3f507111149c4282f66b68eaad1dc79eeb13b9ce5
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl bubblewrap util-linux python3 \
 && rm -rf /var/lib/apt/lists/*
RUN curl -fsSLo /tmp/gh.tgz https://github.com/cli/cli/releases/download/v${GH_VERSION}/gh_${GH_VERSION}_linux_arm64.tar.gz \
 && echo "${GH_SHA256}  /tmp/gh.tgz" | sha256sum -c - \
 && tar -xzf /tmp/gh.tgz -C /tmp && install -m 0755 /tmp/gh_${GH_VERSION}_linux_arm64/bin/gh /usr/local/bin/gh \
 && rm -rf /tmp/gh*
RUN useradd -u 10001 -m runner
COPY probe.sh /usr/local/bin/probe.sh
USER 10001
ENTRYPOINT ["/usr/local/bin/probe.sh"]
