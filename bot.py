import discord
from discord.ext import commands
import yt_dlp as youtube_dl
import asyncio
import re
import os
import json
import logging

# Load configuration from config.json
with open('config.json') as config_file:
    config = json.load(config_file)

# Load or initialize statistics from stats.json
if os.path.exists('stats.json'):
    with open('stats.json') as stats_file:
        stats = json.load(stats_file)
else:
    stats = {
        "total_songs_played": 0,
        "total_hours_played": 0.0
    }

# Set up logging to bot.log file
logging.basicConfig(
    filename='bot.log',
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

intents = discord.Intents.all()
bot = commands.Bot(command_prefix='.', intents=intents)

ffmpeg_options = config.get("ffmpeg_options", {"options": "-vn"})
ytdl_format_options = config.get("ytdl_format_options", {"format": "bestaudio/best", "retries": 3,  "nocheckcertificate": True})
language_outputs = config.get("language_outputs", {})
custom_idle_presence = config.get("custom_idle_presence", "Listening for commands 👽")

ytdl = youtube_dl.YoutubeDL(ytdl_format_options)
queue = []
is_counting_down = False

class YTDLSource(discord.PCMVolumeTransformer):
    def __init__(self, source, *, data, volume=0.5):
        super().__init__(source, volume)
        self.data = data
        self.title = data.get('title')
        self.url = data.get('url')
        self.thumbnail = data.get('thumbnail')
        self.duration = data.get('duration', 0)  # Duration in seconds

    @classmethod
    async def from_url(cls, url, *, loop=None, stream=False):
        loop = loop or asyncio.get_event_loop()
        try:
            data = await loop.run_in_executor(None, lambda: ytdl.extract_info(url, download=not stream))
            if 'entries' in data:
                data = data['entries'][0]  # Handle playlists by selecting the first entry

            filename = data['url'] if stream else ytdl.prepare_filename(data)
            return cls(discord.FFmpegPCMAudio(filename, **ffmpeg_options), data=data)
        except Exception as e:
            print(f"Error with URL extraction: {e}")
            return None

@bot.event
async def on_ready():
    logging.info(f'Bot connected as {bot.user}')
    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.playing, name=custom_idle_presence))

@bot.command(name='p', help='Plays a song from YouTube or a direct URL')
async def play(ctx, *, search: str):
    global is_counting_down
    if ctx.author.voice is None:
        await ctx.send(language_outputs["not_in_voice_channel"])
        return
    
    voice_channel = ctx.author.voice.channel
    if ctx.voice_client is None:
        await voice_channel.connect()

    voice_client = ctx.voice_client
    is_youtube_url = re.match(r'https?://(www\.)?(youtube\.com|youtu\.?be)/.+', search)
    is_direct_url = re.match(r'https?://.+\.(mp3|wav|ogg|flac|mp4)', search)  # Add other formats if needed

    async with ctx.typing():
        try:
            if is_youtube_url:
                query = search
            elif is_direct_url:
                query = search
            else:
                query = f"ytsearch:{search}"

            player = await YTDLSource.from_url(query, loop=bot.loop, stream=True)
            
            if player is None:
                await ctx.send("Failed to retrieve data from the URL.")
                return
            
            title = player.title
            queue.append((player, title))
            logging.info(f'Added "{title}" to queue.')
        except ValueError:
            await ctx.send("No results found for the query.")
            return

    if not voice_client.is_playing():
        await start_playback(ctx)
    else:
        embed = discord.Embed(title=language_outputs["song_added"], description=title, color=0x00ff00)
        embed.set_thumbnail(url=player.thumbnail)
        await ctx.send(embed=embed)


async def start_playback(ctx):
    global is_counting_down
    if queue:
        is_counting_down = False
        player, title = queue.pop(0)
        
        # Attempt to play the audio
        ctx.voice_client.play(player, after=lambda e: bot.loop.create_task(play_next(ctx)) if e is None else print(f'Player error: {e}'))
        
        # Update stats
        stats["total_songs_played"] += 1
        stats["total_hours_played"] += player.duration / 3600  # Convert seconds to hours

        # Save stats to file
        with open('stats.json', 'w') as stats_file:
            json.dump(stats, stats_file)

        # Log the playback start
        logging.info(f'Now playing: "{title}". Total songs played: {stats["total_songs_played"]}. Total hours played: {round(stats["total_hours_played"], 2)} hours.')

        # Prepare the "Now Playing" embed
        embed = discord.Embed(title=language_outputs["now_playing"], description=title, color=0x00ff00)
        if isinstance(player, YTDLSource):
            embed.set_thumbnail(url=player.thumbnail)

        # Send the "Now Playing" embed asynchronously
        asyncio.create_task(ctx.send(embed=embed))

        # Update Discord presence asynchronously
        asyncio.create_task(bot.change_presence(activity=discord.Activity(type=discord.ActivityType.listening, name=title)))
    else:
        await handle_empty_queue(ctx)


async def play_next(ctx):
    if queue:
        await start_playback(ctx)
    else:
        await handle_empty_queue(ctx)

async def handle_empty_queue(ctx):
    global is_counting_down
    if not is_counting_down:
        is_counting_down = True
        await ctx.send(language_outputs["queue_empty"])
        await asyncio.sleep(3)
        if is_counting_down and ctx.voice_client and not ctx.voice_client.is_connected():
            try:
                await ctx.voice_client.connect(reconnect=True)
            except discord.ClientException:
                logging.info("Attempted reconnection failed.")
        elif is_counting_down and ctx.voice_client:
            await ctx.voice_client.disconnect()
        is_counting_down = False
        await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.playing, name=custom_idle_presence))


@bot.command(name='skip', help='Skips the current song')
async def skip(ctx):
    if ctx.voice_client.is_playing():
        ctx.voice_client.stop()
        await ctx.send(language_outputs["song_skipped"])
        logging.info("Song skipped by user.")
    else:
        await ctx.send(language_outputs["no_song_playing"])

@bot.command(name='stop', help='Stops the music and clears the queue')
async def stop(ctx):
    queue.clear()
    if ctx.voice_client:
        await ctx.voice_client.disconnect()
    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.playing, name=custom_idle_presence))
    await ctx.send(language_outputs["music_stopped"])
    logging.info("Music stopped and queue cleared.")

@bot.command(name='stats', help='Shows the total number of songs played and total hours of audio played')
async def stats_command(ctx):
    total_songs = stats["total_songs_played"]
    total_hours = round(stats["total_hours_played"], 2)
    await ctx.send(f"Total songs played: {total_songs}\nTotal hours played: {total_hours} hours")
    logging.info(f".stats command called. Total songs played: {total_songs}. Total hours played: {total_hours} hours.")

bot.run(config['token'])
