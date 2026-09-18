"""
Embed builders and small utilities shared across the bot.
"""

from __future__ import annotations

import datetime as dt

import discord

import config

# Colors
COLOR_WARN = 0xF1C40F     # yellow
COLOR_TIMEOUT = 0xE67E22  # orange
COLOR_SPAM = 0xE74C3C     # red-ish
COLOR_INVITE = 0x9B59B6   # purple
COLOR_FOUNDER = 0x3498DB  # blue
COLOR_KICK = 0x992D22     # dark red


def _base(title: str, color: int) -> discord.Embed:
    embed = discord.Embed(title=title, color=color,
                          timestamp=dt.datetime.now(dt.timezone.utc))
    embed.set_footer(text=f"{config.BOT_NAME} — {config.SERVER_NAME}")
    return embed


def format_duration(minutes: float) -> str:
    minutes = int(minutes)
    if minutes >= 10080:
        return f"{minutes / 10080:g} day(s)"
    if minutes >= 1440 and minutes % 60 == 0:
        return f"{minutes / 1440:g} day(s)"
    if minutes >= 60:
        return f"{minutes / 60:g} hour(s)"
    return f"{minutes} minute(s)"


def user_tag(member: discord.abc.User) -> str:
    return f"{member} ({member.id})"


def clip(text: str, limit: int = 200) -> str:
    text = (text or "").replace("`", "'")
    if len(text) > limit:
        text = text[: limit - 1] + "…"
    return text or "(no text content)"


# ---------------------------------------------------------------------------
# Log embeds
# ---------------------------------------------------------------------------

def warning_embed(member, category: str, reason: str, count: int,
                  threshold: int, expires_at: float,
                  jump_url: str | None) -> discord.Embed:
    embed = _base("⚠️ Warning issued", COLOR_WARN)
    embed.add_field(name="User", value=user_tag(member), inline=False)
    embed.add_field(name="Rule", value=category, inline=True)
    embed.add_field(name="Warning count", value=f"{count}/{threshold}",
                    inline=True)
    embed.add_field(
        name="Expires",
        value=f"<t:{int(expires_at)}:R>" if expires_at else "—",
        inline=True,
    )
    embed.add_field(name="Details", value=clip(reason, 500), inline=False)
    if jump_url:
        embed.add_field(name="Message", value=f"[Jump to message]({jump_url})",
                        inline=False)
    return embed


def timeout_embed(member, minutes: float, reason: str, ladder_level: int,
                  success: bool) -> discord.Embed:
    title = "🔇 Timeout applied" if success else "⚠️ Timeout FAILED (missing permission/hierarchy)"
    embed = _base(title, COLOR_TIMEOUT if success else COLOR_SPAM)
    embed.add_field(name="User", value=user_tag(member), inline=False)
    embed.add_field(name="Duration", value=format_duration(minutes),
                    inline=True)
    # ladder_level < 0 means "not a ladder timeout" (e.g. invite timeouts)
    rung = f"{ladder_level + 1}" if ladder_level >= 0 else "— (fixed rule)"
    embed.add_field(name="Escalation rung", value=rung, inline=True)
    embed.add_field(name="Reason", value=clip(reason, 500), inline=False)
    return embed


def spam_embed(member, category: str, reason: str, content: str,
               channel: discord.abc.GuildChannel,
               jump_url: str | None, action_taken: str) -> discord.Embed:
    embed = _base("🚫 Spam detected", COLOR_SPAM)
    embed.add_field(name="User", value=user_tag(member), inline=False)
    embed.add_field(name="Type", value=category, inline=True)
    embed.add_field(name="Channel", value=channel.mention, inline=True)
    embed.add_field(name="Action taken", value=action_taken, inline=True)
    embed.add_field(name="Detection", value=clip(reason, 500), inline=False)
    embed.add_field(name="Content", value=clip(content, 500), inline=False)
    if jump_url:
        embed.add_field(name="Message", value=f"[Jump to message]({jump_url})",
                        inline=False)
    return embed


def invite_embed(member, codes: list[str], channel, offense_count: int,
                 action_taken: str) -> discord.Embed:
    embed = _base("🔗 Unauthorized invite posted", COLOR_INVITE)
    embed.add_field(name="User", value=user_tag(member), inline=False)
    embed.add_field(name="Channel", value=channel.mention, inline=True)
    embed.add_field(name="Offense #", value=str(offense_count), inline=True)
    embed.add_field(name="Invite code(s)", value=", ".join(codes),
                    inline=True)
    embed.add_field(name="Action taken", value=action_taken, inline=False)
    return embed


def kick_embed(member, reason: str, success: bool) -> discord.Embed:
    title = "🥾 User kicked" if success else "⚠️ Kick FAILED (missing permission/hierarchy)"
    embed = _base(title, COLOR_KICK)
    embed.add_field(name="User", value=user_tag(member), inline=False)
    embed.add_field(name="Reason", value=clip(reason, 500), inline=False)
    return embed


def founder_warning_text(member: discord.Member,
                         founder_role: str) -> str:
    return (
        f"⚠️ {member.mention} — please **do not ping the {founder_role} role "
        "or its members**. They will respond when available. Repeated pings "
        "will not make them respond faster — thanks for your patience!"
    )


def timeout_notice_text(member: discord.Member, minutes: float,
                        reason: str) -> str:
    return (f"🔇 {member.mention} has been timed out for "
            f"**{format_duration(minutes)}** — {reason}")


def kick_notice_text(member: discord.Member, reason: str) -> str:
    return f"🥾 {member.mention} has been kicked — {reason}"


# ---------------------------------------------------------------------------
# Logging helper
# ---------------------------------------------------------------------------

async def log_to_channel(bot: discord.Client, channel_id: int,
                         embed: discord.Embed) -> None:
    """Best-effort embed logging — never raises."""
    try:
        channel = bot.get_channel(channel_id)
        if channel is None:
            channel = await bot.fetch_channel(channel_id)
        if channel is not None:
            await channel.send(embed=embed)
    except Exception as exc:  # noqa: BLE001 - logging must never crash the bot
        print(f"[DCRP] Failed to log to channel {channel_id}: {exc}")
