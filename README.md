# QueueWorker #

[![pypi](https://img.shields.io/pypi/v/asyncdb?style=plastic)](https://pypi.org/project/asyncdb/)
[![versions](https://img.shields.io/pypi/pyversions/blacksheep.svg?style=plastic)](https://github.com/phenobarbital/qworker)
[![MIT licensed](https://img.shields.io/github/license/phenobarbital/qworker?style=plastic)](https://raw.githubusercontent.com/phenobarbital/qworker/master/LICENSE)


QueueWorker is asynchronous Task Queue implementation built to
work with ``asyncio``.
Can you spawn distributed workers to run functions inside workers and outside of
event loop.

``QueueWorker`` requires Python 3.8+ and is distributed under MIT license.

### How do I get set up? ###

First, you need to instal QueueWorker:

.. code-block ::

    pip install qworker

Then, you can start several workers (even sharing the same port):

.. code-block ::

   qw --host <hostname> --port <port-number> --worker <num-workers>

where

- ``<hostname>`` is a hostname of the server
- ``<port-number>`` is a port that server will listen on
- ``<num-workers>`` is a number of worker processes


### License ###

QueueWorker is copyright of Jesus Lara (https://phenobarbital.info) and is under MIT license. I am providing code in this repository under an open source license, remember, this is my personal repository; the license that you receive is from me and not from my employeer.
