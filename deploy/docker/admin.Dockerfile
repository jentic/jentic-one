# syntax=docker/dockerfile:1
FROM python-base:runtime

COPY --from=python-base:builder /build/dist/*.whl /tmp/
# Ubuntu's system python is PEP 668 externally-managed, so the wheel lands in
# a venv; /opt/venv/bin on PATH keeps the `python` runtime contract.
RUN python3.12 -m venv /opt/venv && \
    whl="$(ls /tmp/jentic_one-*.whl)" && \
    /opt/venv/bin/pip install --no-cache-dir "${whl}" && \
    rm /tmp/*.whl
ENV PATH="/opt/venv/bin:${PATH}"

USER jentic
# The identity plane (auth: /register, /oauth, /agents, /me, ...) rides with
# admin: both are owner-facing control surfaces rooted in the admin DB, and
# no other parts-mode image serves them. Without this, parts mode has no
# token issuance or agent registration at all.
ENV JENTIC__APPS=admin,auth
CMD ["python", "-m", "jentic_one"]
