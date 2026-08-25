# syntax=docker/dockerfile:1
FROM python-base:runtime

COPY --from=python-base:builder /build/dist/*.whl /tmp/
RUN pip install --no-cache-dir /tmp/*.whl && rm /tmp/*.whl

USER jentic
# The identity plane (auth: /register, /oauth, /agents, /me, ...) rides with
# admin: both are owner-facing control surfaces rooted in the admin DB, and
# no other parts-mode image serves them. Without this, parts mode has no
# token issuance or agent registration at all.
ENV JENTIC__APPS=admin,auth
CMD ["python", "-m", "jentic_one"]
