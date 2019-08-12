#!/usr/bin/env python
#
# Copyright 2016 Canonical Ltd
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

"""
Basic glance-simplestreams-sync functional tests.
"""

import amulet
import re
import time

from charmhelpers.contrib.openstack.amulet.deployment import (
    OpenStackAmuletDeployment
)

from charmhelpers.contrib.openstack.amulet.utils import (
    OpenStackAmuletUtils,
    DEBUG,
    # ERROR
)

# Use DEBUG to turn on debug logging
u = OpenStackAmuletUtils(DEBUG)


class GlanceBasicDeployment(OpenStackAmuletDeployment):
    """Amulet tests on a basic file-backed glance deployment.  Verify
    relations, service status, endpoint service catalog, create and
    delete new image."""

    SERVICES = ('apache2', 'haproxy', 'glance-api', 'glance-registry')

    def __init__(self, series=None, openstack=None, source=None,
                 stable=True):
        """Deploy the entire test environment."""
        super(GlanceBasicDeployment, self).__init__(series, openstack,
                                                    source, stable)
        self._add_services()
        self._add_relations()
        self._configure_services()
        self._deploy()

        u.log.info('Waiting on extended status checks...')

        # NOTE(thedac):  This charm has a non-standard workload status.
        # The default match for ready will fail. Check the other charms
        # for standard workload status and check this charm for Sync
        # completed.

        # Check for ready
        exclude_services = ['glance-simplestreams-sync']
        self._auto_wait_for_status(exclude_services=exclude_services)

        # Check for Sync completed
        self._auto_wait_for_status(re.compile('Sync completed.*',
                                              re.IGNORECASE),
                                   include_only=exclude_services)

        self.d.sentry.wait()
        self._initialize_tests()

    def _assert_services(self, should_run):
        u.get_unit_process_ids(
            {self.glance_sentry: self.SERVICES},
            expect_success=should_run)

    def _add_services(self):
        """Add services

           Add the services that we're testing, where glance is local,
           and the rest of the service are from lp branches that are
           compatible with the local charm (e.g. stable or next).
           """
        this_service = {'name': 'glance-simplestreams-sync'}
        other_services = [
            self.get_percona_service_entry(),
            {'name': 'glance'},
            {'name': 'rabbitmq-server'},
            {'name': 'keystone'},
        ]
        super(GlanceBasicDeployment, self)._add_services(
            this_service,
            other_services,
            use_source=['glance-simplestreams-sync'],
        )

    def _add_relations(self):
        """Add relations for the services."""
        relations = {
            'glance:identity-service': 'keystone:identity-service',
            'glance:shared-db': 'percona-cluster:shared-db',
            'keystone:shared-db': 'percona-cluster:shared-db',
            'glance:amqp': 'rabbitmq-server:amqp',
            'glance-simplestreams-sync:identity-service':
                'keystone:identity-service',
            'glance-simplestreams-sync:amqp':
                'rabbitmq-server:amqp',
        }

        super(GlanceBasicDeployment, self)._add_relations(relations)

    def _configure_services(self):
        """Configure all of the services."""
        gss_config = {
            # https://bugs.launchpad.net/bugs/1686437
            'source': 'ppa:simplestreams-dev/trunk',
            'use_swift': 'False',
        }
        glance_config = {}
        keystone_config = {
            'admin-password': 'openstack',
            'admin-token': 'ubuntutesting',
        }
        pxc_config = {
            'dataset-size': '25%',
            'max-connections': 1000,
            'root-password': 'ChangeMe123',
            'sst-password': 'ChangeMe123',
        }
        configs = {
            'glance-simplestreams-sync': gss_config,
            'glance': glance_config,
            'keystone': keystone_config,
            'percona-cluster': pxc_config,
        }
        super(GlanceBasicDeployment, self)._configure_services(configs)

    def _initialize_tests(self):
        """Perform final initialization before tests get run."""
        # Access the sentries for inspecting service units
        self.gss_sentry = self.d.sentry['glance-simplestreams-sync'][0]
        self.pxc_sentry = self.d.sentry['percona-cluster'][0]
        self.glance_sentry = self.d.sentry['glance'][0]
        self.keystone_sentry = self.d.sentry['keystone'][0]
        self.rabbitmq_sentry = self.d.sentry['rabbitmq-server'][0]
        u.log.debug('openstack release val: {}'.format(
            self._get_openstack_release()))
        u.log.debug('openstack release str: {}'.format(
            self._get_openstack_release_string()))

        # Authenticate admin with keystone
        self.keystone_session, self.keystone = u.get_default_keystone_session(
            self.keystone_sentry,
            openstack_release=self._get_openstack_release())

        # Authenticate admin with glance endpoint
        self.glance = u.authenticate_glance_admin(self.keystone)

    def test_001_wait_for_image_sync(self):
        """Wait for images to be synced. Expect at least one."""

        max_image_wait = 600
        retry_sleep = 2
        images = []

        time_start = time.time()
        while not images:
            images = [image.name for image in self.glance.images.list()]
            u.log.debug('Images: {}'.format(images))
            if images:
                break

            time_now = time.time()
            if time_now - time_start >= max_image_wait:
                raise Exception('Images not synced within '
                                '{}s'.format(time_now - time_start))
            else:
                u.log.debug('Waiting {}s'.format(retry_sleep))
                time.sleep(retry_sleep)
                retry_sleep = retry_sleep + 4 if retry_sleep < 30 else 30

    def test_050_gss_permissions_regression_check_lp1611987(self):
        """Assert the intended file permissions on gss config files
           https://bugs.launchpad.net/bugs/1611987"""

        perm_check = [
            {
                'file_path': '/etc/glance-simplestreams-sync/identity.yaml',
                'expected_perms': '640',
                'unit_sentry': self.gss_sentry
            },
            {
                'file_path': '/etc/glance-simplestreams-sync/mirrors.yaml',
                'expected_perms': '640',
                'unit_sentry': self.gss_sentry
            },
            {
                'file_path': '/var/log/glance-simplestreams-sync.log',
                'expected_perms': '640',
                'unit_sentry': self.gss_sentry
            },
        ]

        for _check in perm_check:
            cmd = 'stat -c %a {}'.format(_check['file_path'])
            output, _ = u.run_cmd_unit(_check['unit_sentry'], cmd)

            assert output == _check['expected_perms'], \
                '{} perms not as expected'.format(_check['file_path'])

            u.log.debug('Permissions on {}: {}'.format(
                _check['file_path'], output))

    def test_102_service_catalog(self):
        """Verify that the service catalog endpoint data is valid."""
        u.log.debug('Checking keystone service catalog...')
        endpoint_check = {
            'adminURL': u.valid_url,
            'id': u.not_null,
            'region': 'RegionOne',
            'publicURL': u.valid_url,
            'internalURL': u.valid_url
        }
        expected = {
            'product-streams': [endpoint_check],
            'image': [endpoint_check],
            'identity': [endpoint_check]
        }
        actual = self.keystone.service_catalog.get_endpoints()

        ret = u.validate_svc_catalog_endpoint_data(
            expected,
            actual,
            openstack_release=self._get_openstack_release())
        if ret:
            amulet.raise_status(amulet.FAIL, msg=ret)
