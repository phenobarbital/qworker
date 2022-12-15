import hashlib
import hmac
import base64
from asyncdb.utils.functions import colors, Msg, cPrint
from qw.exceptions import ConfigError


__all__ = (
    "colors",
    "Msg",
    "cPrint",
)

def make_signature(message: str, key: str) -> str:
    if not key:
        raise ConfigError(
            "QW Server: Error, Empty Signature"
        )
    skey = key.encode('utf-8')
    msg = message.encode('utf-8')
    digest = hmac.new(skey, msg, digestmod=hashlib.sha512).digest()
    return base64.b64encode(digest)
