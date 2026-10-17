# syntax=docker/dockerfile:1
FROM python-base:runtime

# Ubuntu's system python is PEP 668 externally-managed, so the wheel lands in
# a venv; /opt/venv/bin on PATH keeps the `python` runtime contract. The
# locked requirements install first, in their own layer so a code-only change
# reuses it, and the wheel goes in with --no-deps, so pip never re-resolves
# the dependency ranges (`make check-image-lock` guards). --only-binary keeps
# pip from building an sdist, whose build backend would come from PyPI
# without hash checking; `pip check` fails the build when the wheel declares
# a requirement the locked set doesn't satisfy.
COPY --from=python-base:builder /build/dist/requirements.txt /tmp/
RUN python3.12 -m venv /opt/venv && \
    /opt/venv/bin/pip install --no-cache-dir --require-hashes --only-binary=:all: \
        -r /tmp/requirements.txt && \
    rm /tmp/requirements.txt
COPY --from=python-base:builder /build/dist/*.whl /tmp/
RUN whl="$(ls /tmp/jentic_one-*.whl)" && \
    /opt/venv/bin/pip install --no-cache-dir --no-deps "${whl}" && \
    /opt/venv/bin/pip check && \
    rm /tmp/*.whl
ENV PATH="/opt/venv/bin:${PATH}"

# Numeric so kubelet can verify runAsNonRoot (see python-base.Dockerfile).
USER 10001
ENV JENTIC__APPS=broker
CMD ["python", "-m", "jentic_one"]
