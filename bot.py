import discord
from discord import app_commands
from discord.ext import commands
from supabase import create_client
from dotenv import load_dotenv
import random
import string
import os
import asyncio
 
load_dotenv()
 
# ================== CONFIG ==================
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN") or os.getenv("TOKEN") or os.getenv("discord.token")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
 
ADMIN_IDS = [777974080631734314]
CHANGELOG_CHANNEL_ID = 1567931252621709392
DOWNLOAD_URL = "https://drive.google.com/file/d/1utQHDC3-uNICEgvbP7ZjCnSfFpbgvlui/view?usp=sharing"
VERSION = "1.0.0"
# ============================================
 
if not SUPABASE_URL or not SUPABASE_KEY:
    raise SystemExit("ERROR: SUPABASE_URL and SUPABASE_KEY must be set as environment variables.")
 
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
 
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
 
 
class Bot(commands.Bot):
    async def setup_hook(self):
        # Re-register the ticket buttons so old panels keep working after a restart
        self.add_view(TicketView())
 
 
bot = Bot(command_prefix="!", intents=intents)
 
 
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
 
    await interaction.response.defer(ephemeral=True)
 
    def insert_keys():
        created = []
        for _ in range(amount):
            key = generate_license()
            supabase.table("licenses").insert({"license_key": key, "is_used": False}).execute()
            created.append(key)
        return created
 
    keys = await asyncio.to_thread(insert_keys)
 
    embed = discord.Embed(title="✅ Licenses Created", color=0x2ecc71)
    embed.description = "\n".join(f"`{k}`" for k in keys)
    await interaction.followup.send(embed=embed, ephemeral=True)
 
@bot.tree.command(name="delete-license", description="Delete a license key")
@app_commands.describe(key="The license key to delete")
async def delete_license(interaction: discord.Interaction, key: str):
    if not is_admin(interaction):
        return await interaction.response.send_message("❌ You don't have permission.", ephemeral=True)
 
    await interaction.response.defer(ephemeral=True)
    result = await asyncio.to_thread(lambda: supabase.table("licenses").delete().eq("license_key", key).execute())
    if result.data:
        await interaction.followup.send(f"✅ License `{key}` deleted.", ephemeral=True)
    else:
        await interaction.followup.send("❌ License not found.", ephemeral=True)
 
@bot.tree.command(name="list-licenses", description="List recent licenses")
async def list_licenses(interaction: discord.Interaction):
    if not is_admin(interaction):
        return await interaction.response.send_message("❌ You don't have permission.", ephemeral=True)
 
    await interaction.response.defer(ephemeral=True)
    result = await asyncio.to_thread(
        lambda: supabase.table("licenses").select("*").order("created_at", desc=True).limit(15).execute()
    )
    if not result.data:
        return await interaction.followup.send("No licenses found.", ephemeral=True)
 
    lines = [f"`{lic['license_key']}` — {'✅ Used' if lic['is_used'] else '🟡 Available'}" for lic in result.data]
    embed = discord.Embed(title="📜 Recent Licenses", description="\n".join(lines), color=0x3498db)
    await interaction.followup.send(embed=embed, ephemeral=True)
 
# ========== ACCOUNT MANAGEMENT ==========
 
class ConfirmDeleteView(discord.ui.View):
    def __init__(self, username: str):
        super().__init__(timeout=60)
        self.username = username
 
    @discord.ui.button(label="✅ Confirm Delete", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_admin(interaction):
            return await interaction.response.send_message("❌ No permission.", ephemeral=True)
 
        await asyncio.to_thread(lambda: supabase.table("profiles").delete().eq("username", self.username).execute())
        embed = discord.Embed(title="🗑️ Account Deleted", description=f"The account **{self.username}** has been permanently deleted.", color=0xe74c3c)
        await interaction.response.edit_message(embed=embed, view=None)
 
    @discord.ui.button(label="❌ Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="Deletion cancelled.", embed=None, view=None)
 
 
class AccountSelect(discord.ui.Select):
    def __init__(self, accounts: list):
        options = []
        for acc in accounts[:25]:
            premium = "⭐" if acc.get("is_premium") else ""
            linked = "🔗" if acc.get("discord_username") else ""
            label = f"{acc['username']} {premium}{linked}"
            description = f"Premium: {'Yes' if acc.get('is_premium') else 'No'} | Discord: {acc.get('discord_username') or 'Not linked'}"
            options.append(discord.SelectOption(label=label[:100], value=acc["username"], description=description[:100]))
 
        super().__init__(placeholder="Select an account to delete...", options=options)
 
    async def callback(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            return await interaction.response.send_message("❌ No permission.", ephemeral=True)
 
        await interaction.response.defer(ephemeral=True)
        username = self.values[0]
        result = await asyncio.to_thread(lambda: supabase.table("profiles").select("*").eq("username", username).execute())
        if not result.data:
            return await interaction.followup.send("Account not found.", ephemeral=True)
 
        user = result.data[0]
        embed = discord.Embed(title="⚠️ Confirm Account Deletion", color=0xe67e22)
        embed.add_field(name="Username", value=user["username"], inline=True)
        embed.add_field(name="Premium", value="Yes ✅" if user.get("is_premium") else "No ❌", inline=True)
        embed.add_field(name="Discord", value=user.get("discord_username") or "Not linked", inline=True)
        embed.set_footer(text="This action is permanent!")
 
        await interaction.followup.send(embed=embed, view=ConfirmDeleteView(username), ephemeral=True)
 
 
class AccountSelectView(discord.ui.View):
    def __init__(self, accounts: list):
        super().__init__(timeout=120)
        self.add_item(AccountSelect(accounts))
 
 
@bot.tree.command(name="accounts", description="List all accounts and manage them")
async def accounts(interaction: discord.Interaction):
    if not is_admin(interaction):
        return await interaction.response.send_message("❌ You don't have permission.", ephemeral=True)
 
    await interaction.response.defer(ephemeral=True)
    result = await asyncio.to_thread(
        lambda: supabase.table("profiles").select("*").order("created_at", desc=True).limit(25).execute()
    )
    if not result.data:
        return await interaction.followup.send("No accounts found.", ephemeral=True)
 
    accounts_list = result.data
 
    lines = []
    for acc in accounts_list:
        premium = "⭐ Premium" if acc.get("is_premium") else "Free"
        discord_name = acc.get("discord_username") or "Not linked"
        lines.append(f"**{acc['username']}** — {premium} | Discord: {discord_name}")
 
    embed = discord.Embed(title="👥 Accounts", description="\n".join(lines), color=0x9b59b6)
    embed.set_footer(text="Select an account below to delete it")
 
    await interaction.followup.send(embed=embed, view=AccountSelectView(accounts_list), ephemeral=True)
 
# ========== LIVE STATUS ==========

STATUS_MESSAGE = None  # Will store the message

async def update_status_message():
    global STATUS_MESSAGE
    if STATUS_MESSAGE is None:
        return

    try:
        users = supabase.table("profiles").select("id", count="exact").execute()
        premiums = supabase.table("profiles").select("id", count="exact").eq("is_premium", True).execute()
        licenses = supabase.table("licenses").select("id", count="exact").execute()
        used = supabase.table("licenses").select("id", count="exact").eq("is_used", True).execute()

        embed = discord.Embed(
            title="📊 3ncrÿp†× Live Status",
            color=0x00ff88,
            timestamp=discord.utils.utcnow()
        )
        embed.add_field(name="Total Users", value=f"**{users.count or 0}**", inline=True)
        embed.add_field(name="Premium Users", value=f"**{premiums.count or 0}**", inline=True)
        embed.add_field(name="Total Licenses", value=f"**{licenses.count or 0}**", inline=True)
        embed.add_field(name="Used Licenses", value=f"**{used.count or 0}**", inline=True)
        embed.set_footer(text="Auto-updates every 5 minutes")

        await STATUS_MESSAGE.edit(embed=embed)
    except Exception as e:
        print("Failed to update status:", e)


@bot.tree.command(name="status-panel", description="Create a live auto-updating status message")
async def status_panel(interaction: discord.Interaction):
    if not is_admin(interaction):
        return await interaction.response.send_message("❌ You don't have permission.", ephemeral=True)

    global STATUS_MESSAGE

    users = supabase.table("profiles").select("id", count="exact").execute()
    premiums = supabase.table("profiles").select("id", count="exact").eq("is_premium", True).execute()
    licenses = supabase.table("licenses").select("id", count="exact").execute()
    used = supabase.table("licenses").select("id", count="exact").eq("is_used", True).execute()

    embed = discord.Embed(
        title="📊 3ncrÿp†× Live Status",
        color=0x00ff88,
        timestamp=discord.utils.utcnow()
    )
    embed.add_field(name="Total Users", value=f"**{users.count or 0}**", inline=True)
    embed.add_field(name="Premium Users", value=f"**{premiums.count or 0}**", inline=True)
    embed.add_field(name="Total Licenses", value=f"**{licenses.count or 0}**", inline=True)
    embed.add_field(name="Used Licenses", value=f"**{used.count or 0}**", inline=True)
    embed.set_footer(text="Auto-updates every 5 minutes")

    await interaction.response.send_message("✅ Live status created!", ephemeral=True)
    STATUS_MESSAGE = await interaction.channel.send(embed=embed)


# Background task that updates every 5 minutes
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} slash commands")
    except Exception as e:
        print(e)

    # Start the auto-update loop
    bot.loop.create_task(status_updater())


async def status_updater():
    await bot.wait_until_ready()
    while not bot.is_closed():
        await update_status_message()
        await asyncio.sleep(300)  # 5 minutes
 
# ========== IMPROVED TICKET SYSTEM ==========

class TicketControlView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="✅ Claim", style=discord.ButtonStyle.green, custom_id="ticket_claim")
    async def claim(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_admin(interaction):
            return await interaction.response.send_message("❌ Only staff can claim tickets.", ephemeral=True)

        await interaction.channel.send(f"✅ Ticket claimed by {interaction.user.mention}")
        await interaction.response.send_message("You claimed this ticket.", ephemeral=True)

    @discord.ui.button(label="🔒 Close", style=discord.ButtonStyle.red, custom_id="ticket_close")
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("Closing ticket in 5 seconds...")
        await interaction.channel.send(f"Ticket closed by {interaction.user.mention}")
        await interaction.channel.delete(delay=5)


class TicketView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🛒 Purchase", style=discord.ButtonStyle.green, custom_id="ticket_purchase", emoji="🛒")
    async def purchase(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.create_ticket(interaction, "purchase")

    @discord.ui.button(label="🛠️ Support", style=discord.ButtonStyle.blurple, custom_id="ticket_support", emoji="🛠️")
    async def support(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.create_ticket(interaction, "support")

    @discord.ui.button(label="💬 General", style=discord.ButtonStyle.gray, custom_id="ticket_general", emoji="💬")
    async def general(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.create_ticket(interaction, "general")

    async def create_ticket(self, interaction: discord.Interaction, ticket_type: str):
        # Check if user already has a ticket
        for channel in interaction.guild.text_channels:
            if channel.topic == f"ticket-{interaction.user.id}":
                return await interaction.response.send_message(
                    f"You already have an open ticket: {channel.mention}", ephemeral=True
                )

        category = discord.utils.get(interaction.guild.categories, name="Tickets")
        if category is None:
            category = await interaction.guild.create_category("Tickets")

        overwrites = {
            interaction.guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True, attach_files=True, embed_links=True),
            interaction.guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True)
        }

        # Give admins access
        for admin_id in ADMIN_IDS:
            member = interaction.guild.get_member(admin_id)
            if member:
                overwrites[member] = discord.PermissionOverwrite(view_channel=True, send_messages=True)

        channel_name = f"{ticket_type}-{interaction.user.name}".lower().replace(" ", "-")[:90]
        channel = await interaction.guild.create_text_channel(
            name=channel_name,
            category=category,
            overwrites=overwrites,
            topic=f"ticket-{interaction.user.id}"
        )

        colors = {
            "purchase": 0x2ecc71,
            "support": 0x3498db,
            "general": 0x95a5a6
        }

        embed = discord.Embed(
            title=f"{ticket_type.capitalize()} Ticket",
            description=(
                f"Hello {interaction.user.mention}!\n\n"
                f"**Type:** {ticket_type.capitalize()}\n"
                "Please describe your issue or request in detail.\n"
                "A staff member will be with you shortly."
            ),
            color=colors.get(ticket_type, 0x3498db)
        )
        embed.set_footer(text="Staff can use the buttons below")

        await channel.send(
            content=f"{interaction.user.mention}",
            embed=embed,
            view=TicketControlView()
        )

        await interaction.response.send_message(
            f"✅ Your **{ticket_type}** ticket has been created: {channel.mention}",
            ephemeral=True
        )


@bot.tree.command(name="ticket-panel", description="Send the ticket creation panel")
async def ticket_panel(interaction: discord.Interaction):
    if not is_admin(interaction):
        return await interaction.response.send_message("❌ You don't have permission.", ephemeral=True)

    embed = discord.Embed(
        title="🎫 3ncrÿp†× Support",
        description=(
            "Need help? Create a ticket by clicking one of the buttons below.\n\n"
            "🛒 **Purchase** → Questions about buying licenses / Premium\n"
            "🛠️ **Support** → Technical problems with the app\n"
            "💬 **General** → Other questions\n\n"
            "Please only open one ticket at a time."
        ),
        color=0x5865F2
    )
    embed.set_footer(text="3ncrÿp†× Support System")

    await interaction.channel.send(embed=embed, view=TicketView())
    await interaction.response.send_message("✅ Ticket panel sent!", ephemeral=True)


@bot.tree.command(name="close", description="Close the current ticket")
async def close_ticket(interaction: discord.Interaction):
    if not interaction.channel.topic or not interaction.channel.topic.startswith("ticket-"):
        return await interaction.response.send_message("This is not a ticket channel.", ephemeral=True)

    await interaction.response.send_message("Closing ticket in 5 seconds...")
    await interaction.channel.delete(delay=5)
 
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
 
    formatted_changes = changes.replace("\\n", "\n")
    embed = discord.Embed(
        title=f"📢 3ncrÿp†× — v{version}",
        description=f"**{title}**\n\n{formatted_changes}",
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
 
