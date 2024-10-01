import base64
from unittest import mock
import os
import shutil
import tempfile

from hooks import hooks
from test_utils import CharmTestCase


TO_PATCH = [
    'apt_update',
    'apt_install',
    'get_release',
    'install_ca_cert',
    'get_configs',
]


class TestConfigChanged(CharmTestCase):
    def setUp(self):
        CharmTestCase.setUp(self, hooks, TO_PATCH)
        self.tmpdir = tempfile.mkdtemp()
        hooks.CRON_POLL_FILEPATH = os.path.join(self.tmpdir,
                                                hooks.CRON_POLL_FILENAME)
        self.tmpcrond = tempfile.mkdtemp(prefix='cron.d')
        hooks.CRON_D = self.tmpcrond

        self.sharedir = tempfile.mkdtemp(prefix='share')
        hooks.USR_SHARE_DIR = self.sharedir
        setattr(self.test_config, "save", lambda: None)

        hooks.CRON_POLL_FILEPATH = os.path.join(self.tmpcrond,
                                                hooks.CRON_POLL_FILENAME)
        self.get_release.return_value = 'icehouse'
        self.mock_configs = mock.MagicMock()
        self.get_configs.return_value = self.mock_configs

    def tearDown(self):
        CharmTestCase.tearDown(self)
        shutil.rmtree(self.tmpdir)
        shutil.rmtree(self.tmpcrond)
        shutil.rmtree(self.sharedir)

    @mock.patch('os.remove')
    @mock.patch.object(hooks, 'write_file')
    @mock.patch.object(hooks, 'update_nrpe_config')
    @mock.patch('os.symlink')
    @mock.patch('charmhelpers.core.hookenv.config')
    @mock.patch('charmhelpers.core.hookenv.relations_of_type')
    @mock.patch('charmhelpers.contrib.charmsupport.nrpe.get_nagios_hostname')
    @mock.patch('charmhelpers.contrib.charmsupport.nrpe.config')
    @mock.patch('charmhelpers.contrib.charmsupport.nrpe.local_unit')
    def test_default_config(self, local_unit, nrpe_config, nag_host,
                            relations_of_type, config, symlink,
                            update_nrpe_config, write_file, os_remove):
        local_unit.return_value = 'juju/0'
        nag_host.return_value = "nagios_hostname"
        nrpe_config.return_value = self.test_config

        setattr(self.test_config, "changed", lambda x: False)
        config.return_value = self.test_config
        self.test_config.set('run', True)
        self.test_config.set('ssl_ca', base64.b64encode(b'foobar'))

        hooks.config_changed()

        self.mock_configs.write_all.assert_called_once_with()

        symlink.assert_any_call(os.path.join(self.sharedir,
                                             hooks.SCRIPT_WRAPPER_NAME),
                                '/etc/cron.%s/%s'
                                % (self.test_config['frequency'],
                                   hooks.CRON_JOB_FILENAME))
        self.assertTrue(os.path.isfile(os.path.join(self.tmpcrond,
                                                    hooks.CRON_POLL_FILENAME)))
        update_nrpe_config.assert_called()
        self.install_ca_cert.assert_called_with(b'foobar')

        write_file.assert_not_called()
        os_remove.assert_not_called()

    @mock.patch('os.remove')
    @mock.patch.object(hooks, 'write_file')
    @mock.patch.object(hooks, 'update_nrpe_config')
    @mock.patch('os.symlink')
    @mock.patch('charmhelpers.core.hookenv.config')
    @mock.patch('charmhelpers.core.hookenv.relations_of_type')
    @mock.patch('charmhelpers.contrib.charmsupport.nrpe.get_nagios_hostname')
    @mock.patch('charmhelpers.contrib.charmsupport.nrpe.config')
    @mock.patch('charmhelpers.contrib.charmsupport.nrpe.local_unit')
    def test_custom_keyring(self, local_unit, nrpe_config, nag_host,
                            relations_of_type, config, symlink,
                            update_nrpe_config, write_file, os_remove):
        local_unit.return_value = 'juju/0'
        nag_host.return_value = "nagios_hostname"
        nrpe_config.return_value = self.test_config

        setattr(self.test_config, "changed", lambda x: False)
        config.return_value = self.test_config
        self.test_config.set('run', True)
        self.test_config.set('ssl_ca', base64.b64encode(b'foobar'))

        self.test_config.set('custom_keyring', 'dGVzdAo=')
        hooks.config_changed()
        write_file.assert_called_with(hooks.CUSTOM_KEYRING_PATH, b'test\n')
        os_remove.assert_not_called()

    @mock.patch.object(hooks, 'update_nrpe_config')
    @mock.patch('os.path.exists')
    @mock.patch('os.remove')
    @mock.patch('glob.glob')
    @mock.patch('charmhelpers.core.hookenv.config')
    @mock.patch('charmhelpers.core.hookenv.relations_of_type')
    @mock.patch('charmhelpers.contrib.charmsupport.nrpe.get_nagios_hostname')
    @mock.patch('charmhelpers.contrib.charmsupport.nrpe.config')
    @mock.patch('charmhelpers.contrib.charmsupport.nrpe.local_unit')
    def test_uninstall_cron(self, local_unit, nrpe_config, nag_host,
                            relations_of_type, config, glob, remove, exists,
                            update_nrpe_config):
        local_unit.return_value = 'juju/0'
        nag_host.return_value = "nagios_hostname"
        nrpe_config.return_value = self.test_config

        self.test_config.set('run', False)
        setattr(self.test_config, "changed", lambda x: True)
        config.return_value = self.test_config
        glob.return_value = [os.path.join('/etc/cron.daily/',
                                          hooks.CRON_JOB_FILENAME)]
        exists.return_value = True
        hooks.config_changed()

        self.mock_configs.write_all.assert_called_once_with()

        remove.assert_any_call(os.path.join('/etc/cron.daily/',
                                            hooks.CRON_JOB_FILENAME))
        remove.assert_any_call(hooks.CRON_POLL_FILEPATH)
        update_nrpe_config.assert_called()

    @mock.patch("charmhelpers.core.hookenv.resource_get")
    @mock.patch("os.stat")
    def test_resource_get_simplestreams(self, mock_os_stat, mock_resource_get):
        mock_path = "/tmp/file"
        mock_resource_get.return_value = mock_path
        mock_os_stat.return_value.st_size = 100
        self.assertEqual(hooks._resource_get('simplestreams'), mock_path)

        mock_os_stat.return_value.st_size = 0
        self.assertEqual(hooks._resource_get('simplestreams'), None)
