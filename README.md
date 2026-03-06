# Grocery Bot 🛒

A personal grocery assistant on Telegram, powered by a local Hebrew LLM (DictaLM via Ollama).

## Setup

### 1. Install dependencies
```bash
pip install -r requirements.txt
```

### 2. Create a Telegram bot
1. Open Telegram and message **@BotFather**
2. Send `/newbot` and follow the prompts
3. Copy the token you receive

### 3. Configure
Edit `config.py`:
```python
TELEGRAM_TOKEN = "your-token-here"
```

### 4. Test everything (without Telegram)
```bash
# Test DB + executor only (no Ollama needed)
python test_local.py --no-ollama

# Full test including LLM (Ollama must be running)
python test_local.py
```

### 5. Run the bot
```bash
python bot.py
```

### 6. Run on startup (systemd)
```ini
# /etc/systemd/system/grocery-bot.service
[Unit]
Description=Grocery Bot
After=network.target

[Service]
WorkingDirectory=/path/to/grocery-bot
ExecStart=/usr/bin/python3 bot.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```
```bash
sudo systemctl enable grocery-bot
sudo systemctl start grocery-bot
```

## Usage

Just write naturally in Hebrew:

| What you say | What happens |
|---|---|
| קניתי חלב וביצים | Updates inventory (status → יש) |
| נגמר הלחם | Marks depleted, adds to shopping list |
| החמאה על הסף | Marks low, adds to shopping list |
| מה צריך לקנות? | Shows shopping list |
| מה יש בבית? | Shows full inventory |
| מה אפשר לבשל? | Suggests recipes (if any added) |

## File structure
```
grocery-bot/
├── bot.py              # Telegram bot
├── intent_parser.py    # LLM → JSON action (only LLM code)
├── executor.py         # Business logic (no LLM)
├── db.py               # All database operations
├── config.py           # Configuration
├── test_local.py       # Component tests
├── requirements.txt
└── data/
    └── grocery.db      # SQLite database (auto-created)
```
