from abc import ABC
from collections.abc import Awaitable, Callable
from typing import Any, Optional, Union
import asyncio
import inspect
from functools import partial
from concurrent.futures import (
    ThreadPoolExecutor,
    ProcessPoolExecutor
)
from aiohttp import web
from aiogram import Bot, Dispatcher
from aiogram import types
from aiogram import md
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.default import DefaultBotProperties  # type: ignore
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import Message
from navconfig.logging import logging  # type: ignore
from qw.conf import (
    TELEGRAM_BOT_TOKEN
)


class TelegramBot(ABC):
    """Telegram bot.

    This class is a wrapper for the aiogram.Bot class,
    it provides a simple way to create new Telegram Bots.

    Args:
        bot_token (str): Telegram Bot Token.
    """
    def __init__(
        self,
        bot_token: Optional[str] = None,
        **kwargs
    ) -> None:
        self._token = bot_token if bot_token else TELEGRAM_BOT_TOKEN
        self._dispatcher: Awaitable = None
        self.bot: Callable = None
        self.parse_mode = kwargs.pop(
            'parse_mode',
            ParseMode.HTML
        )
        # Start message:
        self._start_message: str = kwargs.pop(
            'start_message',
            "Ready to assist you!"
        )
        # logger
        self.logger = logging.getLogger(
            name='QW.TelegramBot'
        )
        # Executor:
        self._executor = self.get_executor(
            executor=kwargs.pop('executor', 'thread'),
            max_workers=kwargs.pop('max_workers', 2)
        )
        # Support webhook:
        self.use_webhook: bool = kwargs.pop('use_webhook', False)
        self._webook_url: str = kwargs.pop('webhook_url', None)
        self._webhook_route: str = kwargs.pop(
            'webhook_route',
            '/bot/webhook'
        )
        # other arguments:
        self.kwargs = kwargs

    def user_info(self, message: Message) -> dict:
        try:
            user = message.from_user
            # Extract user information
            user_id = user.id
            first_name = user.first_name
            last_name = user.last_name
            username = user.username
            language_code = user.language_code

            # Print or use the extracted information
            user_info = {
                "user_id": user_id,
                "first_name": first_name,
                "last_name": last_name,
                "username": username,
                "language_code": language_code
            }
            return user_info
        except Exception as e:
            self.logger.error(
                f"Error extracting user information: {e}"
            )
            return None

    def get_message(self, message: types.Message, text: str):
        safe_text = text
        if self.parse_mode == ParseMode.MARKDOWN_V2:
            safe_text = md.quote(safe_text)
            return message.answer(
                safe_text,
                parse_mod=ParseMode.MARKDOWN_V2
            )
        return message.answer(safe_text)

    async def handle_webhook(self, request: web.Request):
        update = await request.json()
        await self._dispatcher.feed_update(self.bot, types.Update(**update))
        return web.Response(text='OK')

    def setup(self, app: Optional[web.Application] = None):
        self._session = AiohttpSession()
        bot_settings = {
            "session": self._session,
            "default": DefaultBotProperties(
                parse_mode=self.parse_mode,
                disable_notification=True,
                allow_sending_without_reply=True
            ),
            "request_timeout": 10,
            **self.kwargs
        }
        self.bot = Bot(  # pylint: disable=E1123
            token=self._token,
            **bot_settings
        )
        self._storage = MemoryStorage()
        self._dispatcher = Dispatcher(storage=self._storage)
        # Analyze any method class to be a command assigned to this bot:
        for name, method in inspect.getmembers(self, predicate=inspect.ismethod):
            if name.startswith('handler_'):
                # Extract command name by removing 'handler_' prefix
                command_name = name.replace('handler_', '')
                # Register the method as a command handler
                self._dispatcher.message.register(
                    method,
                    Command(command_name)
                )
        if app:
            if self.use_webhook is True:
                app['telegram_bot'] = self
                app.router.add_post(
                    self._webhook_route,
                    self.handle_webhook
                )
                # added to on_startup:
                app.on_startup.append(self.on_startup)

    async def on_startup(self, app: web.Application):
        if self.use_webhook is True:
            await self.bot.set_webhook(self._webook_url)
            print(f"Webhook set to: {self._webook_url}")

    async def run_forever(self) -> None:
        # And the run events dispatching
        if not self.bot:
            self.setup()
        try:
            # Check if there is an active webhook
            webhook_info = await self.bot.get_webhook_info()
            if webhook_info.url:
                print(
                    f"Webhook is currently set to: {webhook_info.url}"
                )
                # Delete the webhook if it exists
                await self.bot.delete_webhook()
                print("Webhook deleted to enable polling mode.")
            else:
                print("No webhook set, proceeding with polling.")
        except Exception as e:
            print(f"Error checking webhook status: {e}")
        # Getting Bot info:
        self.bot_info = await self.bot.get_me()
        self.logger.debug(
            f"🤖 Hello, I'm {self.bot_info.first_name}.\n{self._start_message}"
        )
        await self._dispatcher.start_polling(self.bot)

    def get_executor(self, executor: str = None, max_workers: int = 2) -> Any:
        if executor == "thread":
            return ThreadPoolExecutor(
                max_workers=max_workers
            )
        elif executor == "process":
            return ProcessPoolExecutor(
                max_workers=max_workers
            )
        elif self._executor is not None:
            return self._executor
        else:
            return None

    async def run_in_thread(
        self,
        fn: Union[Callable, Awaitable],
        *args,
        executor: Any = None,
        **kwargs
    ):
        """_execute.

        Returns a future to be executed into a Thread Pool.
        """
        loop = asyncio.get_event_loop()
        func = partial(fn, *args, **kwargs)
        if not executor:
            executor = self._executor
        try:
            fut = loop.run_in_executor(executor, func)
            return await fut
        except Exception as e:
            self.logger.exception(e, stack_info=True)
            raise
