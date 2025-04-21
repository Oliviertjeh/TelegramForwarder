import time
import asyncio
from telethon import TelegramClient, errors

# Async input helper
def ainput(prompt: str = '') -> asyncio.Future:
    """Run blocking input() in the default executor to avoid blocking the event loop."""
    loop = asyncio.get_event_loop()
    return loop.run_in_executor(None, input, prompt)

class TelegramForwarder:
    def __init__(self, api_id, api_hash, phone_number):
        self.client = TelegramClient('session_' + phone_number, api_id, api_hash)
        self.phone_number = phone_number
        self.history_file = "forward_history.txt"

    async def _ensure_authorized(self):
        await self.client.connect()
        if not await self.client.is_user_authorized():
            await self.client.send_code_request(self.phone_number)
            try:
                code = await ainput('Enter the code: ')
                await self.client.sign_in(self.phone_number, code)
            except errors.SessionPasswordNeededError:
                pw = await ainput('Two-step verification enabled. Enter password: ')
                await self.client.sign_in(password=pw)

    async def list_chats(self):
        await self._ensure_authorized()
        dialogs = await self.client.get_dialogs()
        fname = f"chats_of_{self.phone_number}.txt"
        with open(fname, "w", encoding="utf-8") as f:
            for dlg in dialogs:
                line = f"Chat ID: {dlg.id}, Title: {dlg.title}"
                print(line)
                f.write(f"{dlg.id}\t{dlg.title}\n")
        print(f"Chats saved to {fname}")

    def _record_forwarding(self, src_ids, dest_id, keywords):
        timestamp = time.strftime('%Y-%m-%d %H:%M:%S')
        with open(self.history_file, "a", encoding="utf-8") as f:
            f.write(f"{timestamp}\t{','.join(map(str, src_ids))}\t{dest_id}\t{','.join(keywords)}\n")

    async def _forward_loop(self, job):
        src_ids, dest_id, keywords = job
        await self._ensure_authorized()
        # Initialize last IDs
        last_ids = {}
        for cid in src_ids:
            msgs = await self.client.get_messages(cid, limit=1)
            last_ids[cid] = msgs[0].id if msgs else 0
        # Record history
        self._record_forwarding(src_ids, dest_id, keywords)

        while True:
            for cid in src_ids:
                new_msgs = await self.client.get_messages(cid, min_id=last_ids[cid])
                for msg in reversed(new_msgs):
                    # Keyword filter applies to text only
                    text = msg.text or ''
                    if not keywords or any(kw.lower() in text.lower() for kw in keywords):
                        # Actually forward the message (preserves media and forwarded header)
                        await self.client.forward_messages(dest_id, msg.id, from_peer=cid)
                    last_ids[cid] = max(last_ids[cid], msg.id)
            await asyncio.sleep(5)

async def main():
    def read_creds():
        try:
            with open('credentials.txt', 'r') as f:
                return [line.strip() for line in f]
        except FileNotFoundError:
            return None

    def write_creds(api_id, api_hash, phone):
        with open('credentials.txt', 'w') as f:
            f.write(f"{api_id}\n{api_hash}\n{phone}\n")

    creds = read_creds()
    if not creds:
        api_id = await ainput('API ID: ')
        api_hash = await ainput('API Hash: ')
        phone = await ainput('Phone number: ')
        write_creds(api_id, api_hash, phone)
    else:
        api_id, api_hash, phone = creds

    forwarder = TelegramForwarder(api_id, api_hash, phone)
    jobs = []
    tasks = []

    while True:
        print("\nOptions:")
        print("1. List Chats")
        print("2. Add Forwarding Job")
        print("3. List Forwarding Jobs")
        print("4. Start Forwarding")
        print("5. Stop Forwarding")
        print("6. Exit")
        choice = await ainput('Choice: ')

        if choice == '1':
            await forwarder.list_chats()
        elif choice == '2':
            src = await ainput('Source chat IDs (comma-separated): ')
            src_ids = [int(x.strip()) for x in src.split(',') if x.strip()]
            dest = int((await ainput('Destination chat/channel ID: ')).strip())
            kws = await ainput('Keywords (comma-separated, leave blank for all): ')
            keywords = [kw.strip() for kw in kws.split(',') if kw.strip()]
            jobs.append((src_ids, dest, keywords))
            print(f"Added job #{len(jobs)}")
        elif choice == '3':
            if not jobs:
                print('No jobs.')
            else:
                for i, (s, d, k) in enumerate(jobs, 1):
                    print(f"{i}. {s} → {d}, keywords={k}")
        elif choice == '4':
            if tasks:
                print('Already running.')
            elif not jobs:
                print('No jobs defined.')
            else:
                for job in jobs:
                    tasks.append(asyncio.create_task(forwarder._forward_loop(job)))
                print('Forwarding started in background.')
        elif choice == '5':
            if not tasks:
                print('Nothing to stop.')
            else:
                for t in tasks:
                    t.cancel()
                tasks.clear()
                print('All forwarding tasks stopped.')
        elif choice == '6':
            print('Exiting.')
            break
        else:
            print('Invalid choice.')

if __name__ == '__main__':
    asyncio.run(main())
