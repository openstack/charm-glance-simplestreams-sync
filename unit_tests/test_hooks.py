import mock
import os
import shutil
import tempfile
import yaml

from hooks import hooks
from test_utils import CharmTestCase


TO_PATCH = [
    'apt_update',
    'apt_install',
    'get_release',
]


class TestConfigChanged(CharmTestCase):
    def setUp(self):
        CharmTestCase.setUp(self, hooks, TO_PATCH)
        self.tmpdir = tempfile.mkdtemp()
        mirrors_fname = os.path.basename(hooks.MIRRORS_CONF_FILE_NAME)
        self.mirrors_conf_fpath = os.path.join(self.tmpdir, mirrors_fname)
        hooks.MIRRORS_CONF_FILE_NAME = self.mirrors_conf_fpath
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

    def tearDown(self):
        CharmTestCase.tearDown(self)
        shutil.rmtree(self.tmpdir)
        shutil.rmtree(self.tmpcrond)
        shutil.rmtree(self.sharedir)

    @mock.patch('os.symlink')
    @mock.patch('hooks.charmhelpers.core.hookenv.config')
    @mock.patch('hooks.charmhelpers.core.hookenv.relations_of_type')
    @mock.patch('hooks.charmhelpers.contrib.charmsupport.nrpe'
                '.get_nagios_hostname')
    @mock.patch('hooks.charmhelpers.contrib.charmsupport.nrpe.config')
    @mock.patch('hooks.charmhelpers.contrib.charmsupport.nrpe.local_unit')
    def test_default_config(self, local_unit, nrpe_config, nag_host,
                            relations_of_type, config, symlink):
        local_unit.return_value = 'juju/0'
        nag_host.return_value = "nagios_hostname"
        nrpe_config.return_value = self.test_config

        setattr(self.test_config, "changed", lambda x: False)
        config.return_value = self.test_config
        hooks.config_changed()

        symlink.assert_any_call(os.path.join(self.sharedir,
                                             hooks.SCRIPT_WRAPPER_NAME),
                                '/etc/cron.%s/%s'
                                % (self.test_config['frequency'],
                                   hooks.CRON_JOB_FILENAME))
        self.assertTrue(os.path.isfile(os.path.join(self.tmpcrond,
                                                    hooks.CRON_POLL_FILENAME)))
        self.assertTrue(os.path.isfile(self.mirrors_conf_fpath))
        with open(self.mirrors_conf_fpath, 'r') as f:
            mirrors = yaml.safe_load(f)

        for k in ['cloud_name', 'region', 'use_swift']:
            self.assertEqual(self.test_config[k], mirrors[k])

        mirror_list = yaml.safe_load(self.test_config['mirror_list'])
        self.assertEqual(mirrors['mirror_list'], mirror_list)

    @mock.patch('os.path.exists')
    @mock.patch('os.remove')
    @mock.patch('glob.glob')
    @mock.patch('hooks.charmhelpers.core.hookenv.config')
    @mock.patch('hooks.charmhelpers.core.hookenv.relations_of_type')
    @mock.patch('hooks.charmhelpers.contrib.charmsupport.nrpe'
                '.get_nagios_hostname')
    @mock.patch('hooks.charmhelpers.contrib.charmsupport.nrpe.config')
    @mock.patch('hooks.charmhelpers.contrib.charmsupport.nrpe.local_unit')
    def test_uninstall_cron(self, local_unit, nrpe_config, nag_host,
                            relations_of_type, config, glob, remove, exists):
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

        remove.assert_any_call(os.path.join('/etc/cron.daily/',
                                            hooks.CRON_JOB_FILENAME))
        remove.assert_any_call(hooks.CRON_POLL_FILEPATH)
