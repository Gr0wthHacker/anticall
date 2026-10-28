"""Interactive prompts for `callshield init`. All input goes through the injected functions."""

from __future__ import annotations

from callshield.acknowledgment.content import ACKNOWLEDGMENT_V1
from callshield.cli.answers import DEFAULT_DB_URL, Answers
from callshield.cli.context import CliContext
from callshield.cli.out import out
from callshield.configreg.ext_settings import VOICE_STT_ENGINES, VOICE_TTS_ENGINES
from callshield.configreg.gates import (
    CALLBACK_CONFIRMATION_SENTENCE,
    RISKY_FEATURES,
    RiskyFeature,
)
from callshield.providers.base import ProviderProfile
from callshield.providers.registry import get_profile, profile_ids


def ask(ctx: CliContext, prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    reply = ctx.input_fn(f"{prompt}{suffix}: ").strip()
    return reply or default


def ask_choice(
    ctx: CliContext, prompt: str, choices: tuple[str, ...] | list[str], default: str
) -> str:
    while True:
        reply = ask(ctx, f"{prompt} ({'/'.join(choices)})", default)
        if reply in choices:
            return reply
        out(f"Please choose one of: {', '.join(choices)}")


def ask_yes_no(ctx: CliContext, prompt: str) -> bool:
    """Default is always NO. Only an explicit y/yes counts."""
    return ctx.input_fn(f"{prompt} [y/N]: ").strip().lower() in ("y", "yes")


def _ask_database(ctx: CliContext, ans: Answers) -> None:
    kind = ask_choice(ctx, "Database", ["sqlite", "postgres"], "sqlite")
    if kind == "sqlite":
        ans.db_url = DEFAULT_DB_URL
        return
    out("Postgres URL, e.g. postgresql+psycopg://user:pass@localhost:5432/callshield")
    ans.db_url = ask(ctx, "Postgres URL")


def _ask_admin(ctx: CliContext, ans: Answers) -> None:
    ans.admin_email = ask(ctx, "Admin email (blank to skip creating an admin now)")
    if not ans.admin_email:
        return
    ans.tenant_name = ask(ctx, "Household / tenant name", ans.tenant_name)
    ans.admin_password = ctx.secret_fn("Admin password (min 12 chars, not shown): ")


def _ask_provider(ctx: CliContext, ans: Answers) -> ProviderProfile:
    out("Provider profiles are UNVERIFIED templates. Confirm everything with your provider.")
    ans.provider_profile = ask_choice(ctx, "Provider profile", profile_ids(), "generic_sip")
    profile = get_profile(ans.provider_profile)
    if profile.acceptable_use_url:
        out(f"Read their acceptable-use terms yourself: {profile.acceptable_use_url}")
    for spec in profile.required_inputs:
        label = f"{spec.label}{'' if spec.required else ' (optional)'}"
        if spec.secret:
            ans.provider_inputs[spec.key] = ctx.secret_fn(f"{label}: ")
        else:
            ans.provider_inputs[spec.key] = ask(ctx, label, spec.default)
    return profile


def _ask_ari_voice(ctx: CliContext, ans: Answers) -> None:
    out("Asterisk ARI credentials: a strong password is generated for you.")
    ans.ari_url = ask(ctx, "ARI URL", ans.ari_url)
    ans.ari_username = ask(ctx, "ARI username", ans.ari_username)
    ans.stt_engine = ask_choice(ctx, "Speech-to-text engine", VOICE_STT_ENGINES, "none")
    ans.tts_engine = ask_choice(ctx, "Text-to-speech engine", VOICE_TTS_ENGINES, "none")


def _ask_feature(ctx: CliContext, feature: RiskyFeature) -> bool:
    out("")
    out(f"{feature.title}  [{feature.env_var}]")
    out(f"  WARNING: {feature.warning}")
    out(f"  Related gates: {feature.gates}")
    return ask_yes_no(ctx, f"  Enable {feature.title}?")


def _ask_acknowledgment(ctx: CliContext, ans: Answers) -> None:
    out("")
    out(ACKNOWLEDGMENT_V1["preamble"])
    for item in ACKNOWLEDGMENT_V1["items"]:
        out(f"  - {item['text']}")
        if not ask_yes_no(ctx, "    I accept"):
            out("Not accepted. Gated features will stay OFF.")
            ans.features = {}
            ans.acknowledgment_items = []
            return
        ans.acknowledgment_items.append(item["id"])


def _ask_features(ctx: CliContext, ans: Answers) -> None:
    out("\nGated features. Every one defaults to NO. None is cleared for real use yet.")
    for feature in RISKY_FEATURES:
        ans.features[feature.key] = _ask_feature(ctx, feature)
    if not ans.enabled_keys():
        return
    _ask_acknowledgment(ctx, ans)
    if ans.features.get("callback"):
        out(f'\nTo enable callbacks type exactly:\n  "{CALLBACK_CONFIRMATION_SENTENCE}"')
        ans.callback_confirmation = ctx.input_fn("> ").strip()
        if ans.callback_confirmation != CALLBACK_CONFIRMATION_SENTENCE:
            out("Sentence did not match. Callbacks stay OFF.")
            ans.features["callback"] = False


def run_wizard(ctx: CliContext) -> Answers:
    ans = Answers()
    out("CallShield setup. Press Enter to accept [defaults]. Nothing here is legal advice.")
    _ask_database(ctx, ans)
    ans.environment = ask_choice(ctx, "Environment", ["dev", "prod"], "dev")
    _ask_admin(ctx, ans)
    ans.jurisdiction = ask(ctx, "Jurisdiction code (e.g. US-CA, or UNKNOWN)", "UNKNOWN")
    ans.timezone = ask(ctx, "Timezone (IANA name)", "UTC")
    _ask_provider(ctx, ans)
    _ask_ari_voice(ctx, ans)
    _ask_features(ctx, ans)
    ans.backup_note = ask(ctx, "Where will you keep backups? (free text, optional)")
    return ans
