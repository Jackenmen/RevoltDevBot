import asyncio
import logging
import os
import sys
import warnings
from pprint import pprint
from types import TracebackType

from dotenv import load_dotenv
from mutiny import Client, events
from ulid import monotonic as ulid

from .dev import Dev

load_dotenv()
load_dotenv(".env.user")

warnings.filterwarnings("default", category=DeprecationWarning)

PREFIX = os.environ["REVOLTBOT_PREFIX"]
if len(PREFIX.split()) != 1:
    raise RuntimeError("can't have prefix with spaces")

if bool(int(os.getenv("REVOLTBOT_IS_USER", 0))):
    client = Client(session_token=os.environ["REVOLTBOT_TOKEN"])
else:
    client = Client(token=os.environ["REVOLTBOT_TOKEN"])


def excepthook(
    type_: type[BaseException], value: BaseException, traceback: TracebackType
) -> None:
    log.critical("Unhandled exception occurred", exc_info=(type_, value, traceback))


def setup_logging():
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)
    file_handler = logging.FileHandler("latest.log")
    formatter = logging.Formatter(
        "[%(asctime)s] [%(levelname)s] %(name)s: %(message)s", datefmt="%X"
    )
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)
    stream_handler = logging.StreamHandler()
    root_logger.addHandler(stream_handler)
    sys.excepthook = excepthook


setup_logging()
log = logging.getLogger("revoltbot")


class MyDev(Dev):
    async def send(self, channel_id: str, content: str) -> None:
        await send(self.client, channel_id, content)


dev = MyDev(client)


async def send(client: Client, channel_id: str, content: str) -> None:
    rest = client._rest
    nonce = ulid.new().str
    smiley = "\N{SMILING FACE WITH OPEN MOUTH}"
    await rest.request(
        "POST",
        f"{rest.api_url}/channels/{channel_id}/messages",
        json={"content": f"{smiley}\n{content}\n{smiley}", "nonce": nonce},
        headers=rest.headers,
    )


@client.listen()
async def on_event(event: events.Event):
    if type(event) in (
        events.ChannelStartTypingEvent,
        events.ChannelStopTypingEvent,
        events.UserUpdateEvent,
    ):
        return
    pprint(event.raw_data)


@client.listen()
async def on_ready(event: events.ReadyEvent):
    log.info("--- I am ready! ---")


@client.listen()
async def on_message(event: events.MessageEvent):
    msg = event.message
    if msg.author_id != client._state.user.bot.owner_id:
        return

    if msg.content is None:
        return
    parts = msg.content.split(maxsplit=1)
    if not parts:
        return
    command = parts[0]
    body = parts[1] if len(parts) == 2 else ""
    if command == f"{PREFIX}ping":
        await send(client, msg.channel_id, "Pong!")
        return
    if command == f"{PREFIX}shutdown":
        await send(client, msg.channel_id, "Shutting down...")
        await client.close()
        return

    if not body:
        return

    if command == f"{PREFIX}eval":
        await dev.eval(msg.channel_id, body, event)
    elif command == f"{PREFIX}debug":
        await dev.debug(msg.channel_id, body, event)


def _cancel_all_tasks(loop: asyncio.AbstractEventLoop) -> None:
    to_cancel = asyncio.all_tasks(loop)
    if not to_cancel:
        return

    for task in to_cancel:
        task.cancel()

    loop.run_until_complete(asyncio.gather(*to_cancel, return_exceptions=True))

    for task in to_cancel:
        if task.cancelled():
            continue
        if (exception := task.exception()) is not None:
            loop.call_exception_handler(
                {
                    "message": "unhandled exception during shutdown",
                    "exception": exception,
                    "task": task,
                }
            )


def main() -> None:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(client.start())
    except KeyboardInterrupt:
        print("Ctrl+C received, exiting...")
    finally:
        try:
            loop.run_until_complete(client.close())
            _cancel_all_tasks(loop)
            loop.run_until_complete(asyncio.sleep(5))
            loop.run_until_complete(loop.shutdown_asyncgens())
            loop.run_until_complete(loop.shutdown_default_executor())
        finally:
            asyncio.set_event_loop(None)
            loop.close()


if __name__ == "__main__":
    main()
