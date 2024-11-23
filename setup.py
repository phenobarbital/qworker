#!/usr/bin/env python
"""QWorker.

    Distributed System built on top of asyncio and cloudpickle.
See:
https://github.com/phenobarbital/qworker
"""
import ast
from os import path
from setuptools import find_packages, setup, Extension
from Cython.Build import cythonize


def get_path(filename):
    return path.join(path.dirname(path.abspath(__file__)), filename)


def readme():
    with open(get_path('README.md'), 'r', encoding='utf-8') as rd:
        return rd.read()


version = get_path('qw/version.py')
with open(version, 'r', encoding='utf-8') as meta:
    # exec(meta.read())
    t = compile(meta.read(), version, 'exec', ast.PyCF_ONLY_AST)
    for node in (n for n in t.body if isinstance(n, ast.Assign)):
        if len(node.targets) == 1:
            name = node.targets[0]
            if isinstance(name, ast.Name) and \
                name.id in (
                    '__version__',
                    '__title__',
                    '__description__',
                    '__author__',
                    '__license__', '__author_email__'):
                v = node.value
                if name.id == '__version__':
                    __version__ = v.s
                if name.id == '__title__':
                    __title__ = v.s
                if name.id == '__description__':
                    __description__ = v.s
                if name.id == '__license__':
                    __license__ = v.s
                if name.id == '__author__':
                    __author__ = v.s
                if name.id == '__author_email__':
                    __author_email__ = v.s

COMPILE_ARGS = ["-O2"]

extensions = [
    Extension(
        name='qw.exceptions',
        sources=['qw/exceptions.pyx'],
        extra_compile_args=COMPILE_ARGS,
        language="c"
    ),
    Extension(
        name='qw.utils.json',
        sources=['qw/utils/json.pyx'],
        extra_compile_args=COMPILE_ARGS,
        language="c++"
    ),
]

setup(
    name=__title__,
    version=__version__,
    python_requires=">=3.8.0",
    url='https://github.com/phenobarbital/qworker',
    description=__description__,
    long_description=readme(),
    long_description_content_type='text/markdown',
    keywords="distributed objects, workers, asyncio, task queue, RPC, remote method call",
    license=__license__,
    classifiers=[
        'Development Status :: 4 - Beta',
        'Intended Audience :: Developers',
        'Intended Audience :: System Administrators',
        'Topic :: Software Development :: Libraries :: Python Modules',
        'Topic :: Software Development :: Build Tools',
        'Topic :: Software Development :: Object Brokering',
        'Topic :: System :: Distributed Computing',
        'Topic :: System :: Networking',
        'Operating System :: OS Independent',
        'Environment :: Web Environment',
        'Programming Language :: Python :: 3.8',
        'Programming Language :: Python :: 3.9',
        'Programming Language :: Python :: 3.10',
        'Programming Language :: Python :: 3.11',
        'Programming Language :: Python :: 3.12',
        'Framework :: AsyncIO'
    ],
    author='Jesus Lara',
    author_email='jesuslara@phenobarbital.info',
    packages=find_packages(exclude=["contrib", "docs", "tests", "settings"]),
    setup_requires=[
        'setuptools==67.6.1',
        'Cython==3.0.11',
        'wheel==0.44.0',
    ],
    install_requires=[
        'asyncio==3.4.3',
        'ciso8601>=2.2.0',
        'navconfig[uvloop,default]>=1.7.9',
        'asyncdb[default]>=2.9.0',
        'async-notify[default]>=1.3.4',
        'cloudpickle>=3.0.0',
        'jsonpickle>=3.0.2',
        'beautifulsoup4>=4.12.3',
        'async-timeout==4.0.3',
        'msgpack==1.1.0',
        'aiormq==6.8.1',
    ],
    ext_modules=cythonize(extensions),
    entry_points={
        'console_scripts': [
            'qw = qw.__main__:main'
        ]
    },
    project_urls={  # Optional
        'Source': 'https://github.com/phenobarbital/qworker',
        'Funding': 'https://paypal.me/phenobarbital',
        'Say Thanks!': 'https://saythanks.io/to/phenobarbital',
    },
)
