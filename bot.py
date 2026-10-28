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

# Set DISCORD_TOKEN in your hosting panel. Never hard-code tokens here.
TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
GUILD_ID = int(os.getenv("GUILD_ID", "0") or 0)
DATABASE_PATH = os.getenv("DATABASE_PATH", "bot.sqlite3")
BRAND_NAME = os.getenv("BRAND_NAME", "3ncrÿp†×")
SUPPORT_ROLE_ID = int(os.getenv("SUPPORT_ROLE_ID", "0") or 0)
TICKET_CATEGORY_ID = int(os.getenv("TICKET_CATEGORY_ID", "0") or 0)
PURCHASE_CATEGORY_ID = int(os.getenv("PURCHASE_CATEGORY_ID", "1564461465786785903") or 0)
SUPPORT_CATEGORY_ID = int(os.getenv("SUPPORT_CATEGORY_ID", "1564464246002024448") or 0)
GENERAL_CATEGORY_ID = int(os.getenv("GENERAL_CATEGORY_ID", "1564881268066484284") or 0)
TICKET_LOG_CHANNEL_ID = int(os.getenv("TICKET_LOG_CHANNEL_ID", "1564881666223112302") or 0)
STATUS_CHANNEL_ID = int(os.getenv("STATUS_CHANNEL_ID", "0") or 0)
STATUS_MESSAGE_ID = int(os.getenv("STATUS_MESSAGE_ID", "1564810335871959071") or 0)
TOOL_HEALTH_URL = os.getenv("TOOL_HEALTH_URL", "").strip()
TOOL_HEALTH_TOKEN = os.getenv("TOOL_HEALTH_TOKEN", "").strip()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("encryptx-bot")
START_TIME = time.monotonic()
TICKET_TYPES = {
    "purchase": ("Purchase", "🛒", discord.Color.gold()),
    "support": ("Technical Support", "🛠️", discord.Color.blurple()),
    "general": ("General Inquiry", "💬", discord.Color.teal()),
}


def now():
    return datetime.now(timezone.utc)


def time_text(value=None):
    return (value or now()).strftime("%Y-%m-%d %H:%M UTC")


def safe_text(value, limit=1000):
    value = str(value or "Not configured")
    return value if len(value) <= limit else value[:limit - 3] + "..."


def duration_seconds(raw):
    raw = raw.strip().lower()
    units = {"d": 86400, "h": 3600, "m": 60, "s": 1}
    number, total = "", 0
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


class Database:
    def __init__(self, path):
        self.path = path

    async def initialize(self):
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        async with aiosqlite.connect(self.path) as con:
            await con.execute("""CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY, value TEXT NOT NULL)""")
            await con.execute("""CREATE TABLE IF NOT EXISTS tickets (
                channel_id INTEGER PRIMARY KEY, guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL, claimed_by INTEGER, closed INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL, ticket_type TEXT NOT NULL DEFAULT 'support',
                ticket_number INTEGER NOT NULL DEFAULT 0, closed_at TEXT, closed_by INTEGER)""")
            columns = {r[1] for r in await (await con.execute("PRAGMA table_info(tickets)")).fetchall()}
            for name, sql in {
                "ticket_type": "ALTER TABLE tickets ADD COLUMN ticket_type TEXT NOT NULL DEFAULT 'support'",
                "ticket_number": "ALTER TABLE tickets ADD COLUMN ticket_number INTEGER NOT NULL DEFAULT 0",
                "closed_at": "ALTER TABLE tickets ADD COLUMN closed_at TEXT",
                "closed_by": "ALTER TABLE tickets ADD COLUMN closed_by INTEGER",
            }.items():
                if name not in columns:
                    await con.execute(sql)
            await con.execute("""CREATE TABLE IF NOT EXISTS giveaways (
                message_id INTEGER PRIMARY KEY, guild_id INTEGER NOT NULL, channel_id INTEGER NOT NULL,
                prize TEXT NOT NULL, winner_count INTEGER NOT NULL, ends_at TEXT NOT NULL,
                ended INTEGER NOT NULL DEFAULT 0)""")
            await con.execute("""CREATE TABLE IF NOT EXISTS entries (
                message_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
                PRIMARY KEY(message_id, user_id))""")
            defaults = {
                "tool_status": "Not configured", "tool_version": "Not configured",
                "tool_last_update": "Not configured", "tool_release_notes": "No release notes yet.",
                "tool_last_check": "Never", "tool_last_success": "Never",
                "status_message_id": str(STATUS_MESSAGE_ID or 0), "ticket_counter": "0",
            }
            for k, v in defaults.items():
                await con.execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)", (k, v))
            await con.commit()

    async def get(self, key, default="Not configured"):
        async with aiosqlite.connect(self.path) as con:
            async with con.execute("SELECT value FROM settings WHERE key=?", (key,)) as cur:
                row = await cur.fetchone()
        return row[0] if row else default

    async def set(self, key, value):
        async with aiosqlite.connect(self.path) as con:
            await con.execute("INSERT INTO settings(key,value) VALUES(?,?) "
                              "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))
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

    async def next_ticket_number(self):
        async with aiosqlite.connect(self.path) as con:
            await con.execute("BEGIN IMMEDIATE")
            async with con.execute("SELECT value FROM settings WHERE key='ticket_counter'") as cur:
                row = await cur.fetchone()
            n = int(row[0] if row else 0) + 1
            await con.execute("INSERT INTO settings(key,value) VALUES('ticket_counter',?) "
                              "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(n),))
            await con.commit()
            return n


db = Database(DATABASE_PATH)


def is_admin(interaction):
    return (interaction.guild is not None and isinstance(interaction.user, discord.Member)
            and interaction.user.guild_permissions.administrator)


def is_support(member):
    return (isinstance(member, discord.Member) and
            (member.guild_permissions.administrator or member.guild_permissions.manage_channels or
             (SUPPORT_ROLE_ID and any(r.id == SUPPORT_ROLE_ID for r in member.roles))))


async def require_admin(interaction):
    if not is_admin(interaction):
        msg = "You need server administrator permissions to use this command."
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)
        return False
    return True


async def require_support(interaction):
    if not is_support(interaction.user):
        msg = "Only support staff can use this command."
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)
        return False
    return True


def category_for(guild, ticket_type):
    cid = {"purchase": PURCHASE_CATEGORY_ID, "support": SUPPORT_CATEGORY_ID,
           "general": GENERAL_CATEGORY_ID}.get(ticket_type, 0) or TICKET_CATEGORY_ID
    cat = guild.get_channel(cid) if cid else None
    return cat if isinstance(cat, discord.CategoryChannel) else None


def ticket_overwrites(guild, user):
    ow = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        user: discord.PermissionOverwrite(view_channel=True, send_messages=True,
            read_message_history=True, attach_files=True, embed_links=True),
    }
    if guild.me:
        ow[guild.me] = discord.PermissionOverwrite(view_channel=True, send_messages=True,
            read_message_history=True, manage_channels=True, manage_messages=True,
            attach_files=True, embed_links=True)
    role = guild.get_role(SUPPORT_ROLE_ID) if SUPPORT_ROLE_ID else None
    if role:
        ow[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True,
            read_message_history=True, manage_messages=True, attach_files=True, embed_links=True)
    return ow


async def send_ticket_log(guild, content, file=None):
    if not TICKET_LOG_CHANNEL_ID or guild is None:
        return False
    channel = guild.get_channel(TICKET_LOG_CHANNEL_ID)
    if channel is None:
        try:
            channel = await bot.fetch_channel(TICKET_LOG_CHANNEL_ID)
        except discord.HTTPException:
            log.warning("Could not fetch ticket log channel")
            return False
    if not isinstance(channel, discord.TextChannel):
        return False
    try:
        await channel.send(content, file=file)
        return True
    except discord.HTTPException:
        log.exception("Could not send ticket log")
        return False


class EncryptXBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.members = True
        super().__init__(command_prefix="!", intents=intents)
        self.status_message_id = STATUS_MESSAGE_ID
        self.tool_detail = "Manually managed; no health endpoint configured"

    async def setup_hook(self):
        await db.initialize()
        self.add_view(TicketPanelView())
        self.add_view(TicketControlsView())
        self.add_view(GiveawayView())
        self.add_view(StatusPanelView())
        if GUILD_ID:
            guild = discord.Object(id=GUILD_ID)
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            log.info("Synced %s commands to guild %s", len(synced), GUILD_ID)
        else:
            synced = await self.tree.sync()
            log.info("Synced %s global commands", len(synced))
        self.giveaway_loop.start()
        self.tool_loop.start()
        self.status_loop.start()

    async def on_ready(self):
        log.info("Logged in as %s", self.user)
        await self.change_presence(activity=discord.Activity(
            type=discord.ActivityType.watching, name="Encrypting."))

    async def check_tool(self):
        if not TOOL_HEALTH_URL:
            self.tool_detail = "Manual status; no health endpoint configured"
            return await db.get("tool_status"), self.tool_detail
        headers = {"Authorization": f"Bearer {TOOL_HEALTH_TOKEN}"} if TOOL_HEALTH_TOKEN else {}
        await db.set("tool_last_check", time_text())
        try:
            timeout = aiohttp.ClientTimeout(total=6)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(TOOL_HEALTH_URL, headers=headers) as resp:
                    self.tool_detail = f"Health check returned HTTP {resp.status}"
                    if 200 <= resp.status < 300:
                        await db.set("tool_last_success", time_text())
                        return "Online", self.tool_detail
                    return "Offline", self.tool_detail
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            self.tool_detail = f"Health check failed: {type(exc).__name__}"
            return "Offline", self.tool_detail

    async def make_status_embed(self, guild):
        tool_status, details = await self.check_tool()
        uptime = max(0, int(time.monotonic() - START_TIME))
        days, rem = divmod(uptime, 86400)
        hours, rem = divmod(rem, 3600)
        minutes, seconds = divmod(rem, 60)
        color = discord.Color.green() if tool_status == "Online" else (
            discord.Color.orange() if tool_status in ("Updating", "Maintenance", "Not configured")
            else discord.Color.red())
        embed = discord.Embed(title=f"{BRAND_NAME} | System Status",
            description="Live overview of the bot, community, and tool.",
            color=color, timestamp=now())
        embed.add_field(name="🤖 Discord bot",
            value=f"**Status:** 🟢 Online\n**Latency:** {round(self.latency * 1000)} ms\n"
                  f"**Uptime:** {days}d {hours}h {minutes}m {seconds}s", inline=True)
        if guild:
            embed.add_field(name="🌐 Community",
                value=f"**Server:** {safe_text(guild.name, 90)}\n"
                      f"**Members:** {guild.member_count if guild.member_count is not None else 'Unavailable'}\n"
                      f"**Boosts:** {guild.premium_subscription_count if guild.premium_subscription_count is not None else 'Unavailable'}\n"
                      f"**Boost level:** {guild.premium_tier}", inline=True)
        embed.add_field(name=f"🔐 {BRAND_NAME}",
            value=f"**Status:** {safe_text(tool_status, 60)}\n"
                  f"**Version:** {safe_text(await db.get('tool_version'), 80)}\n"
                  f"**Last update:** {safe_text(await db.get('tool_last_update'), 80)}\n"
                  f"**Last health check:** {safe_text(await db.get('tool_last_check', 'Never'), 80)}\n"
                  f"**Last successful check:** {safe_text(await db.get('tool_last_success', 'Never'), 80)}",
            inline=False)
        embed.add_field(name="📝 Release notes", value=safe_text(await db.get("tool_release_notes"), 900),
                        inline=False)
        embed.add_field(name="🔎 Health details", value=safe_text(details, 900), inline=False)
        embed.set_footer(text=f"Updated {time_text()} • Refreshes automatically")
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
            saved = int(await db.get("status_message_id", str(self.status_message_id or 0)) or 0)
            message = None
            if saved:
                try:
                    message = await channel.fetch_message(saved)
                except discord.NotFound:
                    pass
            embed = await self.make_status_embed(channel.guild)
            if message:
                await message.edit(embed=embed, view=StatusPanelView())
            else:
                message = await channel.send(embed=embed, view=StatusPanelView())
                self.status_message_id = message.id
                await db.set("status_message_id", message.id)
        except (discord.HTTPException, discord.Forbidden):
            log.exception("Could not update status panel")

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
        rows = await db.all("SELECT message_id,guild_id,channel_id,prize,winner_count,ends_at "
                            "FROM giveaways WHERE ended=0")
        for mid, gid, cid, prize, winners, ends in rows:
            try:
                end = datetime.fromisoformat(ends)
                if end.tzinfo is None:
                    end = end.replace(tzinfo=timezone.utc)
                if now() >= end:
                    await finish_giveaway(self, mid, gid, cid, prize, winners)
            except Exception:
                log.exception("Could not process giveaway %s", mid)

    @giveaway_loop.before_loop
    async def before_giveaway_loop(self):
        await self.wait_until_ready()


bot = EncryptXBot()


class RulesModal(discord.ui.Modal, title="Create a Rules Post"):
    rule_title = discord.ui.TextInput(label="Rules title", placeholder="e.g. Community Rules",
                                     max_length=200, required=True)
    rule_text = discord.ui.TextInput(label="Rules text", placeholder="Write your rules here...",
                                     style=discord.TextStyle.paragraph, max_length=3500, required=True)

    async def on_submit(self, interaction: discord.Interaction):
        title = self.rule_title.value.strip()
        body = self.rule_text.value.strip()
        if not title or not body:
            await interaction.response.send_message("Both fields are required.", ephemeral=True)
            return
        embed = discord.Embed(title=title, description=body, color=discord.Color.blurple(),
                              timestamp=now())
        embed.set_footer(text=f"{BRAND_NAME} • Rules and community guidelines")
        try:
            await interaction.channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
            await interaction.response.send_message("Rules post published in this channel.", ephemeral=True)
        except discord.HTTPException:
            await interaction.response.send_message("I couldn't publish the rules post. Check my channel permissions.", ephemeral=True)


class StatusPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Refresh status", style=discord.ButtonStyle.secondary,
                       emoji="🔄", custom_id="status:refresh")
    async def refresh_status(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        try:
            await interaction.message.edit(embed=await bot.make_status_embed(interaction.guild), view=self)
            await interaction.followup.send("Status refreshed.", ephemeral=True)
        except discord.HTTPException:
            await interaction.followup.send("Couldn't refresh the status message.", ephemeral=True)


class TicketIntakeModal(discord.ui.Modal):
    def __init__(self, ticket_type):
        self.ticket_type = ticket_type
        label = TICKET_TYPES[ticket_type][0]
        super().__init__(title=f"{label} Ticket", timeout=300)
        self.subject = discord.ui.TextInput(label="Subject", placeholder="Briefly describe what you need",
                                            max_length=100, required=True)
        self.details = discord.ui.TextInput(label="Details", placeholder="Explain your request or issue",
                                            style=discord.TextStyle.paragraph, max_length=1500, required=True)
        self.extra = discord.ui.TextInput(
            label="Order reference (optional)" if ticket_type == "purchase" else "Tool version / error code (optional)",
            placeholder="Leave blank if not applicable", max_length=100, required=False)
        self.add_item(self.subject)
        self.add_item(self.details)
        if ticket_type != "general":
            self.add_item(self.extra)

    async def on_submit(self, interaction: discord.Interaction):
        guild, user = interaction.guild, interaction.user
        if guild is None or not isinstance(user, discord.Member):
            await interaction.response.send_message("Use this inside the server.", ephemeral=True)
            return
        existing = await db.one("SELECT channel_id FROM tickets WHERE guild_id=? AND user_id=? AND closed=0",
                                (guild.id, user.id))
        if existing and guild.get_channel(existing[0]):
            await interaction.response.send_message(
                f"You already have an open ticket: <#{existing[0]}>", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            channel = await create_ticket(interaction, self.ticket_type, self.subject.value.strip(),
                                          self.details.value.strip(),
                                          self.extra.value.strip() if self.ticket_type != "general" else "")
            await interaction.followup.send(f"Your ticket is ready: {channel.mention}", ephemeral=True)
        except Exception:
            log.exception("Ticket creation failed")
            await interaction.followup.send(
                "Ticket creation failed. Check the bot's permissions and category settings.", ephemeral=True)


class TicketPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Purchase", style=discord.ButtonStyle.success, emoji="🛒",
                       custom_id="tickets:purchase")
    async def purchase(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(TicketIntakeModal("purchase"))

    @discord.ui.button(label="Technical support", style=discord.ButtonStyle.primary, emoji="🛠️",
                       custom_id="tickets:support")
    async def support(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(TicketIntakeModal("support"))

    @discord.ui.button(label="General inquiry", style=discord.ButtonStyle.secondary, emoji="💬",
                       custom_id="tickets:general")
    async def general(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(TicketIntakeModal("general"))


class TicketControlsView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Claim", style=discord.ButtonStyle.success, emoji="🧑‍💻",
                       custom_id="tickets:claim")
    async def claim(self, interaction: discord.Interaction, button: discord.ui.Button):
        row = await db.one("SELECT claimed_by,closed FROM tickets WHERE channel_id=?", (interaction.channel_id,))
        if not row or row[1]:
            await interaction.response.send_message("This ticket is closed or unrecognized.", ephemeral=True)
            return
        if not await require_support(interaction):
            return
        if row[0]:
            await interaction.response.send_message(f"Already claimed by <@{row[0]}>.", ephemeral=True)
            return
        await db.execute("UPDATE tickets SET claimed_by=? WHERE channel_id=?",
                         (interaction.user.id, interaction.channel_id))
        await interaction.response.send_message(f"Claimed by {interaction.user.mention}.")
        await send_ticket_log(interaction.guild, f"🧑‍💻 {interaction.user.mention} claimed <#{interaction.channel_id}>.")

    @discord.ui.button(label="Close ticket", style=discord.ButtonStyle.danger, emoji="🔒",
                       custom_id="tickets:close")
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button):
        row = await db.one("SELECT user_id,closed FROM tickets WHERE channel_id=?", (interaction.channel_id,))
        if not row or row[1]:
            await interaction.response.send_message("Ticket already archived or not recognized.", ephemeral=True)
            return
        if interaction.user.id != row[0] and not is_support(interaction.user):
            await interaction.response.send_message("Only the ticket owner or staff can close it.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        ok = await archive_ticket(interaction.channel, interaction.guild, interaction.user)
        await interaction.followup.send(
            "Ticket archived." + (" Transcript logged." if ok else " Configure TICKET_LOG_CHANNEL_ID for transcript delivery."),
            ephemeral=True)

    @discord.ui.button(label="Reopen", style=discord.ButtonStyle.primary, emoji="🔓",
                       custom_id="tickets:reopen")
    async def reopen(self, interaction: discord.Interaction, button: discord.ui.Button):
        row = await db.one("SELECT user_id,closed FROM tickets WHERE channel_id=?", (interaction.channel_id,))
        if not row or not row[1]:
            await interaction.response.send_message("This ticket is not archived.", ephemeral=True)
            return
        if not await require_support(interaction):
            return
        channel, guild = interaction.channel, interaction.guild
        member = guild.get_member(row[0]) if guild else None
        if member:
            await channel.set_permissions(member, view_channel=True, send_messages=True, read_message_history=True,
                                          attach_files=True, embed_links=True)
        number = await db.one("SELECT ticket_type,ticket_number FROM tickets WHERE channel_id=?", (interaction.channel_id,))
        await channel.edit(name=f"{number[0]}-{number[1]:04d}")
        await db.execute("UPDATE tickets SET closed=0,closed_at=NULL,closed_by=NULL WHERE channel_id=?",
                         (interaction.channel_id,))
        await interaction.response.send_message(f"Reopened by {interaction.user.mention}.")
        await send_ticket_log(guild, f"🔓 {interaction.user.mention} reopened <#{interaction.channel_id}>.")


async def create_ticket(interaction, ticket_type, subject, details, extra=""):
    guild, user = interaction.guild, interaction.user
    number = await db.next_ticket_number()
    label, emoji, color = TICKET_TYPES[ticket_type]
    channel = await guild.create_text_channel(
        name=f"{ticket_type}-{number:04d}", category=category_for(guild, ticket_type),
        overwrites=ticket_overwrites(guild, user),
        topic=f"{label} ticket #{number:04d} | Owner ID: {user.id}",
        reason=f"{label} ticket opened by {user}")
    try:
        await db.execute("INSERT INTO tickets(channel_id,guild_id,user_id,created_at,ticket_type,ticket_number) "
                         "VALUES(?,?,?,?,?,?)",
                         (channel.id, guild.id, user.id, time_text(), ticket_type, number))
        embed = discord.Embed(title=f"{emoji} {label} Ticket #{number:04d}",
            description=f"Welcome {user.mention}. Staff will help you as soon as possible.\n\n"
                        f"**Subject**\n{safe_text(subject,300)}\n\n**Details**\n{safe_text(details,1200)}",
            color=color, timestamp=now())
        if extra:
            embed.add_field(name="Order reference" if ticket_type == "purchase" else "Tool version / error code",
                            value=safe_text(extra,500), inline=False)
        embed.add_field(name="Owner", value=f"{user.mention}\n`{user.id}`", inline=True)
        embed.add_field(name="Status", value="🟡 Open • Waiting for staff", inline=True)
        embed.set_footer(text="Never share passwords, recovery codes, or payment-card details.")
        content = user.mention + (f" <@&{SUPPORT_ROLE_ID}>" if SUPPORT_ROLE_ID else "")
        await channel.send(content=content, embed=embed, view=TicketControlsView(),
                           allowed_mentions=discord.AllowedMentions(users=True, roles=bool(SUPPORT_ROLE_ID)))
        await send_ticket_log(guild, f"🎫 Opened **{label} #{number:04d}**: {channel.mention} by {user.mention}.")
        return channel
    except Exception:
        await channel.delete(reason="Ticket setup failed")
        raise


async def archive_ticket(channel, guild, actor):
    row = await db.one("SELECT user_id,ticket_type,ticket_number FROM tickets WHERE channel_id=? AND closed=0",
                       (channel.id,))
    if not row:
        return False
    user_id, kind, number = row
    logged = False
    try:
        lines = [f"Transcript #{channel.name} | Channel {channel.id} | Generated {time_text()}", "-" * 70]
        async for msg in channel.history(limit=1000, oldest_first=True):
            content = msg.clean_content or ""
            if msg.attachments:
                content += " Attachments: " + ", ".join(a.url for a in msg.attachments)
            if msg.embeds:
                content += " [Embed(s) included]"
            lines.append(f"[{time_text(msg.created_at)}] {msg.author} ({msg.author.id}): {content}")
        file = discord.File(io.BytesIO("\n".join(lines).encode("utf-8", errors="replace")),
                            filename=f"{kind}-{number:04d}-transcript.txt")
        logged = await send_ticket_log(guild, f"📁 Archived **{kind} #{number:04d}** by {actor.mention}.", file=file)
    except (discord.Forbidden, discord.HTTPException):
        log.exception("Transcript generation failed")
    await db.execute("UPDATE tickets SET closed=1,closed_at=?,closed_by=? WHERE channel_id=?",
                     (time_text(), actor.id, channel.id))
    member = guild.get_member(user_id) if guild else None
    try:
        if member:
            await channel.set_permissions(member, view_channel=False, send_messages=False)
        await channel.edit(name=f"closed-{kind}-{number:04d}",
                           topic=f"Archived ticket #{number:04d} | Owner ID: {user_id}")
        await channel.send(f"🔒 Archived by {actor.mention}. Support staff can reopen with the Reopen button.")
    except discord.HTTPException:
        log.exception("Could not archive channel %s", channel.id)
    return logged


@bot.tree.command(name="ticket-panel", description="Post the public ticket panel.")
@app_commands.default_permissions(administrator=True)
async def ticket_panel(interaction: discord.Interaction):
    if not await require_admin(interaction):
        return
    embed = discord.Embed(title=f"{BRAND_NAME} | Support Center",
        description="Choose a private ticket type:\n\n🛒 **Purchase** — pricing and orders\n"
                    "🛠️ **Technical support** — setup and troubleshooting\n"
                    "💬 **General inquiry** — other questions",
        color=discord.Color.blurple())
    embed.set_footer(text="Never share passwords, recovery codes, or payment-card details.")
    await interaction.channel.send(embed=embed, view=TicketPanelView())
    await interaction.response.send_message("Ticket panel posted.", ephemeral=True)


@bot.tree.command(name="ticket-add-user", description="Add a member to the current ticket.")
@app_commands.describe(member="Member to grant access to")
async def ticket_add_user(interaction: discord.Interaction, member: discord.Member):
    if not await require_support(interaction):
        return
    row = await db.one("SELECT closed FROM tickets WHERE channel_id=?", (interaction.channel_id,))
    if not row:
        await interaction.response.send_message("Use this inside a ticket channel.", ephemeral=True)
        return
    if row[0]:
        await interaction.response.send_message("Reopen the ticket first.", ephemeral=True)
        return
    await interaction.channel.set_permissions(member, view_channel=True, send_messages=True,
        read_message_history=True, attach_files=True, embed_links=True)
    await interaction.response.send_message(f"Added {member.mention} to this ticket.")
    await send_ticket_log(interaction.guild, f"➕ {interaction.user.mention} added {member.mention} to <#{interaction.channel_id}>.")


@bot.tree.command(name="ticket-remove-user", description="Remove a member from the current ticket.")
@app_commands.describe(member="Member to remove from the ticket")
async def ticket_remove_user(interaction: discord.Interaction, member: discord.Member):
    if not await require_support(interaction):
        return
    row = await db.one("SELECT user_id,closed FROM tickets WHERE channel_id=?", (interaction.channel_id,))
    if not row:
        await interaction.response.send_message("Use this inside a ticket channel.", ephemeral=True)
        return
    if member.id == row[0]:
        await interaction.response.send_message("You cannot remove the ticket owner.", ephemeral=True)
        return
    await interaction.channel.set_permissions(member, overwrite=None)
    await interaction.response.send_message(f"Removed {member.mention} from this ticket.")
    await send_ticket_log(interaction.guild, f"➖ {interaction.user.mention} removed {member.mention} from <#{interaction.channel_id}>.")


@bot.tree.command(name="ticket-rename", description="Rename the current ticket.")
@app_commands.describe(name="New channel name")
async def ticket_rename(interaction: discord.Interaction, name: str):
    if not await require_support(interaction):
        return
    row = await db.one("SELECT closed FROM tickets WHERE channel_id=?", (interaction.channel_id,))
    if not row:
        await interaction.response.send_message("Use this inside a ticket channel.", ephemeral=True)
        return
    name = "-".join(name.lower().split())[:90]
    if not name:
        await interaction.response.send_message("Enter a valid name.", ephemeral=True)
        return
    await interaction.channel.edit(name=name, reason=f"Ticket renamed by {interaction.user}")
    await interaction.response.send_message(f"Ticket renamed to **{name}**.", ephemeral=True)


@bot.tree.command(name="ticket-stats", description="View ticket system statistics.")
async def ticket_stats(interaction: discord.Interaction):
    if not await require_support(interaction):
        return
    total = (await db.one("SELECT COUNT(*) FROM tickets WHERE guild_id=?", (interaction.guild_id,)))[0]
    opened = (await db.one("SELECT COUNT(*) FROM tickets WHERE guild_id=? AND closed=0", (interaction.guild_id,)))[0]
    closed = (await db.one("SELECT COUNT(*) FROM tickets WHERE guild_id=? AND closed=1", (interaction.guild_id,)))[0]
    embed = discord.Embed(title="Ticket statistics", color=discord.Color.blurple())
    embed.add_field(name="Total", value=str(total), inline=True)
    embed.add_field(name="Open", value=str(opened), inline=True)
    embed.add_field(name="Archived", value=str(closed), inline=True)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="rules", description="Create a rules post using a popup form.")
async def rules_command(interaction: discord.Interaction):
    if not isinstance(interaction.user, discord.Member) or not (
        interaction.user.guild_permissions.manage_guild or interaction.user.guild_permissions.administrator
    ):
        await interaction.response.send_message("You need Manage Server permission to publish rules.", ephemeral=True)
        return
    await interaction.response.send_modal(RulesModal())


@bot.tree.command(name="status", description="View bot, server, and tool status.")
async def status_command(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    await interaction.followup.send(embed=await bot.make_status_embed(interaction.guild), ephemeral=True)


@bot.tree.command(name="status-panel", description="Create or update the persistent public status panel.")
@app_commands.default_permissions(administrator=True)
async def status_panel(interaction: discord.Interaction):
    if not await require_admin(interaction):
        return
    if not isinstance(interaction.channel, discord.TextChannel):
        await interaction.response.send_message("Run this in a text channel.", ephemeral=True)
        return
    saved = int(await db.get("status_message_id", "0") or 0)
    message = None
    channel = bot.get_channel(STATUS_CHANNEL_ID) if STATUS_CHANNEL_ID else interaction.channel
    if saved and channel:
        try:
            message = await channel.fetch_message(saved)
        except discord.HTTPException:
            pass
    if message:
        await message.edit(embed=await bot.make_status_embed(interaction.guild), view=StatusPanelView())
    else:
        message = await interaction.channel.send(embed=await bot.make_status_embed(interaction.guild),
                                                 view=StatusPanelView())
        await db.set("status_message_id", message.id)
        bot.status_message_id = message.id
    await interaction.response.send_message(f"Status panel ready: {message.jump_url}", ephemeral=True)


@bot.tree.command(name="tool-version", description="Set the displayed tool version.")
@app_commands.default_permissions(administrator=True)
async def tool_version(interaction: discord.Interaction, version: str):
    if not await require_admin(interaction):
        return
    version = version.strip()
    if not version or len(version) > 50:
        await interaction.response.send_message("Version must be 1–50 characters.", ephemeral=True)
        return
    await db.set("tool_version", version)
    await interaction.response.send_message(f"Tool version set to **{version}**.", ephemeral=True)


@bot.tree.command(name="tool-update", description="Record a tool update.")
@app_commands.default_permissions(administrator=True)
async def tool_update(interaction: discord.Interaction, date: str, notes: str):
    if not await require_admin(interaction):
        return
    try:
        datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        await interaction.response.send_message("Use YYYY-MM-DD for the date.", ephemeral=True)
        return
    if len(notes) > 500:
        await interaction.response.send_message("Release notes must be 500 characters or fewer.", ephemeral=True)
        return
    await db.set("tool_last_update", date)
    await db.set("tool_release_notes", notes)
    await interaction.response.send_message(f"Update recorded for **{date}**.", ephemeral=True)


@bot.tree.command(name="tool-status", description="Manually set the displayed tool status.")
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
    await interaction.response.send_message(f"Tool status set to **{status.value}**.", ephemeral=True)


@bot.tree.command(name="tool-info", description="View saved tool information.")
@app_commands.default_permissions(administrator=True)
async def tool_info(interaction: discord.Interaction):
    if not await require_admin(interaction):
        return
    await interaction.response.send_message(
        f"**Status:** {await db.get('tool_status')}\n**Version:** {await db.get('tool_version')}\n"
        f"**Last update:** {await db.get('tool_last_update')}\n**Release notes:** {await db.get('tool_release_notes')}\n"
        f"**Last check:** {await db.get('tool_last_check','Never')}\n**Last successful check:** {await db.get('tool_last_success','Never')}",
        ephemeral=True)


@bot.tree.command(name="tool-check", description="Check the configured tool health URL.")
@app_commands.default_permissions(administrator=True)
async def tool_check(interaction: discord.Interaction):
    if not await require_admin(interaction):
        return
    status, detail = await bot.check_tool()
    await interaction.response.send_message(f"**Tool status:** {status}\n**Details:** {detail}", ephemeral=True)


class GiveawayView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Enter Giveaway", style=discord.ButtonStyle.success, emoji="🎉",
                       custom_id="giveaway:enter")
    async def enter(self, interaction: discord.Interaction, button: discord.ui.Button):
        row = await db.one("SELECT ended FROM giveaways WHERE message_id=?", (interaction.message.id,))
        if not row or row[0]:
            await interaction.response.send_message("This giveaway has ended.", ephemeral=True)
            return
        try:
            await db.execute("INSERT INTO entries(message_id,user_id) VALUES(?,?)",
                             (interaction.message.id, interaction.user.id))
            await interaction.response.send_message("You're entered. Good luck!", ephemeral=True)
        except Exception:
            await interaction.response.send_message("You're already entered.", ephemeral=True)


async def finish_giveaway(bot_instance, message_id, guild_id, channel_id, prize, winner_count):
    row = await db.one("SELECT ended FROM giveaways WHERE message_id=?", (message_id,))
    if not row or row[0]:
        return
    entries = await db.all("SELECT user_id FROM entries WHERE message_id=?", (message_id,))
    participants = [r[0] for r in entries]
    winners = random.sample(participants, min(winner_count, len(participants))) if participants else []
    await db.execute("UPDATE giveaways SET ended=1 WHERE message_id=?", (message_id,))
    channel = bot_instance.get_channel(channel_id)
    if channel is None:
        try:
            channel = await bot_instance.fetch_channel(channel_id)
        except discord.HTTPException:
            return
    if winners:
        await channel.send(f"🎉 Giveaway ended! **{safe_text(prize,200)}** winner(s): " +
                           ", ".join(f"<@{uid}>" for uid in winners))
    else:
        await channel.send(f"Giveaway ended for **{safe_text(prize,200)}**, but nobody entered.")
    try:
        msg = await channel.fetch_message(message_id)
        embed = msg.embeds[0] if msg.embeds else discord.Embed()
        embed.title = "🎉 Giveaway ended"
        embed.color = discord.Color.dark_grey()
        await msg.edit(embed=embed, view=None)
    except discord.HTTPException:
        pass


giveaway_group = app_commands.Group(name="giveaway", description="Manage giveaways.")


@giveaway_group.command(name="start", description="Start a giveaway.")
@app_commands.describe(prize="Giveaway prize", duration="Duration like 10m, 2h, or 1d", winners="Number of winners")
async def giveaway_start(interaction: discord.Interaction, prize: str, duration: str,
                         winners: app_commands.Range[int, 1, 20] = 1):
    if not await require_admin(interaction):
        return
    if not interaction.guild or not interaction.channel:
        await interaction.response.send_message("Use this inside a server channel.", ephemeral=True)
        return
    try:
        seconds = duration_seconds(duration)
    except ValueError as exc:
        await interaction.response.send_message(str(exc), ephemeral=True)
        return
    end = now() + timedelta(seconds=seconds)
    embed = discord.Embed(title="🎉 Giveaway!",
        description=f"**Prize:** {safe_text(prize,200)}\n**Winners:** {winners}\n"
                    f"**Ends:** <t:{int(end.timestamp())}:R>\n\nClick Enter Giveaway to participate.",
        color=discord.Color.gold())
    message = await interaction.channel.send(embed=embed, view=GiveawayView())
    await db.execute("INSERT INTO giveaways(message_id,guild_id,channel_id,prize,winner_count,ends_at,ended) "
                     "VALUES(?,?,?,?,?,?,0)",
                     (message.id, interaction.guild.id, interaction.channel.id, prize, winners, end.isoformat()))
    await interaction.response.send_message(f"Giveaway created: {message.jump_url}", ephemeral=True)


@giveaway_group.command(name="end", description="End a giveaway early.")
@app_commands.describe(message_id="Giveaway message ID")
async def giveaway_end(interaction: discord.Interaction, message_id: str):
    if not await require_admin(interaction):
        return
    try:
        mid = int(message_id)
    except ValueError:
        await interaction.response.send_message("Provide the giveaway message ID.", ephemeral=True)
        return
    row = await db.one("SELECT guild_id,channel_id,prize,winner_count FROM giveaways WHERE message_id=? AND ended=0",
                       (mid,))
    if not row:
        await interaction.response.send_message("No active giveaway found with that ID.", ephemeral=True)
        return
    await finish_giveaway(bot, mid, row[0], row[1], row[2], row[3])
    await interaction.response.send_message("Giveaway ended.", ephemeral=True)


@giveaway_group.command(name="reroll", description="Pick a new giveaway winner.")
@app_commands.describe(message_id="Giveaway message ID")
async def giveaway_reroll(interaction: discord.Interaction, message_id: str):
    if not await require_admin(interaction):
        return
    try:
        mid = int(message_id)
    except ValueError:
        await interaction.response.send_message("Provide the giveaway message ID.", ephemeral=True)
        return
    row = await db.one("SELECT channel_id,prize,ended FROM giveaways WHERE message_id=?", (mid,))
    if not row or not row[2]:
        await interaction.response.send_message("That giveaway doesn't exist or hasn't ended.", ephemeral=True)
        return
    entries = await db.all("SELECT user_id FROM entries WHERE message_id=?", (mid,))
    if not entries:
        await interaction.response.send_message("No participants entered.", ephemeral=True)
        return
    winner = random.choice([r[0] for r in entries])
    channel = bot.get_channel(row[0])
    if channel:
        await channel.send(f"🎉 Reroll for **{safe_text(row[1],200)}**: <@{winner}> wins!")
    await interaction.response.send_message("New winner selected.", ephemeral=True)


bot.tree.add_command(giveaway_group)


@bot.tree.error
async def command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    log.error("Slash command error: %s", error)
    msg = "The command failed. Please try again or contact an administrator."
    try:
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)
    except discord.HTTPException:
        pass


if __name__ == "__main__":
    if not TOKEN:
        raise RuntimeError("DISCORD_TOKEN is missing. Set it in your host environment variables; never hard-code it.")
    bot.run(TOKEN)
