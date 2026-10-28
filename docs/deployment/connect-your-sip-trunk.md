# Connect your SIP trunk

> **Not verified end to end.** No real SIP trunk has been exercised with this project. The provider profiles are unverified templates. Follow this guide with a test number you own, expect to adjust Asterisk settings, and confirm every value with your provider's own documentation. Not legal advice.

CallShield does not care which carrier you use. It needs a SIP trunk that delivers inbound calls to an Asterisk you run, with Asterisk's ARI interface (`ARI`) under CallShield's control. `callshield init` writes a trunk section for the provider profile you pick; `generic_sip` is fully manual and works for any provider.

## 0. Read your provider's terms first

Provider terms may forbid call screening, automated or AI-assisted calls, recording, or specific uses. That is release gate **G5** ([`release-gates.md`](../legal/release-gates.md)). Every profile carries a link to the provider's acceptable-use page for you to read yourself; the link only means the page loaded, not that its content was reviewed. Ask your provider in writing:

1. Is inbound call screening by software on my side allowed?
2. Are automated, prerecorded or AI-voice calls allowed on my account? (Only matters if you ever enable outbound features.)
3. Is call recording allowed, and who is responsible for consent?
4. Do inbound calls carry STIR/SHAKEN information to me, and where (SIP `Identity` header, a vendor header, something else)?
5. Can I originate calls with **A attestation** for my own number? How is it set up?
6. What are the SIP host/port, transport (UDP/TCP/TLS), codecs, and the source IP ranges of inbound calls?
7. Registration login, or IP-based authentication?
8. How is the called number (DID) presented: request URI, `To` header, or a custom header?

## 1. Run `callshield init`

Pick your profile and answer the trunk questions. Choose **registration** if the provider gives you a username and password (you log in to them), or **ip_auth** if they send calls from fixed IPs to yours. Always enter the provider's source IPs when you have them: the generated `identify` section uses them to recognize inbound calls and, if you leave it blank, falls back to the provider host name.

Output: `pjsip.callshield.conf` (contains your SIP password; mode 0600), `ari.callshield.conf` (ARI password), `.env`, `SETUP_REPORT.md`.

## 2. Install the configs into Asterisk

- Include `pjsip.callshield.conf` from your `pjsip.conf` (or paste it in). If your `pjsip.conf` already has a transport, point `transport_name` at it instead of generating a second one.
- Put the ARI user from `ari.callshield.conf` into `ari.conf`. Its password must equal `CALLSHIELD_ARI_PASSWORD`.
- Route the trunk's context (`from-trunk` by default) to the Stasis application named in `CALLSHIELD_ARI_APP`. See [`integrations/asterisk/extensions.conf`](../../integrations/asterisk/extensions.conf) for a harness example. If the application is down your dialplan must fall back to ringing your phone; test that.

## 3. NAT and RTP

Most home and cloud setups sit behind NAT. Symptoms: calls connect but audio is one-way or silent, or calls drop after ~30 seconds.

- The generated endpoint sets `direct_media=no`, `rtp_symmetric=yes`, `force_rport=yes`, `rewrite_contact=yes`, the usual NAT-friendly options.
- Set `external_media_address` and `external_signaling_address` on the transport to your public IP (the init `external_ip` input does this when it generates a transport).
- Open the RTP port range from `rtp.conf` (Asterisk default is 10000-20000/UDP) **to the provider's IP ranges only**, plus the SIP port. Do not open them to the world: exposed SIP is a toll-fraud target.
- Prefer a host with a public address, or ask your provider about a keep-alive/qualify option.

## 4. Keep ARI private

ARI (default `http://127.0.0.1:8088/ari`) can originate calls and control channels.

- Bind it to `127.0.0.1`. Never expose port 8088. `callshield doctor` warns if the URL host is not localhost or if the pjsip transport binds `0.0.0.0`.
- Use the generated random password. Rotate it by editing both `ari.conf` and `.env`.
- If CallShield and Asterisk run on different machines, tunnel (WireGuard/SSH) rather than opening the port.

## 5. What STIR/SHAKEN attestation is, and why callbacks need A

STIR/SHAKEN is a call-signing system. The originating carrier signs a call and states how well it knows the caller:

- **A (full)**: the carrier knows the customer and that they are entitled to use that number.
- **B (partial)**: knows the customer, not the number.
- **C (gateway)**: only knows where the call entered its network.

Attestation is **not** proof that a call is legitimate, and nothing here treats it that way. CallShield's optional callback flow allows a callback only when the inbound call's attestation was **A**, because a spoofed number (which would send your callback to an innocent person) is far more likely at B or C. The setting is locked to A (`CALLSHIELD_CALLBACK_ALLOWED_ATTESTATION`). Where the attestation appears (an `Identity` header, a verification-status field, or a vendor header) differs by provider and is on the question list above. Until you have confirmed it with a test call, assume callbacks cannot work. Callbacks are also gated by the unresolved legal questions G1-G5 and are off by default.

## 6. Test-call checklist (your own numbers only)

Use only numbers you own. Never test against someone else's phone or a business.

1. `callshield doctor` shows no FAIL. ARI PASS. Trunk status PASS or a WARN you understand.
2. Provider portal shows the trunk registered (registration mode) or your IP allow-listed (IP auth).
3. Call your DID from your mobile. Asterisk logs the INVITE and ARI receives the call. Your phone still rings: the default routing is **screen unknown callers, fall back to ordinary routing on any failure**.
4. Stop the ARI application and call again: your phone must still ring (safe fallback).
5. Audio both ways for at least a minute (NAT/RTP check).
6. If your provider exposes attestation, call from a number whose carrier you know and confirm what header you see. Record it; do not assume.
7. The call appears in the dashboard **Calls** page.
8. Run `callshield config show` and confirm every gated feature is still `False`.
9. Leave gated features off. Enabling anything is a separate decision: [`going-live-checklist.md`](going-live-checklist.md).
