import time
import asyncio
import logging
from functools import partial
from telethon import TelegramClient, errors, events
from telethon.tl.custom import Message

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s.%(msecs)03d - %(name)s - %(levelname)s - [%(funcName)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# Async input helper
def ainput(prompt: str = '') -> asyncio.Future:
    loop = asyncio.get_event_loop()
    return loop.run_in_executor(None, input, prompt)

class TelegramForwarder:
    def __init__(self, api_id, api_hash, phone_number):
        self.session_name = 'forwarder_session_' + phone_number.replace('+', '')
        self.client = TelegramClient(self.session_name, api_id, api_hash)
        self.phone_number = phone_number
        self.history_file = "forward_history.txt"
        self._running = False
        # Cache of individual message IDs forwarded as part of an album
        self.recently_processed_message_ids = set()
        self.msg_cache_clear_delay = 60.0

    async def _ensure_authorized(self):
        if not self.client.is_connected():
            await self.client.connect()
        if not await self.client.is_user_authorized():
            await self.client.send_code_request(self.phone_number)
            code = await ainput('Code: ')
            await self.client.sign_in(self.phone_number, code)

    async def _handle_album(self, event: events.Album.Event, dest_chat_id: int, keywords: list[str]):
        source = event.chat_id
        msgs = [m for m in event.messages if isinstance(m, Message)]
        if not msgs:
            return
        caption = event.text or ''
        if keywords and not any(kw.lower() in caption.lower() for kw in keywords):
            return

        # Cache each message ID immediately
        loop = asyncio.get_running_loop()
        ids = []
        for m in msgs:
            mid = m.id
            ids.append(mid)
            self.recently_processed_message_ids.add(mid)
            loop.call_later(self.msg_cache_clear_delay, self.recently_processed_message_ids.discard, mid)

        try:
            res = await self.client.forward_messages(entity=dest_chat_id, messages=msgs, from_peer=source)
            if res:
                self._record_forwarding(source, dest_chat_id, ids, keywords, album=True)
        except errors.FloodWaitError as e:
            await asyncio.sleep(e.seconds + 1)
            res = await self.client.forward_messages(entity=dest_chat_id, messages=msgs, from_peer=source)
            if res:
                self._record_forwarding(source, dest_chat_id, ids, keywords, album=True)
        except Exception:
            logger.error(f"Album forward failed for group {event.grouped_id}", exc_info=True)

    async def _handle_message(self, event: events.NewMessage.Event, dest_chat_id: int, keywords: list[str]):
        # Skip any part of an album; albums are handled separately
        if getattr(event, 'grouped_id', None):
            return

        msg_id = event.id
        text = event.text or ''

        # Skip if this message was just forwarded as part of an album
        if msg_id in self.recently_processed_message_ids:
            logger.info(f"Skipping already processed message {msg_id} (album part)")
            return

        # Keyword filter
        if keywords and not any(kw.lower() in text.lower() for kw in keywords):
            return

        try:
            await event.forward_to(dest_chat_id)
            self._record_forwarding(event.chat_id, dest_chat_id, [msg_id], keywords, album=False)
        except errors.FloodWaitError as e:
            await asyncio.sleep(e.seconds + 1)
            try:
                await event.forward_to(dest_chat_id)
                self._record_forwarding(event.chat_id, dest_chat_id, [msg_id], keywords, album=False)
            except Exception:
                logger.error(f"Retry failed for message {msg_id}", exc_info=True)
        except Exception:
            logger.error(f"Forward failed for message {msg_id}", exc_info=True)

    def _record_forwarding(self, src, dest, mids, kws, album=False):
        ts = time.strftime('%Y-%m-%d %H:%M:%S')
        ids_str = ','.join(map(str, mids))
        kwstr = ','.join(kws) if kws else 'ANY'
        tag = '[ALBUM]' if album else '[MSG]'
        line = f"{ts}\tSRC:{src}\tDEST:{dest}\tIDS:{ids_str}\tKWS:{kwstr}\t{tag}\n"
        logger.info(f"Forwarded {tag} {ids_str}")
        try:
            with open(self.history_file, 'a') as f:
                f.write(line)
        except Exception:
            logger.error("Failed writing history", exc_info=True)

    async def start_listening(self, jobs):
        if self._running or not jobs:
            return
        await self._ensure_authorized()
        if self.client.is_connected():
            await self.client.disconnect()
        await self.client.connect()

        # Clear existing handlers
        self.client.remove_event_handler(self._handle_album)
        self.client.remove_event_handler(self._handle_message)

        # Register handlers for each job
        for src_ids, dest_id, kws in jobs:
            album_h = partial(self._handle_album, dest_chat_id=dest_id, keywords=kws)
            msg_h = partial(self._handle_message, dest_chat_id=dest_id, keywords=kws)
            self.client.add_event_handler(album_h, events.Album(chats=src_ids))
            self.client.add_event_handler(msg_h, events.NewMessage(chats=src_ids, incoming=True))

        self._running = True
        await self.client.run_until_disconnected()
        self._running = False

    async def stop_listening(self):
        if self.client.is_connected():
            await self.client.disconnect()
        self._running = False

# Helpers to read config and credentials

def read_jobs(file='forwarding_config.txt'):
    jobs = []
    try:
        with open(file) as f:
            for line in f:
                raw = line.split('#', 1)[0].strip()
                if not raw:
                    continue
                parts = [p.strip() for p in raw.split(';')]
                if len(parts) < 2:
                    continue
                try:
                    src_ids = [int(x) for x in parts[0].split(',') if x.strip()]
                    dest_id = int(parts[1])
                except ValueError:
                    continue
                kws = [kw.lower() for kw in parts[2].split(',')] if len(parts) >= 3 and parts[2] else []
                jobs.append((src_ids, dest_id, kws))
    except FileNotFoundError:
        pass
    return jobs


def read_creds():
    try:
        with open('credentials.txt') as f:
            lines = [l.strip() for l in f if l.strip()]
        if len(lines) >= 3:
            return int(lines[0]), lines[1], lines[2]
    except Exception:
        pass
    return None


def write_creds(api_id, api_hash, phone):
    with open('credentials.txt', 'w') as f:
        f.write(f"{api_id}\n{api_hash}\n{phone}\n")

async def main():
    creds = read_creds()
    if not creds:
        api_id = int(await ainput('API ID: '))
        api_hash = await ainput('API Hash: ')
        phone = await ainput('Phone: ')
        write_creds(api_id, api_hash, phone)
    else:
        api_id, api_hash, phone = creds

    forwarder = TelegramForwarder(api_id, api_hash, phone)
    jobs = read_jobs()

    while True:
        is_running = forwarder._running
        print("\n--- Telegram Forwarder Menu ---")
        print("1. List Chats")
        print("2. Show Jobs")
        print(f"3. Start Listener {'(RUNNING)' if is_running else ''}")
        print(f"4. Stop Listener {'(NOT RUNNING)' if not is_running else ''}")
        print("5. Reload Jobs")
        print("6. Exit")
        choice = await ainput('Enter choice: ')

        if choice == '1':
            await forwarder.list_chats()
        elif choice == '2':
            if not jobs:
                print('No jobs loaded.')
            else:
                for i, (s, d, k) in enumerate(jobs, 1):
                    print(f"Job {i}: SRC={s} -> DEST={d}, KWS={[kw for kw in k]}")
        elif choice == '3':
            if not is_running and jobs:
                await forwarder.start_listening(jobs)
        elif choice == '4':
            if is_running:
                await forwarder.stop_listening()
        elif choice == '5':
            if not forwarder._running:
                jobs = read_jobs()
        elif choice == '6':
            if forwarder._running:
                await forwarder.stop_listening()
            break

if __name__ == '__main__':
    print("Starting Telegram Forwarder script...")
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Program terminated by user.")
