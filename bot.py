import discord
from discord.ext import commands
import yt_dlp as youtube_dl
import asyncio
import re
import time
import os
import json
import logging

# Load configuration from config.json
with open('config.json') as config_file:
    config = json.load(config_file)

language_outputs = config.get("language_outputs", {})
stats_path = 'stats.json'

# Load or initialize statistics
if os.path.exists(stats_path):
    with open(stats_path) as stats_file:
        stats = json.load(stats_file)
else:
    stats = {
        "total_songs_played": 0,
        "total_hours_played": 0.0
    }
    with open(stats_path, 'w') as stats_file:
        json.dump(stats, stats_file)

# Set up logging
logging.basicConfig(
    filename='bot.log',
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

intents = discord.Intents.all()
bot = commands.Bot(command_prefix='.', intents=intents)

ffmpeg_options = config.get("ffmpeg_options", {"options": "-vn"})
ytdl_format_options = config.get("ytdl_format_options", {"format": "bestaudio/best", "retries": 3, "nocheckcertificate": True})
custom_idle_presence = config.get("custom_idle_presence", "Listening for commands 👽")

ytdl = youtube_dl.YoutubeDL(ytdl_format_options)

# --- Per-guild state ---
# Dict: guild_id -> { 'queue': [...], 'is_counting_down': False, 'playing': False, 'current_title': None }
guild_states = {}

def get_guild_state(guild_id):
    if guild_id not in guild_states:
        guild_states[guild_id] = {
            "queue": [],
            "is_counting_down": False,
            "playing": False,
            "current_title": None
        }
    return guild_states[guild_id]

def get_playing_guild_count():
    return sum(
        1 for state in guild_states.values()
        if state.get("playing", False)
    )

async def update_bot_presence():
    playing_count = get_playing_guild_count()
    if playing_count == 0:
        await bot.change_presence(activity=discord.Activity(
            type=discord.ActivityType.playing,
            name=custom_idle_presence
        ))
    elif playing_count == 1:
        # Show the song title of the only playing guild
        for state in guild_states.values():
            if state.get("playing", False) and state.get("current_title"):
                await bot.change_presence(activity=discord.Activity(
                    type=discord.ActivityType.listening,
                    name=state["current_title"]
                ))
                return
        # Fallback
        await bot.change_presence(activity=discord.Activity(
            type=discord.ActivityType.listening,
            name="a song"
        ))
    else:
        await bot.change_presence(activity=discord.Activity(
            type=discord.ActivityType.listening,
            name=f"music in {playing_count} servers"
        ))

class YTDLSource(discord.PCMVolumeTransformer):
    def __init__(self, source, *, data, volume=0.5):
        super().__init__(source, volume)
        self.data = data
        self.title = data.get('title')
        self.url = data.get('url')
        self.thumbnail = data.get('thumbnail')
        self.duration = data.get('duration', 0)

    @classmethod
    async def from_url(cls, url, *, loop=None, stream=False):
        loop = loop or asyncio.get_event_loop()
        try:
            data = await loop.run_in_executor(None, lambda: ytdl.extract_info(url, download=not stream))
            if 'entries' in data:
                data = data['entries'][0]
            filename = data['url'] if stream else ytdl.prepare_filename(data)
            return cls(discord.FFmpegPCMAudio(filename, **ffmpeg_options), data=data)
        except Exception as e:
            print(f"Error with URL extraction: {e}")
            return None

@bot.event
async def on_ready():
    logging.info(f'Bot connected as {bot.user}')
    await update_bot_presence()
    print(f"Bot is online! Credits: Bot developed by Nixietab. GitHub: https://github.com/nixietab/el-miron")

@bot.command(name='p', help='Plays a song or playlist from YouTube, SoundCloud, or a direct URL')
async def play(ctx, *, search: str):
    state = get_guild_state(ctx.guild.id)

    if ctx.author.voice is None:
        await ctx.send(language_outputs.get("not_in_voice_channel", "You are not in a voice channel."))
        return

    voice_channel = ctx.author.voice.channel
    if ctx.voice_client is None:
        await voice_channel.connect()

    voice_client = ctx.voice_client
    is_url = bool(re.match(r'https?://.+', search))
    is_playlist = "list=" in search

    async with ctx.typing():
        try:
            if is_playlist:
                playlist_tracks = await load_playlist(search)
                if not playlist_tracks:
                    await ctx.send("No tracks found in the playlist.")
                    return
                for player in playlist_tracks:
                    state['queue'].append((player, player.title))
                    logging.info(f'Added "{player.title}" to queue for guild {ctx.guild.id}.')
                await ctx.send(f"Added {len(playlist_tracks)} tracks from the playlist to the queue.")
            else:
                # If not a URL, search on YouTube
                if not is_url:
                    search = f"ytsearch:{search}"

                player = await YTDLSource.from_url(search, loop=bot.loop, stream=True)
                if player is None:
                    await ctx.send("Failed to retrieve data from the URL.")
                    return
                state['queue'].append((player, player.title))
                logging.info(f'Added "{player.title}" to queue for guild {ctx.guild.id}.')

        except Exception as e:
            await ctx.send(f"Error retrieving data: {str(e)}")
            return

    if not voice_client.is_playing():
        await start_playback(ctx)
    else:
        title = player.title if 'player' in locals() else search
        embed = discord.Embed(
            title=language_outputs.get("song_added", "Song Added"),
            description=title,
            color=0x00ff00
        )
        await ctx.send(embed=embed)

async def load_playlist(playlist_url):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: get_playlist_videos(playlist_url))

def get_playlist_videos(playlist_url):
    ydl_opts = {
        'format': 'bestaudio/best',
        'noplaylist': False,
        'extract_flat': True
    }
    with youtube_dl.YoutubeDL(ydl_opts) as ydl:
        info_dict = ydl.extract_info(playlist_url, download=False)
        if 'entries' not in info_dict:
            return []
        result = []
        for entry in info_dict['entries']:
            try:
                with youtube_dl.YoutubeDL(ytdl_format_options) as ydl_full:
                    full = ydl_full.extract_info(entry['url'], download=False)
                    filename = full['url']
                    source = YTDLSource(discord.FFmpegPCMAudio(filename, **ffmpeg_options), data=full)
                    result.append(source)
            except Exception:
                continue
        return result

async def start_playback(ctx):
    state = get_guild_state(ctx.guild.id)
    if state['queue']:
        player, title = state['queue'].pop(0)
        state["playing"] = True
        state["current_title"] = title

        def after_playing(e):
            fut = play_next(ctx)
            asyncio.run_coroutine_threadsafe(fut, bot.loop)

        ctx.voice_client.play(player, after=after_playing)

        # Update stats
        stats["total_songs_played"] += 1
        stats["total_hours_played"] += player.duration / 3600
        with open(stats_path, 'w') as stats_file:
            json.dump(stats, stats_file)

        logging.info(f'Now playing: "{title}" in guild {ctx.guild.id}. Total songs: {stats["total_songs_played"]}. Total hours: {round(stats["total_hours_played"], 2)}.')

        embed = discord.Embed(
            title=language_outputs.get("now_playing", "Now Playing"),
            description=title,
            color=0x00ff00
        )
        if isinstance(player, YTDLSource):
            embed.set_thumbnail(url=player.thumbnail)
        asyncio.create_task(ctx.send(embed=embed))
        await update_bot_presence()
    else:
        state["playing"] = False
        state["current_title"] = None
        await update_bot_presence()
        await handle_empty_queue(ctx)

async def play_next(ctx):
    state = get_guild_state(ctx.guild.id)
    if state['queue']:
        await start_playback(ctx)
    else:
        state["playing"] = False
        state["current_title"] = None
        await update_bot_presence()
        await handle_empty_queue(ctx)

async def handle_empty_queue(ctx):
    state = get_guild_state(ctx.guild.id)
    if not state['is_counting_down']:
        state['is_counting_down'] = True
        await ctx.send(language_outputs.get("queue_empty", "The queue is empty."))
        await asyncio.sleep(3)
        if state['is_counting_down'] and ctx.voice_client and ctx.voice_client.is_connected():
            try:
                await ctx.voice_client.disconnect()
            except discord.ClientException:
                logging.info(f"Disconnect failed in guild {ctx.guild.id}.")
        state['is_counting_down'] = False
        state["playing"] = False
        state["current_title"] = None
        await update_bot_presence()

@bot.command(name='skip', help='Skips the current song')
async def skip(ctx):
    if ctx.voice_client and ctx.voice_client.is_playing():
        ctx.voice_client.stop()
        await ctx.send(language_outputs.get("song_skipped", "Song skipped. 👽"))
        logging.info(f"Song skipped by user in guild {ctx.guild.id}.")
    else:
        await ctx.send(language_outputs.get("no_song_playing", "No song is currently playing. 👽"))

@bot.command(name='stop', help='Stops the music and clears the queue')
async def stop(ctx):
    state = get_guild_state(ctx.guild.id)
    state['queue'].clear()
    state["playing"] = False
    state["current_title"] = None
    if ctx.voice_client:
        await ctx.voice_client.disconnect()
    await update_bot_presence()
    await ctx.send(language_outputs.get("music_stopped", "Music stopped."))
    logging.info(f"Music stopped and queue cleared in guild {ctx.guild.id}.")

@bot.command(name='stats', help='Shows the total number of songs played and total hours of audio played')
async def stats_command(ctx):
    total_songs = stats["total_songs_played"]
    total_hours = round(stats["total_hours_played"], 2)
    await ctx.send(f"Total songs played: {total_songs}\nTotal hours played: {total_hours} hours")
    logging.info(f".stats command called in guild {ctx.guild.id}. Total songs played: {total_songs}. Total hours played: {total_hours} hours.")

@bot.command(name='queue', help='Shows the current song and the queue')
async def show_queue(ctx):
    state = get_guild_state(ctx.guild.id)

    if not ctx.voice_client:
        await ctx.send(language_outputs.get("not_in_voice_channel", "I am not connected to a voice channel."))
        return

    if not state['queue'] and not ctx.voice_client.is_playing():
        empty_queue_message = language_outputs.get("empty_queue", "The queue is currently empty.")
        await ctx.send(empty_queue_message)
        return

    embed = discord.Embed(
        title=language_outputs.get("queue_title", "Music Queue 🎶"),
        color=0x00ff00
    )
    currently_playing_title = getattr(ctx.voice_client.source, "title", None)

    if currently_playing_title:
        embed.add_field(
            name=language_outputs.get("currently_playing", "Currently Playing:"),
            value=currently_playing_title,
            inline=False
        )
    else:
        embed.add_field(
            name=language_outputs.get("currently_playing", "Currently Playing:"),
            value=language_outputs.get("no_song_playing", "No song is currently playing."),
            inline=False
        )

    if state['queue']:
        up_next_songs = state['queue'][1:] if currently_playing_title and state['queue'][0][1] == currently_playing_title else state['queue']
        queue_list = "\n".join([f"{i+1}. {song[1]}" for i, song in enumerate(up_next_songs)])
        if queue_list:
            embed.add_field(
                name=language_outputs.get("up_next", "Up Next:"),
                value=queue_list,
                inline=False
            )
    else:
        embed.add_field(
            name=language_outputs.get("up_next", "Up Next:"),
            value=language_outputs.get("no_songs_in_queue", "No songs in the queue."),
            inline=False
        )

    await ctx.send(embed=embed)

@bot.command(name='config', help='Displays the current bot configuration')
async def show_config(ctx):
    try:
        with open('config.json') as config_file:
            config_data = json.load(config_file)

        filtered_config = {
            key: config_data[key]
            for key in ['ffmpeg_options', 'ytdl_format_options', 'language_outputs']
            if key in config_data
        }

        message = ""
        if 'ffmpeg_options' in filtered_config:
            message += "## FFmpeg Options:\n"
            for option, value in filtered_config['ffmpeg_options'].items():
                message += f"- {option}: {value}\n"
            message += "\n"
        if 'ytdl_format_options' in filtered_config:
            message += "## yt-dlp Options:\n"
            for option, value in filtered_config['ytdl_format_options'].items():
                message += f"- {option}: {value}\n"
            message += "\n"
        if 'language_outputs' in filtered_config:
            message += "## Language Outputs:\n"
            for language, output in filtered_config['language_outputs'].items():
                message += f"- {language}: {output}\n"
            message += "\n"
        if len(message) > 2000:
            for i in range(0, len(message), 2000):
                await ctx.send(f"\n{message[i:i+2000]}")
        else:
            await ctx.send(f"\n{message}")

    except Exception as e:
        logging.error(f"Failed to load config.json: {e}")
        await ctx.send("Error loading the configuration file.")

@bot.command(name='ping', help='Responds with pong and shows the message send and receive times.')
async def ping(ctx):
    start_time = time.monotonic()
    message = await ctx.send("🏓 Pong! Calculating latency...")
    end_time = time.monotonic()
    receive_latency = (end_time - start_time) * 1000
    send_latency = (message.created_at.timestamp() - ctx.message.created_at.timestamp()) * 1000
    await message.edit(content=f"🏓 Pong!\nReceive Latency: {receive_latency:.2f} ms\nSend Latency: {send_latency:.2f} ms")

# Default name and avatar
DEFAULT_NAME = "ElMiron"
DEFAULT_AVATAR = "https://i.imgur.com/0snQXry.jpeg"

@bot.command()
async def pretend(ctx, *args):
    await ctx.message.delete()

    # Parse input manually
    if len(args) == 0:
        await ctx.send("❌ Usage: `!pretend [name (opt)] [avatar_url (opt)] <message>`", delete_after=5)
        return

    # Determine which arguments are present
    if len(args) >= 3:
        name = args[0]
        avatar_url = args[1]
        message = " ".join(args[2:])
    elif len(args) == 2:
        # Check if second arg looks like a URL
        if args[1].startswith("http://") or args[1].startswith("https://"):
            name = args[0]
            avatar_url = args[1]
            message = f""
        else:
            name = args[0]
            avatar_url = DEFAULT_AVATAR
            message = args[1]
    else:  # Only one argument (the message)
        name = DEFAULT_NAME
        avatar_url = DEFAULT_AVATAR
        message = args[0]

    if not message.strip():
        await ctx.send("❌ You must provide a message.", delete_after=5)
        return

    # Create or reuse webhook
    webhooks = await ctx.channel.webhooks()
    webhook = discord.utils.get(webhooks, name="PretendWebhook")
    if webhook is None:
        webhook = await ctx.channel.create_webhook(name="PretendWebhook")

    # Send webhook message
    await webhook.send(
        content=message,
        username=name,
        avatar_url=avatar_url
    )


bot.run(config['token'])
