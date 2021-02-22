import asyncio
import logging
import re
import sys
import time
from typing import Dict, Iterable, List, Optional

from ampris2 import Mpris2Dbussy, PlaybackStatus, PlayerInterfaces as Player, unwrap_metadata
import dbussy
from discord_rpc.async_ import (AsyncDiscordRpc, DiscordRpcError, JSON,
                                exceptions as async_exceptions)

from .config import Config

''' # master
CLIENT_ID = '435587535150907392'
PLAYER_ICONS = {
    'Clementine': 'clementine',
    'Media Player Classic Qute Theater': 'mpc-qt',
    'mpv': 'mpv',
    'Music Player Daemon': 'mpd',
    'VLC media player': 'vlc'
'''

# The icons below use Papirus icon theme.
# https://github.com/PapirusDevelopmentTeam/papirus-icon-theme/.
CLIENT_ID = '634301827558670357'
PLAYER_ICONS = {
    'Chromium': 'chromium-papirus',
    'Clementine': 'clementine-papirus',
    'Elisa': 'elisa-papirus',
    'Google Chrome': 'google-chrome-papirus',
    'Gwenview': 'gwenview-papirus',
    'Media Player Classic Qute Theater': 'mpc-qt-papirus',
    'Firefox Web Browser': 'firefox-papirus',
    'mpv': 'mpv-papirus',
    'SMPlayer': 'smplayer-papirus',
    'Spotify on Mozilla Firefox': 'spotify-papirus',
    'Strawberry': 'strawberry-papirus',
    'VLC media player': 'vlc-papirus',
    'YouTube on Mozilla Firefox': 'youtube-on-firefox-papirus',
    'youtube': 'youtube-papirus' # i forgot which app used this exact player name for its mpris2 interface
}
PLAYER_ALIASES = {
    'Clementine': 'Clementine Music Player',
    'Elisa': 'Elisa Music Player',
    'Firefox Web Browser': 'Mozilla Firefox',
    'Strawberry': 'Strawberry Music Player'
}
DEFAULT_LOG_LEVEL = logging.WARNING

logger = logging.getLogger(__name__)
logging.basicConfig(level=DEFAULT_LOG_LEVEL)

STATE_PRIORITY = (PlaybackStatus.PLAYING,
                  PlaybackStatus.PAUSED,
                  PlaybackStatus.STOPPED)


class DiscordMpris:

    active_player: Optional[Player] = None
    last_activity: Optional[JSON] = None

    def __init__(self, mpris: Mpris2Dbussy, discord: AsyncDiscordRpc, config: Config,
                 ) -> None:
        self.mpris = mpris
        self.discord = discord
        self.config = config

    async def connect_discord(self) -> None:
        if self.discord.connected:
            return
        logger.debug("Trying to connect to Discord client...")
        while True:
            try:
                await self.discord.connect()
            except DiscordRpcError:
                logger.debug("Failed to connect to Discord client")
            except async_exceptions:
                logger.debug("Connection to Discord lost")
            else:
                logger.info("Connected to Discord client")
                return
            await asyncio.sleep(self.config.raw_get('global.reconnect_wait', 1))

    async def run(self) -> int:
        await self.connect_discord()

        while True:
            try:
                await self.tick()

            except async_exceptions as e:
                if logger.isEnabledFor(logging.DEBUG):
                    logger.debug("Connection error during tick", exc_info=e)
                # logger.info("Connection to Discord client lost. Reconnecting...")
                # await self.connect_discord()
                # NOTE exits when connection lost, cause restarting seems to be more reliable
                logger.info("Connection to Discord client lost.")
                return 1

            except dbussy.DBusError as e:
                if e.name == "org.freedesktop.DBus.Error.ServiceUnknown":
                    # bus probably terminated during tick
                    continue
                logger.exception("Unknown DBusError encountered during tick", exc_info=e)
                return 1  # TODO for now, this is unrecoverable

            await asyncio.sleep(self.config.raw_get('global.poll_interval', 5))

    async def tick(self) -> None:
        player = await self.find_active_player()
        if not player:
            if self.active_player:
                logger.info(f"Player {self.active_player.bus_name!r} unselected")
            if self.last_activity:
                await self.discord.clear_activity()
                self.last_activity = None
            self.active_player = None
            return

        # firefox's own basic MPRIS2 interface (only basic play/pause/stop controls)
        if player.bus_name.startswith("firefox.instance"):
            return

        # store for future prioritization
        if not self.active_player or self.active_player.bus_name != player.bus_name:
            logger.info(f"Selected player bus {player.bus_name!r}")
        self.active_player = player

        activity: JSON = {}

        try:
            metadata, position, state = \
                await asyncio.gather(
                    player.player.Metadata,  # type: ignore
                    player.player.Position,  # type: ignore
                    player.player.PlaybackStatus,  # type: ignore
                )
        except Exception as e:
            logger.warn(f"{self.active_player.bus_name}:{e}")
            return

        metadata = unwrap_metadata(metadata)
        logger.debug(f"Metadata: {metadata}")
        length = metadata.get('mpris:length', 0)

        replacements = self.build_replacements(player, metadata)
        replacements['position'] = self.format_timestamp(position)
        replacements['length'] = self.format_timestamp(length)
        replacements['player'] = player.name
        replacements['state'] = state

        # icons
        large_image = PLAYER_ICONS[replacements['player']]

        # attempt to use alias for large text
        if replacements['player'] in PLAYER_ALIASES:
            large_text = PLAYER_ALIASES[replacements['player']]
        else:
            large_text = replacements['player']

        # modify large text if playing YouTube or Spotify on web browsers
        # currently having interface issues with Chromium browsers
        # ERROR:ampris2:Unable to fetch interfaces for player 'chrome.instanceXXXXX' - org.freedesktop.DBus.Error.UnknownInterface -- peer “org.mpris.MediaPlayer2.chrome.instanceXXXXX” object “/org/mpris/MediaPlayer2” does not understand interface “org.mpris.MediaPlayer2”
        if player.bus_name == "plasma-browser-integration" and replacements['xesam_url']:
            if re.match(r'^https?://(www|music)\.youtube\.com/watch\?.*$', replacements['xesam_url'], re.M):
                large_text = f"YouTube on {large_text}"
                large_image = PLAYER_ICONS[large_text]
            elif re.match(r'^https?://open\.spotify\.com/.*$', replacements['xesam_url'], re.M):
                large_text = f"Spotify on {large_text}"
                large_image = PLAYER_ICONS[large_text]

        # set timestamps, small text (and state fallback)
        activity['timestamps'] = {}
        if state == PlaybackStatus.PLAYING:
            show_time = self.config.player_get(player, 'show_time', 'elapsed')
            start_time = int(time.time() - position / 1e6)
            if show_time == 'elapsed':
                activity['timestamps']['start'] = start_time
            elif show_time == 'remaining':
                end_time = start_time + (length / 1e6)
                activity['timestamps']['end'] = end_time
            if replacements['length'] != "0:00":
                small_text = self.format_details("{state} [{length}]", replacements)
            elif player.name == "youtube":
                small_text = self.format_details("{state} [LIVE]", replacements)
            else:
                small_text = self.format_details("{state}", replacements)
        elif state == PlaybackStatus.PAUSED:
            if replacements['length'] != "0:00":
                small_text = self.format_details("{state} [{position}/{length}]", replacements)
            else:
                small_text = self.format_details("{state} [{position}]", replacements)
        else:
            small_text = self.format_details("{state}", replacements)

        # set details and state
        activity['details'] = self.format_details("{title}", replacements)
        if replacements['artist']:
            if replacements['album']:
                activity['state'] = self.format_details("{artist} on {album}", replacements)
            else:
                activity['state'] = self.format_details("{artist}", replacements)
        elif replacements['album']:
            activity['state'] = self.format_details("{album}", replacements)
        else:
            activity['state'] = small_text

        # set icons and hover texts
        if replacements['player'] in PLAYER_ICONS:
            activity['assets'] = {'large_text': large_text,
                                  'large_image': large_image,
                                  'small_image': state.lower(),
                                  'small_text': small_text}
        else:
            activity['assets'] = {'large_text': f"{large_text} ({state})",
                                  'large_image': state.lower()}

        # slice strings
        if activity['state'] and (len(activity['state']) > 128):
            activity['state'] = activity['state'][:127] + '\u2026'

        if activity['details'] and (len(activity['details']) > 128):
            activity['details'] = activity['details'][:127] + '\u2026'

        if 'large_text' in activity['assets'] and (len(activity['assets']['large_text']) > 128):
            activity['assets']['large_text'] = activity['assets']['large_text'][:127] + '\u2026'

        if 'small_text' in activity['assets'] and (len(activity['assets']['small_text']) > 128):
            activity['assets']['small_text'] = activity['assets']['small_text'][:127] + '\u2026'

        if activity != self.last_activity:
            op_recv, result = await self.discord.set_activity(activity)
            if result['evt'] == 'ERROR':
                logger.error(f"Error setting activity: {result['data']['message']}")
            self.last_activity = activity
        else:
            logger.debug("Not sending activity because it didn't change")

    async def find_active_player(self) -> Optional[Player]:
        active_player = self.active_player
        players = await self.mpris.get_players()

        # refresh active player (in case it restarted or sth)
        if active_player:
            for p in players:
                if p.bus_name == active_player.bus_name:
                    active_player = p
                    break
            else:
                logger.info(f"Player {active_player.bus_name!r} lost")
                self.active_player = active_player = None

        groups = await self.group_players(players)
        if logger.isEnabledFor(logging.DEBUG):
            debug_list = [(state, ", ".join(p.bus_name for p in groups[state]))
                          for state in STATE_PRIORITY]
            logger.debug(f"found players: {debug_list}")

        # Prioritize last active player per group,
        # but don't check stopped players.
        for state in STATE_PRIORITY[:2]:
            group = groups[state]
            candidates: List[Player] = []
            for p in group:
                if p is active_player:
                    candidates.insert(0, p)
                else:
                    candidates.append(p)

            for player in group:
                if (
                    not self.config.player_get(player, "ignore", False)
                    and (state == PlaybackStatus.PLAYING
                         or self.config.player_get(player, 'show_paused', True))
                ):
                    return player

        # no playing or paused player found
        if active_player and self.config.player_get(active_player, 'show_stopped', False):
            return active_player
        else:
            return None

    def _player_not_ignored(self, player: Player) -> bool:
        return (not self.config.player_get(player, "ignore", False))

    def build_replacements(self, player: Player, metadata) -> Dict[str, Optional[str]]:
        replacements = metadata.copy()

        # aggregate artist and albumArtist fields
        for key in ('artist', 'albumArtist'):
            source = metadata.get(f'xesam:{key}', ())
            if isinstance(source, str):  # In case the server doesn't follow mpris specs
                replacements[key] = source
            else:
                replacements[key] = " & ".join(source)
        # shorthands
        replacements['title'] = metadata.get('xesam:title', "")
        replacements['album'] = metadata.get('xesam:album', "")

        # replace invalid indent char
        replacements = {key.replace(':', '_'): val for key, val in replacements.items()}

        return replacements

    @staticmethod
    async def group_players(players: Iterable[Player]
                            ) -> Dict[PlaybackStatus, List[Player]]:
        groups: Dict[PlaybackStatus, List[Player]] = {state: [] for state in PlaybackStatus}
        for p in players:
            playbackStatus = await p.player.PlaybackStatus
            try:
                state = PlaybackStatus(playbackStatus)
            except ValueError as error:
                logger.info(f"Caugh a ValueError {playbackStatus}")
                continue
            groups[state].append(p)

        return groups

    @staticmethod
    def format_timestamp(microsecs: Optional[int]) -> Optional[str]:
        if microsecs is None:
            return None
        microsecs = int(microsecs)
        secs = microsecs // int(1e6)
        mins, secs = divmod(secs, 60)
        hours, mins = divmod(mins, 60)
        string = f"{mins:d}:{secs:02d}"
        if hours > 0:
            string = f"{hours:d}:{mins:02d}:{secs:02d}"
        return string

    @staticmethod
    def format_details(template: str, replacements: Dict[str, Optional[str]]) -> str:
        return template.format(**replacements)


async def main_async(loop: asyncio.AbstractEventLoop):
    config = Config.load()
    # TODO validate?

    log_level_name = None
    if config.raw_get('global.debug', False):
        log_level_name = 'DEBUG'
    log_level_name = config.raw_get('global.log_level', log_level_name)
    if log_level_name and log_level_name.isupper():
        log_level = getattr(logging, log_level_name, logging.WARNING)
        logging.getLogger().setLevel(log_level)

    logger.debug(f"Config: {config.raw_config}")

    mpris = await Mpris2Dbussy.create(loop=loop)
    async with AsyncDiscordRpc.for_platform(CLIENT_ID) as discord:
        instance = DiscordMpris(mpris, discord, config)
        return await instance.run()


def main() -> int:
    loop = asyncio.get_event_loop()
    main_task = loop.create_task(main_async(loop))
    try:
        return loop.run_until_complete(main_task)
    except BaseException as e:
        main_task.cancel()
        wait_task = asyncio.wait_for(main_task, 5, loop=loop)
        try:
            loop.run_until_complete(wait_task)
        except asyncio.CancelledError:
            pass
        except asyncio.TimeoutError:
            logger.error("Task didn't terminate within the set timeout")

        if isinstance(e, Exception):
            logger.exception("Unknown exception", exc_info=e)
            return 1

    return 0


if __name__ == '__main__':
    sys.exit(main())
