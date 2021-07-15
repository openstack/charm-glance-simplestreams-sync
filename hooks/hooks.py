#!/usr/bin/env python3
#
# Copyright 2018 Canonical Ltd
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

import base64
import glob
import os
import shutil
import sys


_path = os.path.dirname(os.path.realpath(__file__))
_root = os.path.abspath(os.path.join(_path, '..'))


def _add_path(path):
    if path not in sys.path:
        sys.path.insert(1, path)


_add_path(_root)

from charmhelpers.fetch import add_source, apt_install, apt_update
from charmhelpers.fetch.snap import snap_install
from charmhelpers.core import hookenv
from charmhelpers.payload.execd import execd_preinstall

from charmhelpers.contrib.openstack.context import (IdentityServiceContext,
                                                    OSContextGenerator)
from charmhelpers.contrib.openstack.utils import (
    get_os_codename_package,
    clear_unit_paused,
    clear_unit_upgrading,
    set_unit_paused,
    set_unit_upgrading,
)

from charmhelpers.contrib.openstack.templating import OSConfigRenderer

from charmhelpers.contrib.charmsupport import nrpe

from charmhelpers.contrib.openstack.cert_utils import (
    get_certificate_request,
    process_certificates,
)

from charmhelpers.core.host import (
    CompareHostReleases,
    lsb_release,
    install_ca_cert,
)

CONF_FILE_DIR = '/etc/glance-simplestreams-sync'
USR_SHARE_DIR = '/usr/share/glance-simplestreams-sync'

MIRRORS_CONF_FILE_NAME = os.path.join(CONF_FILE_DIR, 'mirrors.yaml')
ID_CONF_FILE_NAME = os.path.join(CONF_FILE_DIR, 'identity.yaml')

SYNC_SCRIPT_NAME = "glance_simplestreams_sync.py"
SCRIPT_WRAPPER_NAME = "glance-simplestreams-sync.sh"

CRON_D = '/etc/cron.d/'
CRON_JOB_FILENAME = 'glance_simplestreams_sync'
CRON_POLL_FILENAME = 'glance_simplestreams_sync_fastpoll'
CRON_POLL_FILEPATH = os.path.join(CRON_D, CRON_POLL_FILENAME)

ERR_FILE_EXISTS = 17

PACKAGES = ['python-glanceclient',
            'python-yaml', 'python-keystoneclient',
            'python-swiftclient', 'ubuntu-cloudimage-keyring', 'snapd']

PY3_PACKAGES = ['python3-glanceclient',
                'python3-yaml', 'python3-keystoneclient',
                'python3-swiftclient']

KEYSTONE_CA_CERT = "/usr/local/share/ca-certificates/keystone_juju_ca_cert.crt"
VAULT_CA_CERT = "/usr/local/share/ca-certificates/vault_juju_ca_cert.crt"

hooks = hookenv.Hooks()


class MultipleImageModifierSubordinatesIsNotSupported(Exception):
    """Raise this if multiple image-modifier subordinates are related to
    this charm.
    """


class UnitNameContext(OSContextGenerator):
    """Simple context to generate local unit_name"""
    def __call__(self):
        return {'unit_name': hookenv.local_unit()}


def get_ca_cert_file():
    """Return the cacert file from a relation if there is one.

    :returns: Path to certificate
    :rtype: str
    """
    if os.path.exists(VAULT_CA_CERT):
        return VAULT_CA_CERT
    if os.path.exists(KEYSTONE_CA_CERT):
        return KEYSTONE_CA_CERT
    return None


class SSLIdentityServiceContext(IdentityServiceContext):
    """Modify the IdentityServiceContext to includea an SSL option.

    This is just a simple way of getting the CA to the
    glance-simplestreams-sync.py script.
    """
    def __call__(self):
        ctxt = super(SSLIdentityServiceContext, self).__call__()
        ssl_ca = hookenv.config('ssl_ca')
        relation_ca_cert_file = get_ca_cert_file()
        if ctxt:
            if ssl_ca:
                ctxt['ssl_ca'] = ssl_ca
            elif relation_ca_cert_file:
                with open(relation_ca_cert_file, 'rb') as ca_cert:
                    ctxt['ssl_ca'] = base64.b64encode(ca_cert.read()).decode()
        return ctxt


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
                    ignore_proxy_for_object_store=config[
                        'ignore_proxy_for_object_store'],
                    region=config['region'],
                    cloud_name=config['cloud_name'],
                    user_agent=config['user_agent'],
                    custom_properties=config['custom_properties'],
                    hypervisor_mapping=config['hypervisor_mapping'])


def ensure_perms():
    """Ensure gss file permissions."""
    if os.path.isfile(ID_CONF_FILE_NAME):
        os.chmod(ID_CONF_FILE_NAME, 0o640)

    if os.path.isfile(MIRRORS_CONF_FILE_NAME,):
        os.chmod(MIRRORS_CONF_FILE_NAME, 0o640)


def get_release():
    return get_os_codename_package('glance-common', fatal=False) or 'icehouse'


def get_configs():
    configs = OSConfigRenderer(templates_dir='templates/',
                               openstack_release=get_release())

    configs.register(MIRRORS_CONF_FILE_NAME, [MirrorsConfigServiceContext()])
    configs.register(ID_CONF_FILE_NAME, [SSLIdentityServiceContext(),
                                         UnitNameContext()])
    return configs


def install_gss_wrappers():
    """Installs wrapper scripts for execution of simplestreams sync."""
    for fn in [SYNC_SCRIPT_NAME, SCRIPT_WRAPPER_NAME]:
        shutil.copy(os.path.join("files", fn), USR_SHARE_DIR)


def install_cron_script():
    """Installs cron job in /etc/cron.$frequency/ for repeating sync

    Script is not a template but we always overwrite, to ensure it is
    up-to-date.

    """
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
    poll_file_source = os.path.join("files", CRON_POLL_FILENAME)
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

    _packages = PACKAGES
    if not hookenv.config("use_swift"):
        hookenv.log('Configuring for local hosting of product stream.')
        _packages += ["apache2"]

    if CompareHostReleases(lsb_release()['DISTRIB_CODENAME']) >= 'disco':
        _packages = [pkg for pkg in _packages if not pkg.startswith('python-')]
        _packages.extend(PY3_PACKAGES)

    apt_update(fatal=True)

    apt_install(_packages)

    snap_install('simplestreams',
                 *['--channel={}'.format(hookenv.config('snap-channel'))])

    install_gss_wrappers()

    hookenv.log('end install hook.')


@hooks.hook('config-changed',
            'image-modifier-relation-changed',
            'image-modifier-relation-joined')
def config_changed():
    hookenv.log('begin config-changed hook.')
    configs = get_configs()
    configs.write_all()
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

    if config.get('ssl_ca'):
        install_ca_cert(
            base64.b64decode(config.get('ssl_ca')),
        )

    config.save()


@hooks.hook('upgrade-charm')
def upgrade_charm():
    install()
    update_nrpe_config()
    configs = get_configs()
    configs.write_all()
    ensure_perms()


@hooks.hook('nrpe-external-master-relation-joined',
            'nrpe-external-master-relation-changed')
def update_nrpe_config():
    hostname = nrpe.get_nagios_hostname()
    nrpe_setup = nrpe.NRPE(hostname=hostname)
    nrpe_setup.write()


@hooks.hook('pre-series-upgrade')
def pre_series_upgrade():
    hookenv.log("Running prepare series upgrade hook", "INFO")
    # NOTE: In order to indicate the step of the series upgrade process for
    # administrators and automated scripts, the charm sets the paused and
    # upgrading states.
    set_unit_paused()
    set_unit_upgrading()
    hookenv.status_set("blocked",
                       "Ready for do-release-upgrade and reboot. "
                       "Set complete when finished.")


@hooks.hook('post-series-upgrade')
def post_series_upgrade():
    hookenv.log("Running complete series upgrade hook", "INFO")
    # In order to indicate the step of the series upgrade process for
    # administrators and automated scripts, the charm clears the paused and
    # upgrading states.
    clear_unit_paused()
    clear_unit_upgrading()
    hookenv.status_set("active", "")


@hooks.hook('certificates-relation-joined')
def certs_joined(relation_id=None):
    hookenv.relation_set(
        relation_id=relation_id,
        relation_settings=get_certificate_request())


@hooks.hook('certificates-relation-changed')
def certs_changed(relation_id=None, unit=None):
    process_certificates('glance-simplestreams-sync', relation_id, unit)
    configs = get_configs()
    configs.write_all()
    identity_service_changed()


if __name__ == '__main__':
    try:
        hooks.execute(sys.argv)
    except hookenv.UnregisteredHookError as e:
        hookenv.log('Unknown hook {} - skipping.'.format(e))
