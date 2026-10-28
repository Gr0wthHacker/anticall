"""Provider-neutral Asterisk (ARI) integration: any SIP trunk, self-hosted.

Importing this package never imports aiohttp; the modules that need it do. Nothing connects
anywhere unless `CALLSHIELD_ASTERISK_ENABLED=true` and a runner/dialer is explicitly started.
"""
