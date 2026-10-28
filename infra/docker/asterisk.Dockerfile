# Asterisk for CallShield, from the Debian distribution package (no source build).
# Build from repo root: docker build -f infra/docker/asterisk.Dockerfile .
# Not built or run in CI. Pin the base image by digest before any non-local use.
FROM debian:bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      asterisk asterisk-modules asterisk-voicemail asterisk-core-sounds-en \
      gettext-base curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# Config templates ship in the image; secrets arrive only through the environment (env_file).
COPY integrations/asterisk/pjsip.conf integrations/asterisk/extensions.conf \
     integrations/asterisk/ari.conf integrations/asterisk/http.conf \
     integrations/asterisk/rtp.conf integrations/asterisk/modules.conf \
     /usr/share/callshield/asterisk/
COPY infra/docker/asterisk-entrypoint.sh /usr/local/bin/asterisk-entrypoint.sh
COPY infra/docker/asterisk-healthcheck.sh /usr/local/bin/asterisk-healthcheck.sh

# The Debian package creates the unprivileged `asterisk` user. Only /etc/asterisk (rendered
# config) and the runtime dirs need to be writable by it.
RUN chmod 0755 /usr/local/bin/asterisk-entrypoint.sh /usr/local/bin/asterisk-healthcheck.sh \
 && chown -R asterisk:asterisk /etc/asterisk /var/lib/asterisk /var/log/asterisk \
      /var/spool/asterisk /var/run/asterisk

USER asterisk
# ARI HTTP (publish on 127.0.0.1 only), SIP, RTP range (must match rtp.conf).
EXPOSE 8088 5060/udp 10000-10100/udp

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD ["/usr/local/bin/asterisk-healthcheck.sh"]

ENTRYPOINT ["/usr/local/bin/asterisk-entrypoint.sh"]
CMD ["asterisk", "-f", "-vvv"]
