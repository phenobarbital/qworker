import asyncio
from aiogram import types
from qw.bots.telegram import TelegramBot


# Example subclass
class JiraBot(TelegramBot):
    async def handler_create(
        self,
        message: types.Message
    ) -> None:
        await self.get_message(message, text="Creating a ticket...")

    async def handler_start(
        self,
        message: types.Message
    ) -> None:
        await self.get_message(
            message,
            text=f"Hello, {message.from_user.full_name}!"
        )

    async def handler_transition(
        self,
        message: types.Message
    ) -> None:
        await self.get_message(message, text="Transition command executed!")

    async def handler_progress(
        self,
        message: types.Message
    ) -> None:
        userinfo = self.user_info(message)
        await self.get_message(message, text=f"Progress command executed for {userinfo}")


# Running the bot
async def main():
    bot = JiraBot()
    bot.setup()  # Register the command handlers
    await bot.start_polling()


if __name__ == "__main__":
    asyncio.run(main())
