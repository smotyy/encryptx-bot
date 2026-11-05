import discord
from discord import app_commands
from discord.ext import commands
from supabase import create_client
import random
import string
import os

# ================== CONFIG ==================
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN") or os.getenv("TOKEN") or os.getenv("discord.token")
SUPABASE_URL = "https://ykhzykmcmozwpouewbdw.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InlraHp5a21jbW96d3BvdWV3YmR3Iiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc5MzIzMTc1OCwiZXhwIjoyMTA4ODA3NzU4fQ.W2pcdg60zYes_jziY50AIyBIM1lNzdUYPHVkwG9RhRY"

ADMIN_IDS = [777974080631734314]  # ← REPLACE WITH YOUR DISCORD ID
CHANGELOG_CHANNEL_ID = 1564810335871959071  # ← REPLACE WITH CHANGELOG CHANNEL ID
DOWNLOAD_URL = "https://drive.google.com/file/d/13oVZ4gwGma4-DW09AZEL_n1HmOnzigtf/view?usp=sharing"  # ← CHANGE THIS
VERSION = "1.0.0"
# ============================================

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

def is_admin(interaction: discord.Interaction) -> bool:
    return interaction.user.id in ADMIN_IDS

def generate_license():
    chars = string.ascii_uppercase + string.digits
    random_part = ''.join(random.choice(chars) for _ in range(15))
    return f"3ncryptx-{random_part}"

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} slash commands")
    except Exception as e:
        print(e)

# ========== LICENSE COMMANDS ==========

@bot.tree.command(name="create-license", description="Create a new license key")
@app_commands.describe(amount="How many licenses to create (default 1)")
async def create_license(interaction: discord.Interaction, amount: int = 1):
    if not is_admin(interaction):
        return await interaction.response.send_message("❌ You don't have permission.", ephemeral=True)

    if amount < 1 or amount > 20:
        return await interaction.response.send_message("Amount must be between 1 and 20.", ephemeral=True)

    keys = []
    for _ in range(amount):
        key = generate_license()
        supabase.table("licenses").insert({"license_key": key, "is_used": False}).execute()
        keys.append(key)

    embed = discord.Embed(title="✅ Licenses Created", color=0x2ecc71)
    embed.description = "\n".join(f"`{k}`" for k in keys)
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="delete-license", description="Delete a license key")
@app_commands.describe(key="The license key to delete")
async def delete_license(interaction: discord.Interaction, key: str):
    if not is_admin(interaction):
        return await interaction.response.send_message("❌ You don't have permission.", ephemeral=True)

    result = supabase.table("licenses").delete().eq("license_key", key).execute()
    if result.data:
        await interaction.response.send_message(f"✅ License `{key}` deleted.", ephemeral=True)
    else:
        await interaction.response.send_message("❌ License not found.", ephemeral=True)

@bot.tree.command(name="list-licenses", description="List recent licenses")
async def list_licenses(interaction: discord.Interaction):
    if not is_admin(interaction):
        return await interaction.response.send_message("❌ You don't have permission.", ephemeral=True)

    result = supabase.table("licenses").select("*").order("created_at", desc=True).limit(15).execute()
    if not result.data:
        return await interaction.response.send_message("No licenses found.", ephemeral=True)

    lines = [f"`{lic['license_key']}` — {'✅ Used' if lic['is_used'] else '🟡 Available'}" for lic in result.data]
    embed = discord.Embed(title="📜 Recent Licenses", description="\n".join(lines), color=0x3498db)
    await interaction.response.send_message(embed=embed, ephemeral=True)

# ========== ACCOUNT MANAGEMENT ==========

class ConfirmDeleteView(discord.ui.View):
    def __init__(self, username: str, user_data: dict):
        super().__init__(timeout=60)
        self.username = username
        self.user_data = user_data

    @discord.ui.button(label="✅ Confirm Delete", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_admin(interaction):
            return await interaction.response.send_message("❌ No permission.", ephemeral=True)

        supabase.table("profiles").delete().eq("username", self.username).execute()

        embed = discord.Embed(
            title="🗑️ Account Deleted",
            description=f"The account **{self.username}** has been permanently deleted.",
            color=0xe74c3c
        )
        await interaction.response.edit_message(embed=embed, view=None)

    @discord.ui.button(label="❌ Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="Deletion cancelled.", embed=None, view=None)


class AccountSelect(discord.ui.Select):
    def __init__(self, accounts: list):
        options = []
        for acc in accounts[:25]:  # Discord limit is 25 options
            premium = "⭐" if acc.get("is_premium") else ""
            discord_linked = "🔗" if acc.get("discord_username") else ""
            label = f"{acc['username']} {premium}{discord_linked}"
            description = f"Premium: {'Yes' if acc.get('is_premium') else 'No'} | Discord: {acc.get('discord_username') or 'Not linked'}"
            options.append(discord.SelectOption(label=label[:100], value=acc["username"], description=description[:100]))

        super().__init__(placeholder="Select an account to delete...", options=options, min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction):
        username = self.values[0]
        result = supabase.table("profiles").select("*").eq("username", username).execute()
        if not result.data:
            return await interaction.response.send_message("Account not found.", ephemeral=True)

        user = result.data[0]

        embed = discord.Embed(title="⚠️ Confirm Account Deletion", color=0xe67e22)
        embed.add_field(name="Username", value=user["username"], inline=True)
        embed.add_field(name="Premium", value="Yes ✅" if user.get("is_premium") else "No ❌", inline=True)
        embed.add_field(name="Discord", value=user.get("discord_username") or "Not linked", inline=True)
        embed.set_footer(text="This action is permanent!")

        await interaction.response.send_message(embed=embed, view=ConfirmDeleteView(username, user), ephemeral=True)


class AccountSelectView(discord.ui.View):
    def __init__(self, accounts: list):
        super().__init__(timeout=120)
        self.add_item(AccountSelect(accounts))


@bot.tree.command(name="accounts", description="List all accounts and manage them")
async def accounts(interaction: discord.Interaction):
    if not is_admin(interaction):
        return await interaction.response.send_message("❌ You don't have permission.", ephemeral=True)

    result = supabase.table("profiles").select("*").order("created_at", desc=True).limit(25).execute()
    if not result.data:
        return await interaction.response.send_message("No accounts found.", ephemeral=True)

    accounts = result.data

    # Create list embed
    lines = []
    for acc in accounts:
        premium = "⭐ Premium" if acc.get("is_premium") else "Free"
        @bot.tree.command(name="accounts", description="List all accounts and manage them")
async def accounts(interaction: discord.Interaction):
    if not is_admin(interaction):
        return await interaction.response.send_message("❌ You don't have permission.", ephemeral=True)

    result = supabase.table("profiles").select("*").order("created_at", desc=True).limit(25).execute()
    if not result.data:
        return await interaction.response.send_message("No accounts found.", ephemeral=True)

    accounts_list = result.data

    lines = []
    for acc in accounts_list:
        premium = "⭐ Premium" if acc.get("is_premium") else "Free"
        discord_name = acc.get("discord_username") or "Not linked"
        lines.append(f"**{acc['username']}** — {premium} | Discord: {discord_name}")

    embed = discord.Embed(
        title="👥 Accounts",
        description="\n".join(lines),
        color=0x9b59b6
    )
    embed.set_footer(text="Select an account below to delete it")

    await interaction.response.send_message(embed=embed, view=AccountSelectView(accounts_list), ephemeral=True)

# ========== STATUS ==========

@bot.tree.command(name="status", description="Show 3ncrÿp†× stats")
async def status(interaction: discord.Interaction):
    users = supabase.table("profiles").select("id", count="exact").execute()
    premiums = supabase.table("profiles").select("id", count="exact").eq("is_premium", True).execute()
    licenses = supabase.table("licenses").select("id", count="exact").execute()
    used = supabase.table("licenses").select("id", count="exact").eq("is_used", True).execute()

    embed = discord.Embed(title="3ncrÿp†× Status", color=0x00ff88)
    embed.add_field(name="Total Users", value=users.count or 0, inline=True)
    embed.add_field(name="Premium Users", value=premiums.count or 0, inline=True)
    embed.add_field(name="Total Licenses", value=licenses.count or 0, inline=True)
    embed.add_field(name="Used Licenses", value=used.count or 0, inline=True)
    await interaction.response.send_message(embed=embed)

# ========== TICKET SYSTEM ==========

class TicketView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🛒 Purchase", style=discord.ButtonStyle.green, custom_id="ticket_purchase")
    async def purchase_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.create_ticket(interaction, "purchase")

    @discord.ui.button(label="🛠️ Support", style=discord.ButtonStyle.blurple, custom_id="ticket_support")
    async def support_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.create_ticket(interaction, "support")

    @discord.ui.button(label="💬 General", style=discord.ButtonStyle.gray, custom_id="ticket_general")
    async def general_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.create_ticket(interaction, "general")

    async def create_ticket(self, interaction: discord.Interaction, ticket_type: str):
        for channel in interaction.guild.channels:
            if isinstance(channel, discord.TextChannel) and channel.topic == f"ticket-{interaction.user.id}":
                return await interaction.response.send_message(f"You already have an open ticket: {channel.mention}", ephemeral=True)

        category = discord.utils.get(interaction.guild.categories, name="Tickets")
        if not category:
            category = await interaction.guild.create_category("Tickets")

        overwrites = {
            interaction.guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True, attach_files=True),
            interaction.guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True)
        }

        channel_name = f"{ticket_type}-{interaction.user.name}".lower().replace(" ", "-")[:90]
        channel = await interaction.guild.create_text_channel(
            name=channel_name,
            category=category,
            overwrites=overwrites,
            topic=f"ticket-{interaction.user.id}"
        )

        colors = {"purchase": 0x2ecc71, "support": 0x3498db, "general": 0x95a5a6}
        embed = discord.Embed(
            title=f"{ticket_type.capitalize()} Ticket",
            description=f"Hello {interaction.user.mention}!\n\nPlease describe your issue.\nStaff will help you soon.",
            color=colors.get(ticket_type, 0x3498db)
        )
        embed.set_footer(text="Use /close to close this ticket")

        await channel.send(content=interaction.user.mention, embed=embed)
        await interaction.response.send_message(f"✅ Your {ticket_type} ticket has been created: {channel.mention}", ephemeral=True)


@bot.tree.command(name="ticket-panel", description="Send the ticket creation panel")
async def ticket_panel(interaction: discord.Interaction):
    if not is_admin(interaction):
        return await interaction.response.send_message("❌ You don't have permission.", ephemeral=True)

    embed = discord.Embed(
        title="🎫 Support Tickets",
        description="Select the type of ticket:\n\n🛒 **Purchase** → Buying / licenses\n🛠️ **Support** → Technical help\n💬 **General** → Other questions",
        color=0x5865F2
    )
    await interaction.channel.send(embed=embed, view=TicketView())
    await interaction.response.send_message("✅ Ticket panel sent!", ephemeral=True)


@bot.tree.command(name="close", description="Close the current ticket")
async def close_ticket(interaction: discord.Interaction):
    if not interaction.channel.topic or not interaction.channel.topic.startswith("ticket-"):
        return await interaction.response.send_message("This is not a ticket channel.", ephemeral=True)
    await interaction.response.send_message("Closing ticket in 3 seconds...")
    await interaction.channel.delete(delay=3)

# ========== DOWNLOAD PANEL ==========

@bot.tree.command(name="download-panel", description="Send the download panel")
async def download_panel(interaction: discord.Interaction):
    if not is_admin(interaction):
        return await interaction.response.send_message("❌ You don't have permission.", ephemeral=True)

    embed = discord.Embed(
        title="📥 Download 3ncrÿp†×",
        description=f"**Version:** `{VERSION}`\n\nSingle `.exe` file — no installation needed.\nFree features available for everyone.\nPremium unlocks Decrypt + more.",
        color=0x00ff88
    )
    embed.add_field(name="📝 Changelog", value="• Account system\n• License system\n• Strong obfuscation\n• Discord linking\n• Decrypt tool (Premium)", inline=False)

    view = discord.ui.View()
    view.add_item(discord.ui.Button(label=f"Download v{VERSION}", style=discord.ButtonStyle.green, url=DOWNLOAD_URL, emoji="📥"))
    await interaction.channel.send(embed=embed, view=view)
    await interaction.response.send_message("✅ Download panel sent!", ephemeral=True)

# ========== CHANGELOG ==========

@bot.tree.command(name="changelog", description="Post a new changelog update")
@app_commands.describe(version="Version number", title="Title of the update", changes="Changes (use \\n for new lines)")
async def changelog(interaction: discord.Interaction, version: str, title: str, changes: str):
    if not is_admin(interaction):
        return await interaction.response.send_message("❌ You don't have permission.", ephemeral=True)

    channel = bot.get_channel(CHANGELOG_CHANNEL_ID)
    if not channel:
        return await interaction.response.send_message("❌ Changelog channel not found.", ephemeral=True)

    embed = discord.Embed(
        title=f"📢 3ncrÿp†× — v{version}",
        description=f"**{title}**\n\n{changes.replace('\\\\n', '\n')}",
        color=0x00ff88,
        timestamp=discord.utils.utcnow()
    )
    embed.set_footer(text="3ncrÿp†× Changelog")
    await channel.send(embed=embed)
    await interaction.response.send_message(f"✅ Changelog posted in {channel.mention}", ephemeral=True)

# ================== START ==================
if not DISCORD_TOKEN:
    print("ERROR: No Discord token found!")
else:
    bot.run(DISCORD_TOKEN)
