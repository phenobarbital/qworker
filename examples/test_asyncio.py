import asyncio

class DiscoveryServiceProtocol(asyncio.Protocol):
    def __init__(self):
        self.transport = None
        self.clients = {}

    def connection_made(self, transport):
        self.transport = transport

    def data_received(self, data):
        message = data.decode()
        if message.startswith("REGISTER:"):
            client_name = message.split(":")[1]
            self.clients[client_name] = self.transport
            self.transport.write(f"REGISTERED:{client_name}".encode())
        elif message.startswith("LOOKUP:"):
            client_name = message.split(":")[1]
            if client_name in self.clients:
                self.transport.write(f"FOUND:{client_name}".encode())
            else:
                self.transport.write(f"NOTFOUND:{client_name}".encode())
        else:
            self.transport.write("INVALID_REQUEST".encode())

loop = asyncio.get_event_loop()
# Each client connects to the server at localhost:1234
coro = loop.create_server(DiscoveryServiceProtocol, '127.0.0.1', 1234)
server = loop.run_until_complete(coro)

try:
    loop.run_forever()
except KeyboardInterrupt:
    pass

server.close()
loop.run_until_complete(server.wait_closed())
loop.close()
