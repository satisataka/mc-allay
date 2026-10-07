import os
import sys
import time
from pathlib import Path

from .advancements import AdvancementParser
from .bot import Allay
from .config import Config, ConfigError
from .deaths import DeathTranslator
from .lang import load_lang
from .monitor import Monitor
from .rcon import Rcon
from .server import Server
from .telegram import Telegram
from .util import log, setup_logging
from .workers import follow_log, run_forever, stale_workers, watch_whitelist

# a worker may legitimately block for ~2 min (Telegram retries), but not for 10
STUCK_AFTER = 600
HEALTH_FILE = Path("/tmp/allay-healthy")  # docker healthcheck reads its mtime


def main():
    try:
        cfg = Config.from_env()
    except ConfigError as ex:
        print(f"config error: {ex}", file=sys.stderr)
        sys.exit(1)

    setup_logging(cfg.tg_token)
    log.info("allay starting")

    server = Server(Rcon(cfg.rcon_host, cfg.rcon_port, cfg.rcon_password), cfg.data_dir, cfg.backups_dir)
    en, ru = load_lang(cfg.lang_dir)
    bot = Allay(cfg, Telegram(cfg.tg_token, cfg.proxy), server,
                DeathTranslator(en, ru), AdvancementParser(en, ru), ru)

    try:
        bot.restore_state()
    except Exception:  # nice to have, never a reason not to start
        log.exception("restoring state from logs failed")
    run_forever(lambda: follow_log(server.log_file, bot.handle_log_line), "follow_log")
    run_forever(lambda: watch_whitelist(server.whitelist_file, bot.announce_new_player), "watch_whitelist")
    run_forever(bot.poll_commands, "poll_commands")
    if cfg.admin_id:
        run_forever(Monitor(bot).run, "monitor")
    else:
        log.info("TG_ADMIN_ID is not set, admin alerts are disabled")
    # watchdog: exit when a worker is stuck, so docker restarts the whole bot
    while True:
        stuck = stale_workers(STUCK_AFTER)
        if stuck:
            log.error(f"workers stuck: {', '.join(stuck)}; exiting for a restart")
            os._exit(1)  # sys.exit would wait for the stuck threads
        HEALTH_FILE.touch()
        time.sleep(30)


if __name__ == "__main__":
    main()
