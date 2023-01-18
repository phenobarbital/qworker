"""version.

Extract the version of all required packages and showed in a response.
"""
import importlib

# TODO: Add Qworker, Scheduler and other DI packages.
package_list = ('asyncdb', 'qw', 'querysource', 'navconfig')


def get_versions():
    """
    ---
    summary: Return versions of all required packages
    tags:
    - version
    produces:
    - dict
    responses:
        "200":
            description: list of packages and versions.
    """
    versions = {}
    for package in package_list:
        mdl = importlib.import_module(f'{package}.version', package='version')
        obj = getattr(mdl, '__version__')
        versions[package] = obj
    return versions
