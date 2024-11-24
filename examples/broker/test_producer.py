from aiohttp import web
from navigator_auth import AuthHandler
from qw.broker.producer import BrokerManager


app = web.Application()
# create a new instance of Auth System
auth = AuthHandler()
auth.setup(app)  # configure this Auth system into App.


broker = BrokerManager()
broker.setup(app)

if __name__ == '__main__':
    try:
        web.run_app(
            app, host='localhost', port=5000, handle_signals=True
        )
    except KeyboardInterrupt:
        print('EXIT FROM APP =========')
