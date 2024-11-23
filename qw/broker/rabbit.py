"""
RabbitMQ interface (connection and disconnections).
"""
from typing import Optional, Union
from collections.abc import Callable, Awaitable
import asyncio
import aiormq
from aiormq.abc import AbstractConnection, AbstractChannel
from navconfig.logging import logging
from navigator.libs.json import json_encoder
from ..conf import rabbitmq_dsn


class RabbitMQConnection:
    """
    Manages connection and disconnection of RabbitMQ Service.
    """
    def __init__(
        self,
        dsn: Optional[str] = None,
        timeout: Optional[int] = 5,
        **kwargs
    ):
        self._dsn = rabbitmq_dsn if dsn is None else dsn
        self._connection: Optional[AbstractConnection] = None
        self._channel: Optional[AbstractChannel] = None
        self._timeout: int = timeout
        self._monitor_task: Optional[Awaitable] = None
        self.logger = logging.getLogger('RabbitMQConnection')
        self.reconnect_attempts = 0
        self.max_reconnect_attempts = kwargs.get(
            'max_reconnect_attempts', 3
        )
        self.reconnect_delay = 1  # Initial delay in seconds
        self._lock = asyncio.Lock()

    def get_connection(self) -> Optional[AbstractConnection]:
        if not self._connection:
            raise RuntimeError('No connection established.')
        return self._connection

    def get_channel(self) -> Optional[AbstractChannel]:
        return self._channel

    async def connect(self) -> None:
        """
        Creates a Connection to RabbitMQ Server.
        """
        try:
            self.logger.debug(
                f":: Connecting to RabbitMQ: {self._dsn}"
            )
            async with self._lock:
                self._connection = await asyncio.wait_for(
                    aiormq.connect(
                        self._dsn
                    ),
                    timeout=self._timeout
                )
                self.reconnect_attempts = 0
                self._channel = await self._connection.channel()
                if not self._monitor_task or self._monitor_task.done():
                    await self._start_connection_monitor()
        except asyncio.TimeoutError:
            self.logger.error("Connection timed out")
            await self.schedule_reconnect()
        except Exception as err:
            self.logger.error(
                f"Error while connecting to RabbitMQ: {err}"
            )
            await self.schedule_reconnect()

    async def _start_connection_monitor(self):
        """Start a background task to monitor the RabbitMQ connection."""
        self._monitor_task = asyncio.create_task(
            self.connection_monitor()
        )

    async def connection_monitor(self):
        """Monitor the RabbitMQ connection and
            attempt to reconnect if disconnected.
        """
        while True:
            if not self._connection or self._connection.is_closed:
                self.logger.warning(
                    "Connection lost. Attempting to reconnect..."
                )
                try:
                    await self.connect()
                except Exception as e:
                    self.logger.error(f"Reconnection attempt failed: {e}")
            await asyncio.sleep(60)

    async def schedule_reconnect(self):
        """
        Using exponential Backoff to schedule reconnections (in seconds).
        """
        if self.reconnect_attempts < self.max_reconnect_attempts:
            self.reconnect_attempts += 1
            delay = self.reconnect_delay * (
                2 ** (self.reconnect_attempts - 1)
            )  # Exponential backoff
            self.logger.info(
                f"Scheduling reconnect in {delay} seconds..."
            )
            await asyncio.sleep(delay)
            await self.connect()
        else:
            self.logger.error(
                "RabbitMQ: Max reconnect attempts reached. Giving up."
            )
            raise RuntimeError(
                "Unable to connect to RabbitMQ Server."
            )

    async def disconnect(self) -> None:
        """
        Disconnect from RabbitMQ.
        """
        if self._channel is not None:
            try:
                await self._channel.close()
                self._channel = None
            except Exception as err:
                self.logger.warning(
                    f"Error while closing channel: {err}"
                )
        if self._connection is not None:
            try:
                await self._connection.close()
                self._connection = None
            except Exception as err:
                self.logger.warning(
                    f"Error while closing connection: {err}"
                )
        # finishing the Monitoring Task.
        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass

    async def ensure_connection(self) -> None:
        """
        Ensures that the connection is active.
        """
        if self._connection is None or self._connection.is_closed:
            await self.connect()

    async def create_exchange(
        self,
        exchange_name: str,
        exchange_type: str = 'topic',
        durable: bool = True,
        **kwargs
    ):
        """
        Declare an exchange on RabbitMQ.

        Methods to create and ensure the existence of exchanges.
        """
        if not self._channel:
            self.logger.error(
                "RabbitMQ channel is not established."
            )
            return

        try:
            await self._channel.exchange_declare(
                exchange=exchange_name,
                exchange_type=exchange_type,
                durable=durable,
                arguments=kwargs
            )
            self.logger.info(
                f"Exchange '{exchange_name}' declared successfully."
            )
        except Exception as e:
            self.logger.error(
                f"Failed to declare exchange '{exchange_name}': {e}"
            )

    async def ensure_exchange(
        self,
        exchange_name: str,
        exchange_type: str = 'topic',
        **kwargs
    ) -> None:
        """
        Ensure that the specified exchange exists in RabbitMQ.
        """
        await self.create_exchange(exchange_name, exchange_type, **kwargs)

    async def publish_message(
        self,
        exchange_name: str,
        routing_key: str,
        body: Union[str, list, dict],
        **kwargs
    ) -> None:
        """
        Publish a message to a RabbitMQ exchange.
        """
        await self.ensure_connection()
        # Ensure the exchange exists before publishing
        await self.ensure_exchange(exchange_name)

        args = {
            "mandatory": True,
            "timeout": None,
            **kwargs
        }
        if isinstance(body, (dict, list)):
            body = json_encoder(body)
        try:
            await self._channel.basic_publish(
                body.encode('utf-8'),
                exchange=exchange_name,
                routing_key=routing_key,
                properties=aiormq.spec.Basic.Properties(delivery_mode=2),
                **args
            )
        except Exception as exc:
            self.logger.error(
                f"Failed to publish message: {exc}"
            )

    async def consume_messages(
        self,
        queue: str,
        callback: Union[Callable, Awaitable]
    ) -> None:
        """
        Consumes a Message from RabbitMQ Queue.
        """
        await self.ensure_connection()
        try:
            await self._channel.queue_declare(queue)
            await self._channel.basic_consume(queue, callback)
        except Exception as e:
            self.logger.error(
                f"Failed to consume message: {e}"
            )
