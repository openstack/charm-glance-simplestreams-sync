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

import base64
import os
import re
import tempfile

from charmhelpers.contrib.openstack.amulet.deployment import (
    OpenStackAmuletDeployment
)

from charmhelpers.contrib.openstack.amulet.utils import (
    OpenStackAmuletUtils,
    DEBUG,
    # ERROR
)

import generate_certs

# Use DEBUG to turn on debug logging
u = OpenStackAmuletUtils(DEBUG)


class GlanceBasicDeployment(OpenStackAmuletDeployment):
    """Amulet tests on a basic file-backed glance deployment.  Verify
    relations, service status, endpoint service catalog, create and
    delete new image."""

    SERVICES = ('apache2', 'haproxy', 'glance-api', 'glance-registry')

    def __init__(self, series=None, openstack=None, source=None,
                 stable=False):
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

        # Check for Sync completed; if SSL is okay, this should work
        self._auto_wait_for_status(re.compile('Sync completed.*',
                                              re.IGNORECASE),
                                   include_only=exclude_services)

        self.d.sentry.wait()

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
            {'name': 'percona-cluster', 'constraints': {'mem': '3072M'}},
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
        _path = tempfile.gettempdir()
        generate_certs.generate_certs(_path)

        _cacert = self.load_base64(_path, 'cacert.pem')
        _cert = self.load_base64(_path, 'cert.pem')
        _key = self.load_base64(_path, 'cert.key')

        gss_config = {
            # https://bugs.launchpad.net/bugs/1686437
            'source': 'ppa:simplestreams-dev/trunk',
            'use_swift': 'False',
            'ssl_ca': _cacert,
        }
        glance_config = {
            'ssl_ca': _cacert,
            'ssl_cert': _cert,
            'ssl_key': _key,
        }
        keystone_config = {
            'admin-password': 'openstack',
            'admin-token': 'ubuntutesting',
            'ssl_ca': _cacert,
            'ssl_cert': _cert,
            'ssl_key': _key,
        }
        pxc_config = {
            'dataset-size': '25%',
            'max-connections': 1000,
            'root-password': 'ChangeMe123',
            'sst-password': 'ChangeMe123',
        }
        rabbitmq_server_config = {
            'ssl': 'on',
        }
        configs = {
            'glance-simplestreams-sync': gss_config,
            'glance': glance_config,
            'keystone': keystone_config,
            'percona-cluster': pxc_config,
            'rabbitmq-server': rabbitmq_server_config,
        }
        super(GlanceBasicDeployment, self)._configure_services(configs)

    @staticmethod
    def load_base64(*path):
        with open(os.path.join(*path)) as f:
            return base64.b64encode(f.read())
