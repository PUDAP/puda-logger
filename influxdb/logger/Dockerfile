FROM natsio/nats-box:0.19.2@sha256:8031d190c7ee24081f3f27cc939fb647a1eeb29ebb5c60fef9b5b6c7a846d6a2 AS natscli

FROM telegraf:1.39@sha256:49899960eccad39415b91cf654324f0bb7efddaef8f1aa3ec24e2d8c6e19618e

USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends gettext-base jq \
    && rm -rf /var/lib/apt/lists/*

COPY --from=natscli /usr/local/bin/nats /usr/local/bin/nats
COPY --chown=root:root --chmod=0444 nats-config.sh /usr/local/lib/nats-config.sh
COPY --chown=root:root --chmod=0555 docker-entrypoint.sh /docker-entrypoint.sh
COPY --chown=root:root --chmod=0555 healthcheck.sh /healthcheck.sh

USER telegraf

# Nine bounded NATS queries are performed (three streams and six consumers).
# The timeout exceeds their 5s per-query worst case so the script can signal
# PID 1 rather than being killed by Docker before fail-closed recovery runs.
HEALTHCHECK --interval=60s --timeout=60s --start-period=90s --retries=3 \
    CMD ["/healthcheck.sh"]

ENTRYPOINT ["/docker-entrypoint.sh"]
