# Telegram AutoForwarder [Working in 2025]

Effortlessly forward messages—including text, images, videos, voice notes, and more—between any number of Telegram chats or channels, using native forwarding and keyword filters.

---

## Key Capabilities

- **True Forwarding**  
  Leverages Telethon’s `forward_messages` method to preserve the original sender, timestamp, and “Forwarded” header for every message and media type.

- **Keyword-Based Filtering**  
  Forward only messages that match one or more case‑insensitive keywords of your choice.

- **Multiple Jobs & Background Operation**  
  Create and manage multiple “forwarding jobs” in a single session. Start them all at once—the script runs each job in the background while you retain menu control.

- **Persistent Job History**  
  Every forwarding session is logged (timestamp, sources, destination, keywords) to `forward_history.txt` for audit or review.

- **Interactive Menu**  
  1. **List Chats** – Export all your dialogs to a text file.  
  2. **Add Forwarding Job** – Specify source chat(s), destination channel/chat, and optional keywords.  
  3. **List Forwarding Jobs** – Review all configured jobs.  
  4. **Start Forwarding** – Launch all jobs asynchronously; menu remains responsive.  
  5. **Stop Forwarding** – Cancel all background tasks without exiting.  
  6. **Exit** – Quit cleanly.

---

## How It Works

1. **Authentication**  
   On first run, enter your Telegram API ID, API Hash, and phone number (saved to `credentials.txt`). Supports two‑step verification if enabled.

2. **Job Setup**  
   Use the menu to add as many source→destination jobs as you need, with optional keyword filters.

3. **Background Forwarding**  
   Each job polls its source chats for new messages, filters by keyword, and uses Telethon’s `forward_messages` to relay content.

4. **Logging**  
   Sessions are appended to `forward_history.txt` with timestamp, source IDs, destination ID, and keywords.

---

## Installation & Usage
Remember to edit the forwarding_config.txt, where the sources and destinations must be added.
```bash
sudo apt update && sudo apt upgrade
sudo apt install git -y
git clone https://github.com/Oliviertjeh/TelegramForwarder
cd TelegramForwarder
python3 -m venv .venv/TGforwarder
source .venv/TGforwarder/bin/activate
pip install -r requirements.txt
python TelegramForwarder.py

# (optional) You can run it in the background by:
sudo nano /etc/systemd/system/telegram_forwarder.service
and add the code of the telegram_forwarder.service file

# restart
sudo systemctl daemon-reload
sudo systemctl restart telegram_forwarder.service

#check the status
sudo systemctl status telegram_forwarder.service
journalctl -u telegram_forwarder.service -f
then you can press ctrl + c to stop live logs and close for example Putty

