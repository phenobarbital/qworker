import asyncio
import uvloop


def enable_uvloop():
    try:
        asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
        uvloop.install()
        return True
    except ImportError:
        return False
