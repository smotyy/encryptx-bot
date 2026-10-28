
import os
import time
import sqlite3
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

TOKEN = os.getenv("DISCORD_TOKEN", "MTU2NDgwNjAzNjQ0NjI1NzI2Mw.Gah2QQ.nwhOJPPdTRRV30NAuoOB79QgTkm2k_PXnrZhWM")
GUILD_ID = os.getenv("GUILD_ID", "")
DB_PATH = os.getenv("DATABASE_PATH", "bot.sqlite3")

START_TIME = time.monotonic()

DEFAULTS = {
    "version": "Not configured",
    "last_update": "Not configured",
    "release_notes": "No release notes yet.",
    "status": "Not configured",
}


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def connect_db():
    db = sqlite3.connect(DB_PATH)
    db.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)
    for key, value in DEFAULTS.items():
        db.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
            (key, value)
        )
    db.commit()
    return db


def get_setting(key):
    with connect_db() as db:
        row = db.execute(
            "SELECT value FROM settings WHERE key = ?", (key,)
        ).fetchone()
        return row[0] if row else DEFAULTS.get(key, "Not configured")


def set_setting(key, value):
    with connect_db() as db:
        db.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value)
        )


def is_admin(interaction: discord.Interaction):
    return (
        interaction.guild is not None
        and interaction.user.guild_permissions.administrator
    )


async def require_admin(interaction: discord.Interaction):
    if not is_admin(interaction):
        await interaction.response.send_message(
            "You need server administrator permissions to use this command.",
            ephemeral=True
        )
        return False
    return True


intents = discord.Intents.default()


class StatusBot(commands.Bot):
    async def setup_hook(self):
        connect_db().close()

        if GUILD_ID:
            guild = discord.Object(id=int(GUILD_ID))
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            print(f"Synced {len(synced)} commands to server {GUILD_ID}")
        else:
            synced = await self.tree.sync()
            print(f"Synced {len(synced)} global commands")


bot = StatusBot(command_prefix="!", intents=intents)


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    await bot.change_presence(
        activity=discord.Activity(
            type=discord.ActivityType.watching,
            name="/status"
        )
    )


@bot.tree.command(name="status", description="View the system status dashboard.")
async def status(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)

    latency = round(bot.latency * 1000)
    uptime = int(time.monotonic() - START_TIME)
    days, remainder = divmod(uptime, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds = divmod(remainder, 60)

    guild = interaction.guild
    tool_status = get_setting("status")

    color = (
        discord.Color.green()
        if tool_status.lower() == "online"
        else discord.Color.orange()
    )

    embed = discord.Embed(
        title="System Status Dashboard",
        description="Live bot and server information with saved tool details.",
        color=color,
        timestamp=datetime.now(timezone.utc)
    )

    embed.add_field(
        name="Bot Status",
        value=(
            "🟢 **Online**\n"
            f"**Latency:** {latency} ms\n"
            f"**Uptime:** {days}d {hours}h {minutes}m {seconds}s"
        ),
        inline=False
    )

    if guild:
        boosts = guild.premium_subscription_count
        embed.add_field(
            name="Server Status",
            value=(
                f"**Name:** {guild.name}\n"
                f"**Members:** {guild.member_count or 'Unavailable'}\n"
                f"**Boosts:** {boosts if boosts is not None else 'Unavailable'}\n"
                f"**Boost Level:** {guild.premium_tier}"
            ),
            inline=False
        )
    else:
        embed.add_field(
            name="Server Status",
            value="Server information is unavailable in this interaction.",
            inline=False
        )

    embed.add_field(
        name="3ncrÿp†×",
        value=(
            f"**Status:** {tool_status}\n"
            f"**Version:** {get_setting('version')}\n"
            f"**Last Update:** {get_setting('last_update')}\n"
            f"**Release Notes:** {get_setting('release_notes')}"
        ),
        inline=False
    )

    embed.set_footer(text=f"Dashboard generated {now()}")
    await interaction.followup.send(embed=embed, ephemeral=True)


@bot.tree.command(
    name="tool-version",
    description="Set the displayed decryption tool version."
)
@app_commands.describe(version="Version number, for example 2.1.0")
@app_commands.default_permissions(administrator=True)
async def tool_version(interaction: discord.Interaction, version: str):
    if not await require_admin(interaction):
        return

    version = version.strip()
    if not version or len(version) > 50:
        await interaction.response.send_message(
            "Version must contain between 1 and 50 characters.",
            ephemeral=True
        )
        return

    set_setting("version", version)
    await interaction.response.send_message(
        f"Tool version updated to **{version}**.",
        ephemeral=True
    )


@bot.tree.command(
    name="tool-update",
    description="Record a tool update and its release notes."
)
@app_commands.describe(
    date="Update date, e.g. 2026-10-28",
    notes="Short description of what changed"
)
@app_commands.default_permissions(administrator=True)
async def tool_update(
    interaction: discord.Interaction,
    date: str,
    notes: str
):
    if not await require_admin(interaction):
        return

    try:
        datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        await interaction.response.send_message(
            "Use the date format YYYY-MM-DD, for example 2026-10-28.",
            ephemeral=True
        )
        return

    if len(notes) > 500:
        await interaction.response.send_message(
            "Release notes must be 500 characters or fewer.",
            ephemeral=True
        )
        return

    set_setting("last_update", date)
    set_setting("release_notes", notes)

    await interaction.response.send_message(
        f"Tool update recorded for **{date}**.\n"
        f"**Release notes:** {notes}",
        ephemeral=True
    )


@bot.tree.command(
    name="tool-status",
    description="Set the displayed decryption tool status."
)
@app_commands.describe(status="Choose the status to display")
@app_commands.choices(status=[
    app_commands.Choice(name="Online", value="Online"),
    app_commands.Choice(name="Offline", value="Offline"),
    app_commands.Choice(name="Updating", value="Updating"),
    app_commands.Choice(name="Maintenance", value="Maintenance"),
])
@app_commands.default_permissions(administrator=True)
async def tool_status(
    interaction: discord.Interaction,
    status: app_commands.Choice[str]
):
    if not await require_admin(interaction):
        return

    set_setting("status", status.value)
    await interaction.response.send_message(
        f"Displayed tool status changed to **{status.value}**.",
        ephemeral=True
    )


@bot.tree.command(
    name="3ncrÿp†×",
    description="View the saved decryption tool settings."
)
@app_commands.default_permissions(administrator=True)
async def tool_info(interaction: discord.Interaction):
    if not await require_admin(interaction):
        return

    await interaction.response.send_message(
        f"**Status:** {get_setting('status')}\n"
        f"**Version:** {get_setting('version')}\n"
        f"**Last update:** {get_setting('last_update')}\n"
        f"**Release notes:** {get_setting('release_notes')}",
        ephemeral=True
    )


if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN is missing from environment variables.")

bot.run(TOKEN)
