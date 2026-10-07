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

У `/top` и `/stats` есть кнопки под сообщением: категории рейтинга и игроки, вводить аргументы вручную не нужно.
| `/wl_add ник [@telegram]` | только админ (`TG_ADMIN_ID`), в чате сервера или в личке с ботом |
| `/wl_remove ник` | только админ |

`/wl_add` пишет в `whitelist.json` запись с offline-UUID и делает `whitelist reload` через RCON;
telegram-ник сохраняется в `state/players.json`. Остальным бот отвечает, что команда только для админа.

## Еженедельный дайджест

По воскресеньям в 20:00 (`DIGEST_WEEKDAY`, `DIGEST_TIME`) бот публикует итоги недели через канал:
кто сколько играл, рекорды, смерти и самая нелепая из них, достижения, как изменился мир.
Начало недели хранится снимком статистики в `state/digest.json`; `/week` показывает итоги на сейчас.

## Оповещения админу

Если задан `TG_ADMIN_ID`, раз в минуту бот проверяет сервер и пишет админу в личку:
сервер упал или не поднялся после остановки, краш (новый файл в `crash-reports/`), лаги,
устаревший бэкап (только если кто-то играет 30+ минут: `mc-backup` без игроков на паузе),
мало места на диске. Каждая проблема приходит один раз, затем «✅ всё ок»; бэкап и диск
напоминаются раз в `ALERT_REPEAT_HOURS`. Пороги — `ALERT_*` в `.env`.

## Структура

| Модуль | Что внутри |
|---|---|
| `allay/config.py` | чтение env-переменных |
| `allay/bot.py` | обработчики событий и команд |
| `allay/server.py` | RCON-запросы и чтение файлов сервера |
| `allay/deaths.py` | перевод сообщений о смерти (`lang/*.json`) |
| `allay/workers.py` | tail лога, слежение за whitelist |
| `allay/telegram.py`, `allay/rcon.py` | клиенты |
