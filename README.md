# wilma-telegram

Forwards new **Wilma** (school) and **Daisy Family** (daycare) messages to a Telegram chat. Optionally extracts dates from those messages with xAI Grok and adds them to an iCloud calendar.

Wilma messages that go to several of your children are sent once. Daisy is optional: leave `DAISY_USERNAME` / `DAISY_PASSWORD` empty to skip it.

## Requirements

- Linux (systemd user service is the intended way to run it)
- Python 3.13 or newer (`wilhelmina` needs it)
- A [Telegram bot](https://core.telegram.org/bots#how-do-i-create-a-bot) and a chat with that bot
- Wilma login for your municipality (URL like `https://<municipality>.inschool.fi`)

## Setup

```bash
git clone <repo-url>
cd wilma-telegram
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env`. Required:

| Variable | Purpose |
| --- | --- |
| `WILMA_URL` | School Wilma base URL |
| `WILMA_USERNAME` / `WILMA_PASSWORD` | Wilma login |
| `TELEGRAM_BOT_TOKEN` | From [@BotFather](https://t.me/BotFather) |
| `TELEGRAM_CHAT_ID` | Numeric chat id (see below) |

Leave `INITIAL_SYNC=1` for the first run so existing inbox mail is marked seen and not dumped into Telegram. After that first poll, set it to `0` (or remove it) and restart.

### Telegram chat id

1. Message the bot yourself (a `/start` is enough).
2. Open `https://api.telegram.org/bot<TELEGRAM_BOT_TOKEN>/getUpdates` in a browser.
3. Copy `result[0].message.chat.id` into `TELEGRAM_CHAT_ID`.

Group chats work too; the id is often negative.

## Optional: calendar events

If `XAI_API_KEY` is set, each new message is sent to Grok (`XAI_MODEL`, default `grok-4.6`). Concrete dates become calendar events (title like `Automaatti (Maija): Retki`, or `Automaatti: …` when several children share the same Wilma message). A `.ics` file is still posted to Telegram.

To put events straight into Apple Calendar, also set:

- `ICLOUD_APPLE_ID`
- `ICLOUD_APP_PASSWORD` — [app-specific password](https://appleid.apple.com), not your Apple ID password
- `ICLOUD_CALENDAR` — display name of the calendar, for example `Koti`

The iPhone must be signed into that same iCloud account with Calendar sync on. Message text is sent to xAI when this feature is enabled.

## Run

One-shot (useful to test config):

```bash
source venv/bin/activate
python main.py --once
```

Continuously, sleeping `POLL_INTERVAL` seconds between polls (`.env.example` uses 900):

```bash
python main.py
```

Install as a systemd **user** service (starts now and on login):

```bash
./install-service.sh
```

```bash
systemctl --user status wilma-telegram
journalctl --user -u wilma-telegram -f
systemctl --user restart wilma-telegram
```

To keep it running after reboot without logging in:

```bash
loginctl enable-linger $USER
```

Seen-message state is stored in `data/seen.json` (override with `STATE_PATH`). That directory is gitignored.

## Configuration reference

See `.env.example` for every variable. Daisy uses `DAISY_URL` (like `https://<municipality>.daisyfamily.fi`) plus `DAISY_USERNAME` and `DAISY_PASSWORD`.
