import asyncio
from navconfig import config
from notify.models import Actor
from notify import Notify
from qw.client import QClient

stmp_host_user=config.get('stmp_host_user')
stmp_host_password=config.get('stmp_host_password')
stmp_host=config.get('stmp_host')
stmp_port=config.get('stmp_port')

user = {
    "name": "Jesus Lara",
    "account": {
        "address": "jesuslarag@gmail.com",
    }
}
jesus = Actor(**user)

async def send_email():
    account = {
        "hostname": stmp_host,
        "port": stmp_port,
        "password": stmp_host_password,
        "username": stmp_host_user
    }
    email = Notify('email', **account)
    await email.send(
        recipient=jesus,
        subject='Epale, vente a jugar bolas criollas!',
        event_name='Partido de bolas Criollas',
        event_address='Bolodromo Caucagua',
        template='email_applied.html'
    )
    await email.close()


if __name__ == '__main__':
    loop = asyncio.get_event_loop()
    asyncio.set_event_loop(loop)
    qw = QClient()
    print(':: SELECTED SERVER : ', qw.get_servers())
    try:
        result = loop.run_until_complete(
            qw.queue(send_email)
        )
    finally:
        loop.stop()
