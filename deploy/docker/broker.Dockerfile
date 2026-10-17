# syntax=docker/dockerfile:1
FROM python-base:runtime

COPY --from=python-base:builder /build/dist/*.whl /build/dist/requirements.txt /tmp/
# Ubuntu's system python is PEP 668 externally-managed, so the wheel lands in
# a venv; /opt/venv/bin on PATH keeps the `python` runtime contract. The
# locked requirements install first and the wheel goes in with --no-deps, so
# pip never re-resolves the dependency ranges (`make check-image-lock` guards).
RUN python3.12 -m venv /opt/venv && \
    /opt/venv/bin/pip install --no-cache-dir --require-hashes -r /tmp/requirements.txt && \
    whl="$(ls /tmp/jentic_one-*.whl)" && \
    /opt/venv/bin/pip install --no-cache-dir --no-deps "${whl}" && \
    rm /tmp/*.whl /tmp/requirements.txt
ENV PATH="/opt/venv/bin:${PATH}"

# Numeric so kubelet can verify runAsNonRoot (see python-base.Dockerfile).
USER 10001
ENV JENTIC__APPS=broker
CMD ["python", "-m", "jentic_one"]
