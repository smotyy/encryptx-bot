
import os
import io
import time
import random
import asyncio
import logging
from datetime import datetime, timezone, timedelta

import aiohttp
import aiosqlite
import discord
from discord import app_commands
from discord.ext import commands, tasks
from dotenv import load_dotenv

load_dotenv()

# ---------- CONFIGURATION ----------

TOKEN = os.getenv("DISCORD_TOKEN", "MTU2NDgwNjAzNjQ0NjI1NzI2Mw.Gah2QQ.nwhOJPPdTRRV30NAuoOB79QgTkm2k_PXnrZhWM")
GUILD_ID = int(os.getenv("GUILD_ID", "1564461463613997086") or 0)
DATABASE_PATH = os.getenv("DATABASE_PATH", "bot.sqlite3")
BRAND_NAME = os.getenv("BRAND_NAME", "Decryption Tool")

SUPPORT_ROLE_ID = int(os.getenv("SUPPORT_ROLE_ID", "0") or 0)
TICKET_CATEGORY_ID = int(os.getenv("TICKET_CATEGORY_ID", "0") or 0)
TICKET_LOG_CHANNEL_ID = int(os.getenv("TICKET_LOG_CHANNEL_ID", "0") or 0)

STATUS_CHANNEL_ID = int(os.getenv("STATUS_CHANNEL_ID", "0") or 0)
STATUS_MESSAGE_ID = int(os.getenv("STATUS_MESSAGE_ID", "0") or 0)

TOOL_HEALTH_URL = os.getenv("TOOL_HEALTH_URL", "").strip()
TOOL_HEALTH_TOKEN = os.getenv("TOOL_HEALTH_TOKEN", "").strip()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("decryption-bot")
START_TIME = time.monotonic()


def now():
    return datetime.now(timezone.utc)


def time_text(value=None):
    return (value or now()).strftime("%Y-%m-%d %H:%M UTC")


def duration_seconds(raw):
    """Accept 30s, 10m, 2h, 1d, or combinations such as 1d2h."""
    raw = raw.strip().lower()
    units = {"d": 86400, "h": 3600, "m": 60, "s": 1}
    number = ""
    total = 0

    for char in raw:
        if char.isdigit():
            number += char
        elif char in units and number:
            total += int(number) * units[char]
            number = ""
        else:
            raise ValueError("Use a duration such as 10m, 2h, or 1d.")

    if number or total < 30 or total > 30 * 86400:
        raise ValueError("Duration must be between 30 seconds and 30 days.")

    return total


# ---------- DATABASE ----------

class Database:
    def __init__(self, path):
        self.path = path

    async def initialize(self):
        async with aiosqlite.connect(self.path) as con:
            await con.execute("""
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
            """)
            await con.execute("""
                CREATE TABLE IF NOT EXISTS tickets (
                    channel_id INTEGER PRIMARY KEY,
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    claimed_by INTEGER,
                    closed INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                )
            """)
            await con.execute("""
                CREATE TABLE IF NOT EXISTS giveaways (
                    message_id INTEGER PRIMARY KEY,
                    guild_id INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL,
                    prize TEXT NOT NULL,
                    winner_count INTEGER NOT NULL,
                    ends_at TEXT NOT NULL,
                    ended INTEGER NOT NULL DEFAULT 0
                )
            """)
            await con.execute("""
                CREATE TABLE IF NOT EXISTS entries (
                    message_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    PRIMARY KEY (message_id, user_id)
                )
            """)

            defaults = {
                "tool_status": "Not configured",
                "tool_version": "Not configured",
                "tool_last_update": "Not configured",
                "tool_release_notes": "No release notes yet.",
                "tool_last_check": "Never",
                "tool_last_success": "Never",
            }

            for key, value in defaults.items():
                await con.execute(
                    "INSERT OR IGNORE INTO settings(key, value) VALUES(?, ?)",
                    (key, value),
                )
            await con.commit()

    async def get(self, key, default="Not configured"):
        async with aiosqlite.connect(self.path) as con:
            async with con.execute(
                "SELECT value FROM settings WHERE key = ?", (key,)
            ) as cur:
                row = await cur.fetchone()
        return row[0] if row else default

    async def set(self, key, value):
        async with aiosqlite.connect(self.path) as con:
            await con.execute(
                "INSERT INTO settings(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, str(value)),
            )
            await con.commit()

    async def execute(self, sql, args=()):
        async with aiosqlite.connect(self.path) as con:
            cur = await con.execute(sql, args)
            await con.commit()
            return cur.lastrowid

    async def one(self, sql, args=()):
        async with aiosqlite.connect(self.path) as con:
            async with con.execute(sql, args) as cur:
                return await cur.fetchone()

    async def all(self, sql, args=()):
        async with aiosqlite.connect(self.path) as con:
            async with con.execute(sql, args) as cur:
                return await cur.fetchall()


db = Database(DATABASE_PATH)


# ---------- PERMISSIONS ----------

def is_admin(interaction):
    return (
        interaction.guild is not None
        and isinstance(interaction.user, discord.Member)
        and interaction.user.guild_permissions.administrator
    )


async def require_admin(interaction):
    if not is_admin(interaction):
        await interaction.response.send_message(
            "You need server administrator permissions to use this command.",
            ephemeral=True,
        )
        return False
    return True


def is_support(member):
    if not isinstance(member, discord.Member):
        return False

    return (
        member.guild_permissions.administrator
        or member.guild_permissions.manage_channels
        or (
            SUPPORT_ROLE_ID != 0
            and any(role.id == SUPPORT_ROLE_ID for role in member.roles)
        )
    )


# ---------- BOT ----------

class DecryptionBot(commands.Bot):
    def __init__(self):
        super().__init__(
            command_prefix="!",
            intents=discord.Intents.default(),
        )
        self.status_message_id = STATUS_MESSAGE_ID
        self.tool_detail = "No health endpoint configured"

    async def setup_hook(self):
        await db.initialize()

        self.add_view(TicketPanelView())
        self.add_view(TicketControlsView())
        self.add_view(GiveawayView())

        if GUILD_ID:
            guild = discord.Object(id=GUILD_ID)
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            log.info("Synced %s commands to server %s", len(synced), GUILD_ID)
        else:
            synced = await self.tree.sync()
            log.info("Synced %s global commands", len(synced))

        self.giveaway_loop.start()
        self.tool_loop.start()
        self.status_loop.start()

    async def on_ready(self):
        log.info("Connected as %s", self.user)
        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name="/status",
            )
        )

    async def check_tool(self):
        # Without a health URL, use the manually configured Discord status.
        if not TOOL_HEALTH_URL:
            status = await db.get("tool_status")
            self.tool_detail = "Manual status; no health endpoint configured"
            return status, self.tool_detail

        headers = {}
        if TOOL_HEALTH_TOKEN:
            headers["Authorization"] = f"Bearer {TOOL_HEALTH_TOKEN}"

        await db.set("tool_last_check", time_text())

        try:
            timeout = aiohttp.ClientTimeout(total=5)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(
                    TOOL_HEALTH_URL, headers=headers
                ) as response:
                    if 200 <= response.status < 300:
                        await db.set("tool_last_success", time_text())
                        self.tool_detail = f"Health check returned HTTP {response.status}"
                        return "Online", self.tool_detail

                    self.tool_detail = f"Health check returned HTTP {response.status}"
                    return "Offline", self.tool_detail

        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            self.tool_detail = f"Health check failed: {type(exc).__name__}"
            return "Offline", self.tool_detail

    async def make_status_embed(self, guild):
        tool_status, tool_detail = await self.check_tool()

        uptime = int(time.monotonic() - START_TIME)
        days, rem = divmod(uptime, 86400)
        hours, rem = divmod(rem, 3600)
        minutes, seconds = divmod(rem, 60)

        color = (
            discord.Color.green()
            if tool_status == "Online"
            else discord.Color.orange()
        )

        embed = discord.Embed(
            title=f"{BRAND_NAME} — System Status",
            description="Current bot, server, and tool information.",
            color=color,
            timestamp=now(),
        )

        embed.add_field(
            name="Bot Status",
            value=(
                "🟢 **Online**\n"
                f"**Latency:** {round(self.latency * 1000)} ms\n"
                f"**Uptime:** {days}d {hours}h {minutes}m {seconds}s"
            ),
            inline=False,
        )

        if guild:
            boosts = guild.premium_subscription_count
            embed.add_field(
                name="Server Status",
                value=(
                    f"**Server:** {guild.name}\n"
                    f"**Members:** {guild.member_count if guild.member_count is not None else 'Unavailable'}\n"
                    f"**Boosts:** {boosts if boosts is not None else 'Unavailable'}\n"
                    f"**Boost Level:** {guild.premium_tier}"
                ),
                inline=False,
            )

        embed.add_field(
            name="3ncrÿp†×",
            value=(
                f"**Status:** {tool_status}\n"
                f"**Version:** {await db.get('tool_version')}\n"
                f"**Last Update:** {await db.get('tool_last_update')}\n"
                f"**Release Notes:** {await db.get('tool_release_notes')}\n"
                f"**Last Health Check:** {await db.get('tool_last_check', 'Never')}\n"
                f"**Last Successful Check:** {await db.get('tool_last_success', 'Never')}\n"
                f"**Details:** {tool_detail}"
            )[:1024],
            inline=False,
        )

        embed.set_footer(text=f"Generated {time_text()}")
        return embed

    @tasks.loop(seconds=60)
    async def status_loop(self):
        if not STATUS_CHANNEL_ID:
            return

        try:
            channel = self.get_channel(STATUS_CHANNEL_ID)
            if channel is None:
                channel = await self.fetch_channel(STATUS_CHANNEL_ID)

            if not isinstance(channel, discord.TextChannel):
                return

            embed = await self.make_status_embed(channel.guild)

            message = None
            if self.status_message_id:
                try:
                    message = await channel.fetch_message(self.status_message_id)
                except discord.NotFound:
                    message = None

            if message:
                await message.edit(embed=embed)
            else:
                message = await channel.send(embed=embed)
                self.status_message_id = message.id
                log.info(
                    "Status panel created with message ID %s. "
                    "Set STATUS_MESSAGE_ID in Railway to reuse it after restarts.",
                    message.id,
                )

        except (discord.HTTPException, discord.Forbidden):
            log.exception("Could not update the status panel")

    @status_loop.before_loop
    async def before_status_loop(self):
        await self.wait_until_ready()

    @tasks.loop(seconds=60)
    async def tool_loop(self):
        await self.check_tool()

    @tool_loop.before_loop
    async def before_tool_loop(self):
        await self.wait_until_ready()

    @tasks.loop(seconds=15)
    async def giveaway_loop(self):
        rows = await db.all(
            "SELECT message_id, guild_id, channel_id, prize, winner_count, ends_at "
            "FROM giveaways WHERE ended = 0"
        )

        for message_id, guild_id, channel_id, prize, winner_count, ends_at in rows:
            try:
                end_time = datetime.fromisoformat(ends_at)
                if end_time.tzinfo is None:
                    end_time = end_time.replace(tzinfo=timezone.utc)

                if now() >= end_time:
                    await finish_giveaway(
                        self, message_id, guild_id, channel_id, prize, winner_count
                    )
            except Exception:
                log.exception("Could not process giveaway %s", message_id)

    @giveaway_loop.before_loop
    async def before_giveaway_loop(self):
        await self.wait_until_ready()


bot = DecryptionBot()


# ---------- TICKETS ----------

def ticket_overwrites(guild, user):
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        user: discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            read_message_history=True,
            attach_files=True,
            embed_links=True,
        ),
    }

    if guild.me:
        overwrites[guild.me] = discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            read_message_history=True,
            manage_channels=True,
            attach_files=True,
            embed_links=True,
        )

    if SUPPORT_ROLE_ID:
        role = guild.get_role(SUPPORT_ROLE_ID)
        if role:
            overwrites[role] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                manage_messages=True,
                attach_files=True,
                embed_links=True,
            )

    return overwrites


class TicketPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Create Ticket",
        style=discord.ButtonStyle.primary,
        emoji="🎫",
        custom_id="tickets:create",
    )
    async def create_ticket(self, interaction, button):
        guild = interaction.guild

        if guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                "Use this button inside the server.", ephemeral=True
            )
            return

        existing = await db.one(
            "SELECT channel_id FROM tickets WHERE guild_id = ? AND user_id = ? AND closed = 0",
            (guild.id, interaction.user.id),
        )

        if existing:
            channel = guild.get_channel(existing[0])
            if channel:
                await interaction.response.send_message(
                    f"You already have an open ticket: {channel.mention}",
                    ephemeral=True,
                )
                return

        category = (
            guild.get_channel(TICKET_CATEGORY_ID)
            if TICKET_CATEGORY_ID else None
        )
        if not isinstance(category, discord.CategoryChannel):
            category = None

        safe_name = "".join(
            c for c in interaction.user.name.lower() if c.isalnum() or c == "-"
        )[:40] or "user"

        channel = await guild.create_text_channel(
            name=f"ticket-{safe_name}",
            category=category,
            overwrites=ticket_overwrites(guild, interaction.user),
            reason=f"Support ticket opened by {interaction.user}",
        )

        await db.execute(
            "INSERT OR REPLACE INTO tickets "
            "(channel_id, guild_id, user_id, claimed_by, closed, created_at) "
            "VALUES (?, ?, ?, NULL, 0, ?)",
            (channel.id, guild.id, interaction.user.id, time_text()),
        )

        embed = discord.Embed(
            title="Support Ticket",
            description=(
                f"Hello {interaction.user.mention}!\n"
                "Please describe your issue. A support team member will help you soon."
            ),
            color=discord.Color.blurple(),
        )

        await channel.send(
            content=interaction.user.mention,
            embed=embed,
            view=TicketControlsView(),
        )
        await interaction.response.send_message(
            f"Your ticket has been created: {channel.mention}",
            ephemeral=True,
        )


class TicketControlsView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Claim",
        style=discord.ButtonStyle.success,
        emoji="🧑‍💻",
        custom_id="tickets:claim",
    )
    async def claim_ticket(self, interaction, button):
        row = await db.one(
            "SELECT claimed_by, closed FROM tickets WHERE channel_id = ?",
            (interaction.channel_id,),
        )

        if not row or row[1]:
            await interaction.response.send_message(
                "This ticket is not open.", ephemeral=True
            )
            return

        if not is_support(interaction.user):
            await interaction.response.send_message(
                "Only support staff can claim tickets.", ephemeral=True
            )
            return

        if row[0]:
            await interaction.response.send_message(
                f"This ticket is already claimed by <@{row[0]}>.",
                ephemeral=True,
            )
            return

        await db.execute(
            "UPDATE tickets SET claimed_by = ? WHERE channel_id = ?",
            (interaction.user.id, interaction.channel_id),
        )
        await interaction.response.send_message(
            f"Ticket claimed by {interaction.user.mention}."
        )

    @discord.ui.button(
        label="Close Ticket",
        style=discord.ButtonStyle.danger,
        emoji="🔒",
        custom_id="tickets:close",
    )
    async def close_ticket_button(self, interaction, button):
        row = await db.one(
            "SELECT user_id, closed FROM tickets WHERE channel_id = ?",
            (interaction.channel_id,),
        )

        if not row or row[1]:
            await interaction.response.send_message(
                "This ticket is already closed or was not created by the bot.",
                ephemeral=True,
            )
            return

        is_owner = interaction.user.id == row[0]
        if not (is_owner or is_support(interaction.user)):
            await interaction.response.send_message(
                "Only the ticket creator or support staff can close this ticket.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        await close_ticket(interaction.channel, interaction.guild)
        await interaction.followup.send(
            "Ticket closed. A transcript will be saved if logging is configured.",
            ephemeral=True,
        )


async def close_ticket(channel, guild):
    try:
        lines = [f"Transcript for #{channel.name}", ""]
        async for message in channel.history(limit=500, oldest_first=True):
            content = message.content or ""
            if message.attachments:
                content += " " + " ".join(a.url for a in message.attachments)
            lines.append(
                f"[{time_text(message.created_at)}] {message.author}: {content}"
            )

        if TICKET_LOG_CHANNEL_ID:
            log_channel = guild.get_channel(TICKET_LOG_CHANNEL_ID)
            if log_channel is None:
                log_channel = await bot.fetch_channel(TICKET_LOG_CHANNEL_ID)

            transcript = discord.File(
                io.BytesIO("\n".join(lines).encode("utf-8")),
                filename=f"{channel.name}-transcript.txt",
            )
            await log_channel.send(
                f"Transcript for **{channel.name}**.",
                file=transcript,
            )

    except (discord.Forbidden, discord.HTTPException):
        log.exception("Could not create or send ticket transcript")

    await db.execute(
        "UPDATE tickets SET closed = 1 WHERE channel_id = ?", (channel.id,)
    )
    await asyncio.sleep(3)

    try:
        await channel.delete(reason="Ticket closed")
    except discord.HTTPException:
        log.exception("Could not delete ticket channel")


@bot.tree.command(name="ticket-panel", description="Post the support ticket panel.")
@app_commands.default_permissions(administrator=True)
async def ticket_panel(interaction: discord.Interaction):
    if not await require_admin(interaction):
        return

    embed = discord.Embed(
        title=f"{BRAND_NAME} Support",
        description="Click below to create a private support ticket.",
        color=discord.Color.blurple(),
    )

    await interaction.channel.send(embed=embed, view=TicketPanelView())
    await interaction.response.send_message(
        "Ticket panel posted.", ephemeral=True
    )


# ---------- TOOL MANAGEMENT ----------

@bot.tree.command(name="tool-version", description="Set the displayed tool version.")
@app_commands.default_permissions(administrator=True)
@app_commands.describe(version="Version number, e.g. 2.1.0")
async def tool_version(interaction: discord.Interaction, version: str):
    if not await require_admin(interaction):
        return

    version = version.strip()
    if not version or len(version) > 50:
        await interaction.response.send_message(
            "Version must be between 1 and 50 characters.", ephemeral=True
        )
        return

    await db.set("tool_version", version)
    await interaction.response.send_message(
        f"Tool version set to **{version}**.", ephemeral=True
    )


@bot.tree.command(name="tool-update", description="Record a tool update.")
@app_commands.default_permissions(administrator=True)
@app_commands.describe(date="Date in YYYY-MM-DD format", notes="Release notes")
async def tool_update(interaction: discord.Interaction, date: str, notes: str):
    if not await require_admin(interaction):
        return

    try:
        datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        await interaction.response.send_message(
            "Use the date format YYYY-MM-DD.", ephemeral=True
        )
        return

    if len(notes) > 500:
        await interaction.response.send_message(
            "Release notes must be 500 characters or fewer.", ephemeral=True
        )
        return

    await db.set("tool_last_update", date)
    await db.set("tool_release_notes", notes)
    await interaction.response.send_message(
        f"Tool update recorded for **{date}**.", ephemeral=True
    )


@bot.tree.command(name="tool-status", description="Set the displayed tool status.")
@app_commands.default_permissions(administrator=True)
@app_commands.choices(status=[
    app_commands.Choice(name="Online", value="Online"),
    app_commands.Choice(name="Offline", value="Offline"),
    app_commands.Choice(name="Updating", value="Updating"),
    app_commands.Choice(name="Maintenance", value="Maintenance"),
    app_commands.Choice(name="Not configured", value="Not configured"),
])
async def tool_status(interaction: discord.Interaction, status: app_commands.Choice[str]):
    if not await require_admin(interaction):
        return

    await db.set("tool_status", status.value)
    await interaction.response.send_message(
        f"Tool status set to **{status.value}**.", ephemeral=True
    )


@bot.tree.command(name="tool-info", description="View saved tool information.")
@app_commands.default_permissions(administrator=True)
async def tool_info(interaction: discord.Interaction):
    if not await require_admin(interaction):
        return

    await interaction.response.send_message(
        f"**Status:** {await db.get('tool_status')}\n"
        f"**Version:** {await db.get('tool_version')}\n"
        f"**Last update:** {await db.get('tool_last_update')}\n"
        f"**Release notes:** {await db.get('tool_release_notes')}\n"
        f"**Last health check:** {await db.get('tool_last_check', 'Never')}\n"
        f"**Last successful check:** {await db.get('tool_last_success', 'Never')}",
        ephemeral=True,
    )


@bot.tree.command(name="tool-check", description="Check the configured tool health URL.")
@app_commands.default_permissions(administrator=True)
async def tool_check(interaction: discord.Interaction):
    if not await require_admin(interaction):
        return

    status, details = await bot.check_tool()
    await interaction.response.send_message(
        f"**Tool status:** {status}\n**Details:** {details}",
        ephemeral=True,
    )


# ---------- GIVEAWAYS ----------

class GiveawayView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Enter Giveaway",
        style=discord.ButtonStyle.success,
        emoji="🎉",
        custom_id="giveaway:enter",
    )
    async def enter_giveaway(self, interaction, button):
        row = await db.one(
            "SELECT ended FROM giveaways WHERE message_id = ?",
            (interaction.message.id,),
        )

        if not row or row[0]:
            await interaction.response.send_message(
                "This giveaway has ended.", ephemeral=True
            )
            return

        try:
            await db.execute(
                "INSERT INTO entries(message_id, user_id) VALUES (?, ?)",
                (interaction.message.id, interaction.user.id),
            )
            await interaction.response.send_message(
                "You're entered in the giveaway. Good luck!", ephemeral=True
            )
        except Exception:
            await interaction.response.send_message(
                "You're already entered in this giveaway.", ephemeral=True
            )


async def finish_giveaway(message_id, channel_id, prize, winner_count):
    row = await db.one(
        "SELECT ended FROM giveaways WHERE message_id = ?", (message_id,)
    )
    if not row or row[0]:
        return

    entries = await db.all(
        "SELECT user_id FROM entries WHERE message_id = ?", (message_id,)
    )
    participants = [r[0] for r in entries]
    winners = (
        random.sample(participants, min(winner_count, len(participants)))
        if participants else []
    )

    await db.execute(
        "UPDATE giveaways SET ended = 1 WHERE message_id = ?", (message_id,)
    )

    channel = bot.get_channel(channel_id)
    if channel is None:
        try:
            channel = await bot.fetch_channel(channel_id)
        except discord.HTTPException:
            channel = None

    if channel is None:
        return

    if winners:
        mentions = ", ".join(f"<@{user_id}>" for user_id in winners)
        await channel.send(f"🎉 Giveaway ended! **{prize}** winner(s): {mentions}")
    else:
        await channel.send(
            f"Giveaway ended for **{prize}**, but nobody entered."
        )

    try:
        message = await channel.fetch_message(message_id)
        embed = message.embeds[0] if message.embeds else discord.Embed(
            title="Giveaway ended"
        )
        embed.color = discord.Color.dark_grey()
        await message.edit(embed=embed, view=None)
    except discord.HTTPException:
        pass


giveaway_group = app_commands.Group(
    name="giveaway", description="Manage giveaways."
)


@giveaway_group.command(name="start", description="Start a giveaway.")
@app_commands.default_permissions(administrator=True)
@app_commands.describe(
    prize="Giveaway prize",
    duration="Duration such as 10m, 2h, or 1d",
    winners="Number of winners",
)
async def giveaway_start(
    interaction: discord.Interaction,
    prize: str,
    duration: str,
    winners: app_commands.Range[int, 1, 20] = 1,
):
    if not await require_admin(interaction):
        return

    if interaction.guild is None or interaction.channel is None:
        await interaction.response.send_message(
            "Use this command inside a server channel.", ephemeral=True
        )
        return

    try:
        seconds = duration_seconds(duration)
    except ValueError as exc:
        await interaction.response.send_message(str(exc), ephemeral=True)
        return

    end_time = now() + timedelta(seconds=seconds)
    embed = discord.Embed(
        title="🎉 Giveaway!",
        description=(
            f"**Prize:** {prize}\n"
            f"**Winners:** {winners}\n"
            f"**Ends:** <t:{int(end_time.timestamp())}:R>\n\n"
            "Click Enter Giveaway to participate."
        ),
        color=discord.Color.gold(),
    )

    message = await interaction.channel.send(embed=embed, view=GiveawayView())
    await db.execute(
        "INSERT INTO giveaways "
        "(message_id, guild_id, channel_id, prize, winner_count, ends_at, ended) "
        "VALUES (?, ?, ?, ?, ?, ?, 0)",
        (
            message.id,
            interaction.guild.id,
            interaction.channel.id,
            prize,
            winners,
            end_time.isoformat(),
        ),
    )

    await interaction.response.send_message(
        f"Giveaway created: {message.jump_url}", ephemeral=True
    )


@giveaway_group.command(name="end", description="End a giveaway early.")
@app_commands.default_permissions(administrator=True)
@app_commands.describe(message_id="Giveaway message ID")
async def giveaway_end(interaction: discord.Interaction, message_id: str):
    if not await require_admin(interaction):
        return

    try:
        mid = int(message_id)
    except ValueError:
        await interaction.response.send_message(
            "Provide the giveaway message ID.", ephemeral=True
        )
        return

    row = await db.one(
        "SELECT channel_id, prize, winner_count FROM giveaways "
        "WHERE message_id = ? AND ended = 0",
        (mid,),
    )
    if not row:
        await interaction.response.send_message(
            "No active giveaway found with that message ID.", ephemeral=True
        )
        return

    await finish_giveaway(mid, row[0], row[1], row[2])
    await interaction.response.send_message("Giveaway ended.", ephemeral=True)


@giveaway_group.command(name="reroll", description="Pick a new giveaway winner.")
@app_commands.default_permissions(administrator=True)
@app_commands.describe(message_id="Giveaway message ID")
async def giveaway_reroll(interaction: discord.Interaction, message_id: str):
    if not await require_admin(interaction):
        return

    try:
        mid = int(message_id)
    except ValueError:
        await interaction.response.send_message(
            "Provide the giveaway message ID.", ephemeral=True
        )
        return

    row = await db.one(
        "SELECT channel_id, prize, ended FROM giveaways WHERE message_id = ?",
        (mid,),
    )
    if not row or not row[2]:
        await interaction.response.send_message(
            "That giveaway does not exist or has not ended.", ephemeral=True
        )
        return

    entries = await db.all(
        "SELECT user_id FROM entries WHERE message_id = ?", (mid,)
    )
    if not entries:
        await interaction.response.send_message(
            "No participants entered that giveaway.", ephemeral=True
        )
        return

    winner_id = random.choice([entry[0] for entry in entries])
    channel = bot.get_channel(row[0])

    if channel:
        await channel.send(
            f"🎉 Giveaway reroll for **{row[1]}**: new winner is <@{winner_id}>!"
        )

    await interaction.response.send_message(
        "A new winner was selected.", ephemeral=True
    )


bot.tree.add_command(giveaway_group)


# ---------- ERROR HANDLING ----------

@bot.tree.error
async def command_error(interaction, error):
    log.error("Slash command error: %s", error)

    message = "The command failed. Please try again or contact an administrator."
    try:
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
    except discord.HTTPException:
        pass


# ---------- START ----------

if __name__ == "__main__":
    if not TOKEN:
        raise RuntimeError(
            "DISCORD_TOKEN is missing from the environment variables."
        )

    bot.run(TOKEN)
