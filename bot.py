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

# Put your Discord User ID here
ADMIN_IDS = [777974080631734314]  # ← REPLACE THIS WITH YOUR DISCORD ID

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
        supabase.table("licenses").insert({
            "license_key": key,
            "is_used": False
        }).execute()
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

    lines = []
    for lic in result.data:
        status = "✅ Used" if lic["is_used"] else "🟡 Available"
        lines.append(f"`{lic['license_key']}` — {status}")

    embed = discord.Embed(title="📜 Recent Licenses", description="\n".join(lines), color=0x3498db)
    await interaction.response.send_message(embed=embed, ephemeral=True)

# ========== PREMIUM / ACCOUNT ==========

@bot.tree.command(name="give-premium", description="Give premium to a user by username")
@app_commands.describe(username="The 3ncrÿp†× username")
async def give_premium(interaction: discord.Interaction, username: str):
    if not is_admin(interaction):
        return await interaction.response.send_message("❌ You don't have permission.", ephemeral=True)

    result = supabase.table("profiles").update({"is_premium": True}).eq("username", username).execute()
    if result.data:
        await interaction.response.send_message(f"✅ `{username}` is now **Premium**.", ephemeral=True)
    else:
        await interaction.response.send_message("❌ User not found.", ephemeral=True)

@bot.tree.command(name="revoke-premium", description="Revoke premium from a user")
@app_commands.describe(username="The 3ncrÿp†× username")
async def revoke_premium(interaction: discord.Interaction, username: str):
    if not is_admin(interaction):
        return await interaction.response.send_message("❌ You don't have permission.", ephemeral=True)

    result = supabase.table("profiles").update({"is_premium": False}).eq("username", username).execute()
    if result.data:
        await interaction.response.send_message(f"✅ Premium revoked from `{username}`.", ephemeral=True)
    else:
        await interaction.response.send_message("❌ User not found.", ephemeral=True)

@bot.tree.command(name="user-info", description="Get info about a 3ncrÿp†× user")
@app_commands.describe(username="The 3ncrÿp†× username")
async def user_info(interaction: discord.Interaction, username: str):
    if not is_admin(interaction):
        return await interaction.response.send_message("❌ You don't have permission.", ephemeral=True)

    result = supabase.table("profiles").select("*").eq("username", username).execute()
    if not result.data:
        return await interaction.response.send_message("❌ User not found.", ephemeral=True)

    user = result.data[0]
    embed = discord.Embed(title=f"User: {user['username']}", color=0x9b59b6)
    embed.add_field(name="Premium", value="Yes ✅" if user["is_premium"] else "No ❌", inline=True)
    embed.add_field(name="Discord", value=user.get("discord_username") or "Not linked", inline=True)
    await interaction.response.send_message(embed=embed, ephemeral=True)

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
    embed.set_footer(text="3ncrÿp†× Management Bot")
    await interaction.response.send_message(embed=embed)

# ========== TICKET SYSTEM (Panel) ==========

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
        # Check if user already has an open ticket
        for channel in interaction.guild.channels:
            if isinstance(channel, discord.TextChannel) and channel.topic == f"ticket-{interaction.user.id}":
                return await interaction.response.send_message(
                    f"You already have an open ticket: {channel.mention}", ephemeral=True
                )

        category = discord.utils.get(interaction.guild.categories, name="Tickets")
        if not category:
            category = await interaction.guild.create_category("Tickets")

        overwrites = {
            interaction.guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True, attach_files=True),
            interaction.guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True)
        }

        # Add admin roles if you want (optional)
        # for role_id in ADMIN_ROLE_IDS:
        #     role = interaction.guild.get_role(role_id)
        #     if role:
        #         overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True)

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
            description=f"Hello {interaction.user.mention}!\n\nPlease describe your issue or request.\nA staff member will be with you shortly.",
            color=colors.get(ticket_type, 0x3498db)
        )
        embed.set_footer(text="Use /close to close this ticket")

        await channel.send(content=f"{interaction.user.mention}", embed=embed)
        await interaction.response.send_message(f"✅ Your {ticket_type} ticket has been created: {channel.mention}", ephemeral=True)


@bot.tree.command(name="ticket-panel", description="Send the ticket creation panel")
async def ticket_panel(interaction: discord.Interaction):
    if not is_admin(interaction):
        return await interaction.response.send_message("❌ You don't have permission.", ephemeral=True)

    embed = discord.Embed(
        title="🎫 Support Tickets",
        description="Please select the type of ticket you want to create:\n\n"
                    "🛒 **Purchase** → Questions about buying / licenses\n"
                    "🛠️ **Support** → Technical problems / help\n"
                    "💬 **General** → Other questions",
        color=0x5865F2
    )
    embed.set_footer(text="3ncrÿp†× Support")

    await interaction.channel.send(embed=embed, view=TicketView())
    await interaction.response.send_message("✅ Ticket panel sent!", ephemeral=True)


@bot.tree.command(name="close", description="Close the current ticket")
async def close_ticket(interaction: discord.Interaction):
    if not interaction.channel.topic or not interaction.channel.topic.startswith("ticket-"):
        return await interaction.response.send_message("This is not a ticket channel.", ephemeral=True)

    await interaction.response.send_message("Closing ticket in 3 seconds...")
    await interaction.channel.delete(delay=3)

# ========== GIVEAWAY ==========

@bot.tree.command(name="giveaway", description="Start a simple giveaway")
@app_commands.describe(prize="What is the prize?", winners="Number of winners")
async def giveaway(interaction: discord.Interaction, prize: str, winners: int = 1):
    if not is_admin(interaction):
        return await interaction.response.send_message("❌ You don't have permission.", ephemeral=True)

    embed = discord.Embed(title="🎉 Giveaway!", description=f"**Prize:** {prize}\n**Winners:** {winners}\n\nReact with 🎉 to enter!", color=0xf1c40f)
    embed.set_footer(text=f"Started by {interaction.user}")
    await interaction.response.send_message(embed=embed)
    msg = await interaction.original_response()
    await msg.add_reaction("🎉")

# ================== START ==================
if not DISCORD_TOKEN:
    print("ERROR: No Discord token found in environment variables!")
else:
    bot.run(DISCORD_TOKEN)
