FROM natsio/nats-box:latest AS natscli

FROM telegraf:1.39

USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends gettext-base jq \
    && rm -rf /var/lib/apt/lists/*

COPY --from=natscli /usr/local/bin/nats /usr/local/bin/nats

COPY docker-entrypoint.sh /docker-entrypoint.sh
COPY healthcheck.sh /healthcheck.sh
RUN chmod +x /docker-entrypoint.sh /healthcheck.sh

# Detects a Telegraf nats_consumer JetStream stall (see healthcheck.sh) that
# otherwise leaves the container running while silently dropping responses.
HEALTHCHECK --interval=60s --timeout=15s --start-period=90s --retries=3 \
    CMD ["/healthcheck.sh"]

ENTRYPOINT ["/docker-entrypoint.sh"]
