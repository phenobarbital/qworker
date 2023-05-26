"""Client Example of Queue Worker."""
import asyncio
from collections import ChainMap, Counter
import time
import requests
from qw.client import QClient
from qw.utils import cPrint

# workers = [("nav-api.dev.local", 8888)]

qw = QClient()

print('SERVER : ', qw.get_servers())
URLS = {
    "http://www.gutenberg.org/cache/epub/996/pg996.txt",
    "http://www.gutenberg.org/files/1342/1342-0.txt",
    "http://www.gutenberg.org/cache/epub/1661/pg1661.txt",
    "https://stackoverflow.com/questions/29756507/how-can-i-add-a-connection-timeout-with-asyncio",
    "https://stackoverflow.com/questions/37327372/how-to-handle-connectionrefusederror-when-connecting-with-other-peers-using-asyn",
    "http://www.andy-pearce.com/blog/posts/2016/Jul/the-state-of-python-coroutines-asyncio-callbacks-vs-coroutines/",
    "https://www.codespeedy.com/itertools-cycle-in-python/",
    "https://codereview.stackexchange.com/questions/156729/python-list-dictionary-items-round-robin-mixing",
    "https://www.programcreek.com/python/?code=goldmansachs%2Fgs-quant%2Fgs-quant-master%2Fgs_quant%2Fapi%2Frisk.py",
    "https://dle.rae.es/"
}

def top_words(url, n):
    """Returns top n words from text specified by url."""
    text = requests.get(url, timeout=5).text.split()
    return {url: Counter(text).most_common(n)}

async def get_top_words(urls, n):
    """Returns top n words in documents specified by URLs."""
    tops_in_url = await asyncio.gather(
        *[qw.run(top_words, url, n) for url in urls]
    )
    return ChainMap(*tops_in_url)

if __name__ == '__main__':
    start_time = time.time()
    loop = asyncio.get_event_loop()
    top = loop.run_until_complete(
        get_top_words(URLS, 50)
    )
    end_time = time.time() - start_time
    print(top)
    cPrint(f'Task took {end_time} seconds to run', level='DEBUG')
