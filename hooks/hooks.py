#!/usr/bin/env python2.7
#
# Copyright 2014 Canonical Ltd. released under AGPL
#
# Authors:
#  Tycho Andersen <tycho.andersen@canonical.com>
#

# This file is part of the glance-simplestreams sync charm.
#
# The glance-simplestreams sync charm is free software: you can
# redistribute it and/or modify it under the terms of the GNU Affero General
# Public License as published by the Free Software Foundation, either
# version 3 of the License, or (at your option) any later version.
#
# The charm is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this charm.  If not, see <http://www.gnu.org/licenses/>.

import glob
import os
import sys
import shutil

from charmhelpers.fetch import add_source, apt_install, apt_update
from charmhelpers.core import hookenv
from charmhelpers.payload.execd import execd_preinstall

from charmhelpers.contrib.openstack.context import (AMQPContext,
                                                    IdentityServiceContext,
                                                    OSContextGenerator)
from charmhelpers.contrib.openstack.utils import get_os_codename_package
from charmhelpers.contrib.openstack.templating import OSConfigRenderer

from charmhelpers.contrib.charmsupport import nrpe

CONF_FILE_DIR = '/etc/glance-simplestreams-sync'
USR_SHARE_DIR = '/usr/share/glance-simplestreams-sync'

MIRRORS_CONF_FILE_NAME = os.path.join(CONF_FILE_DIR, 'mirrors.yaml')
ID_CONF_FILE_NAME = os.path.join(CONF_FILE_DIR, 'identity.yaml')

SYNC_SCRIPT_NAME = "glance-simplestreams-sync.py"
SCRIPT_WRAPPER_NAME = "glance-simplestreams-sync.sh"

CRON_D = '/etc/cron.d/'
CRON_JOB_FILENAME = 'glance_simplestreams_sync'
CRON_POLL_FILENAME = 'glance_simplestreams_sync_fastpoll'
CRON_POLL_FILEPATH = os.path.join(CRON_D, CRON_POLL_FILENAME)

ERR_FILE_EXISTS = 17

hooks = hookenv.Hooks()


class MultipleImageModifierSubordinatesIsNotSupported(Exception):
    """Raise this if multiple image-modifier subordinates are related to
    this charm.
    """


class UnitNameContext(OSContextGenerator):
    """Simple context to generate local unit_name"""
    def __call__(self):
        return {'unit_name': hookenv.local_unit()}


class MirrorsConfigServiceContext(OSContextGenerator):
    """Context for mirrors.yaml template.

    Uses image-modifier relation if available to set
    modify_hook_scripts config value.

    """
    interfaces = ['simplestreams-image-service']

    def __call__(self):
        hookenv.log("Generating template ctxt for simplestreams-image-service")
        config = hookenv.config()

        modify_hook_scripts = []
        image_modifiers = hookenv.relations_of_type('image-modifier')
        if len(image_modifiers) > 1:
            raise MultipleImageModifierSubordinatesIsNotSupported()

        if len(image_modifiers) == 1:
            im = image_modifiers[0]
            try:
                modify_hook_scripts.append(im['script-path'])

            except KeyError as ke:
                hookenv.log('relation {} yielded '
                            'exception {} - ignoring.'.format(repr(im),
                                                              repr(ke)))

        # default no-op so that None still means "missing" for config
        # validation (see elsewhere)
        if len(modify_hook_scripts) == 0:
            modify_hook_scripts.append('/bin/true')

        return dict(mirror_list=config['mirror_list'],
                    modify_hook_scripts=', '.join(modify_hook_scripts),
                    name_prefix=config['name_prefix'],
                    content_id_template=config['content_id_template'],
                    use_swift=config['use_swift'],
                    region=config['region'],
                    cloud_name=config['cloud_name'],
                    user_agent=config['user_agent'],
                    hypervisor_mapping=config['hypervisor_mapping'])

def ensure_perms():
    """Ensure gss file permissions."""
    os.chmod(ID_CONF_FILE_NAME, 0o640)
    os.chmod(MIRRORS_CONF_FILE_NAME, 0o640)

def get_release():
    return get_os_codename_package('glance-common', fatal=False) or 'icehouse'


def get_configs():
    configs = OSConfigRenderer(templates_dir='templates/',
                               openstack_release=get_release())

    configs.register(MIRRORS_CONF_FILE_NAME, [MirrorsConfigServiceContext()])
    configs.register(ID_CONF_FILE_NAME, [IdentityServiceContext(),
                                         AMQPContext(),
                                         UnitNameContext()])
    return configs

def install_cron_script():
    """Installs cron job in /etc/cron.$frequency/ for repeating sync

    Script is not a template but we always overwrite, to ensure it is
    up-to-date.

    """
    for fn in [SYNC_SCRIPT_NAME, SCRIPT_WRAPPER_NAME]:
        shutil.copy(os.path.join("scripts", fn), USR_SHARE_DIR)

    config = hookenv.config()
    installed_script = os.path.join(USR_SHARE_DIR, SCRIPT_WRAPPER_NAME)
    linkname = '/etc/cron.{f}/{s}'.format(f=config['frequency'],
                                          s=CRON_JOB_FILENAME)
    try:
        hookenv.log("Creating symlink: %s -> %s" % (installed_script,
                                                    linkname))
        os.symlink(installed_script, linkname)
    except OSError as ex:
        if ex.errno == ERR_FILE_EXISTS:
            hookenv.log('symlink %s already exists' % linkname,
                        level=hookenv.INFO)
        else:
            raise ex


def install_cron_poll():
    "Installs /etc/cron.d every-minute job in crontab for quick polling."
    poll_file_source = os.path.join('scripts', CRON_POLL_FILENAME)
    shutil.copy(poll_file_source, CRON_D)


def uninstall_cron_script():
    "Removes sync program from any cron place it might be"
    for fn in glob.glob("/etc/cron.*/" + CRON_JOB_FILENAME):
        if os.path.exists(fn):
            os.remove(fn)


def uninstall_cron_poll():
    "Removes cron poll"
    if os.path.exists(CRON_POLL_FILEPATH):
        os.remove(CRON_POLL_FILEPATH)


@hooks.hook('identity-service-relation-joined')
def identity_service_joined(relation_id=None):
    config = hookenv.config()

    # Generate temporary bogus service URL to make keystone charm
    # happy. The sync script will replace it with the endpoint for
    # swift, because when this hook is fired, we do not yet
    # necessarily know the swift endpoint URL (it might not even exist
    # yet).

    url = 'http://' + hookenv.unit_get('private-address')
    relation_data = {
        'service': 'image-stream',
        'region': config['region'],
        'public_url': url,
        'admin_url': url,
        'internal_url': url}

    hookenv.relation_set(relation_id=relation_id, **relation_data)







@hooks.hook('identity-service-relation-changed')
def identity_service_changed():
    configs = get_configs()
    configs.write(ID_CONF_FILE_NAME)
    ensure_perms()

@hooks.hook('install.real')
def install():
    execd_preinstall()
    add_source(hookenv.config('source'), hookenv.config('key'))
    for directory in [CONF_FILE_DIR, USR_SHARE_DIR]:
        hookenv.log("creating config dir at {}".format(directory))
        if not os.path.isdir(directory):
            if os.path.exists(directory):
                hookenv.log("error: {} exists but is not a directory."
                            " exiting.".format(directory))
                return
            os.mkdir(directory)

    apt_update(fatal=True)

    apt_install(packages=['python-simplestreams', 'python-glanceclient',
                          'python-yaml', 'python-keystoneclient',
                          'python-kombu',
                          'python-swiftclient', 'ubuntu-cloudimage-keyring'])

    hookenv.log('end install hook.')


@hooks.hook('config-changed',
            'image-modifier-relation-changed',
            'image-modifier-relation-joined')
def config_changed():
    hookenv.log('begin config-changed hook.')
    configs = get_configs()
    configs.write(MIRRORS_CONF_FILE_NAME)
    ensure_perms()

    update_nrpe_config()

    config = hookenv.config()

    if config.changed('frequency'):
        hookenv.log("'frequency' changed, removing cron job")
        uninstall_cron_script()

    if config['run']:
        hookenv.log("installing to cronjob to "
                    "/etc/cron.{}".format(config['frequency']))
        hookenv.log("installing {} for polling".format(CRON_POLL_FILEPATH))
        install_cron_poll()
        install_cron_script()
    else:
        hookenv.log("'run' set to False, removing cron jobs")
        uninstall_cron_script()
        uninstall_cron_poll()

    config.save()


@hooks.hook('upgrade-charm')
def upgrade_charm():
    install()
    update_nrpe_config()
    configs = get_configs()
    configs.write_all()
    ensure_perms()


@hooks.hook('amqp-relation-joined')
def amqp_joined():
    conf = hookenv.config()
    hookenv.relation_set(username=conf['rabbit-user'],
                         vhost=conf['rabbit-vhost'])


@hooks.hook('amqp-relation-changed')
def amqp_changed():
    configs = get_configs()
    if 'amqp' not in configs.complete_contexts():
        hookenv.log('amqp relation incomplete. Peer not ready?')
        return
    configs.write(ID_CONF_FILE_NAME)


@hooks.hook('nrpe-external-master-relation-joined',
            'nrpe-external-master-relation-changed')
def update_nrpe_config():
    hostname = nrpe.get_nagios_hostname()
    nrpe_setup = nrpe.NRPE(hostname=hostname)
    nrpe_setup.write()


if __name__ == '__main__':
    try:
        hooks.execute(sys.argv)
    except hookenv.UnregisteredHookError as e:
        hookenv.log('Unknown hook {} - skipping.'.format(e))
