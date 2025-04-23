import asyncio
import logging
from TelegramForwarder import TelegramForwarder, read_jobs, read_creds

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

async def main():
    # Load credentials and jobs
    creds = read_creds()
    if not creds:
        logger.critical("Credentials not found, exiting.")
        return
    api_id, api_hash, phone = creds

    jobs = read_jobs()
    if not jobs:
        logger.error("No forwarding jobs configured. Exiting.")
        return

    # Initialize and start forwarder
    forwarder = TelegramForwarder(api_id, api_hash, phone)
    try:
        await forwarder.start_listening(jobs)
    except KeyboardInterrupt:
        logger.info("Interrupted by user, shutting down.")
        await forwarder.stop_listening()
    except Exception as e:
        logger.error(f"Unexpected error: {e}", exc_info=True)

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except Exception as e:
        logger.critical(f"Fatal error: {e}", exc_info=True)
