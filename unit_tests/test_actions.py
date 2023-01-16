#!/usr/bin/env python3

"""
Copyright 2023 Canonical Ltd.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
"""

import os
import sys
import unittest.mock as mock
import unittest

_path = os.path.dirname(os.path.realpath(__file__))
_actions = os.path.abspath(os.path.join(_path, "../actions"))


def _add_path(path):
    if path not in sys.path:
        sys.path.insert(1, path)


_add_path(_actions)

import actions


class TestActions(unittest.TestCase):
    def test_add_path(self):
        # random string as a path to add
        newPath = "8zdyhfcnoqe08yhzxzc"
        # Path should get added when it doesn't exist
        actions._add_path(newPath)
        self.assertEqual(sys.path.count(newPath), 1)
        # Path shouldn't be added when it does exist
        actions._add_path(newPath)
        self.assertEqual(sys.path.count(newPath), 1)

    @mock.patch("actions.action_fail")
    @mock.patch("subprocess.call")
    def test_sync_images(self, mock_subprocess_call, mock_action_fail):
        # test pass, action_fail not called:
        mock_subprocess_call.return_value = 0
        self.assertEqual(actions.sync_images(None), 0, "Expect exit status 0")
        self.assertFalse(mock_action_fail.called, "Should not call")

        # test fail - unknown reason, action_fail not called:
        mock_subprocess_call.return_value = 1
        self.assertEqual(actions.sync_images(None), 1, "Expect exit status 1")
        self.assertFalse(mock_action_fail.called, "Should not call")

        # test fail - locked file, action_fail called:
        mock_subprocess_call.return_value = 2
        actions.sync_images(None)
        # check if action_fail has been called exactly once
        mock_action_fail.assert_called_once()
        # check arguments with which it was called
        FILE_PATH = os.path.join("/var/run", "glance-simplestreams-sync.pid")
        mock_action_fail.assert_called_once_with(
            "{} is locked, exiting".format(FILE_PATH)
        )
