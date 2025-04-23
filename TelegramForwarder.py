import time
import asyncio
import logging
from functools import partial
from telethon import TelegramClient, errors, events
from telethon.tl.custom import Message

# Set up logging
logging.basicConfig(
    level=logging.INFO, # Use logging.DEBUG for extremely verbose output during troubleshooting
    format='%(asctime)s.%(msecs)03d - %(name)s - %(levelname)s - [%(funcName)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# Async input helper
def ainput(prompt: str = '') -> asyncio.Future:
    """Run blocking input() in the default executor."""
    loop = asyncio.get_event_loop()
    return loop.run_in_executor(None, input, prompt)

class TelegramForwarder:
    def __init__(self, api_id, api_hash, phone_number):
        # Create a session name based on phone number
        self.session_name = 'forwarder_session_' + phone_number.replace('+', '')
        self.client = TelegramClient(self.session_name, api_id, api_hash)
        self.phone_number = phone_number
        self.history_file = "forward_history.txt"
        self._running = False

        # Cache to store message IDs that have been recently handled as part of an album.
        # This is the primary mechanism to prevent delayed single message duplicates.
        self.recently_processed_message_ids = set()
        # Duration (in seconds) to keep message IDs in the cache
        # Increased to 60 seconds to be safe against delays
        self.msg_cache_clear_delay = 60.0

    async def _ensure_authorized(self):
        """Connects and authorizes the client if needed."""
        if not self.client.is_connected():
             logger.info(f"Connecting client session {self.session_name}...")
             try:
                 await self.client.connect()
                 logger.info("Client connected.")
             except ConnectionError as e:
                 logger.critical(f"Connection failed: {e}")
                 raise # Critical error, can't proceed
             except Exception as e:
                 logger.critical(f"Unexpected error during connect: {e}", exc_info=True)
                 raise # Critical error, can't proceed

        if not await self.client.is_user_authorized():
            logger.info("User not authorized. Requesting code...")
            try:
                await self.client.send_code_request(self.phone_number)
                code = await ainput('Enter the code sent to Telegram: ')
                await self.client.sign_in(self.phone_number, code)
                logger.info("Sign in successful.")
            except errors.SessionPasswordNeededError:
                pw = await ainput('Two-step verification enabled. Enter password: ')
                await self.client.sign_in(password=pw)
                logger.info("Sign in successful with password.")
            except errors.FloodWaitError as e:
                 logger.critical(f"Flood wait during authorization: {e.seconds} seconds. Wait and retry.")
                 raise # Critical error, user must wait
            except errors.PhoneCodeInvalidError:
                 logger.critical("Invalid authorization code provided.")
                 raise # Critical error, cannot auth
            except errors.PhoneNumberInvalidError:
                 logger.critical("Invalid phone number format provided.")
                 raise # Critical error, cannot auth
            except Exception as e:
                logger.critical(f"Unexpected error during authorization: {e}", exc_info=True)
                raise # Critical error, cannot auth

    async def list_chats(self):
        """Lists chats and saves them to a file."""
        await self._ensure_authorized()
        logger.info("Fetching chats...")
        try:
            # Fetch all dialogs to ensure comprehensive list
            dialogs = await self.client.get_dialogs(limit=None)
            fname = f"chats_of_{self.phone_number.replace('+', '')}.txt"
            count = 0
            logger.info(f"Processing {len(dialogs)} dialogs...")
            with open(fname, "w", encoding="utf-8") as f:
                for i, dlg in enumerate(dialogs):
                    entity = dlg.entity
                    if not entity:
                         logger.debug(f"Skipping dialog {dlg.id}: No entity.")
                         continue # Skip if no entity

                    # Determine chat title/name
                    if hasattr(entity, 'title') and entity.title:
                        title = entity.title
                    elif hasattr(entity, 'first_name'):
                         title = f"{getattr(entity, 'first_name', '')} {getattr(entity, 'last_name', '')}".strip()
                         if not title: title = f"User ID {dlg.id}" # Fallback for users
                    elif dlg.is_self:
                         title = "Saved Messages" # Special case for self chat
                    else: # Generic fallback for other types
                        title = f"Unknown Type ({type(entity).__name__}) ID: {dlg.id}"

                    # Ensure ID is an integer before writing
                    if isinstance(dlg.id, int):
                        line = f"Chat ID: {dlg.id}, Title: {title}" # Formatted output line
                        # Print first 100 to console for quick feedback, log rest to file only
                        if i < 100:
                            print(line)
                        elif i == 100:
                            print(f"... (further {len(dialogs) - 100} chats logged to file)")
                        f.write(f"{dlg.id}\t{title}\n") # Write all valid chats to file
                        count += 1
                    else:
                        logger.warning(f"Skipping chat with non-integer ID: {dlg.id}, Title: {title}, Type: {type(entity).__name__}")

            logger.info(f"{count} Valid chats saved to {fname}")
        except errors.RPCError as e:
             logger.error(f"Telegram RPC error while listing chats: {e}", exc_info=True)
        except Exception as e:
             logger.error(f"Unexpected error listing chats: {e}", exc_info=True)

    def _record_forwarding(self, src_chat_id, dest_chat_id, message_ids, keywords, album=False):
        """Logs a successful forward operation to a file."""
        timestamp = time.strftime('%Y-%m-%d %H:%M:%S')
        msg_ids_str = ','.join(map(str, message_ids)) if message_ids else 'N/A'
        keywords_str = ','.join(keywords) if keywords else 'ANY'
        album_str = "[ALBUM]" if album else "[MESSAGE]"
        log_line = f"{timestamp}\tSRC:{src_chat_id}\tDEST:{dest_chat_id}\tIDS:{msg_ids_str}\tKWS:{keywords_str}\t{album_str}\n"
        # Log to console and file
        logger.info(f"RECORDED Forward {album_str}: SRC:{src_chat_id} -> DEST:{dest_chat_id} (Orig IDs: {msg_ids_str}) Keywords:[{keywords_str}]")
        try:
            with open(self.history_file, "a", encoding="utf-8") as f:
                f.write(log_line)
        except Exception as e:
            logger.error(f"Failed to write to history file '{self.history_file}': {e}")

    # Helper to remove specific message ID from cache
    def _remove_from_message_cache(self, message_id):
        """Callback function called by call_later to remove a message ID from the cache."""
        if message_id in self.recently_processed_message_ids:
            logger.debug(f"Removing message ID {message_id} from processed cache (timeout). Cache size before: {len(self.recently_processed_message_ids)}.")
            self.recently_processed_message_ids.discard(message_id)
            logger.debug(f"Msg ID {message_id} removed. Cache size after: {len(self.recently_processed_message_ids)}.")
        # else: logger.debug(f"Msg ID {message_id} removal called, but it was already gone from cache.") # Can be noisy

    # --- Event Handlers ---

    async def _handle_album(self, event: events.Album.Event, dest_chat_id: int, keywords: list[str]):
        """Handles Album events, caching individual message IDs before forwarding."""
        timestamp_monotonic = time.monotonic() # Get precise start time
        source_chat_id = event.chat_id
        album_group_id = event.grouped_id

        # --- Maximum Diagnostics ---
        logger.info(f"--- ALBUM HANDLER (START @ {timestamp_monotonic:.3f}) ---")
        logger.info(f"Group: {album_group_id}, Source: {source_chat_id}, Dest: {dest_chat_id}, KWs: {keywords}")
        logger.info(f"Album event contains {len(event.messages)} potential messages.")
        # logger.debug(f"Raw Album Event Update:\n{event.original_update.stringify()}") # Uncomment for extreme raw data logging

        if not event.messages:
            logger.warning("Album event received with an empty messages list!");
            logger.info(f"--- ALBUM HANDLER (END @ {time.monotonic():.3f}, Empty Album) ---")
            return

        # Filter for valid Message objects and collect their IDs
        valid_messages = [msg for msg in event.messages if isinstance(msg, Message)]
        message_ids_to_forward = [msg.id for msg in valid_messages if hasattr(msg, 'id')] # Ensure it has an ID

        if not message_ids_to_forward:
            logger.error("No valid message IDs found in album event messages!");
            logger.info(f"--- ALBUM HANDLER (END @ {time.monotonic():.3f}, No Valid Msgs) ---")
            return

        logger.info(f"Album group {album_group_id} contains {len(message_ids_to_forward)} processable messages (IDs: {message_ids_to_forward}).")

        # Keyword Check (using event.text for the album caption)
        effective_caption = event.text or ""
        keywords_matched = not keywords or any(kw.lower() in effective_caption.lower() for kw in keywords)
        logger.info(f"Caption: '{effective_caption[:50]}...'. Keyword match result: {keywords_matched}")
        if not keywords_matched:
            logger.info(f"Skipping album {album_group_id} due to keyword mismatch.")
            logger.info(f"--- ALBUM HANDLER (END @ {time.monotonic():.3f}, Keyword Mismatch) ---")
            return

        # --- Add individual message IDs to cache *BEFORE* attempting the forward ---
        # This ensures that if the single message Update arrives *while* we are forwarding
        # the album, the Message handler will see the ID in the cache and ignore it.
        logger.info(f"Adding {len(message_ids_to_forward)} msg IDs to processed cache for group {album_group_id} ({self.msg_cache_clear_delay}s duration). Current cache size: {len(self.recently_processed_message_ids)}.")
        ids_added_now = [] # Track which IDs we add in *this* handler run
        try:
            loop = asyncio.get_running_loop() # Get the currently running event loop
            for msg_id in message_ids_to_forward:
                if msg_id not in self.recently_processed_message_ids:
                    self.recently_processed_message_ids.add(msg_id)
                    ids_added_now.append(msg_id)
                    # Schedule the removal of this specific message ID from the cache after the delay
                    loop.call_later(self.msg_cache_clear_delay, self._remove_from_message_cache, msg_id)
                # else: logger.debug(f"Msg ID {msg_id} (Group: {album_group_id}) was ALREADY in cache.") # Can be noisy
            if ids_added_now:
                logger.debug(f"Successfully added {len(ids_added_now)} IDs to cache from group {album_group_id}: {ids_added_now}")
            else:
                 logger.debug(f"No *new* IDs added to cache from group {album_group_id}. All were already present.")

        except RuntimeError as e:
             # This might happen if called outside an active loop, though less likely with event handlers
             logger.critical(f"Failed to get event loop to schedule message cache cleanup: {e}", exc_info=True)
             # Continue, but cache won't be cleared automatically for these IDs!

        # Attempt Forward
        forward_failed = False
        try:
            logger.info(f"Attempting to forward album group {album_group_id} ({len(valid_messages)} messages)...")
            # Use the list of *validated* Message objects
            forwarded_messages = await self.client.forward_messages(entity=dest_chat_id, messages=valid_messages, from_peer=source_chat_id)

            if forwarded_messages:
                # Check if the returned list makes sense (optional but good practice)
                returned_ids = [m.id for m in forwarded_messages if isinstance(m, Message) and hasattr(m, 'id')]
                logger.info(f"Album Forward SUCCESS (Group: {album_group_id}). Created {len(returned_ids)} new message(s) with IDs: {returned_ids}")
                if len(returned_ids) != len(message_ids_to_forward):
                    logger.warning(f"Mismatch in message count returned by forward_messages! Expected {len(message_ids_to_forward)}, got {len(returned_ids)}.")
                # Record based on original message IDs
                self._record_forwarding(source_chat_id, dest_chat_id, message_ids_to_forward, keywords, album=True)
            else:
                 # This might indicate a silent API failure
                 logger.warning(f"Album forward (Group: {album_group_id}) returned None or empty list. Assume forward failed.")
                 forward_failed = True

        # --- Basic FloodWait handling for forwarding ---
        except errors.FloodWaitError as e:
             logger.warning(f"FLOOD WAIT {e.seconds}s when forwarding album {album_group_id}. Retrying after delay.")
             await asyncio.sleep(e.seconds + 1) # Wait a little more than requested
             try:
                 logger.info(f"Retrying forward for album group {album_group_id} after FloodWait...")
                 # Retry the forward
                 retry_forwarded_messages = await self.client.forward_messages(entity=dest_chat_id, messages=valid_messages, from_peer=source_chat_id)
                 if retry_forwarded_messages:
                     logger.info(f"Retry SUCCESS for album group {album_group_id}.")
                     retry_returned_ids = [m.id for m in retry_forwarded_messages if isinstance(m, Message) and hasattr(m, 'id')]
                     self._record_forwarding(source_chat_id, dest_chat_id, message_ids_to_forward, keywords, album=True)
                 else:
                      logger.warning(f"Retry for album {album_group_id} also returned no result.")
                      forward_failed = True # Still mark as failed after retry
             except Exception as e_retry:
                 logger.error(f"Error forwarding album {album_group_id} AFTER RETRY: {e_retry}", exc_info=True)
                 forward_failed = True # Mark as failed

        # --- Catch other potential errors during forward ---
        except Exception as e:
             logger.error(f"EXCEPTION forwarding album group {album_group_id}: {e}", exc_info=True)
             forward_failed = True # Mark as failed

        # --- If forward failed, remove the message IDs that were just added to the cache ---
        # This prevents delayed single messages from being incorrectly blocked forever if the album forward itself failed.
        if forward_failed and ids_added_now:
            logger.warning(f"Forward failed for group {album_group_id}. Removing {len(ids_added_now)} related msg IDs from cache immediately.")
            # Note: We can't easily cancel the scheduled call_later tasks.
            # Discarding from the set means when call_later *does* run, discard() will just do nothing.
            for msg_id in ids_added_now:
                 self.recently_processed_message_ids.discard(msg_id)
            logger.debug(f"Cache size after removing failed IDs: {len(self.recently_processed_message_ids)}")


        timestamp_end = time.monotonic() # Get precise end time
        logger.info(f"--- ALBUM HANDLER (END @ {timestamp_end:.3f}, Duration: {timestamp_end - timestamp_monotonic:.3f}s) ---")


    async def _handle_message(self, event: events.NewMessage.Event, dest_chat_id: int, keywords: list[str]):
        """Handles NewMessage events, checking *only* the processed message ID cache first."""
        timestamp_monotonic = time.monotonic() # Get precise start time
        message_id = event.id
        source_chat_id = event.chat_id
        current_group_id = event.grouped_id # Still capture for logging/diagnostics

        # --- Maximum Diagnostics ---
        logger.debug(f"--- MSG HANDLER (START @ {timestamp_monotonic:.3f}) ---")
        logger.debug(f"Msg ID: {message_id}, Group: {current_group_id}, Source: {source_chat_id}, Dest: {dest_chat_id}, KWs: {keywords}")
        logger.debug(f"Text: '{(event.text or '')[:50]}...'")
        # logger.debug(f"Raw Message Event Update:\n{event.original_update.stringify()}") # Uncomment for extreme raw data logging

        # *** PRIMARY CHECK: Is this specific message ID in the recent cache? ***
        # This cache is populated by _handle_album for messages it has processed.
        # This is the most direct way to catch the delayed single messages.
        if message_id in self.recently_processed_message_ids:
            logger.info(f"*** Msg ID {message_id} (Group: {current_group_id}) IS IN processed msg ID cache. IGNORING to prevent duplicate. ***")
            logger.debug(f"Cache size: {len(self.recently_processed_message_ids)}")
            logger.debug(f"--- MSG HANDLER (END - SKIPPED CACHE @ {time.monotonic():.3f}) ---")
            return # Stop processing this message

        # *** Sanity Log (If it has grouped_id but missed the message ID cache) ***
        # This indicates something unusual happened - maybe the Album event wasn't processed,
        # or the message arrived before the Album handler finished adding IDs to cache.
        # We log a warning but proceed to treat it as a single message based on keywords.
        if current_group_id:
             logger.warning(f"Msg ID {message_id} has Group ID {current_group_id} but WAS NOT in message ID cache! Processing as potential single message based on keywords.")
             # DO NOT return here. Let the keyword check below decide if it should be forwarded.

        # --- Process as a single, non-album message ---
        logger.debug(f"Msg ID {message_id} not in cache. Checking keywords for single message forwarding.")
        text = event.text or ""

        # Keyword check: Only forward if keywords match OR if no keywords are specified
        keywords_matched = False
        if not keywords:
            keywords_matched = True # No keywords means forward all non-album messages (including media-only)
            logger.debug(f"No keywords specified for job. Message {message_id} will be forwarded.")
        elif text and any(kw.lower() in text.lower() for kw in keywords):
            keywords_matched = True # Message has text and matches keywords
            logger.debug(f"Message {message_id} text matches keywords: {keywords}.")
        else:
            # Keywords specified, but message has no text or text doesn't match
            logger.debug(f"Message {message_id} text did not match keywords {keywords} or had no text. Skipping.")
            logger.debug(f"--- MSG HANDLER (END - SKIPPED KEYWORD/TEXT @ {time.monotonic():.3f}) ---")
            return # Skip if keywords required but not met

        # Forward logic - Only reached if cache missed AND keywords matched (or no keywords)
        logger.info(f"Attempting forward single message {message_id} from {source_chat_id} to {dest_chat_id}...")
        try:
            await event.forward_to(dest_chat_id) # Use the convenient forward_to for single messages
            logger.info(f"Successfully forwarded single message {message_id}.")
            self._record_forwarding(source_chat_id, dest_chat_id, [message_id], keywords, album=False)
        # --- Basic FloodWait handling for forwarding ---
        except errors.FloodWaitError as e:
             logger.warning(f"FLOOD WAIT {e.seconds}s when forwarding message {message_id}. Retrying after delay.")
             await asyncio.sleep(e.seconds + 1)
             try:
                  logger.info(f"Retrying forward message {message_id} after FloodWait...")
                  await event.forward_to(dest_chat_id)
                  logger.info(f"Retry SUCCESS for message {message_id}.")
                  self._record_forwarding(source_chat_id, dest_chat_id, [message_id], keywords, album=False)
             except Exception as e_retry:
                 logger.error(f"Error forwarding message {message_id} AFTER RETRY: {e_retry}", exc_info=True)
        # --- Catch other potential errors during forward ---
        except Exception as e:
            logger.error(f"Error forwarding single message {message_id} from {source_chat_id} to {dest_chat_id}: {e}", exc_info=True)

        timestamp_end = time.monotonic() # Get precise end time
        logger.debug(f"--- MSG HANDLER (END - Processed ID: {message_id} @ {timestamp_end:.3f}, Duration: {timestamp_end - timestamp_monotonic:.3f}s) ---")


    async def start_listening(self, jobs):
        """Registers event handlers for all jobs and runs the client."""
        if self._running:
            logger.warning("Listener start requested, but it seems to be already running.")
            return
        if not jobs:
             logger.error("Cannot start listener: No forwarding jobs loaded/defined.")
             return

        # Ensure the client is connected and authorized
        await self._ensure_authorized()

        logger.info("--- Preparing Event Handlers ---")
        # Remove any existing handlers from previous runs if client wasn't fully stopped/disconnected
        logger.info("Removing any potentially existing message/album event handlers...")
        # --- Handler Count Logging BEFORE Removal ---
        logger.info(f"Handler count BEFORE removal attempt: {len(self.client.list_event_handlers())}")
        # Remove specifically by the *function object*
        self.client.remove_event_handler(self._handle_album)
        self.client.remove_event_handler(self._handle_message)
        # --- Handler Count Logging AFTER Removal ---
        logger.info(f"Handler count AFTER removal: {len(self.client.list_event_handlers())}")

        logger.info(f"Registering new handlers for {len(jobs)} jobs...")
        # Register a pair of handlers for each job defined in the config
        for job_index, (src_ids, dest_id, keywords) in enumerate(jobs):
            logger.info(f"  Job {job_index+1}: SRC={src_ids}, DEST={dest_id}, KWS=[{','.join(keywords) if keywords else 'ANY'}]")

            # Use partial to 'bake in' the job-specific parameters (dest_id, keywords)
            # These partials are the actual callable functions that will be added as handlers.
            album_handler_with_args = partial(self._handle_album, dest_chat_id=dest_id, keywords=keywords)
            message_handler_with_args = partial(self._handle_message, dest_chat_id=dest_id, keywords=keywords)

            # Register the Album handler for the source chat(s)
            self.client.add_event_handler(
                album_handler_with_args,
                events.Album(chats=src_ids) # Filter by source chat(s)
            )
            # Register the NewMessage handler for the source chat(s), specifically for incoming messages
            self.client.add_event_handler(
                message_handler_with_args,
                events.NewMessage(chats=src_ids, incoming=True) # Filter by source chat(s) AND incoming messages
            )
            logger.debug(f"  Registered handlers for job {job_index+1}.")

        # --- Handler Count Logging AFTER Registration ---
        logger.info(f"Total handler count AFTER registration: {len(self.client.list_event_handlers())}")

        self._running = True
        logger.info("Starting listener main loop (client.run_until_disconnected)... Press Ctrl+C to stop.")
        try:
            # This method blocks execution and listens for updates until the client is disconnected
            await self.client.run_until_disconnected()
            # If run_until_disconnected finishes without error, it means the client was disconnected normally
            logger.info("client.run_until_disconnected has finished.")
        except Exception as e:
             # Catch any exception that occurs during the run loop
             logger.error(f"Error during client.run_until_disconnected: {e}", exc_info=True)
             # Attempt to gracefully stop the client if an error occurred
             await self.stop_listening()
        finally:
             # This block executes when the try/except block is exited (either normally or by exception)
             self._running = False # Ensure the running state is false
             logger.info("Listener main loop has exited.")
             # Ensure the client is disconnected in case the loop exited abnormally
             if self.client.is_connected():
                  logger.info("Ensuring client is disconnected after loop exit.")
                  await self.client.disconnect()


    async def stop_listening(self):
        """Disconnects the client, attempting to stop the listener."""
        if not self.client.is_connected():
            logger.info("Stop requested, but client is already disconnected.")
            if self._running:
                self._running = False # Correct the running state if necessary
            return

        if self._running or self.client.is_connected(): # Check both state and connection
            logger.info("Stopping listener and disconnecting client...")
            try:
                # Disconnecting the client will cause run_until_disconnected to finish
                await self.client.disconnect()
                # Give a very small moment for disconnect process
                await asyncio.sleep(0.1)
            except Exception as e:
                logger.error(f"Error during client disconnect: {e}", exc_info=True)
            finally:
                 # Mark as stopped regardless of disconnect success/failure
                 self._running = False
                 logger.info("Client disconnected signal sent. Listener should be stopped.")
        else:
             # Should not be reached if _running check works, but as a failsafe
             logger.info("Stop requested, but listener wasn't marked as running or client not connected.")
             self._running = False # Ensure state is false


# --- Config Reading ---
def read_jobs(config_file: str = 'forwarding_config.txt'):
    """Reads forwarding jobs from the configuration file."""
    jobs = []
    logger.info(f"Reading forwarding jobs from '{config_file}'...")
    try:
        with open(config_file, 'r', encoding='utf-8') as f:
            for line_num, raw in enumerate(f, 1):
                line = raw.split('#', 1)[0].strip() # Remove comments and whitespace
                if not line: continue # Skip empty lines

                parts = [p.strip() for p in line.split(';')]
                if len(parts) < 2:
                    logger.warning(f"CONFIG L{line_num}: Skipped - Invalid format (need at least src; dest): {line}")
                    continue

                # Parse source IDs (can be comma-separated)
                try:
                    src_ids_str = [x.strip() for x in parts[0].split(',') if x.strip()]
                    if not src_ids_str: raise ValueError("No source IDs found")
                    src_ids = [int(x) for x in src_ids_str] # Convert to integers
                except ValueError as e:
                    logger.warning(f"CONFIG L{line_num}: Skipped - Invalid source ID(s) ({e}): {line}")
                    continue

                # Parse destination ID (single ID)
                try:
                    dest_id = int(parts[1])
                except ValueError:
                    logger.warning(f"CONFIG L{line_num}: Skipped - Invalid destination ID: {line}")
                    continue

                # Parse keywords (optional, comma-separated)
                keywords = []
                if len(parts) >= 3 and parts[2]:
                    # Store keywords in lowercase for case-insensitive matching
                    keywords = [kw.strip().lower() for kw in parts[2].split(',') if kw.strip()]

                # Final validation: ensure source(s) and destination were successfully parsed
                if not src_ids or not isinstance(dest_id, int):
                     logger.warning(f"CONFIG L{line_num}: Skipped - Source or destination ID missing/invalid after parse: {line}")
                     continue

                jobs.append((src_ids, dest_id, keywords))
                logger.debug(f"CONFIG L{line_num}: Loaded job - SRC:{src_ids}, DEST:{dest_id}, KWS:{keywords}")

    except FileNotFoundError:
        logger.warning(f"Config file '{config_file}' not found. No jobs loaded.")
    except Exception as e:
        logger.error(f"Error reading config file '{config_file}': {e}", exc_info=True)
    logger.info(f"Finished reading config. Loaded {len(jobs)} jobs.")
    return jobs


# --- Credentials ---
def read_creds():
    """Reads credentials from credentials.txt."""
    logger.debug("Attempting to read credentials from credentials.txt...")
    try:
        with open('credentials.txt', 'r') as f:
            lines = [line.strip() for line in f if line.strip()] # Read non-empty lines
            if len(lines) >= 3:
                 try:
                    api_id = int(lines[0]) # API ID should be integer
                    api_hash = lines[1]
                    phone = lines[2] # Phone number including country code
                    # Basic validation
                    if not (isinstance(api_id, int) and api_hash and phone.startswith('+') and len(phone) > 5):
                         raise ValueError("Invalid format in credentials file.")
                    logger.debug("Credentials read successfully.")
                    return api_id, api_hash, phone
                 except ValueError as e:
                    logger.error(f"Credentials file format error: {e}")
                    return None
    except FileNotFoundError:
        logger.debug("credentials.txt not found.")
        pass # File simply doesn't exist, not an error
    except Exception as e:
        logger.error(f"Error reading credentials: {e}", exc_info=True)
    return None # Return None if file not found, format invalid, or error

def write_creds(api_id, api_hash, phone):
    """Writes credentials to credentials.txt."""
    logger.info("Writing credentials to credentials.txt")
    try:
        with open('credentials.txt', 'w') as f:
            f.write(f"{api_id}\n{api_hash}\n{phone}\n")
    except Exception as e:
        logger.error(f"Error writing credentials: {e}", exc_info=True)


# --- Main Menu Logic ---
async def main():
    """Main execution function displaying the menu and handling user input."""
    # Load credentials or prompt user
    creds = read_creds()
    api_id, api_hash, phone = None, None, None # Initialize variables

    if not creds:
        logger.info("Credentials not found or invalid, please enter them:")
        try:
            # Prompt user for credentials
            api_id_str = await ainput('Enter API ID: ')
            api_id = int(api_id_str) # Ensure API ID is integer
            api_hash = await ainput('Enter API Hash: ')
            phone = await ainput('Enter Phone number (with country code, e.g., +1234567890): ')
            # Basic validation
            if not (api_hash and phone.startswith('+') and len(phone) > 5):
                 raise ValueError("Invalid input format")
            write_creds(api_id, api_hash, phone) # Save valid credentials
            logger.info("Credentials saved.")
        except ValueError:
             # Handle non-integer API ID or invalid phone format
             logger.critical("Invalid credentials format entered. API ID must be numeric, phone must start with +. Exiting.")
             return # Exit if credentials cannot be obtained

    else:
        api_id, api_hash, phone = creds
        logger.info("Credentials loaded from file.")

    # Double check api_id is integer type
    if not isinstance(api_id, int):
        logger.critical("API ID is not an integer type after loading/input. Exiting.");
        return

    # Initialize the Forwarder instance
    forwarder = TelegramForwarder(api_id, api_hash, phone)
    # Load forwarding jobs from config file initially
    jobs = read_jobs()

    # --- Main Menu Loop ---
    while True:
        # Check the listener's current running state
        is_running = forwarder._running # Simple state check for menu display

        print("\n--- Telegram Forwarder Menu ---")
        print("1. List Chats (saves to file)")
        print("2. Show Loaded Forwarding Jobs")
        print(f"3. Start Forwarding Listener {'(RUNNING)' if is_running else ''}")
        print(f"4. Stop Forwarding Listener {'(NOT RUNNING)' if not is_running else ''}")
        print("5. Reload Forwarding Jobs from Config File")
        print("6. Exit")
        choice = await ainput('Enter choice: ')

        if choice == '1':
            logger.info("Menu Action: List Chats"); print("Listing chats...")
            try:
                 # Ensure client is connected and authorized before calling list_chats
                 await forwarder._ensure_authorized()
                 await forwarder.list_chats()
            except Exception as e:
                 logger.error(f"Error occurred while listing chats: {e}", exc_info=True);
                 print("Error listing chats. Check logs.")

        elif choice == '2':
            logger.info("Menu Action: Show Jobs");
            if not jobs:
                print('No jobs loaded. Check forwarding_config.txt or reload (option 5).')
            else:
                print("\n--- Loaded Forwarding Jobs ---")
                for i, (s, d, k) in enumerate(jobs, 1):
                    kw_str = ', '.join(k) if k else 'ANY'
                    print(f"  Job {i}: SRC={s} -> DEST={d}, KWS=[{kw_str}]")
                print("--- End of Jobs ---")

        elif choice == '3': # Start Listener
            logger.info("Menu Action: Start Listener")
            # Log current handler count before attempting to start/register
            logger.info(f"Current handler count before start attempt: {len(forwarder.client.list_event_handlers())}")

            if is_running:
                print("Listener is already running.")
            elif not jobs:
                print('No jobs defined in forwarding_config.txt. Cannot start listener.')
            else:
                print("Attempting to start listener...")
                try:
                    # start_listening includes authorization, handler registration, and then blocks
                    await forwarder.start_listening(jobs)
                    # This line is reached when run_until_disconnected returns (client disconnected)
                    print("Listener has stopped.")
                except KeyboardInterrupt:
                    # Handle Ctrl+C specifically while the listener is running
                    logger.warning("Ctrl+C detected during listener run. Stopping...")
                    print("\nStopping listener due to interrupt...")
                    await forwarder.stop_listening() # Ensure graceful stop
                except Exception as e:
                    # Catch any other unexpected error that stops the listener
                    logger.error(f"An error occurred while running the listener: {e}", exc_info=True)
                    print(f"Listener failed or stopped unexpectedly: {e}. Check logs.")
                    # Attempt to stop the client gracefully if it's still connected
                    if forwarder.client.is_connected():
                         await forwarder.stop_listening()
                    forwarder._running = False # Ensure the state is false

        elif choice == '4': # Stop Listener
            logger.info("Menu Action: Stop Listener")
            if not is_running:
                print("Listener is not running.")
            else:
                print("Attempting to stop listener...")
                await forwarder.stop_listening()
                print("Stop request processed. Listener should disconnect.")

        elif choice == '5': # Reload Jobs
            logger.info("Menu Action: Reload Jobs")
            if is_running:
                print("Cannot reload jobs while the listener is running. Stop it first (option 4).")
            else:
                print("Reloading jobs from forwarding_config.txt...")
                jobs = read_jobs() # Reload the jobs list
                print(f"Reloaded {len(jobs)} jobs.")

        elif choice == '6': # Exit
            logger.info("Menu Action: Exit"); print('Exiting requested...')
            if is_running:
                 print("Stopping listener before exiting...")
                 await forwarder.stop_listening() # Ensure graceful stop
            print("Goodbye!")
            break # Exit the main while loop
        else:
            print('Invalid choice. Please try again.')

# --- Entry Point ---
if __name__ == '__main__':
    print("Starting Telegram Forwarder script...")
    try:
        # Use asyncio.run to manage the event loop
        asyncio.run(main())
    except KeyboardInterrupt:
        # This catches Ctrl+C if pressed during initial setup or final cleanup outside the main loop
        logger.info("\nProgram terminated by user (Ctrl+C outside main loop).")
    except Exception as e:
        # Catch any other critical exception that wasn't handled elsewhere
        logger.critical(f"Unhandled critical exception during script execution: {e}", exc_info=True)
    finally:
        print("Forwarder script finished.")
