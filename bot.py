# bot_musica.py
import discord
from discord.ext import commands
import yt_dlp
import asyncio
import collections
import functools

# --- Exceção Customizada para Restrição de Idade ---
class AgeRestrictionError(yt_dlp.utils.DownloadError):
    def __init__(self, original_query, underlying_exception):
        super().__init__(str(underlying_exception))
        self.original_query = original_query

# --- Configuração ---
import os
TOKEN = os.environ.get("DISCORD_BOT_TOKEN")
if not TOKEN:
    raise ValueError("Token do Discord não encontrado na variável de ambiente DISCORD_BOT_TOKEN")"  # IMPORTANTE: Substitua e use variáveis de ambiente!
COMMAND_PREFIX = "!"

YDL_OPTS_DEFAULT = {
    'format': 'bestaudio[ext=opus]/bestaudio[ext=m4a]/bestaudio[abr<=128]/bestaudio/best',
    'noplaylist': True,
    'quiet': True,
    'no_warnings': True,
    'default_search': 'ytsearch1:',
    'source_address': '0.0.0.0',
    'skip_download': True,
    'socket_timeout': 20,
    'cachedir': False,
    'nocheckcertificate': True,
    'prefer_ffmpeg': True,
}

FFMPEG_OPTS = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 -probesize 32k -analyzeduration 0',
    'options': '-vn'
}

# --- View dos Controles do Player ---
class PlayerControlsView(discord.ui.View):
    def __init__(self, music_cog, guild_id: int):
        super().__init__(timeout=None)
        self.music_cog = music_cog
        self.guild_id = guild_id
        self.add_item(PreviousButton(self.music_cog, self.guild_id))
        self.add_item(PauseResumeButton(self.music_cog, self.guild_id))
        self.add_item(SkipButton(self.music_cog, self.guild_id))
        self.add_item(StopButton(self.music_cog, self.guild_id))
        self._update_pause_resume_button_state()

    def _update_pause_resume_button_state(self):
        for item in self.children:
            if isinstance(item, PauseResumeButton):
                guild = self.music_cog.bot.get_guild(self.guild_id)
                if not guild: return
                vc = guild.voice_client
                is_currently_paused = vc and vc.is_connected() and vc.is_paused()
                item.label = "Retomar" if is_currently_paused else "Pausar"
                item.emoji = "▶️" if is_currently_paused else "⏸️"
                break
    
    async def update_view_for_new_song(self, interaction_or_message_channel, song_info, is_paused=False):
        embed = self.music_cog._create_song_embed(song_info, is_paused)
        self._update_pause_resume_button_state() 
        active_message = self.music_cog.active_player_messages.get(self.guild_id)
        
        if active_message:
            try:
                await active_message.edit(embed=embed, view=self)
            except discord.NotFound: 
                self.music_cog.active_player_messages.pop(self.guild_id, None)
                if isinstance(interaction_or_message_channel, discord.TextChannel):
                    try:
                        new_msg = await interaction_or_message_channel.send(embed=embed, view=self)
                        self.music_cog.active_player_messages[self.guild_id] = new_msg
                    except Exception as e:
                        print(f"Erro ao enviar nova mensagem de player (fallback): {e}")
            except Exception as e: 
                print(f"Erro ao editar mensagem do player: {e}")
        elif isinstance(interaction_or_message_channel, discord.TextChannel):
            try:
                new_msg = await interaction_or_message_channel.send(embed=embed, view=self)
                self.music_cog.active_player_messages[self.guild_id] = new_msg
            except Exception as e:
                print(f"Erro ao enviar mensagem inicial do player: {e}")

# --- Definições dos Botões ---
class PreviousButton(discord.ui.Button):
    def __init__(self, music_cog, guild_id):
        super().__init__(label="Anterior", emoji="⏮️", style=discord.ButtonStyle.secondary, row=0)
        self.music_cog = music_cog
        self.guild_id = guild_id
    async def callback(self, interaction: discord.Interaction):
        if not interaction.guild: 
            return await interaction.response.send_message("Comando indisponível em DMs.", ephemeral=True)
        await interaction.response.defer()
        history = self.music_cog.get_history(self.guild_id) 
        if not history: 
            return await interaction.followup.send("Sem histórico de músicas.", ephemeral=True)
        vc = interaction.guild.voice_client
        if not vc: 
            return await interaction.followup.send("Bot não conectado.", ephemeral=True)
        
        self.music_cog.prefetched_stream_info.pop(self.guild_id, None)
        prev_song_data = history.pop()
        current_song_data = self.music_cog.current_song_info.get(self.guild_id)
        queue = self.music_cog.get_queue(self.guild_id)
        
        if current_song_data: 
            queue.appendleft(current_song_data)
        queue.appendleft(prev_song_data) 
        
        if vc.is_playing() or vc.is_paused(): 
            vc.stop()
        else: 
            await self.music_cog.play_next_song(interaction.guild.id)

class PauseResumeButton(discord.ui.Button):
    def __init__(self, music_cog, guild_id):
        self.music_cog = music_cog
        self.guild_id = guild_id
        super().__init__(label="Pausar", emoji="⏸️", style=discord.ButtonStyle.primary, row=0)

    async def callback(self, interaction: discord.Interaction):
        if not interaction.guild: 
            return await interaction.response.send_message("Comando indisponível em DMs.", ephemeral=True)
        
        vc = interaction.guild.voice_client
        if not vc or not vc.is_connected(): 
            return await interaction.response.send_message("Bot não conectado.", ephemeral=True)
        
        current_song_info = self.music_cog.current_song_info.get(self.guild_id)
        if not current_song_info: 
            return await interaction.response.send_message("Nenhuma música carregada.", ephemeral=True)

        if vc.is_paused(): 
            vc.resume()
            self.label = "Pausar"
            self.emoji = "⏸️"
        elif vc.is_playing(): 
            vc.pause()
            self.label = "Retomar"
            self.emoji = "▶️"
        else:
            return await interaction.response.send_message("Nada tocando para pausar/retomar.", ephemeral=True)
            
        new_embed = self.music_cog._create_song_embed(current_song_info, vc.is_paused())
        await interaction.response.edit_message(embed=new_embed, view=self.view)

class SkipButton(discord.ui.Button):
    def __init__(self, music_cog, guild_id):
        super().__init__(label="Pular", emoji="⏭️", style=discord.ButtonStyle.secondary, row=0)
        self.music_cog = music_cog
        self.guild_id = guild_id

    async def callback(self, interaction: discord.Interaction):
        if not interaction.guild: 
            return await interaction.response.send_message("Comando indisponível em DMs.", ephemeral=True)
        await interaction.response.defer() 
        
        vc = interaction.guild.voice_client
        if vc and (vc.is_playing() or vc.is_paused()): 
            self.music_cog.prefetched_stream_info.pop(self.guild_id, None)
            vc.stop() 
        else: 
            await interaction.followup.send("Nada tocando para pular.", ephemeral=True)

class StopButton(discord.ui.Button):
    def __init__(self, music_cog, guild_id):
        super().__init__(label="Parar", emoji="⏹️", style=discord.ButtonStyle.danger, row=0)
        self.music_cog = music_cog
        self.guild_id = guild_id

    async def callback(self, interaction: discord.Interaction):
        if not interaction.guild: 
            return await interaction.response.send_message("Comando indisponível em DMs.", ephemeral=True)
        await interaction.response.defer()
        await self.music_cog.stop_player_and_cleanup(self.guild_id, interaction.channel, "Reprodução parada pelo botão.")

# --- Classe do Cog de Música ---
class MusicCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.song_queues = {}
        self.current_song_info = {}
        self.active_player_messages = {} 
        self.song_history = {}      
        self.guild_music_channels = {} 
        self.prefetched_stream_info = {}
        self.YDL_OPTS = YDL_OPTS_DEFAULT

    def get_queue(self, guild_id: int) -> collections.deque:
        return self.song_queues.setdefault(guild_id, collections.deque())

    def get_history(self, guild_id: int) -> collections.deque:
        return self.song_history.setdefault(guild_id, collections.deque(maxlen=10))

    def _create_song_embed(self, song_data, is_paused=False):
        title_prefix = "Tocando Agora" if not is_paused else "Pausado"
        platform_emoji = "🎶" 
        webpage_url_lower = song_data.get('webpage_url','').lower()
        
        if "youtube.com/watch" in webpage_url_lower or "youtu.be/" in webpage_url_lower or \
           "youtube.com/watch" in webpage_url_lower or \
           "youtu.be/" in webpage_url_lower: # Corrigido para URLs comuns do YouTube:
            platform_emoji = "🔴" 
        elif 'soundcloud.com' in webpage_url_lower:
            platform_emoji = "☁️"
        
        embed = discord.Embed(
            title=f"{platform_emoji} {title_prefix}", 
            description=f"[{song_data.get('title', 'Título Desconhecido')}]({song_data.get('webpage_url', '#')})", 
            color=discord.Color.blue() if not is_paused else discord.Color.orange()
        )
        
        duration_seconds = song_data.get('duration')
        if duration_seconds:
            try:
                m, s = divmod(int(duration_seconds), 60)
                h, m = divmod(m, 60)
                duration_str = f"{m:02d}:{s:02d}"
                if h > 0: duration_str = f"{h:02d}:{duration_str}"
                embed.add_field(name="Duração", value=duration_str, inline=True)
            except (ValueError, TypeError): pass

        embed.add_field(name="Pedido por", value=song_data.get('requester', 'N/A'), inline=True)
        return embed

    async def _update_player_message(self, guild_id: int, song_data, is_paused=False):
        channel = self.guild_music_channels.get(guild_id)
        if not channel:
            print(f"Alerta: Canal de música não encontrado para o servidor {guild_id} ao tentar atualizar player.")
            return

        view = PlayerControlsView(self, guild_id)
        embed = self._create_song_embed(song_data, is_paused)
        
        active_message = self.active_player_messages.get(guild_id)
        if active_message:
            try:
                await active_message.edit(embed=embed, view=view)
                return
            except discord.NotFound:
                self.active_player_messages.pop(guild_id, None)
            except Exception as e:
                print(f"Erro ao editar mensagem do player existente: {e}")

        try:
            old_msg_to_delete = self.active_player_messages.pop(guild_id, None)
            if old_msg_to_delete:
                try: await old_msg_to_delete.delete()
                except: pass

            new_msg = await channel.send(embed=embed, view=view)
            self.active_player_messages[guild_id] = new_msg
        except Exception as e:
            print(f"Erro crítico ao enviar nova mensagem do player: {e}")

    def _blocking_extract_info(self, query_or_url, 
                               is_soundcloud_search=False, 
                               process_for_stream_url=False, 
                               process_playlist=False,
                               playlist_items_to_extract=None):
        opts = self.YDL_OPTS.copy()
        final_query = query_or_url

        if process_for_stream_url:
            opts.pop('extract_flat', None) 
            opts['noplaylist'] = True 

        if is_soundcloud_search:
            opts['default_search'] = 'scsearch1:'
        else:
            is_youtube_url = "youtube.com/" in query_or_url.lower() or \
                             "youtu.be/" in query_or_url.lower() or \
                             "youtube.com/watch" in query_or_url.lower() or \
                             "youtu.be/" in query_or_url.lower()
            is_playlist_link_detected = is_youtube_url and "list=" in query_or_url.lower()

            if is_playlist_link_detected and process_playlist:
                opts['noplaylist'] = False
                if not process_for_stream_url:
                    opts['extract_flat'] = 'discard_in_extractor'
                else: 
                    opts.pop('extract_flat', None)
                
                if playlist_items_to_extract:
                    opts['playlist_items'] = playlist_items_to_extract
                if 'default_search' in opts:
                    del opts['default_search']
            elif is_youtube_url:
                opts['noplaylist'] = True
                opts.pop('extract_flat', None)
                if 'default_search' in opts:
                    del opts['default_search']
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(final_query, download=False) 
            return info
        except yt_dlp.utils.DownloadError as e:
            if "Sign in to confirm your age" in str(e) and not is_soundcloud_search:
                raise AgeRestrictionError(original_query=query_or_url, underlying_exception=e)
            raise 

    async def _prefetch_next_song_url(self, guild_id: int):
        queue = self.get_queue(guild_id)
        if not queue: 
            self.prefetched_stream_info.pop(guild_id, None)
            return
        
        next_song_data_in_queue = queue[0]
        current_prefetch = self.prefetched_stream_info.get(guild_id)
        if current_prefetch and \
           current_prefetch.get('webpage_url') == next_song_data_in_queue.get('webpage_url') and \
           current_prefetch.get('stream_url'):
            return

        self.prefetched_stream_info[guild_id] = {
            'webpage_url': next_song_data_in_queue.get('webpage_url'), 'stream_url': None, 'title': 'Prefetching...'
        }
        
        try:
            info = await self.bot.loop.run_in_executor(None, 
                functools.partial(self._blocking_extract_info, 
                                  next_song_data_in_queue['webpage_url'], 
                                  is_soundcloud_search=False, 
                                  process_for_stream_url=True))
            
            actual_info = info.get('entries', [info])[0] if info and info.get('entries') else info

            if actual_info and 'url' in actual_info:
                current_queue_after_prefetch = self.get_queue(guild_id) 
                if current_queue_after_prefetch and \
                   current_queue_after_prefetch[0].get('webpage_url') == next_song_data_in_queue.get('webpage_url'):
                    self.prefetched_stream_info[guild_id] = {
                        'webpage_url': next_song_data_in_queue['webpage_url'],
                        'stream_url': actual_info['url'],
                        'title': actual_info.get('title', 'Título Desconhecido'),
                        'duration': actual_info.get('duration')
                    }
                else: self.prefetched_stream_info.pop(guild_id, None)
            else: self.prefetched_stream_info.pop(guild_id, None)
        except Exception as e:
            print(f"Erro ao pré-carregar URL para '{next_song_data_in_queue.get('title', 'Desconhecida')}': {e}")
            self.prefetched_stream_info.pop(guild_id, None)

    async def play_next_song(self, guild_id: int):
        queue = self.get_queue(guild_id)
        history = self.get_history(guild_id)
        guild = self.bot.get_guild(guild_id)
        
        if not guild: 
            print(f"Erro crítico: Guilda {guild_id} não encontrada.")
            return await self.cleanup_player_state(guild_id, "Erro interno (servidor não encontrado).")
            
        voice_client = guild.voice_client
        channel_for_messages = self.guild_music_channels.get(guild_id)

        if not voice_client or not voice_client.is_connected(): 
            msg = "Bot desconectado da voz."
            if channel_for_messages:
                try: await channel_for_messages.send(msg, delete_after=30)
                except: pass
            else: print(f"Guild {guild_id}: {msg}")
            return await self.cleanup_player_state(guild_id)
        
        last_played_song = self.current_song_info.get(guild_id)
        if last_played_song:
             history.append(last_played_song)

        if not queue:
            self.current_song_info.pop(guild_id, None)
            self.prefetched_stream_info.pop(guild_id, None)
            msg = "Fila de músicas finalizada!"
            player_msg_obj = self.active_player_messages.get(guild_id)
            if player_msg_obj and channel_for_messages:
                try:
                    empty_embed = discord.Embed(title="🎶 Fila Vazia", description="Nenhuma música tocando.", color=discord.Color.light_grey())
                    view = PlayerControlsView(self, guild_id)
                    await player_msg_obj.edit(embed=empty_embed, view=view)
                except Exception as e_edit:
                    print(f"Erro ao editar msg do player para fila vazia: {e_edit}")
            elif channel_for_messages:
                try: await channel_for_messages.send(msg, delete_after=30)
                except: pass
            else: print(f"Guild {guild_id}: {msg}")
            return

        song_to_play = queue.popleft()
        self.current_song_info[guild_id] = song_to_play
        
        # >>> OTIMIZAÇÃO: Verifica se a URL do stream já foi obtida pelo play_command <<<
        stream_url = song_to_play.get('stream_url') 

        if not stream_url: # Se não foi obtida antes (ex: item de playlist, ou não era a primeira música)
            prefetched = self.prefetched_stream_info.get(guild_id)
            if prefetched and prefetched.get('webpage_url') == song_to_play.get('webpage_url') and prefetched.get('stream_url'):
                stream_url = prefetched['stream_url']
                song_to_play['title'] = prefetched.get('title', song_to_play.get('title'))
                song_to_play['duration'] = prefetched.get('duration', song_to_play.get('duration'))
                self.prefetched_stream_info.pop(guild_id, None)
            else: 
                try:
                    info = await self.bot.loop.run_in_executor(None, 
                        functools.partial(self._blocking_extract_info, 
                                          song_to_play['webpage_url'], 
                                          is_soundcloud_search=False,
                                          process_for_stream_url=True))
                    
                    actual_info = info.get('entries', [info])[0] if info and info.get('entries') else info

                    if actual_info and 'url' in actual_info: 
                        stream_url = actual_info['url']
                        song_to_play['title'] = actual_info.get('title', song_to_play.get('title'))
                        song_to_play['duration'] = actual_info.get('duration', song_to_play.get('duration'))
                    else: 
                        raise yt_dlp.utils.DownloadError("Informações de stream não encontradas (url faltando).")
                except AgeRestrictionError as are: 
                    if channel_for_messages: 
                        try: await channel_for_messages.send(f"'{song_to_play.get('title', 'Vídeo')}' tem restrição de idade. Tentando SoundCloud...", delete_after=25)
                        except: pass
                    try:
                        search_term_for_sc = song_to_play.get('title', are.original_query) 
                        info_sc_meta = await self.bot.loop.run_in_executor(None, 
                            functools.partial(self._blocking_extract_info, search_term_for_sc, is_soundcloud_search=True, process_for_stream_url=False))
                        entry_sc_meta = info_sc_meta.get('entries', [info_sc_meta])[0] if info_sc_meta and info_sc_meta.get('entries') else info_sc_meta

                        if entry_sc_meta and entry_sc_meta.get('webpage_url'):
                            info_sc_stream = await self.bot.loop.run_in_executor(None, 
                                functools.partial(self._blocking_extract_info, entry_sc_meta['webpage_url'], is_soundcloud_search=False, process_for_stream_url=True))
                            actual_sc_stream_info = info_sc_stream.get('entries', [info_sc_stream])[0] if info_sc_stream and info_sc_stream.get('entries') else info_sc_stream

                            if actual_sc_stream_info and 'url' in actual_sc_stream_info:
                                stream_url = actual_sc_stream_info['url']
                                song_to_play['webpage_url'] = actual_sc_stream_info.get('webpage_url', song_to_play['webpage_url'])
                                song_to_play['title'] = actual_sc_stream_info.get('title', song_to_play['title'])
                                song_to_play['duration'] = actual_sc_stream_info.get('duration', song_to_play.get('duration'))
                            else: raise yt_dlp.utils.DownloadError("Falha no SoundCloud (stream URL).")
                        else: raise yt_dlp.utils.DownloadError("Falha no SoundCloud (metadados).")
                    except Exception as e_sc:
                        if channel_for_messages: 
                            try: await channel_for_messages.send(f"Falha ao buscar no SoundCloud para '{song_to_play.get('title', 'música')}': {e_sc}", delete_after=30)
                            except: pass
                        return self.bot.loop.create_task(self.song_finished_handler(guild_id, e_sc))
                except Exception as e: 
                    if channel_for_messages: 
                        try: await channel_for_messages.send(f"Erro ao obter stream para '{song_to_play['title']}': {e}", delete_after=30)
                        except: pass
                    return self.bot.loop.create_task(self.song_finished_handler(guild_id, e))

        if not stream_url:
            if channel_for_messages: 
                try: await channel_for_messages.send(f"Não foi possível obter URL de stream para '{song_to_play['title']}'. Pulando.", delete_after=25)
                except: pass
            return self.bot.loop.create_task(self.song_finished_handler(guild_id, "URL de stream não encontrada"))
        
        try:
            source = discord.FFmpegPCMAudio(stream_url, **FFMPEG_OPTS)
            voice_client.play(source, after=lambda e: self.bot.loop.create_task(self.song_finished_handler(guild_id, e)))
            await self._update_player_message(guild_id, song_to_play, voice_client.is_paused())
            
            if queue:
                self.bot.loop.create_task(self._prefetch_next_song_url(guild_id))
            else:
                self.prefetched_stream_info.pop(guild_id, None)
        except Exception as e:
            if channel_for_messages: 
                try: await channel_for_messages.send(f"Erro crítico ao tentar tocar '{song_to_play['title']}': {e}", delete_after=25)
                except: pass
            self.bot.loop.create_task(self.song_finished_handler(guild_id, e))

    async def song_finished_handler(self, guild_id: int, error=None):
        if error: 
            print(f"Música finalizada com erro no servidor {guild_id}: {error}")
        guild = self.bot.get_guild(guild_id)
        if guild and guild.voice_client and guild.voice_client.is_connected():
            await self.play_next_song(guild_id)
        else:
            await self.cleanup_player_state(guild_id, "Bot desconectado, parando reprodução.")

    async def cleanup_player_state(self, guild_id: int, cleanup_message: str = None):
        self.current_song_info.pop(guild_id, None)
        self.get_queue(guild_id).clear()
        self.prefetched_stream_info.pop(guild_id, None) 
        
        player_msg = self.active_player_messages.pop(guild_id, None)
        if player_msg:
            try: await player_msg.delete()
            except: pass
        
        channel = self.guild_music_channels.get(guild_id)
        if cleanup_message and channel:
            try: await channel.send(cleanup_message, delete_after=30)
            except: pass

    async def stop_player_and_cleanup(self, guild_id: int, channel_for_message: discord.TextChannel = None, stop_reason: str = "Reprodução parada."):
        guild = self.bot.get_guild(guild_id)
        if not guild: 
            print(f"Erro: Servidor {guild_id} não encontrado em stop.")
            return

        current_song = self.current_song_info.get(guild_id)
        if current_song: self.get_history(guild_id).append(current_song)
            
        vc = guild.voice_client
        if vc and (vc.is_playing() or vc.is_paused()): vc.stop() 
        
        self.get_queue(guild_id).clear()
        self.prefetched_stream_info.pop(guild_id, None)
        self.current_song_info.pop(guild_id, None)
        await self.cleanup_player_state(guild_id, stop_reason if channel_for_message else None)

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if member.id == self.bot.user.id and before.channel is not None and after.channel is None:
            guild_id = before.channel.guild.id
            print(f"Bot desconectado da guild {guild_id}. Limpando estado.")
            current_song = self.current_song_info.get(guild_id)
            if current_song: self.get_history(guild_id).append(current_song)
            await self.cleanup_player_state(guild_id, "O bot foi desconectado do canal de voz.")

    @commands.command(name="join", aliases=["connect"])
    @commands.guild_only()
    async def join_command(self, ctx: commands.Context):
        if not ctx.author.voice or not ctx.author.voice.channel: 
            return await ctx.send("Você não está em um canal de voz!", delete_after=20)
        
        channel = ctx.author.voice.channel
        self.guild_music_channels[ctx.guild.id] = ctx.channel
        
        vc = ctx.guild.voice_client
        if vc and vc.is_connected():
            if vc.channel == channel: 
                await ctx.send("Já estou neste canal.", delete_after=15)
            else: 
                try:
                    await vc.move_to(channel)
                    await ctx.send(f"Movido para: **{channel.name}**")
                except asyncio.TimeoutError:
                    await ctx.send(f"Timeout ao mover para: **{channel.name}**.", delete_after=20)
                except Exception as e:
                    await ctx.send(f"Erro ao mover: {e}", delete_after=20)
        else: 
            try:
                await channel.connect(timeout=10.0, reconnect=True)
                await ctx.send(f"Conectado a: **{channel.name}**")
            except asyncio.TimeoutError:
                await ctx.send(f"Timeout ao conectar em: **{channel.name}**. Verifique permissões.", delete_after=25)
            except Exception as e:
                await ctx.send(f"Erro ao conectar: {e}", delete_after=25)

    @commands.command(name="leave", aliases=["disconnect"])
    @commands.guild_only()
    async def leave_command(self, ctx: commands.Context):
        vc = ctx.guild.voice_client
        if vc and vc.is_connected():
            current_song = self.current_song_info.get(ctx.guild.id)
            if current_song: self.get_history(ctx.guild.id).append(current_song)
            await self.stop_player_and_cleanup(ctx.guild.id, ctx.channel, "Desconectado por comando.") 
            await vc.disconnect(force=False)
        else: 
            await ctx.send("Não estou conectado a um canal de voz.", delete_after=20)
        try: await ctx.message.delete()
        except: pass
    
    @commands.command(name="play", aliases=["p"])
    @commands.guild_only()
    async def play_command(self, ctx: commands.Context, *, query: str):
        self.guild_music_channels[ctx.guild.id] = ctx.channel
        try: await ctx.message.delete()
        except: pass

        if not ctx.author.voice or not ctx.author.voice.channel:
            return await ctx.send("Você precisa estar em um canal de voz.", delete_after=20)

        user_voice_channel = ctx.author.voice.channel
        vc = ctx.guild.voice_client 
        
        if not vc or not vc.is_connected(): 
            try: 
                vc = await user_voice_channel.connect(timeout=10.0, reconnect=True) 
            except asyncio.TimeoutError:
                return await ctx.send(f"Timeout ao conectar em: **{user_voice_channel.name}**.", delete_after=25)
            except Exception as e:
                return await ctx.send(f"Não consegui entrar no seu canal ({user_voice_channel.name}): {e}", delete_after=25)
        elif vc.channel != user_voice_channel: 
            try: 
                await vc.move_to(user_voice_channel) 
            except asyncio.TimeoutError:
                 return await ctx.send(f"Timeout ao mover para: **{user_voice_channel.name}**.", delete_after=20)
            except Exception as e:
                return await ctx.send(f"Não consegui me mover para seu canal ({user_voice_channel.name}): {e}", delete_after=25)
        
        if not vc:
            return await ctx.send("Erro crítico: Bot não conseguiu conectar à voz.", delete_after=20) 
        
        queue = self.get_queue(ctx.guild.id)
        is_starting_playback = not (vc.is_playing() or vc.is_paused())
        
        info = None
        is_yt_link = "youtube.com/" in query.lower() or \
                     "youtu.be/" in query.lower() or \
                     "youtube.com/watch" in query.lower() or \
                     "youtu.be/" in query.lower()
        is_direct_playlist_url = is_yt_link and "list=" in query.lower()

        # >>> OTIMIZAÇÃO: Se for a primeira música (single) a tocar, tenta pegar o stream URL direto <<<
        process_for_stream_now = is_starting_playback and not is_direct_playlist_url

        async with ctx.typing():
            try:
                info = await self.bot.loop.run_in_executor(None, 
                    functools.partial(self._blocking_extract_info, query, 
                                      is_soundcloud_search=False, 
                                      process_for_stream_url=process_for_stream_now, # Passa True se for otimizar
                                      process_playlist=is_direct_playlist_url))
            except AgeRestrictionError as are:
                is_general_search_or_youtube_single = not is_direct_playlist_url and \
                                                     (is_yt_link or not "soundcloud.com" in query.lower())
                if is_general_search_or_youtube_single: 
                    await ctx.send(f"Conteúdo YT restrito. Tentando SC para '{are.original_query}'...", delete_after=20)
                    try:
                        # Para fallback, nunca pegamos stream URL direto, apenas metadados
                        info = await self.bot.loop.run_in_executor(None, 
                            functools.partial(self._blocking_extract_info, are.original_query, 
                                              is_soundcloud_search=True, 
                                              process_for_stream_url=False, # Apenas metadados no fallback
                                              process_playlist=False))
                    except Exception as e_sc:
                        await ctx.send(f"Erro ao buscar '{are.original_query}' no SC: {e_sc}", delete_after=25)
                        return 
                else:
                    await ctx.send(f"Conteúdo ({'playlist' if is_direct_playlist_url else 'link'}) restrito.", delete_after=25)
                    return
            except Exception as e: 
                await ctx.send(f"Erro ao buscar '{query}': {e}", delete_after=25)
                return

        if not info: 
            return await ctx.send(f"Não encontrei nada para: '{query}'", delete_after=20)

        songs_added_count = 0
        if info.get('_type') == 'playlist' and 'entries' in info: 
            playlist_title = info.get('title', 'Playlist Desconhecida')
            skipped_count = 0
            for entry in info.get('entries', []): 
                if entry and entry.get('webpage_url'):
                    song_data = {
                        'webpage_url': entry['webpage_url'], 
                        'title': entry.get('title', 'Título Desconhecido'), 
                        'requester': ctx.author.mention, 
                        'duration': entry.get('duration'),
                        'stream_url': None # Para playlists, stream_url é pego depois
                    }
                    queue.append(song_data)
                    songs_added_count += 1
                else: skipped_count +=1
            
            if songs_added_count > 0:
                msg_playlist = f"Playlist **'{playlist_title}'** ({songs_added_count} músicas) adicionada por {ctx.author.mention}!"
                if skipped_count > 0: msg_playlist += f" ({skipped_count} inválidas puladas)."
                await ctx.send(msg_playlist, delete_after=30)
            else:
                await ctx.send(f"Não carreguei músicas da playlist '{playlist_title}'.", delete_after=20)
        else: 
            song_info_entry = info.get('entries', [info])[0] if info.get('entries') else info
            if not song_info_entry or not song_info_entry.get('webpage_url'):
                return await ctx.send(f"Não obtive informações válidas para '{query}'.", delete_after=20)

            song_data = {
                'webpage_url': song_info_entry['webpage_url'], 
                'title': song_info_entry.get('title', 'Título Desconhecido'), 
                'requester': ctx.author.mention, 
                'duration': song_info_entry.get('duration'),
                # >>> OTIMIZAÇÃO: Armazena stream_url se foi obtido <<<
                'stream_url': song_info_entry.get('url') if process_for_stream_now else None
            }
            queue.append(song_data)
            songs_added_count = 1
            await ctx.send(f"Adicionado: **{song_data['title']}** por {ctx.author.mention}", delete_after=20)

        if songs_added_count > 0 and is_starting_playback:
            await self.play_next_song(ctx.guild.id)
        elif songs_added_count > 0 and queue:
            current_prefetch = self.prefetched_stream_info.get(ctx.guild.id)
            if not current_prefetch or \
               (current_prefetch.get('webpage_url') != queue[0].get('webpage_url')) or \
               not current_prefetch.get('stream_url'):
                self.bot.loop.create_task(self._prefetch_next_song_url(ctx.guild.id))

    # ... (restante dos comandos: skip, stop, pause, resume, queue, clearqueue, history) ...
    # Esses comandos permanecem os mesmos da versão anterior otimizada.
    # Vou colar eles aqui para completude, sem alterações significativas neles.

    @commands.command(name="skip", aliases=["s"])
    @commands.guild_only()
    async def skip_command(self, ctx: commands.Context):
        try: await ctx.message.delete()
        except: pass
        vc = ctx.guild.voice_client
        if vc and (vc.is_playing() or vc.is_paused()):
            self.prefetched_stream_info.pop(ctx.guild.id, None)
            vc.stop()
            await ctx.send("Música pulada!", delete_after=15)
        else: 
            await ctx.send("Nada tocando para pular.", delete_after=15)

    @commands.command(name="stop")
    @commands.guild_only()
    async def stop_command(self, ctx: commands.Context):
        try: await ctx.message.delete()
        except: pass
        await self.stop_player_and_cleanup(ctx.guild.id, ctx.channel, "Reprodução parada.")

    @commands.command(name="pause")
    @commands.guild_only()
    async def pause_command(self, ctx: commands.Context):
        try: await ctx.message.delete()
        except: pass
        vc = ctx.guild.voice_client
        if vc and vc.is_playing():
            vc.pause()
            current_song = self.current_song_info.get(ctx.guild.id)
            if current_song: await self._update_player_message(ctx.guild.id, current_song, is_paused=True)
            await ctx.send("Música pausada.", delete_after=15)
        else: 
            await ctx.send("Nada tocando ou já pausado.", delete_after=15)

    @commands.command(name="resume", aliases=["unpause"])
    @commands.guild_only()
    async def resume_command(self, ctx: commands.Context):
        try: await ctx.message.delete()
        except: pass
        vc = ctx.guild.voice_client
        if vc and vc.is_paused():
            vc.resume()
            current_song = self.current_song_info.get(ctx.guild.id)
            if current_song: await self._update_player_message(ctx.guild.id, current_song, is_paused=False)
            await ctx.send("Música retomada.", delete_after=15)
        else: 
            await ctx.send("Nenhuma música pausada.", delete_after=15)

    @commands.command(name="queue", aliases=["q", "list"])
    @commands.guild_only()
    async def queue_command(self, ctx: commands.Context):
        try: await ctx.message.delete()
        except: pass

        guild_id = ctx.guild.id
        queue = self.get_queue(guild_id)
        embed = discord.Embed(title="🎵 Fila de Músicas", color=discord.Color.gold())
        current = self.current_song_info.get(guild_id)
        vc = ctx.guild.voice_client
        status = " (Pausado)" if vc and vc.is_paused() else ""

        if current:
            duration_s = current.get('duration')
            duration_str = ""
            if duration_s:
                try:
                    m, s = divmod(int(duration_s), 60)
                    h, m = divmod(m, 60)
                    duration_str = f" ({m:02d}:{s:02d})"
                    if h > 0: duration_str = f" ({h:02d}:{m:02d}:{s:02d})"
                except (ValueError, TypeError): pass
            embed.add_field(name=f"💿 Tocando Agora{status}", 
                            value=f"[{current.get('title','N/A')}]({current.get('webpage_url','#')}){duration_str}\n(Por: {current.get('requester','N/A')})", 
                            inline=False)
        else: 
            embed.add_field(name="💿 Tocando Agora", value="Nenhuma música tocando.", inline=False)
        
        if not queue: 
            embed.description = "A fila está vazia."
        else:
            limit = 10
            q_list_str = []
            for i, s_data in enumerate(list(queue)[:limit]):
                s_dur = s_data.get('duration')
                s_dur_str = ""
                if s_dur:
                    try:
                        m, s = divmod(int(s_dur), 60)
                        h, m = divmod(m, 60)
                        s_dur_str = f" ({m:02d}:{s:02d})"
                        if h > 0: s_dur_str = f" ({h:02d}:{m:02d}:{s:02d})"
                    except (ValueError, TypeError): pass
                q_list_str.append(f"{i+1}. [{s_data.get('title','N/A')}]({s_data.get('webpage_url','#')}){s_dur_str} (Por: {s_data.get('requester','N/A')})")
            
            embed.add_field(name=f"🎶 Próximas ({len(queue)} total)", value="\n".join(q_list_str) or "Nenhuma", inline=False)
            if len(queue) > limit: embed.set_footer(text=f"... e mais {len(queue) - limit} música(s).")
        
        await ctx.send(embed=embed, delete_after=60)

    @commands.command(name="clearqueue", aliases=["cq", "clear"])
    @commands.guild_only()
    async def clear_queue_command(self, ctx: commands.Context):
        try: await ctx.message.delete()
        except: pass
        queue = self.get_queue(ctx.guild.id)
        if not queue: msg_text = "A fila já está vazia."
        else:
            queue.clear()
            self.prefetched_stream_info.pop(ctx.guild.id, None)
            msg_text = "Fila de músicas limpa!"
        await ctx.send(msg_text, delete_after=20)

    @commands.command(name="history", aliases=["hist"])
    @commands.guild_only()
    async def history_command(self, ctx: commands.Context):
        try: await ctx.message.delete()
        except: pass
        history = self.get_history(ctx.guild.id)
        if not history:
            return await ctx.send("Nenhuma música no histórico recente.", delete_after=20)
        embed = discord.Embed(title=f"📜 Histórico Recente (Últimas {history.maxlen})", color=discord.Color.light_grey())
        history_list = [f"{i+1}. [{s.get('title','N/A')}]({s.get('webpage_url','#')}) (Por: {s.get('requester','N/A')})"
                        for i, s in enumerate(reversed(list(history)))]
        embed.description = "\n".join(history_list)
        await ctx.send(embed=embed, delete_after=60)

    async def cog_command_error(self, ctx: commands.Context, error: commands.CommandError):
        try: 
            if ctx.message: await ctx.message.delete()
        except: pass 
        
        if isinstance(error, commands.CommandNotFound): return
        
        error_map = {
            commands.MissingRequiredArgument: f"Falta argumento: `{error.param.name}`. Use `{COMMAND_PREFIX}help {ctx.command.name}`.",
            commands.NoPrivateMessage: "Este comando não pode ser usado em DMs.",
            commands.CommandOnCooldown: f"Comando em cooldown. Tente em {error.retry_after:.1f}s.",
            commands.NotOwner: "Você não tem permissão para usar este comando.",
            commands.MissingPermissions: f"Você não tem as permissões necessárias: {', '.join(error.missing_permissions)}",
            commands.BotMissingPermissions: f"Eu não tenho as permissões necessárias: {', '.join(error.missing_permissions)}",
            commands.GuildNotFound: f"Servidor não encontrado: {error.argument}"
        }
        error_message_content = error_map.get(type(error), f"Erro no comando `{ctx.command.name}`: {str(error)[:1000]}")
        
        if not error_map.get(type(error)):
             print(f"Erro Detalhado (Guild: {ctx.guild.id if ctx.guild else 'DM'}, Cmd: {ctx.command.name if ctx.command else 'N/A'}): {error}")

        try: 
            await ctx.send(error_message_content, delete_after=25)
        except discord.Forbidden:
            print(f"Sem permissão para enviar msg de erro em {ctx.channel.id} (Guild: {ctx.guild.id})")
        except Exception as e: 
            print(f"Erro ao enviar mensagem de erro genérica: {e}")
        
async def setup(bot: commands.Bot):
    await bot.add_cog(MusicCog(bot))
    print("MusicCog (otimizado para início rápido) carregado.")

async def main():
    intents = discord.Intents.default()
    intents.message_content = True
    intents.voice_states = True
    bot = commands.Bot(command_prefix=COMMAND_PREFIX, intents=intents, help_command=None)
    
    @bot.event
    async def on_ready():
        print(f'Bot {bot.user.name} (ID: {bot.user.id}) online!')
        print(f"Conectado a {len(bot.guilds)} servidor(es).")
        await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.listening, name=f"{COMMAND_PREFIX}play | {COMMAND_PREFIX}help"))
    
    @bot.command(name="help")
    async def help_command_custom(ctx: commands.Context, *, command_name: str = None):
        try: await ctx.message.delete()
        except: pass
        embed = discord.Embed(title="🎧 Ajuda - Bot de Música 🎧", color=discord.Color.green())
        
        if command_name:
            cmd = bot.get_command(command_name)
            if cmd:
                embed.title = f"Ajuda para: {COMMAND_PREFIX}{cmd.name}"
                aliases = ", ".join([f"`{COMMAND_PREFIX}{a}`" for a in cmd.aliases])
                usage = f"`{COMMAND_PREFIX}{cmd.name} {cmd.signature}`"
                embed.description = f"{cmd.help or 'Sem descrição detalhada.'}\n\n**Uso:** {usage}"
                if aliases: embed.add_field(name="Alternativas", value=aliases, inline=False)
            else: embed.description = f"Comando `{command_name}` não encontrado."
        else:
            embed.description = f"Use `{COMMAND_PREFIX}comando` para interagir. Botões de controle aparecem com a música."
            embed.add_field(name=f"`{COMMAND_PREFIX}play <música/URL/Playlist>`", value="Toca uma música ou playlist. Tenta SoundCloud se YouTube falhar por idade.", inline=False)
            embed.add_field(name=f"`{COMMAND_PREFIX}skip`, `{COMMAND_PREFIX}s`", value="Pula a música atual.", inline=False)
            # ... (demais campos do help) ...
            embed.add_field(name=f"`{COMMAND_PREFIX}stop`", value="Para a música e limpa a fila.", inline=False)
            embed.add_field(name=f"`{COMMAND_PREFIX}pause` / `{COMMAND_PREFIX}resume`", value="Pausa ou retoma a música atual.", inline=False)
            embed.add_field(name=f"`{COMMAND_PREFIX}queue`, `{COMMAND_PREFIX}q`", value="Mostra a fila de músicas.", inline=False)
            embed.add_field(name=f"`{COMMAND_PREFIX}clearqueue`, `{COMMAND_PREFIX}cq`", value="Limpa todas as músicas da fila.", inline=False)
            embed.add_field(name=f"`{COMMAND_PREFIX}history`, `{COMMAND_PREFIX}hist`", value="Mostra as últimas músicas tocadas.", inline=False)
            embed.add_field(name=f"`{COMMAND_PREFIX}join` / `{COMMAND_PREFIX}leave`", value="Conecta ou desconecta o bot do canal de voz.", inline=False)
            embed.set_footer(text="Bot de música rápido e leve!")
        await ctx.send(embed=embed, delete_after=60)
            
    await setup(bot)
    
    try:
        await bot.start(TOKEN)
    except discord.errors.LoginFailure:
        print("FALHA NO LOGIN: Token inválido. Verifique o TOKEN.")
    except Exception as e:
        print(f"Erro crítico ao iniciar o bot: {e}")
        if "PrivilegedIntentsRequired" in str(e):
            print("ERRO: Intents privilegiadas (Message Content e/ou Voice States) podem não estar habilitadas no portal de desenvolvedores do Discord para este bot.")

if __name__ == "__main__":
    try: 
        asyncio.run(main())
    except KeyboardInterrupt: 
        print("\nBot desligado.")
    except Exception as e_main:
        print(f"Erro fatal no loop principal: {e_main}")
