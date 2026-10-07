import sys
import time

from .bot import Allay
from .config import Config, ConfigError
from .deaths import DeathTranslator
from .rcon import Rcon
from .server import Server
from .telegram import Telegram
from .util import log, setup_logging
from .workers import follow_log, run_forever, watch_whitelist


def main():
    try:
        cfg = Config.from_env()
    except ConfigError as ex:
        print(f"config error: {ex}", file=sys.stderr)
        sys.exit(1)

    setup_logging(cfg.tg_token)
    log.info("allay starting")

    server = Server(Rcon(cfg.rcon_host, cfg.rcon_port, cfg.rcon_password), cfg.data_dir, cfg.backups_dir)
    bot = Allay(cfg, Telegram(cfg.tg_token, cfg.proxy), server, DeathTranslator(cfg.lang_dir))

    run_forever(lambda: follow_log(server.log_file, bot.handle_log_line), "follow_log")
    run_forever(lambda: watch_whitelist(server.whitelist_file, bot.announce_new_player), "watch_whitelist")
    run_forever(bot.poll_commands, "poll_commands")
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main()
