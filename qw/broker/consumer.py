"""
RabbitMQ Consumer.

can be used to consume messages from RabbitMQ.
"""
from typing import Union, Optional
from collections.abc import Callable, Awaitable
import asyncio
from aiohttp import web
import aiormq
from navconfig.logging import logging
from navigator.applications.base import BaseApplication
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
        self._routing_key = kwargs.get('routing_key', '*')
        self._exchange_type = kwargs.get('exchange_type', 'topic')
        self._exchange_name = kwargs.get('exchange_name', 'navigator')
        self._queue_name = kwargs.get('queue_name', 'navigator')
        super(BrokerConsumer, self).__init__(dsn, timeout, **kwargs)
        self.logger = logging.getLogger('BrokerConsumer')

    async def subscriber_callback(
        self,
        message: aiormq.abc.DeliveredMessage,
        callback: Union[Callable, Awaitable]
    ) -> None:
        """
        Default Callback for Event Subscription.
        """
        body = message.body.decode('utf-8')
        try:
            if asyncio.iscoroutinefunction(callback):
                await callback(message, body)
            else:
                callback(message, body)
            # Acknowledge the message to indicate it has been processed
            await self._channel.basic_ack(message.delivery_tag)
        except Exception as e:
            self.logger.error(f"Error processing message: {e}")
            # Optionally, reject the message and requeue it
            await self._channel.basic_nack(message.delivery_tag, requeue=True)

    async def event_subscribe(
        self,
        queue: str,
        callback: Union[Callable, Awaitable]
    ) -> None:
        """Event Subscribe.
        """
        await self.consume_messages(
            queue=queue,
            callback=self.wrap_callback(callback)
        )

    async def subscribe_to_events(
        self,
        exchange: str,
        queue_name: str,
        routing_key: str,
        callback: Union[Callable, Awaitable],
        exchange_type: str = 'topic',
        durable: bool = True,
        prefetch_count: int = 1,
        requeue_on_fail: bool = True,
        **kwargs
    ) -> None:
        """
        Subscribe to events from a specific exchange with a given routing key.
        """
        # Declare the queue
        await self.ensure_connection()
        try:
            await self.ensure_exchange(exchange_name=exchange, exchange_type=exchange_type)
            await self._channel.queue_declare(queue=queue_name, durable=durable)

            # Bind the queue to the exchange
            await self._channel.queue_bind(
                queue=queue_name,
                exchange=exchange,
                routing_key=routing_key
            )

            # Set QoS (Quality of Service) settings
            await self._channel.basic_qos(prefetch_count=prefetch_count)

            # Start consuming messages from the queue
            await self._channel.basic_consume(
                queue=queue_name,
                consumer_callback=self.wrap_callback(callback, requeue_on_fail=requeue_on_fail),
                **kwargs
            )
            self.logger.info(
                f"Subscribed to queue '{queue_name}' on exchange '{exchange}' with routing '{routing_key}'."
            )
        except Exception as e:
            self.logger.error(f"Error subscribing to events: {e}")
            raise

    async def start(self, app: web.Application) -> None:
        """Signal Function to be called when the application is started.

        Connect to RabbitMQ, and start consuming.
        """
        await self.connect()
        await self.subscribe_to_events(
            exchange=self._exchange_name,
            queue_name=self._queue_name,
            routing_key=self._routing_key,
            callback=self.subscriber_callback,
            exchange_type=self._exchange_type,
            durable=True,
            prefetch_count=1,
            requeue_on_fail=True,
        )

    async def stop(self, app: web.Application) -> None:
        # close the RabbitMQ connection
        await self.disconnect()

    def setup(self, app: web.Application = None) -> None:
        """
        Setup BrokerManager.
        """
        if isinstance(app, BaseApplication):
            self.app = app.get_app()
        else:
            self.app = app
        if self.app is None:
            raise ValueError(
                'App is not defined.'
            )
        # Initialize the Producer instance.
        self.app.on_startup.append(self.start)
        self.app.on_shutdown.append(self.stop)
        self.app['broker_consumer'] = self
