"""
RabbitMQ Consumer.

can be used to consume messages from RabbitMQ.
"""
from typing import Union, Optional
from collections.abc import Callable, Awaitable
import asyncio
from navconfig.logging import logging
import aiormq
from .rabbit import RabbitMQConnection


# Disable Debug Logging for AIORMQ
logging.getLogger('aiormq').setLevel(logging.INFO)


class BrokerConsumer(RabbitMQConnection):
    """
    Broker Client (Consumer) using RabbitMQ.
    """
    def __init__(
        self,
        dsn: Optional[str] = None,
        timeout: Optional[int] = 5,
        **kwargs
    ):
        super(BrokerConsumer, self).__init__(dsn, timeout, **kwargs)

    async def subcriber_callback(
        self,
        message: aiormq.abc.DeliveredMessage,
        callback: Union[Callable, Awaitable]
    ) -> None:
        """
        Default Callback for Event Subscription.
        """
        body = message.body.decode('utf-8')
        if asyncio.iscoroutinefunction(callback):
            await callback(message, body)
        else:
            callback(message, body)

    def wrap_callback(self, callback: Callable) -> Callable:
        """
        Wrap the user-provided callback to handle message decoding and
        acknowledgment.
        """
        async def wrapped_callback(message: aiormq.abc.DeliveredMessage):
            body = message.body.decode('utf-8')
            await callback(message, body)
            # Acknowledge the message to indicate it has been processed
            await self.channel.basic_ack(message.delivery.delivery_tag)
        return wrapped_callback

    async def event_subscribe(
        self,
        queue: str,
        callback: Union[Callable, Awaitable]
    ) -> None:
        """Event Subscribe.
        """
        await self.channel.queue_declare(queue, durable=True)
        await self.channel.basic_consume(queue, consumer_callback=callback)
