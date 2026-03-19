import json
import logging
import os
import sqlite3
import sys
import time

import requests
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC


# =========================
# PATHS
# =========================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")

CONFIG_FILE = os.path.join(DATA_DIR, "config.json")
DB_FILE = os.path.join(DATA_DIR, "carousell_links.db")
LOG_FILE = os.path.join(DATA_DIR, "scraper.log")
AUTHORIZED_USERS_FILE = os.path.join(DATA_DIR, "authorized_users.txt")
PENDING_USERS_FILE = os.path.join(DATA_DIR, "pending_users.txt")


# =========================
# CONFIG LOADER
# =========================

def ensure_data_directory():
    os.makedirs(DATA_DIR, exist_ok=True)


def ensure_runtime_files():
    for file_path in (AUTHORIZED_USERS_FILE, PENDING_USERS_FILE):
        if not os.path.exists(file_path):
            with open(file_path, "w", encoding="utf-8"):
                pass


def load_config():
    if not os.path.exists(CONFIG_FILE):
        raise FileNotFoundError(
            f"Missing config file: {CONFIG_FILE}\n"
            f"Create it from config.example.json first."
        )

    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        config = json.load(f)

    required_keys = ["telegram_bot_token", "searches"]
    missing_keys = [key for key in required_keys if key not in config]
    if missing_keys:
        raise KeyError(f"Missing required config keys: {', '.join(missing_keys)}")

    if not isinstance(config["searches"], list) or not config["searches"]:
        raise ValueError("config['searches'] must be a non-empty list")

    for i, search in enumerate(config["searches"], start=1):
        if "name" not in search or "url" not in search:
            raise ValueError(f"Search #{i} must contain both 'name' and 'url'")

    return config


# =========================
# LOGGING
# =========================

def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(LOG_FILE, encoding="utf-8"),
        ],
    )


# =========================
# USER FILES
# =========================

def load_authorized_users():
    users = {}

    if not os.path.exists(AUTHORIZED_USERS_FILE):
        return users

    with open(AUTHORIZED_USERS_FILE, "r", encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue

            if "," not in line:
                logging.warning(f"Skipping malformed authorized user line: {line}")
                continue

            chat_id, username = line.split(",", 1)
            users[chat_id.strip()] = username.strip()

    return users


def append_pending_user(chat_id, username):
    chat_id = str(chat_id).strip()
    username = (username or "unknown").strip()

    existing_ids = set()

    if os.path.exists(PENDING_USERS_FILE):
        with open(PENDING_USERS_FILE, "r", encoding="utf-8") as f:
            for raw_line in f:
                line = raw_line.strip()
                if not line or "," not in line:
                    continue
                existing_chat_id = line.split(",", 1)[0].strip()
                existing_ids.add(existing_chat_id)

    if chat_id in existing_ids:
        return

    with open(PENDING_USERS_FILE, "a", encoding="utf-8") as f:
        f.write(f"{chat_id}, {username}\n")

    logging.info(f"Added pending user: {chat_id}, {username}")


def is_authorized_chat(chat_id, authorized_users):
    return str(chat_id) in authorized_users


# =========================
# DATABASE
# =========================

def init_db_connection():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS product_links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            link TEXT NOT NULL UNIQUE,
            source_name TEXT,
            source_url TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS subscribers (
            chat_id TEXT PRIMARY KEY,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS bot_state (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)

    conn.commit()
    return conn


def seed_authorized_users(conn, authorized_users):
    cursor = conn.cursor()

    for chat_id in authorized_users:
        cursor.execute("""
            INSERT OR IGNORE INTO subscribers (chat_id, is_active)
            VALUES (?, 1)
        """, (str(chat_id),))

    conn.commit()


def sync_authorized_users(conn, authorized_users):
    cursor = conn.cursor()

    for chat_id in authorized_users:
        cursor.execute("""
            INSERT OR IGNORE INTO subscribers (chat_id, is_active)
            VALUES (?, 1)
        """, (str(chat_id),))

    conn.commit()


def get_active_chat_ids(conn, authorized_users):
    if not authorized_users:
        return []

    placeholders = ",".join(["?"] * len(authorized_users))
    cursor = conn.cursor()
    cursor.execute(f"""
        SELECT chat_id
        FROM subscribers
        WHERE is_active = 1
          AND chat_id IN ({placeholders})
    """, list(authorized_users.keys()))

    rows = cursor.fetchall()
    return [row[0] for row in rows]


def set_subscription_status(conn, chat_id, is_active):
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO subscribers (chat_id, is_active)
        VALUES (?, ?)
        ON CONFLICT(chat_id) DO UPDATE SET is_active = excluded.is_active
    """, (str(chat_id), 1 if is_active else 0))
    conn.commit()


def get_existing_links(conn, links):
    if not links:
        return set()

    cursor = conn.cursor()
    placeholders = ",".join(["?"] * len(links))
    cursor.execute(
        f"SELECT link FROM product_links WHERE link IN ({placeholders})",
        links
    )

    rows = cursor.fetchall()
    return {row[0] for row in rows}


def save_new_links(conn, new_links_with_source):
    if not new_links_with_source:
        return 0

    cursor = conn.cursor()
    cursor.executemany("""
        INSERT OR IGNORE INTO product_links (link, source_name, source_url)
        VALUES (?, ?, ?)
    """, new_links_with_source)

    conn.commit()
    return cursor.rowcount


def get_bot_state(conn, key, default_value="0"):
    cursor = conn.cursor()
    cursor.execute("SELECT value FROM bot_state WHERE key = ?", (key,))
    row = cursor.fetchone()
    return row[0] if row else default_value


def set_bot_state(conn, key, value):
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO bot_state (key, value)
        VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
    """, (key, str(value)))
    conn.commit()


# =========================
# TELEGRAM
# =========================

def telegram_api_request(bot_token, method, http_method="POST", payload=None, max_retries=3, retry_delay=3):
    url = f"https://api.telegram.org/bot{bot_token}/{method}"
    last_error = None

    for attempt in range(1, max_retries + 1):
        try:
            if http_method.upper() == "GET":
                response = requests.get(url, params=payload, timeout=30)
            else:
                response = requests.post(url, data=payload, timeout=30)

            response.raise_for_status()
            return response.json()

        except requests.RequestException as error:
            last_error = error
            logging.warning(
                f"Telegram API {method} failed on attempt "
                f"{attempt}/{max_retries}: {error}"
            )

            if attempt < max_retries:
                time.sleep(retry_delay)

    raise last_error


def send_telegram_message(bot_token, chat_id, text, max_retries=3, retry_delay=3):
    return telegram_api_request(
        bot_token=bot_token,
        method="sendMessage",
        http_method="POST",
        payload={
            "chat_id": str(chat_id),
            "text": text,
            "disable_web_page_preview": False,
        },
        max_retries=max_retries,
        retry_delay=retry_delay,
    )


def send_to_active_users(conn, authorized_users, bot_token, text, max_retries=3, retry_delay=3):
    chat_ids = get_active_chat_ids(conn, authorized_users)

    if not chat_ids:
        logging.info("No active authorized users to notify.")
        return

    logging.info(f"Sending notification to {len(chat_ids)} active chat(s)")

    for chat_id in chat_ids:
        try:
            send_telegram_message(
                bot_token=bot_token,
                chat_id=chat_id,
                text=text,
                max_retries=max_retries,
                retry_delay=retry_delay,
            )
            logging.info(f"Sent notification to chat_id={chat_id}")
        except Exception as error:
            logging.exception(f"Failed to send notification to chat_id={chat_id}: {error}")


def poll_telegram_commands(conn, authorized_users, bot_token, max_retries=3, retry_delay=3):
    last_update_id = int(get_bot_state(conn, "last_update_id", "0"))

    result = telegram_api_request(
        bot_token=bot_token,
        method="getUpdates",
        http_method="GET",
        payload={
            "offset": last_update_id,
            "timeout": 0,
        },
        max_retries=max_retries,
        retry_delay=retry_delay,
    )

    updates = result.get("result", [])
    if not updates:
        return

    next_update_id = last_update_id

    for update in updates:
        update_id = update.get("update_id")
        if update_id is not None:
            next_update_id = max(next_update_id, update_id + 1)

        message = update.get("message")
        if not message:
            continue

        chat_id = str(message.get("chat", {}).get("id", "")).strip()
        text = (message.get("text") or "").strip().lower()
        username = message.get("from", {}).get("username") or message.get("from", {}).get("first_name") or "unknown"

        if not chat_id or not text:
            continue

        logging.info(f"Received Telegram command from {chat_id}: {text}")

        if not is_authorized_chat(chat_id, authorized_users):
            append_pending_user(chat_id, username)

            try:
                send_telegram_message(
                    bot_token=bot_token,
                    chat_id=chat_id,
                    text="You are not authorized yet. Your chat ID has been logged.",
                    max_retries=max_retries,
                    retry_delay=retry_delay,
                )
            except Exception:
                logging.exception(f"Failed to reply to unauthorized user {chat_id}")

            continue

        if text == "/start":
            set_subscription_status(conn, chat_id, True)
            send_telegram_message(
                bot_token=bot_token,
                chat_id=chat_id,
                text="Notifications enabled.",
                max_retries=max_retries,
                retry_delay=retry_delay,
            )

        elif text == "/stop":
            set_subscription_status(conn, chat_id, False)
            send_telegram_message(
                bot_token=bot_token,
                chat_id=chat_id,
                text="Notifications stopped. Send /start to enable them again.",
                max_retries=max_retries,
                retry_delay=retry_delay,
            )

        elif text == "/help":
            send_telegram_message(
                bot_token=bot_token,
                chat_id=chat_id,
                text="Available commands:\n/start - enable notifications\n/stop - disable notifications\n/help - show this message",
                max_retries=max_retries,
                retry_delay=retry_delay,
            )

        else:
            send_telegram_message(
                bot_token=bot_token,
                chat_id=chat_id,
                text="Unknown command. Use /start, /stop, or /help.",
                max_retries=max_retries,
                retry_delay=retry_delay,
            )

    set_bot_state(conn, "last_update_id", str(next_update_id))


# =========================
# SELENIUM
# =========================

def get_driver():
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
    return webdriver.Chrome(options=options)


def collect_links(driver, search, limit_per_search):
    search_name = search["name"]
    search_url = search["url"]

    logging.info(f"Checking search: {search_name}")
    driver.get(search_url)

    wait = WebDriverWait(driver, 20)
    wait.until(
        EC.presence_of_element_located((By.XPATH, "//a[contains(@href,'/p/')]"))
    )

    driver.execute_script("window.scrollTo(0, document.body.scrollHeight)")
    time.sleep(2)

    elements = driver.find_elements(By.XPATH, "//a[contains(@href,'/p/')]")

    links = []
    seen = set()

    for element in elements:
        href = element.get_attribute("href")
        if not href:
            continue

        href = href.split("?")[0]

        if href not in seen:
            seen.add(href)
            links.append(href)

        if len(links) >= limit_per_search:
            break

    logging.info(f"Collected {len(links)} link(s) for search: {search_name}")
    return links


# =========================
# SCRAPER LOGIC
# =========================

def seed_existing_listings(driver, conn, searches, limit_per_search):
    logging.info("Seeding current listings without Telegram notifications")
    total_seeded = 0

    for search in searches:
        try:
            links = collect_links(driver, search, limit_per_search)
            existing_links = get_existing_links(conn, links)

            new_links_with_source = [
                (link, search["name"], search["url"])
                for link in links
                if link not in existing_links
            ]

            inserted = save_new_links(conn, new_links_with_source)
            total_seeded += inserted

            logging.info(f"Seeded {inserted} unseen link(s) for search: {search['name']}")
        except Exception as error:
            logging.exception(f"Error during seed for {search['name']}: {error}")

    logging.info(f"Initial seeding complete. Total inserted: {total_seeded}")


def monitor_new_listings(driver, conn, config):
    searches = config["searches"]
    limit_per_search = config.get("limit_per_search", 20)
    check_interval_seconds = config.get("check_interval_seconds", 60)
    bot_token = config["telegram_bot_token"]
    max_retries = config.get("telegram_max_retries", 3)
    retry_delay = config.get("telegram_retry_delay_seconds", 3)

    logging.info("Monitoring started. Press Ctrl+C to stop.")

    while True:
        cycle_new_count = 0

        authorized_users = load_authorized_users()
        sync_authorized_users(conn, authorized_users)

        try:
            poll_telegram_commands(
                conn=conn,
                authorized_users=authorized_users,
                bot_token=bot_token,
                max_retries=max_retries,
                retry_delay=retry_delay,
            )
        except Exception as error:
            logging.exception(f"Error while polling Telegram commands at cycle start: {error}")

        for search in searches:
            try:
                links = collect_links(driver, search, limit_per_search)
                existing_links = get_existing_links(conn, links)

                new_links = [link for link in links if link not in existing_links]
                new_links_with_source = [
                    (link, search["name"], search["url"])
                    for link in new_links
                ]

                if new_links_with_source:
                    inserted = save_new_links(conn, new_links_with_source)
                    logging.info(
                        f"Found {len(new_links)} new link(s), inserted {inserted} into database"
                    )

                    authorized_users = load_authorized_users()
                    sync_authorized_users(conn, authorized_users)

                    for link in new_links:
                        message = (
                            "New Carousell listing found\n\n"
                            f"Listing: {link}\n"
                            f"Source: {search['name']}"
                        )
                        send_to_active_users(
                            conn=conn,
                            authorized_users=authorized_users,
                            bot_token=bot_token,
                            text=message,
                            max_retries=max_retries,
                            retry_delay=retry_delay,
                        )

                    cycle_new_count += len(new_links)
                else:
                    logging.info(f"No new listings for search: {search['name']}")

            except Exception as error:
                logging.exception(f"Error during scrape cycle for {search['name']}: {error}")

        try:
            authorized_users = load_authorized_users()
            sync_authorized_users(conn, authorized_users)

            poll_telegram_commands(
                conn=conn,
                authorized_users=authorized_users,
                bot_token=bot_token,
                max_retries=max_retries,
                retry_delay=retry_delay,
            )
        except Exception as error:
            logging.exception(f"Error while polling Telegram commands at cycle end: {error}")

        logging.info(f"Cycle complete. Total new listings found: {cycle_new_count}")
        time.sleep(check_interval_seconds)


# =========================
# MAIN
# =========================

def main():
    ensure_data_directory()
    ensure_runtime_files()

    config = load_config()
    setup_logging()
    logging.info("Starting script")

    conn = None
    driver = None

    try:
        authorized_users = load_authorized_users()

        conn = init_db_connection()
        seed_authorized_users(conn, authorized_users)
        logging.info(f"Connected to SQLite database: {DB_FILE}")

        driver = get_driver()
        logging.info("Chrome driver started")

        seed_existing_listings(
            driver=driver,
            conn=conn,
            searches=config["searches"],
            limit_per_search=config.get("limit_per_search", 20),
        )

        monitor_new_listings(
            driver=driver,
            conn=conn,
            config=config,
        )

    except KeyboardInterrupt:
        logging.info("Stopped by user")

    finally:
        if driver is not None:
            driver.quit()
            logging.info("Chrome driver closed")

        if conn is not None:
            conn.close()
            logging.info("SQLite connection closed")


if __name__ == "__main__":
    main()