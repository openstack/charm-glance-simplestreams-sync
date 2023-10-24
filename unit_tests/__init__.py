import os
import sys
from unittest.mock import patch

_path = os.path.dirname(os.path.realpath(__file__))
_actions = os.path.abspath(os.path.join(_path, '../actions'))
_hooks = os.path.abspath(os.path.join(_path, '../hooks'))
_charmhelpers = os.path.abspath(os.path.join(_path, '../charmhelpers'))
_unit_tests = os.path.abspath(os.path.join(_path, '../unit_tests'))


def _add_path(path):
    if path not in sys.path:
        sys.path.insert(1, path)


_add_path(_actions)
_add_path(_hooks)
_add_path(_charmhelpers)
_add_path(_unit_tests)


# Patch out lsb_release() and get_platform() as unit tests should be fully
# insulated from the underlying platform.  Unit tests assume that the system is
# ubuntu jammy.
patch(
    'charmhelpers.osplatform.get_platform', return_value='ubuntu'
).start()
patch(
    'charmhelpers.core.host.lsb_release',
    return_value={
        'DISTRIB_CODENAME': 'jammy'
    }).start()
