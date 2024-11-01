import discord
from discord.ext import commands
import yt_dlp as youtube_dl
import asyncio
import re
import os
import json

# Load configuration from config.json
with open('config.json') as config_file:
    config = json.load(config_file)

intents = discord.Intents.all()
bot = commands.Bot(command_prefix='.', intents=intents)

ffmpeg_options = config.get("ffmpeg_options", {"options": "-vn"})
ytdl_format_options = config.get("ytdl_format_options", {"format": "bestaudio/best"})
language_outputs = config.get("language_outputs", {})

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
    print(f'Bot connected as {bot.user}')

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

    async with ctx.typing():
        try:
            query = search if is_youtube_url else f"ytsearch:{search}"
            player = await YTDLSource.from_url(query, loop=bot.loop, stream=True)
            
            if player is None:
                await ctx.send("Failed to retrieve data from the URL.")
                return
            
            title = player.title
            queue.append((player, title))
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
        ctx.voice_client.play(player, after=lambda e: bot.loop.create_task(play_next(ctx)) if e is None else print(f'Player error: {e}'))
        
        # Send the "Now Playing" embed
        embed = discord.Embed(title=language_outputs["now_playing"], description=title, color=0x00ff00)
        if isinstance(player, YTDLSource):
            embed.set_thumbnail(url=player.thumbnail)
        await ctx.send(embed=embed)
        
        # Set Rich Presence to show the currently playing song
        await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.listening, name=title))
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
        if is_counting_down and ctx.voice_client:
            await ctx.voice_client.disconnect()
        is_counting_down = False
        # Clear the Rich Presence when queue is empty
        await bot.change_presence(activity=None)

# Make sure to reset presence when the bot starts up
@bot.event
async def on_ready():
    print(f'Bot connected as {bot.user}')
    await bot.change_presence(activity=None)  # Clear Rich Presence on startup


    
@bot.command(name='skip', help='Skips the current song')
async def skip(ctx):
    if ctx.voice_client.is_playing():
        ctx.voice_client.stop()
        await ctx.send(language_outputs["song_skipped"])
    else:
        await ctx.send(language_outputs["no_song_playing"])

@bot.command(name='stop', help='Stops the music and clears the queue')
async def stop(ctx):
    queue.clear()
    if ctx.voice_client:
        await ctx.voice_client.disconnect()
    await ctx.send(language_outputs["music_stopped"])

bot.run(config['token'])
