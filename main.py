import aiofiles
import discord
from discord.ext import commands, tasks
from discord import app_commands
import json
import os
from datetime import datetime, timezone
import asyncio
from dotenv import load_dotenv
from discord import TextChannel
load_dotenv()

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="tbptnbu.", intents=intents, dm_help=None) # just in case you're wondering, "ticket bot prefix to not be used."

DM_LOG_FILE = "dm_logs.json"
CONFIG_FILE = "config.json"
FILTERED_WORDS_FILE = "filtered_words.json"
USER_DATA_FILE = "user.json"

json_lock = asyncio.Lock()

async def save_json(file_path, data):
    async with json_lock:
        async with aiofiles.open(file_path, 'w', encoding='utf-8') as f:
            await f.write(json.dumps(data, indent=4))

async def load_json(file_path: str, default):
    async with json_lock:
        if not os.path.exists(file_path):
            return default
        try:
            async with aiofiles.open(file_path, 'r', encoding='utf-8') as f:
                content = await f.read()
                return json.loads(content)
        except Exception as e:
            print(f"Failed to load {file_path}: {e}")
            return default

async def log_dm(message: discord.Message):
    data = await load_json(DM_LOG_FILE, [])

    data.append({
        "user_id": message.author.id,
        "user_tag": str(message.author),
        "content": message.content,
        "timestamp": message.created_at.isoformat()
    })

    await save_json(DM_LOG_FILE, data)

closestatus: dict = {}

async def unregister_ticket(channel: discord.TextChannel):
    guild_id = str(channel.guild.id)
    channel_id = str(channel.id)

    guild_data = user_data.get(guild_id)
    if not guild_data:
        return

    tickets = guild_data.get("tickets")
    if not tickets or channel_id not in tickets:
        return

    # Remove the ticket
    del tickets[channel_id]
    await save_json(USER_DATA_FILE, user_data)


async def log_ticket_event(guild, title, description, color=discord.Color.blue(), fields=None):
    try:
        guild_config = config.get(str(guild.id), {})
        logging_channel_id = guild_config.get("logging_channel")

        if not logging_channel_id:
            return

        logging_channel = guild.get_channel(logging_channel_id)
        if not logging_channel:
            return

        embed = discord.Embed(
            title=title,
            description=description,
            color=color,
            timestamp=datetime.now(timezone.utc)
        )

        if fields:
            for field_name, field_value in fields.items():
                embed.add_field(name=field_name, value=field_value, inline=False)

        await logging_channel.send(embed=embed)
    except Exception as e:
        print(f"Failed to log event: {e}")


class TicketButton(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Create Ticket", style=discord.ButtonStyle.gray, custom_id="create_ticket", emoji="📩")
    async def create_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        user = interaction.user

        guild_config = config.get(str(guild.id), {})
        staff_role_id = guild_config.get("staff_role")
        category_id = guild_config.get("ticket_category")

        guild_data = user_data.get(str(guild.id), {})
        tickets = guild_data.get("tickets", {})

        for ticket in tickets.values():
            if ticket["owner_id"] == user.id:
                await interaction.response.send_message(
                    "Ticket limit reached. You already have an open ticket.",
                    ephemeral=True
                )
                return


        account_age = datetime.now(timezone.utc) - user.created_at
        account_age_days = account_age.days

        if account_age_days <30:
            await interaction.response.send_message("Creating your ticket...", ephemeral=True)
            try:
                await user.send("You have been kicked because your account was recently made.")
            except:
                pass
            await user.kick(reason="Account created less than 30 days ago")
            return

        category = guild.get_channel(category_id) if category_id else None
        if category and len(category.channels) >= 50:
            category = None            

        guild_id_str = str(guild.id)
        if guild_id_str not in user_data:
            user_data[guild_id_str] = {
                "global_ticket_counter": 0,
                "user_ticket_counts": {}
            }

        user_data[guild_id_str]["global_ticket_counter"] += 1
        ticket_number = user_data[guild_id_str]["global_ticket_counter"]

        user_id_str = str(user.id)
        if user_id_str not in user_data[guild_id_str]["user_ticket_counts"]:
            user_data[guild_id_str]["user_ticket_counts"][user_id_str] = 0

        user_data[guild_id_str]["user_ticket_counts"][user_id_str] += 1
        user_ticket_count = user_data[guild_id_str]["user_ticket_counts"][user_id_str]

        await save_json(USER_DATA_FILE, user_data)

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            user: discord.PermissionOverwrite(read_messages=True, send_messages=True),
            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True)
        }

        if staff_role_id:
            staff_role = guild.get_role(staff_role_id)
            if staff_role:
                overwrites[staff_role] = discord.PermissionOverwrite(read_messages=True, send_messages=True)

        ticket_channel = await guild.create_text_channel(
            
            name=f"ticket-{ticket_number}",
            category=category,
            topic=f"Ticket owner: {user.id} | Ticket #{ticket_number} | User tickets: {user_ticket_count}",
            overwrites=overwrites
        )
        guild_id = str(guild.id)
        channel_id = str(ticket_channel.id)

        user_data[guild_id].setdefault("tickets", {})
        user_data[guild_id]["tickets"][channel_id] = {
            "owner_id": user.id,
            "ticket_number": ticket_number
        }

        await save_json(USER_DATA_FILE, user_data)

        ticket_view = TicketControlPanel()

        embed = discord.Embed(
            description="A staff member will be along as soon as possible to complete your verification. Please be patient.",
            color=discord.Color.from_rgb(47, 49, 54)
        )
        embed.set_footer(text=f"Ticket #{ticket_number} | User has opened {user_ticket_count} ticket(s)")

        questions_text = """**1. Where did you find this server?**
**2. Why did you decide to join?**
**3. Have you read the server's rules?**
**4. Will you be using the server's Guild Tag?**"""

        if 30 <= account_age_days < 90:
            questions_text += "\n**5. Why is your account recently made?**"

        await ticket_channel.send(
            content=f"Welcome, {user.mention}! Please answer these questions to begin verification.\n{questions_text}",
            embed=embed,
            view=ticket_view
        )

        bot.loop.create_task(schedule_first_check(ticket_channel.id, user.id, guild.id))

        await log_ticket_event(
            guild,
            "🎫 Ticket Created",
            f"{user.mention} created a ticket",
            discord.Color.green(),
            {
                "Ticket": ticket_channel.mention,
                "Ticket Number": f"#{ticket_number}",
                "User": f"{user} ({user.id})",
                "Account Age": f"{account_age_days} days",
                "Total User Tickets": user_ticket_count
            }
        )

        await interaction.response.send_message(f"✔ *Ticket Created* {ticket_channel.mention}", ephemeral=True)


class CloseConfirmationView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Close", style=discord.ButtonStyle.red)
    async def confirm_yes(self, interaction: discord.Interaction, button: discord.ui.Button):
        channel = interaction.channel
        owner_id = int(channel.topic.split(": ")[1].split(" |")[0])
        owner = interaction.guild.get_member(owner_id)

        guild = interaction.guild
        guild_config = config.get(str(guild.id), {})
        staff_role_id = guild_config.get("staff_role")

        await interaction.message.delete()
        await interaction.response.defer()
        closestatus[channel.id] = True

        if owner:
            await channel.set_permissions(owner, read_messages=False, send_messages=False)

        await log_ticket_event(
            guild,
            "🔒 Ticket Closed",
            f"Ticket {channel.mention} was closed",
            discord.Color.orange(),
            {
                "Closed By": f"{interaction.user} ({interaction.user.id})",
                "Ticket Owner": f"{owner.mention if owner else 'Unknown'} ({owner_id})",
                "Channel": channel.mention
            }
        )

        closed_embed = discord.Embed(
            description=f"Ticket closed by {interaction.user.mention}.",
            color=discord.Color.yellow()
        )
        await interaction.followup.send(embed=closed_embed)

        control_panel_embed = discord.Embed(
            description="```Staff Team Control Panel```",
        )
        closed_view = ClosedTicketManagementPanel()
        await channel.send(embed=control_panel_embed, view=closed_view)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.gray)
    async def confirm_no(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.message.delete()
        await interaction.response.defer()


class DenyActionPanel(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        guild_config = config.get(str(interaction.guild.id), {})
        staff_role_id = guild_config.get("staff_role")

        if staff_role_id:
            staff_role = interaction.guild.get_role(staff_role_id)
            if staff_role and staff_role in interaction.user.roles:
                return True

            await interaction.response.send_message("Only staff members can interact.", ephemeral=True)
            return False

        return True

    @discord.ui.button(label="Close", style=discord.ButtonStyle.gray, custom_id="close_deny", emoji="🔒")
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = CloseReasonModal()
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Kick", style=discord.ButtonStyle.red, custom_id="kick_deny", emoji="👢")
    async def kick_user(self, interaction: discord.Interaction, button: discord.ui.Button):
        channel = interaction.channel
        owner_id = int(channel.topic.split(": ")[1].split(" |")[0])
        owner = interaction.guild.get_member(owner_id)

        if not owner:
            await interaction.response.send_message("User not found in server!", ephemeral=True)
            return

        await interaction.response.defer()
        await channel.send(
            f"{owner.mention} is kicked. Ticket will be promptly deleted.")

        try:
            await owner.kick(reason=f"Kicked by {interaction.user.name} via ticket system")
        except:
            await channel.send("Failed to kick user.")

        await asyncio.sleep(3)
        await unregister_ticket(channel)
        await asyncio.sleep(1.5)
        await channel.delete()

    @discord.ui.button(label="Ban", style=discord.ButtonStyle.red, custom_id="ban_deny", emoji="🔨")
    async def ban_user(self, interaction: discord.Interaction, button: discord.ui.Button):
        channel = interaction.channel
        owner_id = int(channel.topic.split(": ")[1].split(" |")[0])
        owner = interaction.guild.get_member(owner_id)

        if not owner:
            await interaction.response.send_message("User not found in server.", ephemeral=True)
            return

        await interaction.response.defer()
        await channel.send(
            f"{owner.mention} is banned. Ticket will be promptly deleted. ")

        try:
            await owner.ban(reason=f"Banned by {interaction.user.name} via ticket system")
        except:
            await channel.send("Failed to ban user.")
        await asyncio.sleep(3)
        await unregister_ticket(channel)
        await asyncio.sleep(1.5)
        await channel.delete()


class CloseReasonModal(discord.ui.Modal, title="Close Ticket"):
    reason = discord.ui.TextInput(
        label="Reason for closing",
        placeholder="Enter the reason for closing this ticket...",
        style=discord.TextStyle.paragraph,
        required=False,
        max_length=500
    )

    async def on_submit(self, interaction: discord.Interaction):
        channel = interaction.channel
        owner_id = int(channel.topic.split(": ")[1].split(" |")[0])
        owner = interaction.guild.get_member(owner_id)

        if owner and self.reason.value:
            try:
                await owner.send(f"Your ticket has been denied.\n**Reason:** {self.reason.value}")
            except:
                pass

        await interaction.response.defer()
        await channel.send(
            f"Ticket closed by {interaction.user.mention}. Reason: {self.reason.value}\n\nTicket will be promptly deleted.")

        await asyncio.sleep(3)
        await channel.delete()


class ClosedTicketManagementPanel(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        guild_config = config.get(str(interaction.guild.id), {})
        staff_role_id = guild_config.get("staff_role")

        if staff_role_id:
            staff_role = interaction.guild.get_role(staff_role_id)
            if staff_role and staff_role in interaction.user.roles:
                return True

            await interaction.response.send_message("Only staff members can interact.", ephemeral=True)
            return False

        return True

    @discord.ui.button(label="Transcript", style=discord.ButtonStyle.gray, custom_id="save_transcript_mgmt",emoji="📜")
    async def save_transcript(self, interaction: discord.Interaction, button: discord.ui.Button):
        channel = interaction.channel
        guild_config = config.get(str(interaction.guild.id), {})
        transcript_channel_id = guild_config.get("transcript_channel")

        if not str(transcript_channel_id):
            await interaction.response.send_message("No transcript channel set.", ephemeral=True)
            return

        transcript_channel = interaction.guild.get_channel(transcript_channel_id)
        if not transcript_channel:
            await interaction.response.send_message("Transcript channel not found.", ephemeral=True)
            return

        owner_id = int(channel.topic.split(": ")[1].split(" |")[0])
        owner = interaction.guild.get_member(owner_id)

        transcript = f"Ticket Transcript: {channel.name}\n"
        transcript += f"Opened by: {owner.name if owner else 'Unknown'} ({owner_id})\n"
        transcript += f"Saved at: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}\n"
        transcript += "=" * 50 + "\n\n"

        messages = [msg async for msg in channel.history(limit=200, oldest_first=True)]
        for msg in messages:
            timestamp = msg.created_at.strftime('%Y-%m-%d %H:%M:%S')
            transcript += f"[{timestamp}] {msg.author.name}: {msg.content}\n"

        filename = f"transcript-{channel.name}.txt"
        with open(filename, 'w', encoding='utf-8') as f:
            f.write(transcript)

        embed = discord.Embed(
            title=f"📏 Ticket Transcript: {channel.name}",
            description=f"**Ticket Owner:** {owner.mention if owner else 'Unknown'}\n**Ticket Channel:** {channel.mention}\n**Saved by:** {interaction.user.mention}",
            color=discord.Color.blue(),
            timestamp=datetime.now(timezone.utc)
        )

        await transcript_channel.send(embed=embed, file=discord.File(filename))
        os.remove(filename)

        button.disabled = True
        await interaction.response.edit_message(view=self)
        await interaction.followup.send("Transcript saved.", ephemeral=True)

    @discord.ui.button(label="Open", style=discord.ButtonStyle.gray, custom_id="reopen_ticket_mgmt",
                       emoji="🔓")
    async def reopen_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        channel = interaction.channel
        owner_id = int(channel.topic.split(": ")[1].split(" |")[0])
        owner = interaction.guild.get_member(owner_id)

        closestatus[channel.id] = False

        if owner:
            await channel.set_permissions(owner, read_messages=True, send_messages=True)

        try:
            await interaction.message.delete()
        except:
            pass

        reopen_embed = discord.Embed(
            title="",
            description=f"Ticket Opened by {interaction.user.mention}",
            color=discord.Color.green()
        )

        await interaction.response.send_message(embed=reopen_embed, ephemeral=False)

    @discord.ui.button(label="Verify", style=discord.ButtonStyle.gray, custom_id="verify_accept_mgmt",
                       emoji="✅")
    async def verify_accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        channel = interaction.channel
        owner_id = int(channel.topic.split(": ")[1].split(" |")[0])
        owner = interaction.guild.get_member(owner_id)

        if not owner:
            await interaction.response.send_message("Ticket owner not found!", ephemeral=True)
            return

        guild_config = config.get(str(interaction.guild.id), {})
        remove_role_id = guild_config.get("remove_role")
        add_role_id = guild_config.get("add_role")

        if remove_role_id:
            remove_role = interaction.guild.get_role(remove_role_id)
            if remove_role:
                try:
                    await owner.remove_roles(remove_role)
                except:
                    pass

        if add_role_id:
            add_role = interaction.guild.get_role(add_role_id)
            if add_role:
                try:
                    await owner.add_roles(add_role)
                except:
                    pass

        await log_ticket_event(
            interaction.guild,
            "Ticket Accepted",
            f"{owner.mention}'s ticket was accepted",
            discord.Color.green(),
            {
                "Ticket Owner": f"{owner} ({owner.id})",
                "Accepted By": f"{interaction.user} ({interaction.user.id})",
                "Channel": channel.mention
            }
        )

        accept_embed = discord.Embed(
            description=f"{owner.mention}'s ticket accepted.",
            color=discord.Color.green()
        )

        await interaction.response.send_message(embed=accept_embed)

        await asyncio.sleep(5)
        await channel.delete()

    @discord.ui.button(label="Deny", style=discord.ButtonStyle.gray, custom_id="deny_mgmt", emoji="❌")
    async def deny_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        channel = interaction.channel
        owner_id = int(channel.topic.split(": ")[1].split(" |")[0])
        owner = interaction.guild.get_member(owner_id)

        await log_ticket_event(
            interaction.guild,
            "Ticket Denied.",
            f"{owner.mention if owner else 'User'}'s ticket was denied.",
            discord.Color.red(),
            {
                "Ticket Owner": f"{owner} ({owner.id})" if owner else "Unknown",
                "Denied By": f"{interaction.user} ({interaction.user.id})",
                "Channel": channel.mention
            }
        )

        embed = discord.Embed(
            description=f"{owner.mention if owner else 'User'}'s ticket denied.",
            color=discord.Color.red(),
            timestamp=datetime.now(timezone.utc)
        )

        action_view = DenyActionPanel()
        await interaction.response.send_message(embed=embed, view=action_view)


class TicketControlPanel(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Close Ticket", style=discord.ButtonStyle.gray, custom_id="close_ticket_btn", emoji="🔒")
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        channel = interaction.channel

        if not channel.topic or not channel.topic.startswith("Ticket owner:"):
            await interaction.response.send_message("This is not a ticket channel!", ephemeral=True)
            return

        if closestatus.get(channel.id, False):
            await interaction.response.send_message("Ticket is already closed.", ephemeral=True)
            return
        
        await interaction.response.defer()
        confirm_view = CloseConfirmationView()
        await channel.send("Are you sure you want to close this ticket?", view=confirm_view)


@tasks.loop(hours=1)
async def check_ticket_owners():
    for guild in bot.guilds:
        guild_id = str(guild.id)
        tickets = user_data.get(guild_id, {}).get("tickets", {})

        for channel_id, data in list(tickets.items()):
            channel = guild.get_channel(int(channel_id))

            if not channel:
                del tickets[channel_id]
                continue

            owner = guild.get_member(data["owner_id"])
            if not owner:
                await unregister_ticket(channel)
                try:
                    await channel.delete(reason="Ticket owner left the server")
                except discord.NotFound:
                    pass
                except discord.Forbidden:
                    print(f"Missing permissions to delete channel {channel.name}")
                await asyncio.sleep(1.5)

        await save_json(USER_DATA_FILE, user_data)




@check_ticket_owners.before_loop
async def before_check_ticket_owners():
    await bot.wait_until_ready()

async def schedule_first_check(channel_id: int, owner_id: int, guild_id: int):
    await asyncio.sleep(1200)

    guild = bot.get_guild(guild_id)
    if not guild:
        return

    channel = guild.get_channel(channel_id)
    if not channel:
        return

    owner = guild.get_member(owner_id)
    if not owner:
        try:
            await channel.send("Ticket owner has left the server. DTicket will be promptly deleted.")
            await asyncio.sleep(10)
            await channel.delete(reason="Ticket owner left the server")
        except:
            pass

@bot.event
async def on_message(message):
    if message.author.bot:
        return

    if message.guild is None:
        await log_dm(message)
        return

    if str(message.guild.id) not in config:
        return
    
    if not isinstance(message.channel, TextChannel):
        return
    
    if message.channel.topic and message.channel.topic.startswith("Ticket owner:"):
        try:
            owner_id = int(message.channel.topic.split(": ")[1].split(" |")[0])

            if message.author.id == owner_id:
                message_lower = message.content.lower()

                for word in filtered_words:
                    if word.lower() in message_lower:
                        await message.channel.send(
                            f"{message.author.mention} has been kicked for using filtered language."
                        )

                        await log_ticket_event(
                            message.guild,
                            "User Kicked - Filtered Word",
                            f"{message.author.mention} was kicked for using filtered language",
                            discord.Color.red(),
                            {
                                "User": f"{message.author} ({message.author.id})",
                                "Filtered Word": word,
                                "Message": message.content[:500],
                                "Channel": message.channel.mention
                            }
                        )

                        try:
                            await message.author.send(
                                f"You have been kicked from {message.guild.name} for using filtered language in your ticket."
                            )
                        except:
                            pass

                        try:
                            await message.author.kick(reason=f"Used filtered word in ticket: {word}")
                        except Exception as e:
                            await message.channel.send(f"Failed to kick user: {e}")

                        await asyncio.sleep(5)
                        await message.channel.delete(reason="Ticket owner kicked for filtered language")
                        return
        except:
            pass

    await bot.process_commands(message)


@bot.event
async def on_ready():
    global config, filtered_words, user_data

    config = await load_json(CONFIG_FILE, {})
    filtered_words = await load_json(FILTERED_WORDS_FILE, [])
    user_data = await load_json(USER_DATA_FILE, {})
    for guild in bot.guilds:
        gid = str(guild.id)
        user_data.setdefault(gid, {})
        user_data[gid].setdefault("tickets", {})

    print(f'Logged in as {bot.user.name} ({bot.user.id})')
    print('------')

    bot.add_view(TicketButton())
    bot.add_view(TicketControlPanel())
    bot.add_view(ClosedTicketManagementPanel())
    bot.add_view(DenyActionPanel())

    if not check_ticket_owners.is_running():
        check_ticket_owners.start()

    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} command(s)")
    except Exception as e:
        print(f"Failed to sync commands: {e}")


@bot.tree.command(name="setup", description="Setup the ticket panel")
@app_commands.checks.has_permissions(administrator=True)
async def setup(interaction: discord.Interaction):
    embed = discord.Embed(
        title="Verification Tickets",
        description="Click the button below to create a support ticket.",
        color=discord.Color.dark_green()
    )
    embed.set_footer(text=f"{interaction.guild.name} Support")

    view = TicketButton()
    await interaction.channel.send(embed=embed, view=view)
    await interaction.response.send_message("Ticket panel created!", ephemeral=True)


@bot.tree.command(name="addfilter", description="Add a word to the ticket filter (kicks ticket owners who say it)")
@app_commands.checks.has_permissions(administrator=True)
async def add_filter(interaction: discord.Interaction, word: str):
    global filtered_words

    word = word.strip()

    if word.lower() in [w.lower() for w in filtered_words]:
        await interaction.response.send_message(f"'{word}' is already in the filter list.", ephemeral=True)
        return

    filtered_words.append(word)
    await save_json(FILTERED_WORDS_FILE, filtered_words)

    await interaction.response.send_message(
        f"Added '{word}' to the filter list.", ephemeral=True)


@bot.tree.command(name="removefilter", description="Remove a word from the ticket filter")
@app_commands.checks.has_permissions(administrator=True)
async def remove_filter(interaction: discord.Interaction, word: str):
    global filtered_words

    word = word.strip()

    found = False
    for w in filtered_words:
        if w.lower() == word.lower():
            filtered_words.remove(w)
            found = True
            break

    if not found:
        await interaction.response.send_message(f"'{word}' is not in the filter list.", ephemeral=True)
        return

    await save_json(FILTERED_WORDS_FILE, filtered_words)
    await interaction.response.send_message(f"Removed '{word}' from the filter list.", ephemeral=True)


@bot.tree.command(name="listfilters", description="List all filtered words")
@app_commands.checks.has_permissions(administrator=True)
async def list_filters(interaction: discord.Interaction):
    if not filtered_words:
        await interaction.response.send_message("No filtered words configured.", ephemeral=True)
        return

    words_list = "\n".join([f"- {word}" for word in filtered_words])

    embed = discord.Embed(
        title="Filtered Words",
        description=f"\n\n{words_list}",
        color=discord.Color.red()
    )

    await interaction.response.send_message(embed=embed, ephemeral=True)


bot.run(os.getenv('DISCORD_TOKEN'))