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

intents = discord.Intents.all()  # Enable all intents
bot = commands.Bot(command_prefix='.', intents=intents)

# Ensure FFmpeg is in your PATH
ffmpeg_options = config.get("ffmpeg_options", {"options": "-vn"})
ytdl_format_options = config.get("ytdl_format_options", {})
language_outputs = config.get("language_outputs", {})

ytdl = youtube_dl.YoutubeDL(ytdl_format_options)
queue = []

class YTDLSource(discord.PCMVolumeTransformer):
    def __init__(self, source, *, data, volume=0.5):
        super().__init__(source, volume)
        self.data = data
        self.title = data.get('title')
        self.url = data.get('url')
        self.thumbnail = data.get('thumbnail')  # Store the thumbnail URL

    @classmethod
    async def from_url(cls, url, *, loop=None, stream=False):
        loop = loop or asyncio.get_event_loop()
        data = await loop.run_in_executor(None, lambda: ytdl.extract_info(url, download=not stream))
        
        if 'entries' in data:
            # Playlist, take the first entry
            data = data['entries'][0]

        filename = data['url'] if stream else ytdl.prepare_filename(data)
        return cls(discord.FFmpegPCMAudio(filename, **ffmpeg_options), data=data)

@bot.event
async def on_ready():
    print(f'Bot connected as {bot.user}')

@bot.command(name='p', help='Plays a song from YouTube or a direct URL')
async def play(ctx, *, search: str):
    # Ensure the user is in a voice channel
    if ctx.author.voice is None:
        await ctx.send(language_outputs["not_in_voice_channel"])
        return
    
    voice_channel = ctx.author.voice.channel
    if ctx.voice_client is None:
        await voice_channel.connect()

    voice_client = ctx.voice_client

    # Check if the input is a URL and handle it accordingly
    if re.match(r'^https?://', search):
        # Direct URL
        async with ctx.typing():
            player = discord.FFmpegPCMAudio(search, **ffmpeg_options)
            # Extract the filename from the URL
            title = os.path.basename(search)  # Get just the filename from the URL
            queue.append((player, title))
    else:
        # YouTube search
        async with ctx.typing():
            player = await YTDLSource.from_url(f"ytsearch:{search}", loop=bot.loop, stream=True)
            queue.append((player, player.title))

    if not voice_client.is_playing():
        await start_playback(ctx)
    else:
        embed = discord.Embed(title=language_outputs["song_added"], description=title, color=0x00ff00)
        embed.set_thumbnail(url=player.thumbnail)  # Show the thumbnail if available
        await ctx.send(embed=embed)

async def start_playback(ctx):
    if queue:
        player, title = queue.pop(0)
        ctx.voice_client.play(player, after=lambda e: bot.loop.create_task(play_next(ctx)) if e is None else print(f'Player error: {e}'))
        
        # Embed message with thumbnail for the currently playing song
        embed = discord.Embed(title=language_outputs["now_playing"], description=title, color=0x00ff00)
        if isinstance(player, YTDLSource):
            embed.set_thumbnail(url=player.thumbnail)  # Show the thumbnail if from YouTube
        await ctx.send(embed=embed)
    else:
        await ctx.voice_client.disconnect()

async def play_next(ctx):
    if queue:
        await start_playback(ctx)

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
    await ctx.voice_client.disconnect()
    await ctx.send(language_outputs["music_stopped"])

# Run the bot with the token from the config file
bot.run(config['token'])
