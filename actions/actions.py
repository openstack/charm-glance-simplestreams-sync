#!/usr/bin/env python3
#
# Copyright 2023 Canonical Ltd
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#  http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import os
import sys
import subprocess

_path = os.path.dirname(os.path.realpath(__file__))
_root = os.path.abspath(os.path.join(_path, ".."))


def _add_path(path):
    if path not in sys.path:
        sys.path.insert(1, path)


_add_path(_root)


from charmhelpers.core.hookenv import action_fail

PID_FILE_DIR = "/var/run"
RUNNING_FLAG_FILE_NAME = os.path.join(
    PID_FILE_DIR, "glance-simplestreams-sync.pid"
)


def sync_images(args):
    """Syncs images on local glance instance with the URL provided in the
    config's mirror list
    """
    exit_status = subprocess.call(
        [("/usr/share/glance-simplestreams-sync/"
         "glance-simplestreams-sync.sh")]
    )
    if exit_status == 2:
        action_fail("{} is locked, exiting".format(RUNNING_FLAG_FILE_NAME))
    return exit_status


# A dictionary of all the defined actions to callables (which take
# parsed arguments).
ACTIONS = {"sync-images": sync_images}


def main(args):
    action_name = os.path.basename(args[0])
    try:
        action = ACTIONS[action_name]
    except KeyError:
        return "Action {} undefined".format(action_name)
    else:
        try:
            action(args)
        except Exception as e:
            action_fail(str(e))


if __name__ == "__main__":
    sys.exit(main(sys.argv))
