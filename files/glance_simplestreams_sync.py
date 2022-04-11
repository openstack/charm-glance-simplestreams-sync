#!/usr/bin/env python3
#
# Copyright 2014 Canonical Ltd.
#
# This file is part of the glance-simplestreams sync charm.

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

# This script runs as a cron job installed by the
# glance-simplestreams-sync juju charm.  It reads config files that
# are written by the hooks of that charm based on its config and
# juju relation to keystone. However, it does not execute in a
# juju hook context itself.

import atexit
import base64
import copy
import fcntl
import itertools
import logging
import os
import re
import shutil
import six
import subprocess
import sys
import tempfile
import time
import yaml

from keystoneclient import exceptions as keystone_exceptions
from keystoneclient.v2_0 import client as keystone_client
from keystoneclient.v3 import client as keystone_v3_client
if six.PY3:
    from urllib import parse as urlparse
else:
    import urlparse


def setup_file_logging():
    logfilename = '/var/log/glance-simplestreams-sync.log'

    if not os.path.exists(logfilename):
        open(logfilename, 'a').close()

    os.chmod(logfilename, 0o640)

    h = logging.FileHandler(logfilename)
    h.setFormatter(logging.Formatter(
        '%(levelname)-9s * %(asctime)s [PID:%(process)d] * %(name)s * '
        '%(message)s',
        datefmt='%m-%d %H:%M:%S'))

    logger = logging.getLogger()
    logger.setLevel('DEBUG')
    logger.addHandler(h)


log = logging.getLogger()

KEYRING = '/usr/share/keyrings/ubuntu-cloudimage-keyring.gpg'
CONF_FILE_DIR = '/etc/glance-simplestreams-sync'
PID_FILE_DIR = '/var/run'
CHARM_CONF_FILE_NAME = os.path.join(CONF_FILE_DIR, 'mirrors.yaml')
ID_CONF_FILE_NAME = os.path.join(CONF_FILE_DIR, 'identity.yaml')

SYNC_RUNNING_FLAG_FILE_NAME = os.path.join(PID_FILE_DIR,
                                           'glance-simplestreams-sync.pid')

# juju looks in simplestreams/data/* in swift to figure out which
# images to deploy, so this path isn't really configurable even though
# it is.
SWIFT_DATA_DIR = 'simplestreams/data'

# When running local apache for product-streams use path to place indexes.
APACHE_DATA_DIR = '/var/www/html'

PRODUCT_STREAMS_SERVICE_NAME = 'image-stream'
PRODUCT_STREAMS_SERVICE_TYPE = 'product-streams'
PRODUCT_STREAMS_SERVICE_DESC = 'Ubuntu Product Streams'

CRON_POLL_FILENAME = '/etc/cron.d/glance_simplestreams_sync_fastpoll'

SSTREAM_SNAP_COMMON = '/var/snap/simplestreams/common'
SSTREAM_LOG_FILE = os.path.join(SSTREAM_SNAP_COMMON,
                                'sstream-mirror-glance.log')

CACERT_FILE = os.path.join(SSTREAM_SNAP_COMMON, 'cacert.pem')
SYSTEM_CACERT_FILE = '/etc/ssl/certs/ca-certificates.crt'

ENDPOINT_TYPES = [
    'publicURL',
    'adminURL',
    'internalURL',
]

# TODOs:
#   - allow people to specify their own policy, since they can specify
#     their own mirrors.
#   - potentially allow people to specify backup mirrors?
#   - debug keyring support
#   - figure out what content_id is and whether we should allow users to
#     set it


def read_conf(filename):
    with open(filename) as f:
        confobj = yaml.load(f)
    return confobj


def redact_keys(data_dict, key_list=None):
    """Return a dict with top-level keys having redacted values."""
    if not key_list:
        key_list = [
            'admin',
            'password',
            'rabbit_password',
            'admin_password',
        ]

    _data = copy.deepcopy(data_dict)
    for _key in key_list:
        if _key in _data.keys():
            _data[_key] = '<redacted>'
    return _data


def get_conf():
    conf_files = [ID_CONF_FILE_NAME, CHARM_CONF_FILE_NAME]
    for conf_file_name in conf_files:
        if not os.path.exists(conf_file_name):
            log.info("{} does not exist, exiting.".format(conf_file_name))
            sys.exit(1)

    try:
        id_conf = read_conf(ID_CONF_FILE_NAME)
    except Exception as e:
        msg = ("Error in {} configuration file."
               "Check juju config values for errors."
               "Exception: {}").format(ID_CONF_FILE_NAME, e)
        status_set('blocked', msg)
        log.info(msg)
        sys.exit(1)
    if None in id_conf.values():
        log.info("Configuration value missing in {}:\n"
                 "{}".format(ID_CONF_FILE_NAME, redact_keys(id_conf)))
        sys.exit(1)

    try:
        charm_conf = read_conf(CHARM_CONF_FILE_NAME)
    except Exception as e:
        charm_conf = {}
        msg = ("Error in {} configuration file. "
               "Check juju config values for errors"
               "Exception: {}").format(ID_CONF_FILE_NAME, e)
        status_set('blocked', msg)
        log.info(msg)
        sys.exit(1)
    if None in charm_conf.values():
        log.info("Configuration value missing in {}:\n"
                 "{}".format(CHARM_CONF_FILE_NAME, redact_keys(charm_conf)))
        sys.exit(1)

    return id_conf, charm_conf


def get_keystone_client(api_version):
    if api_version == 3:
        ksc_vars = dict(
            auth_url=os.environ['OS_AUTH_URL'],
            username=os.environ['OS_USERNAME'],
            password=os.environ['OS_PASSWORD'],
            user_domain_name=os.environ['OS_USER_DOMAIN_NAME'],
            project_domain_name=os.environ['OS_PROJECT_DOMAIN_NAME'],
            project_name=os.environ['OS_PROJECT_NAME'],
            project_id=os.environ['OS_PROJECT_ID'])
        ksc_class = keystone_v3_client.Client
    else:
        ksc_vars = dict(
            username=os.environ['OS_USERNAME'],
            password=os.environ['OS_PASSWORD'],
            tenant_id=os.environ['OS_TENANT_ID'],
            tenant_name=os.environ['OS_TENANT_NAME'],
            auth_url=os.environ['OS_AUTH_URL'])
        ksc_class = keystone_client.Client
    os_cacert = os.environ.get('OS_CACERT', None)
    if (os.environ['OS_AUTH_URL'].startswith('https') and
            os_cacert is not None):
        ksc_vars['cacert'] = os_cacert
    return ksc_class(**ksc_vars)


def set_openstack_env(id_conf, charm_conf):
    version = 'v3' if str(id_conf['api_version']).startswith('3') else 'v2.0'
    if id_conf.get('interface') == 'internal':
        host = id_conf['internal_host']
        port = id_conf['internal_port']
        protocol = id_conf['internal_protocol']
    else:
        host = id_conf['service_host']
        port = id_conf['service_port']
        protocol = id_conf['service_protocol']

    auth_url = ("{protocol}://{host}:{port}/{version}"
                .format(protocol=protocol, host=host,
                        port=port, version=version))
    os.environ['OS_AUTH_URL'] = auth_url
    os.environ['OS_USERNAME'] = id_conf['admin_user']
    os.environ['OS_PASSWORD'] = id_conf['admin_password']
    os.environ['OS_REGION_NAME'] = charm_conf['region']
    ssl_ca = id_conf.get('ssl_ca', None)
    if protocol == 'https' and ssl_ca is not None:
        os.environ['OS_CACERT'] = CACERT_FILE
        with open(CACERT_FILE, "wb") as f:
            f.write(base64.b64decode(ssl_ca))
    if version == 'v3':
        # Keystone charm puts all service users in the default domain.
        # Even so, it would be better if keystone passed this information
        # down the relation.
        os.environ['OS_USER_DOMAIN_NAME'] = id_conf['admin_domain_name']
        os.environ['OS_PROJECT_ID'] = id_conf['admin_tenant_id']
        os.environ['OS_PROJECT_NAME'] = id_conf['admin_tenant_name']
        os.environ['OS_PROJECT_DOMAIN_NAME'] = id_conf['admin_domain_name']
        if 'cacert' in id_conf.keys():
            os.environ['OS_CACERT'] = id_conf['cacert']
        if 'interface' in id_conf.keys():
            os.environ['OS_INTERFACE'] = id_conf['interface']
            os.environ['OS_ENDPOINT_TYPE'] = id_conf['interface']
    else:
        os.environ['OS_TENANT_ID'] = id_conf['admin_tenant_id']
        os.environ['OS_TENANT_NAME'] = id_conf['admin_tenant_name']


def do_sync(ksc, charm_conf):

    # NOTE(beisner): the user_agent variable was an unused assignment (lint).
    # It may be worth re-visiting its usage, intent and benefit with the
    # UrlMirrorReader call below at some point.  Leaving it disabled for now,
    # and not assigning it since it is not currently utilized.
    # user_agent = charm_conf.get("user_agent")

    region_name = charm_conf['region']

    for mirror_info in charm_conf['mirror_list']:
        # NOTE: output directory must be under HOME
        #       or snap cannot access it for stream files
        tmpdir = tempfile.mkdtemp(dir=os.environ['HOME'])
        try:
            log.info("Configuring sync for url {}".format(mirror_info))
            content_id = charm_conf['content_id_template'].format(
                region=region_name)

            sync_command = [
                "/snap/bin/simplestreams.sstream-mirror-glance",
                "-vv",
                "--keep",
                "--max", str(mirror_info['max']),
                "--content-id", content_id,
                "--cloud-name", charm_conf['cloud_name'],
                "--path", mirror_info['path'],
                "--name-prefix", charm_conf['name_prefix'],
                "--keyring", KEYRING,
                "--log-file", SSTREAM_LOG_FILE,
            ]

            if charm_conf['use_swift']:
                sync_command += [
                    '--output-swift',
                    "{}/".format(SWIFT_DATA_DIR)
                ]
            else:
                # For debugging purposes only.
                sync_command += [
                    "--output-dir",
                    tmpdir
                ]

            if charm_conf.get('hypervisor_mapping', False):
                sync_command += [
                    '--hypervisor-mapping'
                ]
            if charm_conf.get('custom_properties'):
                custom_properties = charm_conf.get('custom_properties').split()
                for custom_property in custom_properties:
                    sync_command += [
                        '--custom-property',
                        custom_property
                    ]

            sync_command += [
                mirror_info['url'],
            ]
            sync_command += mirror_info['item_filters']

            # Pass the current process' environment down along with proxy
            # settings crafted for sstream-mirror-glance.
            sstream_mirror_env = os.environ.copy()
            sstream_mirror_env.update(get_sstream_mirror_proxy_env(
                ksc, region_name,
                charm_conf['ignore_proxy_for_object_store'],
            ))

            log.info("calling sstream-mirror-glance")
            log.debug("command: %s", " ".join(sync_command))
            log.debug("sstream-mirror environment: %s", sstream_mirror_env)
            subprocess.check_call(sync_command, env=sstream_mirror_env)
        finally:
            shutil.rmtree(tmpdir)


def get_sstream_mirror_proxy_env(ksc, region_name,
                                 ignore_proxy_for_object_store=True):
    '''Get proxy settings to be passed to sstreams-mirror-glance.

    sstream-mirror-glance has multiple endpoints it needs to connect to:

    1. Upstream image mirror (typically, an endpoint in public Internet);
    2. Keystone (typically, a directly reachable endpoint);
    3. Object storage (Swift) (typically a directly reachable endpoint).
    4. Glance (typically, a directly reachable endpoint).

    In a restricted environment where proxy settings have to be used for public
    Internet connectivity we need to be explicit about hosts for which proxy
    settings need to be used by sstream-mirror-glance. This function
    dynamically builds a list of endpoints that need to be added to NO_PROXY
    and optionally allows not including object storage endpoints into the
    NO_PROXY list.

    :param ksc: An instance of a Keystone client.
    :type ksc: :class: `keystoneclient.v3.client.Client`
    :param str region_name: A name of the region to retrieve endpoints for.
    :param bool ignore_proxy_for_object_store: Do not include object-store
                                               endpoints into NO_PROXY.
    '''
    proxy_settings = juju_proxy_settings()
    if proxy_settings is None:
        proxy_settings = {}
        no_proxy_set = set()
    else:
        no_proxy_set = set(proxy_settings.get('NO_PROXY').split(','))
    additional_hosts = set([
        urlparse.urlparse(u).hostname for u in itertools.chain(
            get_service_endpoints(ksc, 'identity', region_name).values(),
            get_service_endpoints(ksc, 'image', region_name).values(),
            get_object_store_endpoints(ksc, region_name)
            if ignore_proxy_for_object_store else [],
        )])
    no_proxy = ','.join(no_proxy_set | additional_hosts)
    proxy_settings['NO_PROXY'] = no_proxy
    proxy_settings['no_proxy'] = no_proxy
    return proxy_settings


def update_product_streams_service(ksc, services, region):
    """Updates URLs of product-streams endpoint to point to swift URLs."""
    object_store_endpoints = get_service_endpoints(ksc, 'object-store', region)
    for endpoint_type in ENDPOINT_TYPES:
        object_store_endpoints[endpoint_type] += "/{}".format(SWIFT_DATA_DIR)

    publicURL, internalURL, adminURL = (object_store_endpoints[t]
                                        for t in ENDPOINT_TYPES)
    # Update the relation to keystone to update the catalog URLs
    update_endpoint_urls(
        region,
        publicURL,
        internalURL,
        adminURL,
    )


def get_object_store_endpoints(ksc, region_name):
    """Get object-store endpoints from the service catalog.

    The lack of those endpoints is not fatal for the purposes of this script
    since a deployment may not have those in which case glance would still
    be populated with images but metadata would not have a place to be stored.

    :param ksc: An instance of a Keystone client.
    :type ksc: :class: `keystoneclient.v3.client.Client`
    :param str region_name: A name of the region to retrieve endpoints for.
    """
    endpoints = []
    try:
        endpoints = list(get_service_endpoints(ksc, 'object-store',
                                               region_name).values())
    except keystone_exceptions.EndpointNotFound:
        log.debug('object-store endpoints are not present')
    return endpoints


def get_service_endpoints(ksc, service_type, region_name):
    """Get endpoints for a given service type from the Keystone catalog.

    :param ksc: An instance of a Keystone client.
    :type ksc: :class: `keystoneclient.v3.client.Client`
    :param str service_type: An endpoint service type to use.
    :param str region_name: A name of the region to retrieve endpoints for.
    :raises :class: `keystone_exceptions.EndpointNotFound`
    """
    try:
        catalog = {
            endpoint_type: ksc.service_catalog.url_for(
                service_type=service_type, endpoint_type=endpoint_type,
                region_name=region_name)
            for endpoint_type in ['publicURL', 'internalURL', 'adminURL']}
    except keystone_exceptions.EndpointNotFound:
        # EndpointNotFound is raised for the case where a service does not
        # exist as well as for the case where the service exists but not
        # endpoints.
        log.error('could not retrieve any {} endpoints'.format(service_type))
        raise
    return catalog


def juju_proxy_settings():
    """Get proxy settings from Juju environment.

    Get charm proxy settings from environment variables that correspond to
    juju-http-proxy, juju-https-proxy juju-no-proxy (available as of 2.4.2, see
    lp:1782236) or the legacy unprefixed settings.

    :rtype: None | dict[str, str]
    """
    # Get proxy settings from the environment variables set by Juju.
    juju_settings = {
        m.groupdict()['var']: m.groupdict()['val']
        for m in re.finditer(
            '^((JUJU_CHARM_)?(?P<var>(HTTP|HTTPS|NO)_PROXY))=(?P<val>.*)$',
            juju_run_cmd(['env']), re.MULTILINE)
    }

    proxy_settings = {}
    for var in ['HTTP_PROXY', 'HTTPS_PROXY', 'NO_PROXY']:
        var_val = juju_settings.get(var)
        if var_val:
            proxy_settings[var] = var_val
            proxy_settings[var.lower()] = var_val
    return proxy_settings if proxy_settings else None


def juju_run_cmd(cmd):
    '''Execute the passed commands under the local unit context if required'''
    # NOTE: determine whether juju-run is actually required
    #       supporting execution via actions.
    if not os.environ.get('JUJU_CONTEXT_ID'):
        id_conf, _ = get_conf()
        unit_name = id_conf['unit_name']
        _cmd = ['juju-run', unit_name, ' '.join(cmd)]
    else:
        _cmd = cmd
    log.info("Executing command: {}".format(_cmd))
    out = subprocess.check_output(_cmd)
    if six.PY3:
        out = out.decode('utf-8')
    return out


def status_set(status, message):
    try:
        # NOTE: format of message is different for out of
        #       context execution.
        if not os.environ.get('JUJU_CONTEXT_ID'):
            juju_run_cmd(['status-set', status,
                          '"{}"'.format(message)])
        else:
            subprocess.check_output([
                'status-set',
                status,
                message
            ])
    except subprocess.CalledProcessError:
        log.info(message)


def update_endpoint_urls(region, publicurl, adminurl, internalurl):
    # Notify keystone via the identity service relation about
    # any endpoint changes.
    for rid in juju_run_cmd(['relation-ids', 'identity-service']).split():
        log.info("Updating relation data for: {}".format(rid))
        _cmd = ['relation-set', '-r', rid]
        relation_data = {
            'service': 'image-stream',
            'region': region,
            'public_url': publicurl,
            'admin_url': adminurl,
            'internal_url': internalurl
        }
        for k, v in relation_data.items():
            _cmd.append('{}={}'.format(k, v))
        juju_run_cmd(_cmd)


def cleanup():
    try:
        os.unlink(SYNC_RUNNING_FLAG_FILE_NAME)
    except OSError as e:
        if e.errno != 2:
            raise e


def set_active_status(is_object_store_present_and_used):
    """Get object-store endpoints from the service catalog.

    The lack of those endpoints is not fatal for the purposes of this script
    since a deployment may not have those in which case glance would still
    be populated with images but metadata would not have a place to be stored.
    """
    ts = time.strftime("%x %X")
    # "Unit is ready" is one of approved message prefixes
    # Prefix the message with it will help zaza to understand the status.
    if is_object_store_present_and_used:
        status_set('active', 'Unit is ready (Glance sync completed at {},'
                   ' metadata uploaded to object store)'.format(ts))
    else:
        status_set('active', 'Unit is ready (Glance sync completed at {},'
                   ' metadata not uploaded - object-store usage disabled)'
                   ''.format(ts))


def assess_object_store_state(object_store_exists, object_store_requested):
    """Decide whether object store"""
    if object_store_requested and not object_store_exists:
        # If use_swift is set, we need to wait for swift to become
        # available.
        msg = ('Swift usage has been requested but'
               ' its endpoints are not yet in the catalog')
        status_set('maintenance', msg)
        log.info(msg)
        return False
    return True


def is_object_store_present(ksc, region_name):
    """Checks whether object store is present (service & endpoints)."""
    return len(get_object_store_endpoints(ksc, region_name)) > 0


def main():

    log.info("glance-simplestreams-sync started.")

    lockfile = open(SYNC_RUNNING_FLAG_FILE_NAME, 'w')

    try:
        fcntl.flock(lockfile, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except IOError:
        log.info("{} is locked, exiting".format(SYNC_RUNNING_FLAG_FILE_NAME))
        sys.exit(0)

    returncode = 0
    atexit.register(cleanup)
    lockfile.write(str(os.getpid()))

    id_conf, charm_conf = get_conf()

    set_openstack_env(id_conf, charm_conf)

    region_name = charm_conf['region']
    ksc = get_keystone_client(id_conf['api_version'])
    services = [s._info for s in ksc.services.list()]
    servicenames = [s['name'] for s in services]
    ps_service_exists = PRODUCT_STREAMS_SERVICE_NAME in servicenames

    object_store_present = is_object_store_present(ksc, region_name)

    use_swift = charm_conf['use_swift']
    log.info("ps_service_exists={}, charm_conf['use_swift']={}"
             ", object_store_present={}".format(ps_service_exists,
                                                use_swift,
                                                object_store_present))

    try:
        if not assess_object_store_state(object_store_present, use_swift):
            return

        is_object_store_present_and_used = use_swift and object_store_present
        if ps_service_exists and is_object_store_present_and_used:
            log.info("Updating product streams service.")
            update_product_streams_service(ksc, services, region_name)
        else:
            log.info("Not updating product streams service.")

        log.info("Beginning image sync")
        status_set('maintenance', 'Synchronising images')

        do_sync(ksc, charm_conf)
        set_active_status(is_object_store_present_and_used)

        # If this is an initial per-minute sync attempt, delete it on success.
        if os.path.exists(CRON_POLL_FILENAME):
            os.unlink(CRON_POLL_FILENAME)
            log.info(
                "Initial sync attempt done: every-minute cronjob removed.")

    except keystone_exceptions.EndpointNotFound as e:
        # matching string "{PublicURL} endpoint for {type}{region} not
        # found".  where {type} is 'image' and {region} is potentially
        # not empty so we only match on this substring:
        if 'endpoint for image' in e.message:
            log.info("Glance endpoint not found, will continue polling.")
            returncode = os.EX_UNAVAILABLE
    except subprocess.CalledProcessError as e:
        returncode = e.returncode
        log.exception("Exception during syncing:")
        status_set('blocked', 'Image sync failed, retrying soon.')

    log.info("sync done.")
    return returncode


if __name__ == "__main__":
    setup_file_logging()
    sys.exit(main())
