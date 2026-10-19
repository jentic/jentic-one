FROM jentic-spike-cli-sandbox:gh-2.89.0
USER root
ARG AWS_VERSION=2.34.63
RUN apt-get update && apt-get install -y --no-install-recommends unzip && rm -rf /var/lib/apt/lists/* \
 && curl -fsSLo /tmp/aws.zip https://awscli.amazonaws.com/awscli-exe-linux-aarch64-${AWS_VERSION}.zip \
 && sha256sum /tmp/aws.zip > /awscli.sha256 && unzip -q /tmp/aws.zip -d /tmp && /tmp/aws/install && rm -rf /tmp/aws*
COPY mock.py run.sh /spike/
USER 10001
ENTRYPOINT ["/bin/sh", "/spike/run.sh"]
