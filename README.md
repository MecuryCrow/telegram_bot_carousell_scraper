# 🛒 Carousell Listing Monitor (Telegram Bot)

A lightweight Python scraper that monitors Carousell listings and sends real-time Telegram notifications when new items appear.

Built as a simple, privacy-safe hobby project with clean structure and minimal dependencies.

---

## 🚀 Features

- 🔍 Monitor multiple Carousell search URLs  
- 🧠 Detect new listings using SQLite (no duplicates)  
- 📩 Send instant Telegram notifications  
- 👥 Multi-user support with approval system  
- 🔒 Safe config handling (no secrets in code)  
- 🔄 Continuous monitoring with configurable intervals  
- 📝 Logging for debugging and tracking  

---

## 🏗️ Project Structure
```bash
carousell-monitor/
│
├── carousell_bot.py
├── requirements.txt
├── README.md
├── .gitignore
│
└─── data/ # (not tracked by git)
├─── config.json
├─── carousell_links.db
├─── scraper.log
├─── authorized_users.txt
└─── pending_users.txt
```
---

## ⚙️ Setup

### 1. Clone the repository
```bash
git clone https://github.com/MecuryCrow/telegram_bot_carousell_scraper.git
```

### 2. Install dependencies
```bash
pip install -r requirements.txt
```
### 3. Create config file (use the example config in /data/config.json or copy config below)
*note: use 'sort_by=3' for most recent listing
```bash
{
  "telegram_bot_token": "YOUR_BOT_TOKEN",
  "check_interval_seconds": 60,
  "limit_per_search": 20,
  "searches": [
    {
      "name": "Pokemon PSA10",
      "url": "https://www.carousell.sg/search/Pokemon%20PSA10?sort_by=3"
    }
  ]
}
```

### 4. Run the bot
```bash
python carousell_bot.py
```
or just use the start.bat included

---
## 🤖 Telegram Setup
### 1. Create a bot

Message @BotFather on Telegram

Use /newbot

Copy your bot token

### 2. Start the bot

Send /start to your bot

👉 Your chat ID will be automatically logged in:
```
data/pending_users.txt
```
### 3. Approve users

Move users from: 
```
data/pending_users.txt
```
to: 
```
data/authorized_users.txt
```
Format:
```
123456789, username
```
---
## 💬 Commands
### Command	Description
/start	Enable notifications

/stop	Disable notifications

---
## 🔄 How It Works

Script loads search URLs from config

First run seeds existing listings (no spam)

Every cycle:

checks for new listings

compares against database

sends only new items

Telegram commands are polled each cycle

---
## 🔒 Security & Privacy

Bot token stored in data/config.json (not tracked)

.gitignore excludes all sensitive files

Chat IDs are not exposed in code

Logging avoids leaking sensitive data

---
## ⚠️ Notes

Requires Google Chrome installed

Uses Selenium (headless mode)

Avoid setting very low intervals (may get rate-limited)

Designed for small personal use 

---
## 🛠️ Configuration Options
Key:                            Description

check_interval_seconds:         Time between scans

limit_per_search:               Max listings per search

telegram_max_retries:           Retry attempts for Telegram

telegram_retry_delay_seconds:   Delay between retries

searches:                       List of monitored queries
