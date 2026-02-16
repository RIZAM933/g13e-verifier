import discord
from discord.ext import commands
from discord import app_commands
import pytesseract
import sqlite3
from PIL import Image
import io
import os
from flask import Flask
import threading
import datetime

# ------------------- KEEP ALIVE -------------------
app = Flask("")

@app.route("/")
def home():
    return "Bot is running!"

def run():
    app.run(host="0.0.0.0", port=8080)

def keep_alive():
    t = threading.Thread(target=run)
    t.start()

keep_alive()
# -----------------------------------------------

# ------------------- OCR Setup ------------------
pytesseract.pytesseract.tesseract_cmd = os.path.join(os.path.dirname(__file__), "tesseract_bin/tesseract")
# -----------------------------------------------

# ------------------- Discord Setup ------------------
TOKEN = os.getenv("DISCORD_TOKEN")

# ------------------- Вставь свои ID ------------------
GUILD_ID = 1376255869578252358
ROLE_ID = 1378338801654693928       # ID роли Verified
LOG_CHANNEL_ID = 1378338260547534908  # ID канала логов
VERIFICATION_CHANNEL_ID = 1378337768958332968  # ID канала для скриншотов
# -----------------------------------------------------

intents = discord.Intents.default()
intents.members = True
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)
# ----------------------------------------------------

# ------------------- SQLite Setup ------------------
conn = sqlite3.connect("database.db")
c = conn.cursor()
c.execute("""
CREATE TABLE IF NOT EXISTS users (
    discord_id TEXT PRIMARY KEY,
    game_id TEXT UNIQUE,
    verified_at TEXT
)
""")
conn.commit()
# ----------------------------------------------------

# ------------------- Helpers ------------------
def get_role(member):
    return member.guild.get_role(ROLE_ID)

def get_log_channel(guild):
    return guild.get_channel(LOG_CHANNEL_ID)

def is_verification_channel(message):
    return message.channel.id == VERIFICATION_CHANNEL_ID

def extract_game_info(image_bytes):
    image = Image.open(io.BytesIO(image_bytes))
    text = pytesseract.image_to_string(image)
    import re
    id_match = re.search(r'\b\d{5,10}\b', text)
    tag_match = re.search(r'\[G13E\]', text)
    game_id = id_match.group() if id_match else None
    has_tag = bool(tag_match)
    return game_id, has_tag
# -------------------------------------------------

# ------------------- Events ------------------
@bot.event
async def on_ready():
    print(f"{bot.user} запущен")
    try:
        synced = await bot.tree.sync()
        print(f"Slash-команды синхронизированы: {len(synced)}")
    except Exception as e:
        print(f"Ошибка синхронизации: {e}")

@bot.event
async def on_member_join(member):
    c.execute("SELECT game_id FROM users WHERE discord_id = ?", (str(member.id),))
    result = c.fetchone()
    if result:
        role = get_role(member)
        if role:
            await member.add_roles(role)
            log_channel = get_log_channel(member.guild)
            if log_channel:
                await log_channel.send(f"Роль восстановлена для {member.mention} ({result[0]})")
# -------------------------------------------------

# ------------------- Slash Commands ------------------
@bot.tree.command(name="idlist", description="Список всех зарегистрированных ID")
@app_commands.checks.has_permissions(administrator=True)
async def idlist(interaction: discord.Interaction):
    c.execute("SELECT discord_id, game_id FROM users")
    rows = c.fetchall()
    description = "\n".join([f"<@{d}> — {g}" for d, g in rows]) or "Нет пользователей"
    embed = discord.Embed(title="Список верифицированных ID", description=description, color=0xFFA500)
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="removeid", description="Удалить ID конкретного пользователя")
@app_commands.checks.has_permissions(administrator=True)
async def removeid(interaction: discord.Interaction, user: discord.Member):
    c.execute("DELETE FROM users WHERE discord_id = ?", (str(user.id),))
    conn.commit()
    role = get_role(user)
    if role:
        await user.remove_roles(role)
    log_channel = get_log_channel(interaction.guild)
    if log_channel:
        await log_channel.send(f"ID удалён и роль снята у {user.mention}")
    await interaction.response.send_message(f"ID удалён у {user.mention}", ephemeral=True)

@bot.tree.command(name="adminhelp", description="Список всех админ-команд")
@app_commands.checks.has_permissions(administrator=True)
async def adminhelp(interaction: discord.Interaction):
    embed = discord.Embed(title="Админ-команды", color=0xFFA500)
    embed.add_field(name="/idlist", value="Показывает список всех ID", inline=False)
    embed.add_field(name="/removeid <user>", value="Удаляет ID и снимает роль", inline=False)
    embed.add_field(name="/adminhelp", value="Показывает этот список", inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)
# -------------------------------------------------

# ------------------- Verification ------------------
@bot.event
async def on_message(message):
    if message.author.bot:
        return
    if not is_verification_channel(message):
        return
    if not message.attachments:
        await message.add_reaction("❌")
        await message.author.send("Пожалуйста, отправьте скриншот профиля.")
        return

    attachment = message.attachments[0]
    img_bytes = await attachment.read()
    game_id, has_tag = extract_game_info(img_bytes)

    log_channel = get_log_channel(message.guild)

    # Проверка тега
    if not has_tag:
        await message.add_reaction("❌")
        await message.author.send("Тег [G13E] не найден.")
        if log_channel:
            await log_channel.send(f"{message.author} отправил скрин без тега [G13E]")
        return

    # Проверка ID
    if not game_id:
        await message.add_reaction("❌")
        await message.author.send("Игровой ID не распознан.")
        if log_channel:
            await log_channel.send(f"{message.author} отправил скрин, но ID не распознан")
        return

    # Проверка уникальности
    c.execute("SELECT discord_id FROM users WHERE game_id = ?", (game_id,))
    existing = c.fetchone()
    if existing and str(message.author.id) != existing[0]:
        await message.add_reaction("❌")
        await message.author.send("Этот ID уже зарегистрирован у другого пользователя. Мут на 3 дня.")
        await message.guild.timeout(discord.Object(id=message.author.id), duration=3*24*60*60)
        if log_channel:
            await log_channel.send(f"{message.author} пытался использовать чужой ID {game_id}")
        return

    # Успешная верификация
    c.execute("INSERT OR REPLACE INTO users (discord_id, game_id, verified_at) VALUES (?, ?, ?)",
              (str(message.author.id), game_id, datetime.datetime.utcnow().isoformat()))
    conn.commit()
    role = get_role(message.author)
    if role:
        await message.author.add_roles(role)
    await message.add_reaction("✅")
    await message.author.send(f"Верификация прошла успешно! Твой ID: {game_id}")
    if log_channel:
        await log_channel.send(f"{message.author} успешно верифицирован. ID: {game_id}")
# -------------------------------------------------

# ------------------- Run Bot ------------------
bot.run(TOKEN)
# -------------------------------------------------
import discord
from discord.ext import commands
from discord import app_commands
import pytesseract
import sqlite3
from PIL import Image
import io
import os
from flask import Flask
import threading
import datetime

# ------------------- KEEP ALIVE -------------------
app = Flask("")

@app.route("/")
def home():
    return "Bot is running!"

def run():
    app.run(host="0.0.0.0", port=8080)

def keep_alive():
    t = threading.Thread(target=run)
    t.start()

keep_alive()
# -----------------------------------------------

# ------------------- OCR Setup ------------------
pytesseract.pytesseract.tesseract_cmd = os.path.join(os.path.dirname(__file__), "tesseract_bin/tesseract")
# -----------------------------------------------

# ------------------- Discord Setup ------------------
TOKEN = os.getenv("DISCORD_TOKEN")

# ------------------- Вставь свои ID ------------------
GUILD_ID = 1376255869578252358
ROLE_ID = 123456789012345678       # ID роли Verified
LOG_CHANNEL_ID = 234567890123456789  # ID канала логов
VERIFICATION_CHANNEL_ID = 345678901234567890  # ID канала для скриншотов
# -----------------------------------------------------

intents = discord.Intents.default()
intents.members = True
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)
# ----------------------------------------------------

# ------------------- SQLite Setup ------------------
conn = sqlite3.connect("database.db")
c = conn.cursor()
c.execute("""
CREATE TABLE IF NOT EXISTS users (
    discord_id TEXT PRIMARY KEY,
    game_id TEXT UNIQUE,
    verified_at TEXT
)
""")
conn.commit()
# ----------------------------------------------------

# ------------------- Helpers ------------------
def get_role(member):
    return member.guild.get_role(ROLE_ID)

def get_log_channel(guild):
    return guild.get_channel(LOG_CHANNEL_ID)

def is_verification_channel(message):
    return message.channel.id == VERIFICATION_CHANNEL_ID

def extract_game_info(image_bytes):
    image = Image.open(io.BytesIO(image_bytes))
    text = pytesseract.image_to_string(image)
    import re
    id_match = re.search(r'\b\d{5,10}\b', text)
    tag_match = re.search(r'\[G13E\]', text)
    game_id = id_match.group() if id_match else None
    has_tag = bool(tag_match)
    return game_id, has_tag
# -------------------------------------------------

# ------------------- Events ------------------
@bot.event
async def on_ready():
    print(f"{bot.user} запущен")
    try:
        synced = await bot.tree.sync()
        print(f"Slash-команды синхронизированы: {len(synced)}")
    except Exception as e:
        print(f"Ошибка синхронизации: {e}")

@bot.event
async def on_member_join(member):
    c.execute("SELECT game_id FROM users WHERE discord_id = ?", (str(member.id),))
    result = c.fetchone()
    if result:
        role = get_role(member)
        if role:
            await member.add_roles(role)
            log_channel = get_log_channel(member.guild)
            if log_channel:
                await log_channel.send(f"Роль восстановлена для {member.mention} ({result[0]})")
# -------------------------------------------------

# ------------------- Slash Commands ------------------
@bot.tree.command(name="idlist", description="Список всех зарегистрированных ID")
@app_commands.checks.has_permissions(administrator=True)
async def idlist(interaction: discord.Interaction):
    c.execute("SELECT discord_id, game_id FROM users")
    rows = c.fetchall()
    description = "\n".join([f"<@{d}> — {g}" for d, g in rows]) or "Нет пользователей"
    embed = discord.Embed(title="Список верифицированных ID", description=description, color=0xFFA500)
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="removeid", description="Удалить ID конкретного пользователя")
@app_commands.checks.has_permissions(administrator=True)
async def removeid(interaction: discord.Interaction, user: discord.Member):
    c.execute("DELETE FROM users WHERE discord_id = ?", (str(user.id),))
    conn.commit()
    role = get_role(user)
    if role:
        await user.remove_roles(role)
    log_channel = get_log_channel(interaction.guild)
    if log_channel:
        await log_channel.send(f"ID удалён и роль снята у {user.mention}")
    await interaction.response.send_message(f"ID удалён у {user.mention}", ephemeral=True)

@bot.tree.command(name="adminhelp", description="Список всех админ-команд")
@app_commands.checks.has_permissions(administrator=True)
async def adminhelp(interaction: discord.Interaction):
    embed = discord.Embed(title="Админ-команды", color=0xFFA500)
    embed.add_field(name="/idlist", value="Показывает список всех ID", inline=False)
    embed.add_field(name="/removeid <user>", value="Удаляет ID и снимает роль", inline=False)
    embed.add_field(name="/adminhelp", value="Показывает этот список", inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)
# -------------------------------------------------

# ------------------- Verification ------------------
@bot.event
async def on_message(message):
    if message.author.bot:
        return
    if not is_verification_channel(message):
        return
    if not message.attachments:
        await message.add_reaction("❌")
        await message.author.send("Пожалуйста, отправьте скриншот профиля.")
        return

    attachment = message.attachments[0]
    img_bytes = await attachment.read()
    game_id, has_tag = extract_game_info(img_bytes)

    log_channel = get_log_channel(message.guild)

    # Проверка тега
    if not has_tag:
        await message.add_reaction("❌")
        await message.author.send("Тег [G13E] не найден.")
        if log_channel:
            await log_channel.send(f"{message.author} отправил скрин без тега [G13E]")
        return

    # Проверка ID
    if not game_id:
        await message.add_reaction("❌")
        await message.author.send("Игровой ID не распознан.")
        if log_channel:
            await log_channel.send(f"{message.author} отправил скрин, но ID не распознан")
        return

    # Проверка уникальности
    c.execute("SELECT discord_id FROM users WHERE game_id = ?", (game_id,))
    existing = c.fetchone()
    if existing and str(message.author.id) != existing[0]:
        await message.add_reaction("❌")
        await message.author.send("Этот ID уже зарегистрирован у другого пользователя. Мут на 3 дня.")
        await message.guild.timeout(discord.Object(id=message.author.id), duration=3*24*60*60)
        if log_channel:
            await log_channel.send(f"{message.author} пытался использовать чужой ID {game_id}")
        return

    # Успешная верификация
    c.execute("INSERT OR REPLACE INTO users (discord_id, game_id, verified_at) VALUES (?, ?, ?)",
              (str(message.author.id), game_id, datetime.datetime.utcnow().isoformat()))
    conn.commit()
    role = get_role(message.author)
    if role:
        await message.author.add_roles(role)
    await message.add_reaction("✅")
    await message.author.send(f"Верификация прошла успешно! Твой ID: {game_id}")
    if log_channel:
        await log_channel.send(f"{message.author} успешно верифицирован. ID: {game_id}")
# -------------------------------------------------

# ------------------- Run Bot ------------------
bot.run(TOKEN)
# -------------------------------------------------
