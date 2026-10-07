# Allay

Telegram-бот для Minecraft-сервера: объявляет новых игроков, входы/выходы и смерти,
отвечает на `/status` и `/players`.

## Запуск на сервере

Бот подключается к docker-сети Minecraft-проекта (`/opt/minecraft`), чтобы ходить
в RCON (`mc`) и Telegram-прокси (`tg-tunnel`) по именам сервисов. Файлы сервера
монтируются только на чтение.

```sh
git clone <repo> /opt/allay && cd /opt/allay
cp .env.example .env    # заполнить TG_BOT_TOKEN, TG_CHAT_ID, RCON_PASSWORD, ...
mkdir -p state          # сюда кладётся players.json
docker compose up -d --build
docker compose logs -f
```

Имя сети смотрите в `docker network ls` (по умолчанию `minecraft_default`) и при
необходимости поменяйте `MC_NETWORK` в `.env`.

Обновление: `git pull && docker compose up -d --build`.

## Команды

| Команда | Кто |
|---|---|
| `/status`, `/players` | все в чате сервера |
| `/world` — день, время, сложность, общая статистика мира | все |
| `/top [time\|distance\|blocks\|diamonds\|mobs\|deaths]` — рейтинги | все |
| `/stats [ник]` — карточка игрока; без ника — по telegram из `players.json` | все |
| `/wl_add ник [@telegram]` | только админ (`TG_ADMIN_ID`), в чате сервера или в личке с ботом |
| `/wl_remove ник` | только админ |

`/wl_add` пишет в `whitelist.json` запись с offline-UUID и делает `whitelist reload` через RCON;
telegram-ник сохраняется в `state/players.json`. Остальным бот отвечает, что команда только для админа.

## Структура

| Модуль | Что внутри |
|---|---|
| `allay/config.py` | чтение env-переменных |
| `allay/bot.py` | обработчики событий и команд |
| `allay/server.py` | RCON-запросы и чтение файлов сервера |
| `allay/deaths.py` | перевод сообщений о смерти (`lang/*.json`) |
| `allay/workers.py` | tail лога, слежение за whitelist |
| `allay/telegram.py`, `allay/rcon.py` | клиенты |
