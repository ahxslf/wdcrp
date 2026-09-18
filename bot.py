"""
DCRP | Utilities — full auto-moderation for the Washington D.C. Roleplay
Discord server.

Features (per owner spec):
  • Profanity: allowed. Only *extreme* profanity spam is flagged, as spam.
  • Emoji spam: allowed normally; excessive use -> warn -> timeout (escalating).
  • Word/message spam: repeated messages, repeated words, char spam, floods
    -> warn -> timeout, with escalating timeout durations.
  • Founder role pings: NEVER punished. A friendly warning is displayed
    EVERY time the Founder role is pinged.
  • Invites: deleted on sight. 1st offense -> 24h timeout, 2nd+ -> kick.
    Never a ban. Staff can whitelist approved invite links.
  • Warning system: 3 warnings (small stuff) -> 1h timeout, then an
    escalating ladder (1h -> 3h -> 6h -> 12h -> 24h). Non-serious warnings
    expire 24h after they were issued.

Requires discord.py 2.x with the "Message Content" and "Server Members"
privileged intents enabled in the Developer Portal.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from datetime import timedelta

import discord
from discord import app_commands
from discord.ext import commands, tasks

import config
import detectors
import health
import helpers
import profanity
from db import Database


class DCRPBot(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.members = True
        intents.message_content = True
        super().__init__(
            command_prefix="!", intents=intents, help_command=None,
            # Custom status => the exact text is shown in the member list
            # too, not just on the profile ("Watching the server").
            activity=discord.CustomActivity(name=config.ACTIVITY_TEXT),
            status=discord.Status.online,
        )

        self.db = Database(config.DB_PATH)
        # Rolling per-user short-term message history: (ts, normalized_text)
        self.msg_history: dict[tuple[int, int], deque] = defaultdict(
            lambda: deque(maxlen=40))
        # Last time a user received an auto-warning (anti-chain cooldown)
        self.warn_cooldown: dict[tuple[int, int], float] = {}
        # Small caches for invite lookups
        self._own_invite_cache: set[str] = set()
        self._invalid_invite_cache: dict[str, float] = {}

        # Register staff commands (takes effect on connection/login)
        self.tree.add_command(exempt_invites_group)
        for cmd in (warnings_cmd, clear_warnings_cmd, mod_status_cmd):
            self.tree.add_command(cmd)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    async def setup_hook(self) -> None:
        # Open the health port for hosts that require one (Render & friends).
        # No-op when PORT is not set. Never fatal to the bot.
        try:
            await health.start_health_server(self, config.BOT_NAME)
        except Exception as exc:  # noqa: BLE001
            print(f"[DCRP] Health server failed to start (non-fatal): {exc}")

        if config.GUILD_ID:
            # Instant command availability in this specific guild
            guild_obj = discord.Object(id=config.GUILD_ID)
            self.tree.copy_global_to(guild=guild_obj)
            await self.tree.sync(guild=guild_obj)
            print(f"[DCRP] Slash commands synced to guild {config.GUILD_ID}")
        else:
            await self.tree.sync()
            print("[DCRP] Slash commands synced globally (may take up to 1h)")

        self.role_sync_loop.start()

    async def on_ready(self) -> None:
        print(f"[DCRP] {config.BOT_NAME} online as {self.user} "
              f"({self.user.id}) — serving {config.SERVER_NAME}")
        print(f"[DCRP] Presence: {config.ACTIVITY_TEXT!r} (custom status)")
        if not self.intents.message_content:
            print("[DCRP] ❌ URGENT: 'Message Content Intent' is NOT enabled "
                  "in the Developer Portal! The bot cannot read message text "
                  "— no spam/profanity/invite detection will work. Enable it: "
                  "Developer Portal -> Bot -> 'Message Content Intent' -> Save,"
                  " then restart the bot.")
        if config.DEBUG:
            print("[DCRP][debug] DEBUG MODE ON")
        else:
            print("[DCRP] (set DEBUG = True in config.py to see per-message "
                  "decisions in this console)")
        await self.startup_selfcheck()

    # ------------------------------------------------------------------
    # Startup self-check: verifies the guild, permissions, roles,
    # log channels and role hierarchy — prints a readable report.
    # ------------------------------------------------------------------
    async def startup_selfcheck(self) -> None:
        print("[DCRP] --- Startup self-check -----------------------------")
        try:
            guild = self.get_guild(config.GUILD_ID) if config.GUILD_ID else None
            if guild is None:
                if len(self.guilds) == 1:
                    guild = self.guilds[0]
                else:
                    print(f"[DCRP] ⚠️ Could not resolve guild "
                          f"(GUILD_ID={config.GUILD_ID}). Visible guilds: "
                          f"{[g.id for g in self.guilds]}")
                    print("[DCRP] --- self-check done ------------------------")
                    return
            print(f"[DCRP] Guild: {guild.name} ({guild.id}) — "
                  f"{guild.member_count} members")

            me = guild.me
            if me is None:
                print("[DCRP] ❌ The bot is NOT a member "
                      f"of {guild.name}! Invite it with the OAuth2 URL.")
                return

            perms = me.guild_permissions
            needed = [
                ("View Channel", perms.view_channel),
                ("Send Messages", perms.send_messages),
                ("Embed Links", perms.embed_links),
                ("Manage Messages (delete)", perms.manage_messages),
                ("Manage Roles (give Warning roles)", perms.manage_roles),
                ("Moderate Members (timeouts)", perms.moderate_members),
                ("Kick Members", perms.kick_members),
                ("Change Nickname", perms.change_nickname),
            ]
            for label, ok in needed:
                print(f"[DCRP]   perm: {label}: {'✅' if ok else '❌ MISSING'}")

            for label, ch_id in (
                ("log", config.LOG_CHANNEL_ID),
                ("staff-chat", config.STAFF_CHAT_CHANNEL_ID),
            ):
                if not ch_id:
                    print(f"[DCRP]   {label} channel: ⚠️ not set")
                    continue
                ch = guild.get_channel(ch_id)
                print(f"[DCRP]   {label} channel: "
                      f"{'✅ #' + ch.name if ch else '❌ NOT FOUND (id ' + str(ch_id) + ')'}")

            founder = guild.get_role(config.FOUNDER_ROLE_ID)
            print(f"[DCRP]   Founder role: "
                  f"{'✅ @' + founder.name if founder else '❌ NOT FOUND'}")

            top = me.top_role
            print(f"[DCRP]   Bot top role: {top.name} (position {top.position})")
            for i, rid in enumerate(config.WARNING_ROLE_IDS, 1):
                r = guild.get_role(rid)
                if r is None:
                    print(f"[DCRP]   Warning {i} role: ❌ NOT FOUND")
                elif r.position >= top.position:
                    print(f"[DCRP]   Warning {i} role: ❌ '@{r.name}' is ABOVE/"
                          f"EQUAL to the bot's top role — move the bot role "
                          f"higher, or role assignment will fail")
                else:
                    print(f"[DCRP]   Warning {i} role: ✅ @{r.name} "
                          f"(position {r.position})")
        except Exception as exc:  # noqa: BLE001 - self-check must never crash
            print(f"[DCRP] self-check error: {exc}")
        print("[DCRP] --- self-check done ----------------------------")
        # Set the in-server nickname to the branding name (best effort;
        # requires the 'Change Nickname' permission).
        for guild in self.guilds:
            try:
                if guild.me and guild.me.nick != config.BOT_NAME \
                        and guild.me.display_name != config.BOT_NAME \
                        and self.user.name != config.BOT_NAME:
                    await guild.me.edit(nick=config.BOT_NAME)
                    print(f"[DCRP] Nickname set in {guild.name}")
            except (discord.Forbidden, discord.HTTPException):
                pass  # missing permission — nickname is cosmetic only

    # ------------------------------------------------------------------
    # Background loop: keep the Warning 1/2/3 roles in sync with the
    # rolling 24-hour warning expiry.
    # ------------------------------------------------------------------
    @tasks.loop(seconds=60)
    async def role_sync_loop(self) -> None:
        try:
            self.db.prune_expired()
            for guild_id, user_id in self.db.users_with_warnings():
                guild = self.get_guild(guild_id)
                if guild is None:
                    continue
                member = guild.get_member(user_id)
                if member is None:
                    try:
                        member = await guild.fetch_member(user_id)
                    except discord.NotFound:
                        continue
                count = self.db.active_warning_count(guild_id, user_id)
                await self.sync_warning_roles(member, count)
        except Exception as exc:  # noqa: BLE001
            print(f"[DCRP] role_sync_loop error: {exc}")

    @role_sync_loop.before_loop
    async def before_role_sync(self) -> None:
        await self.wait_until_ready()

    async def sync_warning_roles(self, member: discord.Member,
                                 active_count: int) -> None:
        """Make the member's Warning 1/2/3 roles equal `active_count`."""
        wanted = set(config.WARNING_ROLE_IDS[:max(0, min(active_count, 3))])
        tracked = set(config.WARNING_ROLE_IDS)
        current = {r.id for r in member.roles if r.id in tracked}
        to_add_ids = wanted - current
        to_remove = [r for r in member.roles
                     if r.id in tracked and r.id not in wanted]
        try:
            if to_add_ids:
                roles = [r for rid in to_add_ids
                         if (r := member.guild.get_role(rid)) is not None]
                if roles:
                    await member.add_roles(
                        *roles, reason=f"Auto-mod: warning count {active_count}")
            if to_remove:
                await member.remove_roles(
                    *to_remove, reason=f"Auto-mod: warning count {active_count}")
        except discord.Forbidden:
            print(f"[DCRP] Missing permission/hierarchy to sync warning "
                  f"roles for {member}")

    # ------------------------------------------------------------------
    # Message pipeline
    # ------------------------------------------------------------------
    async def on_message(self, message: discord.Message) -> None:
        if message.guild is None or message.author.bot:
            return
        member = message.author
        if not isinstance(member, discord.Member):
            return
        if config.DEBUG:
            print(f"[DCRP][debug] msg from {member} "
                  f"content={message.content!r}")

        content = message.content or ""
        now = time.time()
        guild_id = message.guild.id
        user_id = member.id

        # 1) Founder pings — warn EVERY time, never punish. Covers BOTH:
        #    * pinging the @Foundership Team role
        #    * pinging any member who HOLDS the Foundership Team role
        #    Exempt (no reminder shown):
        #    * Foundership Team members themselves
        #    * anyone who can VIEW the staff chat channel — staff may reach
        #      the founders directly
        author_is_founder = any(r.id == config.FOUNDER_ROLE_ID
                                for r in member.roles)
        if not author_is_founder and not self._can_ping_founder(member):
            founder_hit = any(r.id == config.FOUNDER_ROLE_ID
                              for r in message.role_mentions)
            if not founder_hit:
                founder_hit = any(
                    not um.bot and
                    any(r.id == config.FOUNDER_ROLE_ID
                        for r in getattr(um, "roles", ()))
                    for um in message.mentions
                )
            if founder_hit:
                if config.DEBUG:
                    print("[DCRP][debug] -> FOUNDER PING detected, replying")
                await self.handle_founder_ping(message)

        # 2) Invites — delete + punish (24h timeout first, then kick).
        if await self.handle_invites(message, now):
            return

        # 3) Spam pipeline (emoji/word/char/flood/repeat/profanity spam).
        # History entries: (ts, normalized, raw_content, profanity_score,
        # profanity_count) — the last two feed the profanity-flood and
        # letter-by-letter bypass detectors.
        key = (guild_id, user_id)
        history = self.msg_history[key]
        norm = detectors.normalize(content)
        report = profanity.scan_profanity(content)
        history.append((now, norm, content, report.score, report.count))

        detections = detectors.analyze(
            content,
            [h[1] for h in history],
            [h[0] for h in history],
            config,
            now,
            raw_history=[(h[0], h[2]) for h in history],
            prof_history=[(h[0], h[3], h[4]) for h in history],
            prof_report=report,
        )
        if detections:
            category, reason = detections[0]
            if config.DEBUG:
                print(f"[DCRP][debug] -> DETECTION: {category} ({reason})")
            last_warn = self.warn_cooldown.get(key, 0.0)
            if now - last_warn >= config.WARN_COOLDOWN_SECONDS:
                self.warn_cooldown[key] = now
                await self.apply_small_warning(message, category, reason, now)
        elif config.DEBUG:
            print("[DCRP][debug] -> no detection")

        await self.process_commands(message)

    # ------------------------------------------------------------------
    # Founder pings
    # ------------------------------------------------------------------
    def _can_ping_founder(self, member: discord.Member) -> bool:
        """True if the member can view the staff chat channel — per owner
        rule, those members may ping founders without the reminder."""
        ch_id = config.STAFF_CHAT_CHANNEL_ID
        if not ch_id:
            return False
        channel = member.guild.get_channel(ch_id)
        if channel is None:
            return False
        return bool(channel.permissions_for(member).view_channel)

    async def handle_founder_ping(self, message: discord.Message) -> None:
        """Reminder only — pinging Founder is NEVER punished. Shows up
        every single time, as required."""
        founder = message.guild.get_role(config.FOUNDER_ROLE_ID)
        role_name = founder.name if founder else "Founder"
        try:
            await message.reply(
                helpers.founder_warning_text(message.author, role_name),
                mention_author=True,
            )
        except discord.HTTPException:
            # Fallback (e.g. message vanished between detect and reply)
            try:
                await message.channel.send(
                    helpers.founder_warning_text(message.author, role_name))
            except discord.HTTPException:
                pass

    # ------------------------------------------------------------------
    # Invites
    # ------------------------------------------------------------------
    async def handle_invites(self, message: discord.Message,
                             now: float) -> bool:
        """Process invite links. Returns True if the message was treated
        as an invite violation (pipeline stops here)."""
        guild = message.guild
        member = message.author
        codes = detectors.find_invite_codes(message.content or "")
        if not codes:
            return False

        violating: list[str] = []
        for code in codes:
            if self.db.is_exempt_invite(guild.id, code):
                continue  # staff-approved link — always allowed
            if await self.is_own_guild_invite(guild, code):
                continue  # invites that point back at THIS server are fine
            violating.append(code)

        if not violating:
            return False

        # The message itself is always deleted.
        try:
            await message.delete()
        except discord.Forbidden:
            print("[DCRP] Cannot delete invite message — missing "
                  "Manage Messages permission")
        except discord.NotFound:
            pass

        offense_count = self.db.bump_invite_offense(guild.id, member.id)

        if offense_count == 1:
            minutes = config.INVITE_TIMEOUT_HOURS * 60
            action = (f"Message deleted + {config.INVITE_TIMEOUT_HOURS}h "
                      f"timeout (offense #1)")
            success = await self.do_timeout(
                member, guild, minutes,
                reason="Posted an unauthorized invite link",
                notify_channel=message.channel,
                ladder_level=-1,  # fixed-rule timeout, not ladder-based
            )
            if not success:
                action += " (timeout FAILED — check bot permissions/hierarchy)"
        else:
            action = f"Message deleted + KICKED from server (offense #{offense_count})"
            success = await self.do_kick(
                member, guild,
                reason=f"Posted unauthorized invite link (offense "
                       f"#{offense_count}) — repeat offense after timeout",
                notify_channel=message.channel,
            )
            if not success:
                action += " (kick FAILED — check bot permissions/hierarchy)"

        # Per spec: only the log channel is notified definitively; a short
        # in-channel notice helps users understand why the message vanished.
        try:
            notice = (f"🔗 {member.mention} posted an unauthorized invite "
                      f"link and it was removed. **Do not post invite links "
                      f"here.** ({action})")
            await message.channel.send(notice, delete_after=45)
        except discord.HTTPException:
            pass

        await helpers.log_to_channel(
            self, config.INVITE_LOG_CHANNEL_ID,
            helpers.invite_embed(member, violating, message.channel,
                                 offense_count, action),
        )
        return True

    async def is_own_guild_invite(self, guild: discord.Guild,
                                  code: str) -> bool:
        """True if `code` resolves to an invite pointing at this server."""
        if code in self._own_invite_cache:
            return True
        invalid_at = self._invalid_invite_cache.get(code)
        if invalid_at and time.time() - invalid_at < 600:
            return False
        try:
            invite = await self.fetch_invite(code)
            if invite.guild is not None and invite.guild.id == guild.id:
                self._own_invite_cache.add(code)
                return True
        except discord.HTTPException:
            self._invalid_invite_cache[code] = time.time()
        return False

    # ------------------------------------------------------------------
    # Warning system (small offenses)
    # ------------------------------------------------------------------
    async def apply_small_warning(self, message: discord.Message,
                                  category: str, reason: str,
                                  now: float) -> None:
        guild = message.guild
        member = message.author
        expires_at = now + config.WARNING_EXPIRY_HOURS * 3600
        self.db.add_warning(guild.id, member.id, category, reason,
                            serious=False, expires_at=expires_at)
        count = self.db.active_warning_count(guild.id, member.id, now)
        threshold = config.WARNINGS_UNTIL_TIMEOUT

        # Roles always mirror the CURRENT active count (Warning 1/2/3).
        await self.sync_warning_roles(
            member, min(count, len(config.WARNING_ROLE_IDS)))

        # Every warning is logged to the warn channel — including the one
        # that triggers the timeout.
        await helpers.log_to_channel(
            self, config.WARN_LOG_CHANNEL_ID,
            helpers.warning_embed(
                member, category, reason, min(count, threshold), threshold,
                expires_at, message.jump_url),
        )

        if count >= threshold:
            # 3+ active warnings -> timeout on the escalating ladder.
            # Warnings are NOT cleared here: each one expires 24 hours
            # after it was issued (owners's rule). That means a user who
            # keeps spamming right after a timeout gets timed out again
            # immediately on the NEXT, longer ladder rung — automatic
            # escalation with no manual work.
            level = self.db.next_timeout_level(
                guild.id, member.id, config.ESCALATION_RESET_DAYS, now)
            ladder = config.TIMEOUT_LADDER_MINUTES
            minutes = ladder[min(level, len(ladder) - 1)]
            action = (f"Timed out for {helpers.format_duration(minutes)} "
                      f"(warning {count}/{threshold})")
            await self.do_timeout(
                member, guild, minutes,
                reason=f"Warning {count}/{threshold}: {category} ({reason})",
                notify_channel=message.channel,
                ladder_level=level,
            )
        else:
            action = f"Warning issued ({count}/{threshold})"
            try:
                await message.reply(
                    f"⚠️ {member.mention} — **{category}** detected "
                    f"(warning **{count}/{threshold}**). At {threshold} "
                    f"active warnings you'll be timed out for "
                    f"**{helpers.format_duration(config.TIMEOUT_LADDER_MINUTES[0])}**. "
                    "Each warning expires 24h after being issued.",
                    mention_author=True,
                )
            except discord.HTTPException:
                pass

        # Every spam hit is logged to the spam channel.
        await helpers.log_to_channel(
            self, config.SPAM_LOG_CHANNEL_ID,
            helpers.spam_embed(member, category, reason, message.content,
                               message.channel, message.jump_url, action),
        )

    # ------------------------------------------------------------------
    # Enforcement primitives
    # ------------------------------------------------------------------
    async def do_timeout(self, member: discord.Member, guild: discord.Guild,
                         minutes: float, reason: str,
                         notify_channel=None, ladder_level: int = 0) -> bool:
        shown_minutes = minutes
        success = True
        try:
            await member.timeout(timedelta(minutes=minutes), reason=reason)
        except discord.Forbidden:
            success = False
            print(f"[DCRP] Cannot time out {member} — check "
                  f"'Moderate Members' permission and role hierarchy")
        except discord.HTTPException as exc:
            success = False
            print(f"[DCRP] Timeout error for {member}: {exc}")

        await helpers.log_to_channel(
            self, config.TIMEOUT_LOG_CHANNEL_ID,
            helpers.timeout_embed(member, shown_minutes, reason,
                                  ladder_level, success),
        )
        if success and config.NOTIFY_USER_IN_DM:
            try:
                await member.send(
                    f"🔇 You were timed out in **{guild.name}** for "
                    f"**{helpers.format_duration(shown_minutes)}**.\n"
                    f"Reason: {reason}")
            except discord.HTTPException:
                pass  # DMs closed — fine
        if notify_channel is not None:
            try:
                await notify_channel.send(
                    helpers.timeout_notice_text(member, shown_minutes, reason))
            except discord.HTTPException:
                pass
        return success

    async def do_kick(self, member: discord.Member, guild: discord.Guild,
                      reason: str, notify_channel=None) -> bool:
        """Kick — only ever used for repeat invite offenders. NEVER bans."""
        if config.NOTIFY_USER_IN_DM:
            try:
                await member.send(
                    f"🥾 You were kicked from **{guild.name}**.\n"
                    f"Reason: {reason}")
            except discord.HTTPException:
                pass
        success = True
        try:
            await member.kick(reason=reason)
        except discord.Forbidden:
            success = False
            print(f"[DCRP] Cannot kick {member} — check 'Kick Members' "
                  f"permission and role hierarchy")
        except discord.HTTPException as exc:
            success = False
            print(f"[DCRP] Kick error for {member}: {exc}")

        await helpers.log_to_channel(
            self, config.TIMEOUT_LOG_CHANNEL_ID,
            helpers.kick_embed(member, reason, success),
        )
        if notify_channel is not None:
            try:
                await notify_channel.send(
                    helpers.kick_notice_text(member, reason))
            except discord.HTTPException:
                pass
        return success


# ---------------------------------------------------------------------------
# Staff slash commands (require the "Moderate Members" permission)
# ---------------------------------------------------------------------------

STAFF_PERMS = discord.Permissions(moderate_members=True)

exempt_invites_group = app_commands.Group(
    name="invites-exempt",
    description="Manage approved invite links that the bot will ignore.",
    default_permissions=STAFF_PERMS,
)


async def _in_guild(interaction: discord.Interaction) -> bool:
    """Reject command usage outside a server (defensive guard)."""
    if interaction.guild_id is None:
        await interaction.response.send_message(
            "This command can only be used inside the server.",
            ephemeral=True)
        return False
    return True


@exempt_invites_group.command(name="add",
                              description="Allow an invite link (exempt from moderation).")
@app_commands.describe(link="Full invite URL or just the code.")
async def exempt_add(interaction: discord.Interaction, link: str) -> None:
    if not await _in_guild(interaction):
        return
    code = detectors.extract_code(link)
    if not code:
        await interaction.response.send_message(
            "❌ Could not extract an invite code from that.",
            ephemeral=True)
        return
    added = _bot.db.add_exempt_invite(interaction.guild_id, code,
                                      interaction.user.id)
    await interaction.response.send_message(
        f"✅ `discord.gg/{code}` {'added to' if added else 'was already in'}"
        " the exempt list. It will no longer trigger invite moderation.",
        ephemeral=True)


@exempt_invites_group.command(name="remove",
                              description="Remove an invite link from the exempt list.")
@app_commands.describe(code="The invite code to stop allowing.")
async def exempt_remove(interaction: discord.Interaction, code: str) -> None:
    if not await _in_guild(interaction):
        return
    code = detectors.extract_code(code)
    removed = _bot.db.remove_exempt_invite(interaction.guild_id, code)
    await interaction.response.send_message(
        (f"🗑️ `discord.gg/{code}` removed from the exempt list."
         if removed else
         f"ℹ️ `discord.gg/{code}` wasn't on the exempt list."),
        ephemeral=True)


@exempt_invites_group.command(name="list",
                              description="Show all staff-approved invite links.")
async def exempt_list(interaction: discord.Interaction) -> None:
    if not await _in_guild(interaction):
        return
    rows = _bot.db.list_exempt_invites(interaction.guild_id)
    if not rows:
        await interaction.response.send_message(
            "No exempt invite links configured.", ephemeral=True)
        return
    lines = [f"• `discord.gg/{r['code']}` — added <t:{int(r['added_at'])}:R>"
             for r in rows]
    await interaction.response.send_message(
        "**Exempt invite links:**\n" + "\n".join(lines[:40]),
        ephemeral=True)


@app_commands.command(name="warnings",
                      description="View a member's active warnings and offenses.")
@app_commands.describe(member="The member to inspect.")
@app_commands.default_permissions(moderate_members=True)
async def warnings_cmd(interaction: discord.Interaction,
                       member: discord.Member) -> None:
    if not await _in_guild(interaction):
        return
    db = _bot.db
    guild_id = interaction.guild_id
    active = db.active_warnings(guild_id, member.id)
    embed = helpers._base(f"📋 Moderation record — {member}", helpers.COLOR_WARN)
    if active:
        for w in active[:10]:
            ts = f"<t:{int(w['expires_at'])}:R>" if w["expires_at"] else "—"
            embed.add_field(
                name=f"⚠️ {w['category']}",
                value=f"{helpers.clip(w['reason'] or '', 200)}\nExpires: {ts}",
                inline=False,
            )
        embed.add_field(name="Active warnings",
                        value=f"{len(active)}/{config.WARNINGS_UNTIL_TIMEOUT}",
                        inline=True)
    else:
        embed.add_field(name="Active warnings", value="None ✅", inline=True)
    embed.add_field(
        name="Invite offenses",
        value=str(db.invite_offense_count(guild_id, member.id)),
        inline=True,
    )
    embed.add_field(
        name="Timeout ladder rung",
        value=str(db.timeout_level(guild_id, member.id)),
        inline=True,
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


@app_commands.command(name="clearwarnings",
                      description="Clear a member's active small warnings and Warning roles.")
@app_commands.describe(member="The member to clear.")
@app_commands.default_permissions(moderate_members=True)
async def clear_warnings_cmd(interaction: discord.Interaction,
                             member: discord.Member) -> None:
    if not await _in_guild(interaction):
        return
    removed = _bot.db.clear_small_warnings(interaction.guild_id, member.id)
    await _bot.sync_warning_roles(member, 0)
    await interaction.response.send_message(
        f"🧹 Cleared **{removed}** active warning(s) from {member.mention} "
        "and removed their Warning roles.",
        ephemeral=True)


@app_commands.command(name="mod-status",
                      description="Show the current auto-moderation configuration.")
@app_commands.default_permissions(moderate_members=True)
async def mod_status_cmd(interaction: discord.Interaction) -> None:
    if not await _in_guild(interaction):
        return
    ladder = " → ".join(helpers.format_duration(m)
                        for m in config.TIMEOUT_LADDER_MINUTES)
    embed = helpers._base(f"🛠️ {config.BOT_NAME} configuration",
                          helpers.COLOR_FOUNDER)
    embed.add_field(name="Warnings per timeout",
                    value=str(config.WARNINGS_UNTIL_TIMEOUT), inline=True)
    embed.add_field(name="Warning expiry",
                    value=f"{config.WARNING_EXPIRY_HOURS}h", inline=True)
    embed.add_field(name="Timeout ladder", value=ladder, inline=False)
    embed.add_field(name="Invite rule",
                    value=f"1st: {config.INVITE_TIMEOUT_HOURS}h timeout, "
                          "then kick (never ban)", inline=False)
    embed.add_field(name="Emoji limits",
                    value=f"≤{config.MAX_EMOJI_PER_MESSAGE}/msg same ≤"
                          f"{config.MAX_SAME_EMOJI}", inline=True)
    embed.add_field(name="Flood limit",
                    value=f"{config.FLOOD_MAX_MESSAGES} msgs /"
                          f" {config.FLOOD_WINDOW_SECONDS}s", inline=True)
    embed.add_field(name="Exempt invites",
                    value=str(len(_bot.db.list_exempt_invites(
                        interaction.guild_id))), inline=True)
    await interaction.response.send_message(embed=embed, ephemeral=True)


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

_bot = DCRPBot()


def main() -> None:
    if not config.BOT_TOKEN:
        raise SystemExit(
            "DISCORD_BOT_TOKEN is not set. Create a .env file (see "
            ".env.example) or export the variable first.")

    # Supervisor loop: transient Discord/Cloudflare gateway errors (503s and
    # friends) must never take the bot down permanently. On any unexpected
    # crash, wait with exponential backoff and come back up automatically.
    # Ctrl+C (KeyboardInterrupt) or a clean exit stops the loop.
    backoff_seconds = 5
    while True:
        try:
            _bot.run(config.BOT_TOKEN)
            print("[DCRP] Bot exited cleanly.")
            return
        except KeyboardInterrupt:
            print("[DCRP] Stopped by user (Ctrl+C).")
            return
        except Exception as exc:  # noqa: BLE001 - keep the bot alive at all costs
            print(f"[DCRP] ❌ Unexpected crash: {exc!r}")
            print(f"[DCRP] 🔁 Restarting in {backoff_seconds}s "
                  "(Ctrl+C to quit)...")
            try:
                time.sleep(backoff_seconds)
            except KeyboardInterrupt:
                print("[DCRP] Stopped by user (Ctrl+C).")
                return
            backoff_seconds = min(backoff_seconds * 2, 300)  # cap at 5 min


if __name__ == "__main__":
    main()
