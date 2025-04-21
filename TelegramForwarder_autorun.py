import asyncio, time
from telethon import TelegramClient, errors
from TelegramForwarder import read_jobs  # reuse helper from your main script

CRED_FILE = "credentials.txt"

def read_creds():
    try:
        with open(CRED_FILE, "r") as f:
            return [line.strip() for line in f]
    except FileNotFoundError:
        raise SystemExit(f"{CRED_FILE} not found. Run interactive script once to create it.")

class AutoForwarder:
    def __init__(self, api_id, api_hash, phone):
        self.client = TelegramClient(f"session_{phone}", api_id, api_hash)
        self.phone = phone

    async def _auth(self):
        await self.client.connect()
        if not await self.client.is_user_authorized():
            raise SystemExit("Account not authorized. Run interactive script once to log in.")

    async def run(self, jobs):
        await self._auth()
        tasks = [asyncio.create_task(self._forward_loop(job)) for job in jobs]
        print(f"Started {len(tasks)} forwarding job(s).")
        await asyncio.gather(*tasks)

    async def _forward_loop(self, job):
        src_ids, dest_id, keywords = job
        last = {cid: (await self.client.get_messages(cid, limit=1))[0].id for cid in src_ids}
        while True:
            for cid in src_ids:
                msgs = await self.client.get_messages(cid, min_id=last[cid])
                for m in reversed(msgs):
                    if not keywords or any(k.lower() in (m.text or '').lower() for k in keywords):
                        await self.client.forward_messages(dest_id, m.id, from_peer=cid)
                    last[cid] = max(last[cid], m.id)
            await asyncio.sleep(5)

async def main():
    api_id, api_hash, phone = read_creds()
    jobs = read_jobs()
    if not jobs:
        raise SystemExit("No jobs in forwarding_config.txt")
    await AutoForwarder(api_id, api_hash, phone).run(jobs)

if __name__ == "__main__":
    asyncio.run(main())
