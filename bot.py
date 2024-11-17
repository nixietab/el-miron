import discord
from discord.ext import commands, tasks
import yt_dlp as youtube_dl
import aiohttp
import asyncio
import re
import random
import time
import os
import json
import logging

# Load configuration from config.json
with open('config.json') as config_file:
    config = json.load(config_file)

blocked_users = config.get("blocked_users", [])
language_outputs = config.get("language_outputs", {})

# Load or initialize statistics from stats.json
stats_path = 'stats.json'
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

# Set up logging to bot.log file
logging.basicConfig(
    filename='bot.log',
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

intents = discord.Intents.all()
bot = commands.Bot(command_prefix='.', intents=intents)

ffmpeg_options = config.get("ffmpeg_options", {"options": "-vn"})
ytdl_format_options = config.get("ytdl_format_options", {"format": "bestaudio/best", "retries": 3, "nocheckcertificate": True})
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
    print(f"Bot is online! Credits: Bot developed by Nixietab. GitHub: https://github.com/nixietab/el-miron")
    scheduled_version_check.start()

@bot.command(name='p', help='Plays a song or playlist from YouTube or a direct URL')
async def play(ctx, *, search: str):
    if ctx.author.id in blocked_users:
        await ctx.send(language_outputs.get("blocked_user", "You are not allowed to use this bot."))
        return

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
    is_playlist = "list=" in search  # Detect if the URL has a playlist parameter

    async with ctx.typing():
        try:
            if is_youtube_url and is_playlist:
                # Handle playlists by retrieving all videos in the playlist
                playlist_tracks = await load_playlist(search)
                if not playlist_tracks:
                    await ctx.send("No tracks found in the playlist.")
                    return

                # Add each track from the playlist to the queue
                for player in playlist_tracks:
                    queue.append((player, player.title))
                    logging.info(f'Added "{player.title}" to queue.')
                
                await ctx.send(f"Added {len(playlist_tracks)} tracks from the playlist to the queue.")
            
            elif is_youtube_url:
                # Single YouTube video
                player = await YTDLSource.from_url(search, loop=bot.loop, stream=True)
                if player is None:
                    await ctx.send("Failed to retrieve data from the URL.")
                    return
                queue.append((player, player.title))
                logging.info(f'Added "{player.title}" to queue.')

            elif is_direct_url:
                # Direct URL
                player = await YTDLSource.from_url(search, loop=bot.loop, stream=True)
                if player is None:
                    await ctx.send("Failed to retrieve data from the URL.")
                    return
                queue.append((player, player.title))
                logging.info(f'Added "{player.title}" to queue.')

            else:
                # Search query
                player = await YTDLSource.from_url(f"ytsearch:{search}", loop=bot.loop, stream=True)
                if player is None:
                    await ctx.send("No results found for the query.")
                    return
                queue.append((player, player.title))
                logging.info(f'Added "{player.title}" to queue.')
                
        except ValueError:
            await ctx.send("Error retrieving data.")
            return

    if not voice_client.is_playing():
        await start_playback(ctx)
    else:
        # Notify the user that the track or playlist was added to the queue
        title = player.title if player else search
        embed = discord.Embed(title=language_outputs["song_added"], description=title, color=0x00ff00)
        embed.set_thumbnail(url=player.thumbnail if player and hasattr(player, "thumbnail") else "")
        await ctx.send(embed=embed)


async def load_playlist(playlist_url):
    """
    This function will load all videos from a playlist URL asynchronously.
    You can use youtube_dl or other methods to fetch video URLs.
    """
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: get_playlist_videos(playlist_url))


def get_playlist_videos(playlist_url):
    """
    Fetch all videos from a YouTube playlist.
    This function is synchronous because youtube_dl requires synchronous calls.
    """
    ydl_opts = {
        'format': 'bestaudio/best',
        'noplaylist': False,  # This ensures the entire playlist is downloaded
        'extract_flat': True,  # Prevent downloading video files, just extract the video info
    }

    with yt_dlp(ydl_opts) as ydl:
        info_dict = ydl.extract_info(playlist_url, download=False)
        if 'entries' not in info_dict:
            return []
        # Return list of video objects for the playlist
        return [YTDLSource.from_info(entry, loop=bot.loop) for entry in info_dict['entries']]

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
        with open(stats_path, 'w') as stats_file:
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
    if ctx.author.id in blocked_users:
        print(f"Blocked user {ctx.author.id} tried to skip.")  # Debug: Log blocked access attempt
        if "blocked_message" in language_outputs:
            await ctx.send(language_outputs["blocked_message"])
        else:
            await ctx.send("You are blocked from using this bot.")  # Fallback message
        return

    # Continue with command if not blocked
    if ctx.voice_client and ctx.voice_client.is_playing():
        ctx.voice_client.stop()
        await ctx.send(language_outputs.get("song_skipped", "Song skipped. 👽"))
        logging.info("Song skipped by user.")
    else:
        await ctx.send(language_outputs.get("no_song_playing", "No song is currently playing. 👽"))

@bot.command(name='stop', help='Stops the music and clears the queue')
async def stop(ctx):
    if ctx.author.id in blocked_users:
        await ctx.send(language_outputs["blocked_message"])
        return

    queue.clear()
    if ctx.voice_client:
        await ctx.voice_client.disconnect()
    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.playing, name=config["custom_idle_presence"]))
    await ctx.send(language_outputs["music_stopped"])
    logging.info("Music stopped and queue cleared.")

@bot.command(name='stats', help='Shows the total number of songs played and total hours of audio played')
async def stats_command(ctx):
    total_songs = stats["total_songs_played"]
    total_hours = round(stats["total_hours_played"], 2)
    await ctx.send(f"Total songs played: {total_songs}\nTotal hours played: {total_hours} hours")
    logging.info(f".stats command called. Total songs played: {total_songs}. Total hours played: {total_hours} hours.")

@bot.command(name='queue', help='Shows the current song and the queue')
async def show_queue(ctx):
    # Check if the user is blocked
    if ctx.author.id in blocked_users:
        await ctx.send(language_outputs.get("blocked_user", "You are not allowed to use this bot."))
        return

    # Check if the bot is connected to a voice channel
    if not ctx.voice_client:
        await ctx.send(language_outputs.get("not_in_voice_channel", "I am not connected to a voice channel."))
        return

    # Check if the queue is empty and nothing is playing
    if not queue and not ctx.voice_client.is_playing():
        empty_queue_message = language_outputs.get("empty_queue", "The queue is currently empty.")
        await ctx.send(empty_queue_message)
        return

    # Create the embed for the queue
    embed = discord.Embed(title=language_outputs.get("queue_title", "Music Queue 🎶"), color=0x00ff00)

    # Retrieve the currently playing song from voice client source
    currently_playing_title = getattr(ctx.voice_client.source, "title", None)

    # Display the currently playing song if available
    if currently_playing_title:
        embed.add_field(name=language_outputs.get("currently_playing", "Currently Playing:"), value=currently_playing_title, inline=False)
    else:
        embed.add_field(name=language_outputs.get("currently_playing", "Currently Playing:"), value=language_outputs.get("no_song_playing", "No song is currently playing."), inline=False)

    # Display the queue list, excluding the currently playing song if it matches the first song in the queue
    if queue:
        # Start the queue display after the currently playing song if it matches the first in the queue
        up_next_songs = queue[1:] if currently_playing_title and queue[0][1] == currently_playing_title else queue
        queue_list = "\n".join([f"{i+1}. {song[1]}" for i, song in enumerate(up_next_songs)])
        
        if queue_list:
            embed.add_field(name=language_outputs.get("up_next", "Up Next:"), value=queue_list, inline=False)
    else:
        embed.add_field(name=language_outputs.get("up_next", "Up Next:"), value=language_outputs.get("no_songs_in_queue", "No songs in the queue."), inline=False)

    # Send the embed message
    await ctx.send(embed=embed)

@bot.command(name='config', help='Displays the current bot configuration')
async def show_config(ctx):
    try:
        with open('config.json') as config_file:
            config_data = json.load(config_file)

        # Filter the dictionary to include only the specified categories
        filtered_config = {
            key: config_data[key]
            for key in ['ffmpeg_options', 'ytdl_format_options', 'language_outputs']
            if key in config_data
        }

        # Prepare the formatted message
        message = ""

        # Verbosely display ffmpeg_options
        if 'ffmpeg_options' in filtered_config:
            message += "## FFmpeg Options:\n"
            for option, value in filtered_config['ffmpeg_options'].items():
                message += f"- {option}: {value}\n"
            message += "\n"

        # Verbosely display ytdl_format_options
        if 'ytdl_format_options' in filtered_config:
            message += "## yt-dlp Options:\n"
            for option, value in filtered_config['ytdl_format_options'].items():
                message += f"- {option}: {value}\n"
            message += "\n"

        # Verbosely display language_outputs
        if 'language_outputs' in filtered_config:
            message += "## Language Outputs:\n"
            for language, output in filtered_config['language_outputs'].items():
                message += f"- {language}: {output}\n"
            message += "\n"

        # Send the message in chunks if it exceeds 2000 characters
        if len(message) > 2000:
            for i in range(0, len(message), 2000):
                await ctx.send(f"\n{message[i:i+2000]}")
        else:
            await ctx.send(f"\n{message}")

    except Exception as e:
        logging.error(f"Failed to load config.json: {e}")
        await ctx.send("Error loading the configuration file.")

version_url = 'https://raw.githubusercontent.com/nixietab/el-miron/refs/heads/main/version.json'

# Path to local version.json file
local_version_file = 'version.json'

# Path to config.json file
config_file = 'config.json'

# Store the last channel the bot interacted with
last_interacted_channel = None

# Read the config.json file to check if update checks are enabled
def read_config():
    if os.path.exists(config_file):
        with open(config_file, 'r') as f:
            config_data = json.load(f)
            return config_data.get('check_updates', True)
    return True  # Default to True if config doesn't exist

# Function to read local version from file
def read_local_version():
    if os.path.exists(local_version_file):
        with open(local_version_file, 'r') as f:
            local_data = json.load(f)
            return local_data.get('version', None)
    return None

# Send a version update message to the chosen channel
async def send_version_message(channel, new_version, local_version):
    try:
        # Send the message with emojis, alerts, and both versions
        await channel.send(
            f"🚨 **New version available!** 🚨\n\n"
            f"**New version**: {new_version}\n"
            f"**Local version**: {local_version}\n\n"
            f"Check it out here: https://github.com/nixietab/el-miron\n\n"
            f"🔔 Update is recommended always because discord and youtube integrations are trash! 🔔"
        )
    except Exception as e:
        print(f"Error sending message to channel {channel.name}: {e}")

# Check version function
async def check_version():
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(version_url) as response:
                if response.status == 200:
                    # Read the raw content as text (because GitHub serves it as plain text)
                    raw_content = await response.text()

                    try:
                        # Try to parse the raw content as JSON
                        version_data = json.loads(raw_content)
                        remote_version = version_data.get("version")
                        
                        # Read local version
                        local_version = read_local_version()
                        
                        # Compare versions
                        if remote_version and local_version:
                            if remote_version != local_version:
                                print(f"New version detected! Remote: {remote_version}, Local: {local_version}")
                                
                                # Check for the channel to send the update message
                                for guild in bot.guilds:
                                    # First, check if there's a 'general' channel
                                    general_channel = discord.utils.get(guild.text_channels, name="general")
                                    if general_channel:
                                        await send_version_message(general_channel, remote_version, local_version)
                                    # If no 'general' channel, try the last interacted channel
                                    elif last_interacted_channel:
                                        await send_version_message(last_interacted_channel, remote_version, local_version)
                                    # Fallback to the first text channel
                                    else:
                                        channel = guild.text_channels[0]
                                        await send_version_message(channel, remote_version, local_version)
                            else:
                                print(f"Version is up-to-date: {local_version}")
                        else:
                            print("Failed to get valid version data.")
                    except json.JSONDecodeError:
                        print(f"Error: Failed to decode JSON from raw content: {raw_content}")
                else:
                    print(f"Failed to fetch version.json. Status code: {response.status}")
    except Exception as e:
        print(f"Error while checking version: {e}")

# Event to track the last channel the bot interacted with
@bot.event
async def on_message(message):
    global last_interacted_channel
    # Only track messages that are not from the bot itself
    if message.author != bot.user:
        last_interacted_channel = message.channel
    await bot.process_commands(message)

# Scheduled task that runs every 24 hours
@tasks.loop(hours=24)
async def scheduled_version_check():
    if read_config():  # Only check version if update checks are enabled in config.json
        await check_version()

voice_check_task = None  # Will hold the reference to the voice channel checking task


@bot.command(name='fact', help='spreads missinformation')
async def fact(ctx):
    fake_facts = [

    "Las bananas son técnicamente peces debido a su naturaleza resbaladiza en el océano de frutas.",
    "Cada vez que parpadeas, se forma una nueva galaxia en algún lugar del universo.",
    "La Torre Eiffel fue construida originalmente como un soporte gigante para paraguas.",
    "Los gatos están secretamente a cargo de la intensidad de la señal Wi-Fi en tu casa.",
    "La luna está hecha de queso caducado, por eso está llena de agujeros.",
    "Las piñas crecen más rápido cuando las elogias a diario.",
    "Los tiburones inventaron internet para coordinar sus planes de fin de semana.",
    "La primera versión de Microsoft Windows funcionaba con hámsters en ruedas.",
    "Todas las nubes son en realidad ballenas del cielo disfrazadas.",
    "Las hormigas tienen pequeños smartphones, pero solo los usan para selfies.",
    "Los arcoíris son la forma en que la Tierra presume su colección de pegatinas brillantes.",
    "El pan tostado se inventó cuando el pan intentó tomar el sol demasiado cerca de una fogata.",
    "Las tortugas tienen el récord mundial de ser las criaturas más rápidas; simplemente no quieren presumir.",
    "La Gran Muralla China fue originalmente un proyecto gigante de dominó que se salió de control.",
    "Los pingüinos usan esmoquin porque trabajan de noche como bailarines profesionales sobre hielo.",
    "El color azul en realidad no existe; tu cerebro lo inventa como una broma.",
    "El rayo ocurre cuando las nubes chocan los cinco con demasiada fuerza.",
    "Todas las montañas son nubes muy tercas que se negaron a flotar lejos.",
    "Los cuellos largos de las jirafas fueron diseñados originalmente para la comunicación por satélite.",
    "El espagueti es en realidad un tipo de planta alienígena que escapó a la Tierra y prospera en agua hirviendo.",
    "El sol funciona gracias a miles de millones de hámsters bailando con ropa de ejercicio diminuta.",
    "Los copos de nieve son hechos a mano por hadas del cielo jubiladas en su tiempo libre.",
    "Tus calcetines desaparecen en la lavandería porque son reclutados por ninjas secretos de calcetines.",
    "Las bicicletas pueden hablar, pero solo lo hacen cuando no hay nadie alrededor para escucharlas.",
    "El chocolate fue descubierto cuando un árbol intentó hacer caramelos para sí mismo.",
    "Los pájaros no vuelan realmente; son levantados por cuerdas invisibles controladas por ardillas.",
    "El pan siempre cae del lado de la mantequilla porque quiere lamer el suelo por diversión.",
    "El alfabeto fue inventado por ardillas para organizar sus reservas de bellotas.",
    "Los dinosaurios no se extinguieron; solo se cansaron de caminar y se convirtieron en pájaros."
    ]
    random_fact = random.choice(fake_facts)
    await ctx.send(f"💡 **Did you know?:** {random_fact}")

@bot.command(name='ping', help='Responds with pong and shows the message send and receive times.')
async def ping(ctx):
    start_time = time.monotonic()  # Start time for the command execution
    message = await ctx.send("🏓 Pong! Calculating latency...")  # Initial response
    end_time = time.monotonic()  # End time for the send operation
    
    receive_latency = (end_time - start_time) * 1000  # Time it took to send the initial message
    send_latency = (message.created_at.timestamp() - ctx.message.created_at.timestamp()) * 1000  # Time it took to process and respond
    
    await message.edit(content=f"🏓 Pong!\nReceive Latency: {receive_latency:.2f} ms\nSend Latency: {send_latency:.2f} ms")


@bot.command(name='roll', help='Rolls dice in NdN format (e.g., 2d6).')
async def roll(ctx, dice: str = None):  # Default to None if no argument is provided
    if not dice:  # Check if dice is None or empty
        await ctx.send("Please specify the dice to roll in NdN format (e.g., 2d6).")
        return

    try:
        rolls, limit = map(int, dice.lower().split('d'))
        results = [random.randint(1, limit) for _ in range(rolls)]
        await ctx.send(f"🎲 You rolled: {', '.join(map(str, results))} (Total: {sum(results)})")
    except ValueError:
        await ctx.send("Invalid format! Use NdN (e.g., 2d6).")

@bot.command(name='dog', help="Sends a random dog picture.")
async def dog(ctx):
    async with aiohttp.ClientSession() as session:
        async with session.get('https://dog.ceo/api/breeds/image/random') as response:
            if response.status == 200:
                data = await response.json()
                await ctx.send(data['message'])  # Send the dog image URL
            else:
                await ctx.send("Couldn't fetch a dog picture. 🐕 Try again later!")

@bot.command(name='fox', help="Sends a random fox picture.")
async def fox(ctx):
    async with aiohttp.ClientSession() as session:
        async with session.get('https://randomfox.ca/floof/') as response:
            if response.status == 200:
                data = await response.json()
                await ctx.send(data['image'])  # Send the fox image URL
            else:
                await ctx.send("Couldn't fetch a fox picture. 🦊 Try again later!")          

@bot.command(name='gelbooru', help="Search Gelbooru for images using a tag and a quantity.")
async def gelbooru(ctx, tag: str, quantity: int = 1):
    # Ensure quantity is within a valid range (let's assume 1 to 10 for this example)
    if quantity < 1 or quantity > 10:
        await ctx.send("Please specify a quantity between 1 and 10.")
        return

    url = f"https://gelbooru.com/index.php?page=dapi&s=post&q=index&tags={tag}&json=1&limit={quantity * 2}"  # Fetch more images to ensure we get enough unique ones
    
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            if response.status == 200:
                try:
                    data = await response.json()
                    # Check if there are results
                    if data.get('@attributes', {}).get('count', 0) == 0:
                        await ctx.send(f"No images found for the tag '{tag}'.")
                    else:
                        # Track seen URLs to avoid duplicates
                        seen_urls = set()

                        # Counter for the images sent
                        images_sent = 0

                        # Iterate through the posts, stop once we've sent the requested number of unique images
                        for post in data['post']:
                            image_url = post.get('file_url')
                            
                            if image_url and image_url not in seen_urls:
                                await ctx.send(f"Here is an image found with the tag '{tag}':\n{image_url}")
                                seen_urls.add(image_url)  # Mark the URL as seen
                                images_sent += 1
                            
                            if images_sent >= quantity:  # Stop once we've sent the required number of images
                                break

                        # If we haven't sent the requested quantity, let the user know
                        if images_sent < quantity:
                            await ctx.send(f"Could only find {images_sent} unique images for the tag '{tag}'.")
                
                except ValueError:
                    await ctx.send("Error parsing the data from Gelbooru.")
            else:
                await ctx.send("Error fetching data from Gelbooru. Please try again later.")



bot.run(config['token'])
