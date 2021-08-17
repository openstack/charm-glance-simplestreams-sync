#!/usr/bin/env python3

'''
Copyright 2021 Canonical Ltd.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
'''

import files.glance_simplestreams_sync as gss
import mock
import unittest

from keystoneclient import exceptions as keystone_exceptions


class TestGlanceSimpleStreamsSync(unittest.TestCase):

    def setUp(self):
        self.maxDiff = 4096

    @mock.patch('files.glance_simplestreams_sync.juju_run_cmd')
    def test_proxy_settings(self, juju_run_cmd):
        juju_run_cmd.return_value = '''
LANG=C.UTF-8
JUJU_CONTEXT_ID=glance-simplestreams-sync/0-run-commands-3325280900519425661
JUJU_CHARM_HTTP_PROXY=http://squid.internal:3128
JUJU_CHARM_HTTPS_PROXY=https://squid.internal:3128
JUJU_CHARM_NO_PROXY=127.0.0.1,localhost,::1
'''
        self.assertEqual(gss.juju_proxy_settings(), {
            "HTTP_PROXY": "http://squid.internal:3128",
            "HTTPS_PROXY": "https://squid.internal:3128",
            "NO_PROXY": "127.0.0.1,localhost,::1",
            "http_proxy": "http://squid.internal:3128",
            "https_proxy": "https://squid.internal:3128",
            "no_proxy": "127.0.0.1,localhost,::1",
        })

    @mock.patch('files.glance_simplestreams_sync.juju_run_cmd')
    def test_legacy_proxy_settings(self, juju_run_cmd):
        juju_run_cmd.return_value = '''
LANG=C.UTF-8
JUJU_CONTEXT_ID=glance-simplestreams-sync/0-run-commands-3325280900519425661
HTTP_PROXY=http://squid.internal:3128
HTTPS_PROXY=https://squid.internal:3128
NO_PROXY=127.0.0.1,localhost,::1
'''
        self.assertEqual(gss.juju_proxy_settings(), {
            "HTTP_PROXY": "http://squid.internal:3128",
            "HTTPS_PROXY": "https://squid.internal:3128",
            "NO_PROXY": "127.0.0.1,localhost,::1",
            "http_proxy": "http://squid.internal:3128",
            "https_proxy": "https://squid.internal:3128",
            "no_proxy": "127.0.0.1,localhost,::1",
        })

    @mock.patch('files.glance_simplestreams_sync.juju_run_cmd')
    def test_proxy_settings_not_set(self, juju_run_cmd):
        juju_run_cmd.return_value = '''
LANG=C.UTF-8
JUJU_CONTEXT_ID=glance-simplestreams-sync/0-run-commands-3325280900519425661
'''
        self.assertEqual(gss.juju_proxy_settings(), None)

    @mock.patch('files.glance_simplestreams_sync.get_service_endpoints')
    @mock.patch('files.glance_simplestreams_sync.juju_proxy_settings')
    def test_get_sstream_mirror_proxy_env(self,
                                          juju_proxy_settings,
                                          get_service_endpoints):
        # Use a side effect instead of return value to avoid modification of
        # the same dict in different invocations of the tested function.
        def juju_proxy_settings_side_effect():
            return {
                "HTTP_PROXY": "http://squid.internal:3128",
                "HTTPS_PROXY": "https://squid.internal:3128",
                "NO_PROXY": "127.0.0.1,localhost,::1",
                "http_proxy": "http://squid.internal:3128",
                "https_proxy": "https://squid.internal:3128",
                "no_proxy": "127.0.0.1,localhost,::1",
            }

        juju_proxy_settings.side_effect = juju_proxy_settings_side_effect

        def get_service_endpoints_side_effect(ksc, service_type, region_name):
            return {
                'identity': {
                    'publicURL': 'https://192.0.2.42:5000/v3',
                    'internalURL': 'https://192.0.2.43:5000/v3',
                    'adminURL': 'https://192.0.2.44:35357/v3',
                },
                'image': {
                    'publicURL': 'https://192.0.2.45:9292',
                    'internalURL': 'https://192.0.2.45:9292',
                    'adminURL': 'https://192.0.2.47:9292',
                },
                'object-store': {
                    'publicURL': 'https://192.0.2.90:443/swift/v1',
                    'internalURL': 'https://192.0.2.90:443/swift/v1',
                    'adminURL': 'https://192.0.2.90:443/swift',
                },
            }[service_type]

        get_service_endpoints.side_effect = get_service_endpoints_side_effect
        # Besides checking for proxy settings being set, make sure that
        # object-store endpoints are added to NO_PROXY by default or when
        # explicitly asked for.
        for proxy_env in [
                gss.get_sstream_mirror_proxy_env(
                    mock.MagicMock(), 'TestRegion'),
                gss.get_sstream_mirror_proxy_env(
                    mock.MagicMock(), 'TestRegion',
                    ignore_proxy_for_object_store=True)]:
            self.assertEqual(proxy_env['HTTP_PROXY'],
                             'http://squid.internal:3128')
            self.assertEqual(proxy_env['http_proxy'],
                             'http://squid.internal:3128')
            self.assertEqual(proxy_env['HTTPS_PROXY'],
                             'https://squid.internal:3128')
            self.assertEqual(proxy_env['https_proxy'],
                             'https://squid.internal:3128')
            no_proxy_set = set(['127.0.0.1', 'localhost', '::1', '192.0.2.42',
                                '192.0.2.43', '192.0.2.44', '192.0.2.45',
                                '192.0.2.47', '192.0.2.90'])
            self.assertEqual(set(proxy_env['NO_PROXY'].split(',')),
                             no_proxy_set)
            self.assertEqual(set(proxy_env['no_proxy'].split(',')),
                             no_proxy_set)

        # Make sure that object-store endpoints are not included into
        # NO_PROXY when this is explicitly being asked for. In this case
        # the set of expected addresses in NO_PROXY should exclude 192.0.2.90.
        proxy_env = gss.get_sstream_mirror_proxy_env(
            mock.MagicMock(),
            'TestRegion', ignore_proxy_for_object_store=False)
        self.assertEqual(proxy_env['HTTP_PROXY'], 'http://squid.internal:3128')
        self.assertEqual(proxy_env['http_proxy'], 'http://squid.internal:3128')
        self.assertEqual(proxy_env['HTTPS_PROXY'],
                         'https://squid.internal:3128')
        self.assertEqual(proxy_env['https_proxy'],
                         'https://squid.internal:3128')
        no_proxy_set_no_obj = set(['127.0.0.1', 'localhost', '::1',
                                   '192.0.2.42', '192.0.2.43', '192.0.2.44',
                                   '192.0.2.45', '192.0.2.47'])
        self.assertEqual(set(proxy_env['NO_PROXY'].split(',')),
                         no_proxy_set_no_obj)
        self.assertEqual(set(proxy_env['no_proxy'].split(',')),
                         no_proxy_set_no_obj)

        def no_juju_proxy_settings_side_effect():
            return None

        juju_proxy_settings.side_effect = no_juju_proxy_settings_side_effect
        # Make sure that even if Juju does not have any proxy settings set,
        # via the model, we are still adding endpoints to NO_PROXY for
        # sstream-mirror-glance invocations because settings might be sourced
        # from other files (see glance-simplestreams-sync.sh).
        proxy_env = gss.get_sstream_mirror_proxy_env(
            mock.MagicMock(),
            'TestRegion', ignore_proxy_for_object_store=False)
        no_proxy_set_no_obj = set(['192.0.2.42', '192.0.2.43', '192.0.2.44',
                                   '192.0.2.45', '192.0.2.47'])
        self.assertEqual(set(proxy_env['NO_PROXY'].split(',')),
                         no_proxy_set_no_obj)
        self.assertEqual(set(proxy_env['no_proxy'].split(',')),
                         no_proxy_set_no_obj)

    @mock.patch('files.glance_simplestreams_sync.get_service_endpoints')
    @mock.patch('files.glance_simplestreams_sync.juju_proxy_settings')
    def test_get_sstream_mirror_proxy_env_no_object_store(
            self, juju_proxy_settings, get_service_endpoints):
        # Use a side effect instead of return value to avoid modification of
        # the same dict in different invocations of the tested function.
        def juju_proxy_settings_side_effect():
            return {
                "HTTP_PROXY": "http://squid.internal:3128",
                "HTTPS_PROXY": "https://squid.internal:3128",
                "NO_PROXY": "127.0.0.1,localhost,::1",
                "http_proxy": "http://squid.internal:3128",
                "https_proxy": "https://squid.internal:3128",
                "no_proxy": "127.0.0.1,localhost,::1",
            }

        juju_proxy_settings.side_effect = juju_proxy_settings_side_effect

        def get_service_endpoints_side_effect(ksc, service_type, region_name):
            if service_type == 'object-store':
                raise keystone_exceptions.EndpointNotFound('foo')
            return {
                'identity': {
                    'publicURL': 'https://192.0.2.42:5000/v3',
                    'internalURL': 'https://192.0.2.43:5000/v3',
                    'adminURL': 'https://192.0.2.44:35357/v3',
                },
                'image': {
                    'publicURL': 'https://192.0.2.45:9292',
                    'internalURL': 'https://192.0.2.45:9292',
                    'adminURL': 'https://192.0.2.47:9292',
                },
            }[service_type]

        get_service_endpoints.side_effect = get_service_endpoints_side_effect
        for proxy_env in [
                gss.get_sstream_mirror_proxy_env(
                    mock.MagicMock(), 'TestRegion'),
                gss.get_sstream_mirror_proxy_env(
                    mock.MagicMock(), 'TestRegion',
                    ignore_proxy_for_object_store=True)]:
            self.assertEqual(proxy_env['HTTP_PROXY'],
                             'http://squid.internal:3128')
            self.assertEqual(proxy_env['http_proxy'],
                             'http://squid.internal:3128')
            self.assertEqual(proxy_env['HTTPS_PROXY'],
                             'https://squid.internal:3128')
            self.assertEqual(proxy_env['https_proxy'],
                             'https://squid.internal:3128')
            no_proxy_set = set(['127.0.0.1', 'localhost', '::1', '192.0.2.42',
                                '192.0.2.43', '192.0.2.44', '192.0.2.45',
                                '192.0.2.47'])
            self.assertEqual(set(proxy_env['NO_PROXY'].split(',')),
                             no_proxy_set)
            self.assertEqual(set(proxy_env['no_proxy'].split(',')),
                             no_proxy_set)

    def test_get_service_endpoints(self):

        def url_for_side_effect(service_type, endpoint_type, region_name):
            return {
                'TestRegion': {
                    'identity': {
                        'publicURL': 'https://10.5.2.42:443/swift/v1',
                        'internalURL': 'https://10.5.2.42:443/swift/v1',
                        'adminURL': 'https://10.5.2.42:443/swift/v1',
                    },
                    'image': {
                        'publicURL': 'https://10.5.2.43:443/swift/v1',
                        'internalURL': 'https://10.5.2.43:443/swift/v1',
                        'adminURL': 'https://10.5.2.43:443/swift/v1',
                    },
                    'object-store': {
                        'publicURL': 'https://10.5.2.44:443/swift/v1',
                        'internalURL': 'https://10.5.2.44:443/swift/v1',
                        'adminURL': 'https://10.5.2.44:443/swift/v1',
                    },
                }
            }[region_name][service_type][endpoint_type]

        ksc = mock.MagicMock()
        ksc.service_catalog.url_for.side_effect = url_for_side_effect
        self.assertEqual(
            gss.get_service_endpoints(ksc, 'identity', 'TestRegion'), {
                'publicURL': 'https://10.5.2.42:443/swift/v1',
                'internalURL': 'https://10.5.2.42:443/swift/v1',
                'adminURL': 'https://10.5.2.42:443/swift/v1',
            }
        )
        self.assertEqual(
            gss.get_service_endpoints(ksc, 'image', 'TestRegion'), {
                'publicURL': 'https://10.5.2.43:443/swift/v1',
                'internalURL': 'https://10.5.2.43:443/swift/v1',
                'adminURL': 'https://10.5.2.43:443/swift/v1',
            }
        )
        self.assertEqual(
            gss.get_service_endpoints(ksc, 'object-store', 'TestRegion'), {
                'publicURL': 'https://10.5.2.44:443/swift/v1',
                'internalURL': 'https://10.5.2.44:443/swift/v1',
                'adminURL': 'https://10.5.2.44:443/swift/v1',
            }
        )

        ksc.service_catalog.url_for.side_effect = mock.MagicMock(
            side_effect=keystone_exceptions.EndpointNotFound('foo'))

        with self.assertRaises(keystone_exceptions.EndpointNotFound):
            gss.get_service_endpoints(ksc, 'test', 'TestRegion')

    def test_get_object_store_endpoints(self):

        def url_for_side_effect(service_type, endpoint_type, region_name):
            return {
                'TestRegion': {
                    'identity': {
                        'publicURL': 'https://10.5.2.42:443/swift/v1',
                        'internalURL': 'https://10.5.2.42:443/swift/v1',
                        'adminURL': 'https://10.5.2.42:443/swift/v1',
                    },
                    'image': {
                        'publicURL': 'https://10.5.2.43:443/swift/v1',
                        'internalURL': 'https://10.5.2.43:443/swift/v1',
                        'adminURL': 'https://10.5.2.43:443/swift/v1',
                    },
                    'object-store': {
                        'publicURL': 'https://10.5.2.44:443/swift/v1',
                        'internalURL': 'https://10.5.2.44:443/swift/v1',
                        'adminURL': 'https://10.5.2.44:443/swift/v1',
                    },
                }
            }[region_name][service_type][endpoint_type]

        ksc = mock.MagicMock()
        ksc.service_catalog.url_for.side_effect = url_for_side_effect
        self.assertEqual(
            gss.get_object_store_endpoints(ksc, 'TestRegion'),
            ['https://10.5.2.44:443/swift/v1',
             'https://10.5.2.44:443/swift/v1',
             'https://10.5.2.44:443/swift/v1']
        )

        ksc.service_catalog.url_for.side_effect = mock.MagicMock(
            side_effect=keystone_exceptions.EndpointNotFound('foo'))

        self.assertEqual(gss.get_object_store_endpoints(ksc, 'TestRegion'), [])

    @mock.patch('time.strftime')
    @mock.patch('files.glance_simplestreams_sync.status_set')
    def test_set_active_status(self, _status_set, _strftime):
        _status_set.return_value = None
        _strftime.return_value = '42'
        gss.set_active_status(is_object_store_present_and_used=True)
        _status_set.assert_called_once_with(
            'active',
            'Unit is ready (Glance sync completed at 42,'
            ' metadata uploaded to object store)'
        )

        _status_set.reset_mock()

        gss.set_active_status(is_object_store_present_and_used=False)
        _status_set.assert_called_once_with(
            'active',
            'Unit is ready (Glance sync completed at 42,'
            ' metadata not uploaded - object-store usage disabled)'
        )

    @mock.patch('files.glance_simplestreams_sync.status_set')
    def test_assess_object_store_state(self, _status_set):
        self.assertTrue(
            gss.assess_object_store_state(object_store_exists=True,
                                          object_store_requested=False))
        _status_set.assert_not_called()
        _status_set.reset_mock()

        self.assertTrue(
            gss.assess_object_store_state(object_store_exists=True,
                                          object_store_requested=True))
        _status_set.assert_not_called()
        _status_set.reset_mock()

        self.assertTrue(
            gss.assess_object_store_state(object_store_exists=False,
                                          object_store_requested=False))
        _status_set.assert_not_called()
        _status_set.reset_mock()

        self.assertFalse(
            gss.assess_object_store_state(object_store_exists=False,
                                          object_store_requested=True))
        _status_set.assert_called_once_with(
            'maintenance',
            'Swift usage has been requested but its endpoints are not yet in'
            ' the catalog'
        )
        _status_set.reset_mock()

    def test_is_object_store_present(self):

        def url_for_side_effect(service_type, endpoint_type, region_name):
            return {
                'TestRegion': {
                    'identity': {
                        'publicURL': 'https://10.5.2.42:443/swift/v1',
                        'internalURL': 'https://10.5.2.42:443/swift/v1',
                        'adminURL': 'https://10.5.2.42:443/swift/v1',
                    },
                    'image': {
                        'publicURL': 'https://10.5.2.43:443/swift/v1',
                        'internalURL': 'https://10.5.2.43:443/swift/v1',
                        'adminURL': 'https://10.5.2.43:443/swift/v1',
                    },
                    'object-store': {
                        'publicURL': 'https://10.5.2.44:443/swift/v1',
                        'internalURL': 'https://10.5.2.44:443/swift/v1',
                        'adminURL': 'https://10.5.2.44:443/swift/v1',
                    },
                }
            }[region_name][service_type][endpoint_type]

        ksc = mock.MagicMock()
        ksc.service_catalog.url_for.side_effect = url_for_side_effect

        self.assertTrue(gss.is_object_store_present(ksc, 'TestRegion'))

        # Endpoints not found or service not present at all.
        ksc.service_catalog.url_for.side_effect = mock.MagicMock(
            side_effect=keystone_exceptions.EndpointNotFound('foo'))

        self.assertFalse(gss.is_object_store_present(ksc, 'TestRegion'))

    @mock.patch('files.glance_simplestreams_sync.os')
    def test_set_openstack_env_v3(self, mock_os):
        mock_os.environ = dict()

        id_conf = {
            'api_version': '3',
            'auth_host': 'auth-host.local',
            'auth_port': '35357',
            'auth_protocol': 'https',
            'service_host': 'service-host.local',
            'service_port': '5000',
            'service_protocol': 'https',
            'internal_host': 'internal-host.local',
            'internal_port': '5000',
            'internal_protocol': 'https',
            'admin_tenant_id': 'admin-tenant-id',
            'admin_tenant_name': 'services',
            'admin_user': 'image-stream',
            'admin_password': 'insecure',
            'admin_domain_name': 'service_domain',
            'unit_name': 'gss/0',
        }
        gss.set_openstack_env(id_conf, {'region': 'region-one'})

        expected = {
            'OS_AUTH_URL': 'https://service-host.local:5000/v3',
            'OS_USERNAME': 'image-stream',
            'OS_PASSWORD': 'insecure',
            'OS_REGION_NAME': 'region-one',
            'OS_USER_DOMAIN_NAME': 'service_domain',
            'OS_PROJECT_ID': 'admin-tenant-id',
            'OS_PROJECT_NAME': 'services',
            'OS_PROJECT_DOMAIN_NAME': 'service_domain',

        }
        self.assertEqual(expected, mock_os.environ)

        # Configure to use internal endpoints
        id_conf['interface'] = 'internal'
        gss.set_openstack_env(id_conf, {'region': 'region-one'})

        expected['OS_AUTH_URL'] = 'https://internal-host.local:5000/v3'
        expected['OS_INTERFACE'] = 'internal'
        expected['OS_ENDPOINT_TYPE'] = 'internal'

        self.assertEqual(expected, mock_os.environ)
