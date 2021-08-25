# Red - A fully customizable Discord bot
# Copyright (C) 2017-2021  Cog Creators
# Copyright (C) 2015-2017  Twentysix
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import ast
import asyncio
import inspect
import io
import textwrap
import traceback
import re
from abc import ABC, abstractmethod
from collections.abc import Generator
from contextlib import redirect_stdout
from pprint import pprint
from typing import Any, no_type_check

import aiohttp
import mutiny

START_CODE_BLOCK_RE = re.compile(r"^((```py(thon)?)(?=\s)|(```))")


@no_type_check
def pagify(
    text: str,
    delims=["\n"],
    *,
    priority: bool = False,
    shorten_by: int = 12,
    page_length: int = 2000,
) -> Generator[str, None, None]:
    in_text = text
    page_length -= shorten_by
    while len(in_text) > page_length:
        this_page_len = page_length
        closest_delim = (in_text.rfind(d, 1, this_page_len) for d in delims)
        if priority:
            closest_delim = next((x for x in closest_delim if x > 0), -1)
        else:
            closest_delim = max(closest_delim)
        closest_delim = closest_delim if closest_delim != -1 else this_page_len
        to_send = in_text[:closest_delim]
        if len(to_send.strip()) > 0:
            yield to_send
        in_text = in_text[closest_delim:]

    if len(in_text.strip()) > 0:
        yield in_text


def better_vars(obj):
    MISSING = object()
    try:
        return vars(obj)
    except TypeError:
        return {
            attr_name: value
            for base in obj.__class__.__mro__
            for attr_name in getattr(base, "__slots__", ())
            if (value := getattr(obj, attr_name, MISSING)) is not MISSING
        }


def bp(obj: Any) -> None:
    pprint(better_vars(obj))


class Dev(ABC):
    def __init__(self, client: mutiny.Client) -> None:
        self.client = client
        self._last_result = None

    @abstractmethod
    async def send(self, channel_id: str, content: str) -> None:
        ...

    async def send_blocks(self, channel_id: str, content: str) -> None:
        for page in pagify(content):
            await self.send(channel_id, f"```py\n{page}\n```")

    @staticmethod
    def cleanup_code(content):
        """Automatically removes code blocks from the code."""
        # remove ```py\n```
        if content.startswith("```") and content.endswith("```"):
            return START_CODE_BLOCK_RE.sub("", content)[:-3]

        # remove `foo`
        return content.strip("` \n")

    @staticmethod
    def async_compile(source: str, filename: str, mode: str) -> Any:
        return compile(
            source, filename, mode, flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT, optimize=0
        )

    @staticmethod
    async def maybe_await(coro: Any) -> Any:
        for i in range(2):
            if inspect.isawaitable(coro):
                coro = await coro
            else:
                return coro
        return coro

    @staticmethod
    def get_syntax_error(e: SyntaxError) -> str:
        """Format a syntax error to send to the user.
        Returns a string representation of the error formatted as a codeblock.
        """
        if e.text is None:
            return "{0.__class__.__name__}: {0}".format(e)
        return "{0.text}\n{1:>{0.offset}}\n{2}: {0}".format(e, "^", type(e).__name__)

    def sanitize_output(self, input_: str) -> str:
        """Hides the bot's token from a string."""
        auth_data = self.client._authentication_data
        token = auth_data.session_token if auth_data.token is None else auth_data.token
        assert isinstance(token, str)
        return re.sub(re.escape(token), "[EXPUNGED]", input_, re.I)

    def get_environment(self, event: mutiny.events.MessageEvent) -> dict[str, Any]:
        return {
            "client": self.client,
            "asyncio": asyncio,
            "aiohttp": aiohttp,
            "mutiny": mutiny,
            "better_vars": better_vars,
            "bp": bp,
            "pprint": pprint,
            "_": self._last_result,
            "__name__": "__main__",
            "event": event,
            "message": event.message,
        }

    async def eval(
        self, channel_id: str, body: str, event: mutiny.events.MessageEvent
    ) -> None:
        env = self.get_environment(event)
        body = self.cleanup_code(body)
        stdout = io.StringIO()

        to_compile = "async def func():\n%s" % textwrap.indent(body, "  ")

        try:
            compiled = self.async_compile(to_compile, "<string>", "exec")
            exec(compiled, env)
        except SyntaxError as e:
            await self.send_blocks(channel_id, self.get_syntax_error(e))
            return

        func = env["func"]
        result = None
        try:
            with redirect_stdout(stdout):
                result = await func()
        except Exception:
            printed = "{}{}".format(stdout.getvalue(), traceback.format_exc())
        else:
            printed = stdout.getvalue()

        if result is not None:
            self._last_result = result
            msg = "{}{}".format(printed, result)
        else:
            msg = printed
        msg = self.sanitize_output(msg)

        await self.send_blocks(channel_id, msg)

    async def debug(
        self, channel_id: str, body: str, event: mutiny.events.MessageEvent
    ) -> None:
        env = self.get_environment(event)
        code = self.cleanup_code(body)

        try:
            compiled = self.async_compile(code, "<string>", "eval")
            result = await self.maybe_await(eval(compiled, env))
        except SyntaxError as e:
            await self.send_blocks(channel_id, self.get_syntax_error(e))
            return
        except Exception:
            await self.send_blocks(channel_id, traceback.format_exc())
            return

        self._last_result = result
        msg = self.sanitize_output(str(result))

        await self.send_blocks(channel_id, msg)
