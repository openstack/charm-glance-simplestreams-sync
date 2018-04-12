#!/usr/bin/env python
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

import logging
import os


def setup_logging():
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

    return logger


log = setup_logging()


import atexit
import fcntl
from keystoneclient.v2_0 import client as keystone_client
from keystoneclient.v3 import client as keystone_v3_client
import keystoneclient.exceptions as keystone_exceptions
import kombu
from simplestreams.mirrors import glance, UrlMirrorReader
from simplestreams.objectstores.swift import SwiftObjectStore
from simplestreams.util import read_signed, path_from_mirror_url
import sys
import time
import traceback
import yaml
import subprocess

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
SWIFT_DATA_DIR = 'simplestreams/data/'

PRODUCT_STREAMS_SERVICE_NAME = 'image-stream'
PRODUCT_STREAMS_SERVICE_TYPE = 'product-streams'
PRODUCT_STREAMS_SERVICE_DESC = 'Ubuntu Product Streams'

CRON_POLL_FILENAME = '/etc/cron.d/glance_simplestreams_sync_fastpoll'

# TODOs:
#   - allow people to specify their own policy, since they can specify
#     their own mirrors.
#   - potentially allow people to specify backup mirrors?
#   - debug keyring support
#   - figure out what content_id is and whether we should allow users to
#     set it

try:
    from simplestreams.util import ProgressAggregator
    SIMPLESTREAMS_HAS_PROGRESS = True
except ImportError:
    class ProgressAggregator:
        "Dummy class to allow charm to load with old simplestreams"
    SIMPLESTREAMS_HAS_PROGRESS = False


class StatusMessageProgressAggregator(ProgressAggregator):
    def __init__(self, remaining_items, send_status_message):
        super(StatusMessageProgressAggregator, self).__init__(remaining_items)
        self.send_status_message = send_status_message

    def emit(self, progress):
        size = float(progress['size'])
        written = float(progress['written'])
        cur = self.total_image_count - len(self.remaining_items) + 1
        totpct = float(self.total_written) / self.total_size
        msg = "{name} {filepct:.0%}\n"\
              "({cur} of {tot} images) total: "\
              "{totpct:.0%}".format(name=progress['name'],
                                    filepct=(written / size),
                                    cur=cur,
                                    tot=self.total_image_count,
                                    totpct=totpct)
        self.send_status_message(dict(status="Syncing",
                                      message=msg))


def policy(content, path):
    if path.endswith('sjson'):
        return read_signed(content, keyring=KEYRING)
    else:
        return content


def read_conf(filename):
    with open(filename) as f:
        confobj = yaml.load(f)
    return confobj


def get_conf():
    conf_files = [ID_CONF_FILE_NAME, CHARM_CONF_FILE_NAME]
    for conf_file_name in conf_files:
        if not os.path.exists(conf_file_name):
            log.info("{} does not exist, exiting.".format(conf_file_name))
            sys.exit(1)

    id_conf = read_conf(ID_CONF_FILE_NAME)
    if None in id_conf.values():
        log.info("Configuration value missing in {}:\n"
                 "{}".format(ID_CONF_FILE_NAME, id_conf))
        sys.exit(1)
    charm_conf = read_conf(CHARM_CONF_FILE_NAME)
    if None in charm_conf.values():
        log.info("Configuration value missing in {}:\n"
                 "{}".format(CHARM_CONF_FILE_NAME, charm_conf))
        sys.exit(1)

    return id_conf, charm_conf


def get_keystone_client(api_version):
    if api_version == 3:
        ksc = keystone_v3_client.Client(
            auth_url=os.environ['OS_AUTH_URL'],
            username=os.environ['OS_USERNAME'],
            password=os.environ['OS_PASSWORD'],
            user_domain_name=os.environ['OS_USER_DOMAIN_NAME'],
            project_domain_name=os.environ['OS_PROJECT_DOMAIN_NAME'],
            project_name=os.environ['OS_PROJECT_NAME'],
            project_id=os.environ['OS_PROJECT_ID'])
    else:
        ksc = keystone_client.Client(username=os.environ['OS_USERNAME'],
                                     password=os.environ['OS_PASSWORD'],
                                     tenant_id=os.environ['OS_TENANT_ID'],
                                     tenant_name=os.environ['OS_TENANT_NAME'],
                                     auth_url=os.environ['OS_AUTH_URL'])
    return ksc


def set_openstack_env(id_conf, charm_conf):
    version = 'v3' if str(id_conf['api_version']).startswith('3') else 'v2.0'
    auth_url = ("{protocol}://{host}:{port}/{version}"
                .format(protocol=id_conf['service_protocol'],
                        host=id_conf['service_host'],
                        port=id_conf['service_port'],
                        version=version))
    os.environ['OS_AUTH_URL'] = auth_url
    os.environ['OS_USERNAME'] = id_conf['admin_user']
    os.environ['OS_PASSWORD'] = id_conf['admin_password']
    os.environ['OS_REGION_NAME'] = charm_conf['region']
    if version == 'v3':
        # Keystone charm puts all service users in the default domain.
        # Even so, it would be better if keystone passed this information
        # down the relation.
        os.environ['OS_USER_DOMAIN_NAME'] = id_conf['admin_domain_name']
        os.environ['OS_PROJECT_ID'] = id_conf['admin_tenant_id']
        os.environ['OS_PROJECT_NAME'] = id_conf['admin_tenant_name']
        os.environ['OS_PROJECT_DOMAIN_NAME'] = id_conf['admin_domain_name']
    else:
        os.environ['OS_TENANT_ID'] = id_conf['admin_tenant_id']
        os.environ['OS_TENANT_NAME'] = id_conf['admin_tenant_name']


def do_sync(charm_conf, status_exchange):

    # user_agent = charm_conf.get("user_agent")
    for mirror_info in charm_conf['mirror_list']:
        mirror_url, initial_path = path_from_mirror_url(mirror_info['url'],
                                                        mirror_info['path'])

        log.info("configuring sync for url {}".format(mirror_info))

        smirror = UrlMirrorReader(
            mirror_url, policy=policy)  # user_agent=user_agent

        if charm_conf['use_swift']:
            store = SwiftObjectStore(SWIFT_DATA_DIR)
        else:
            store = None

        content_id = charm_conf['content_id_template'].format(
            region=charm_conf['region'])

        config = {'max_items': mirror_info['max'],
                  'modify_hook': charm_conf['modify_hook_scripts'],
                  'keep_items': True,
                  'content_id': content_id,
                  'cloud_name': charm_conf['cloud_name'],
                  'item_filters': mirror_info['item_filters'],
                  'hypervisor_mapping': charm_conf.get('hypervisor_mapping',
                                                       False)}

        mirror_args = dict(config=config, objectstore=store,
                           name_prefix=charm_conf['name_prefix'])

        if SIMPLESTREAMS_HAS_PROGRESS:
            log.info("Calling DryRun mirror to get item list")

            drmirror = glance.ItemInfoDryRunMirror(config=config,
                                                   objectstore=store)
            drmirror.sync(smirror, path=initial_path)
            p = StatusMessageProgressAggregator(drmirror.items,
                                                status_exchange.send_message)
            mirror_args['progress_callback'] = p.progress_callback
        else:
            log.info("Detected simplestreams version without progress"
                     " update support. Only limited feedback available.")

        tmirror = glance.GlanceMirror(**mirror_args)

        log.info("calling GlanceMirror.sync")
        tmirror.sync(smirror, path=initial_path)


def update_product_streams_service(ksc, services, region):
    """
    Updates URLs of product-streams endpoint to point to swift URLs.
    """

    try:
        catalog = {
            endpoint_type: ksc.service_catalog.url_for(
                service_type='object-store', endpoint_type=endpoint_type)
            for endpoint_type in ['publicURL', 'internalURL', 'adminURL']}
    except keystone_exceptions.EndpointNotFound as e:
        log.warning("could not retrieve swift endpoint, not updating "
                    "product-streams endpoint: {}".format(e))
        raise

    for endpoint_type in ['publicURL', 'internalURL']:
        catalog[endpoint_type] += "/{}".format(SWIFT_DATA_DIR)

    endpoints = [endpoint._info for endpoint in ksc.endpoints.list()
                 if endpoint._info['region'] == region]
    ps_services = [s for s in services
                   if s['name'] == PRODUCT_STREAMS_SERVICE_NAME]
    if len(ps_services) != 1:
        log.error("found {} product-streams services. expecting one."
                  " - not updating endpoint.".format(len(ps_services)))
        return

    ps_service_id = ps_services[0]['id']

    ps_endpoints = [endpoint for endpoint in endpoints
                    if endpoint['service_id'] == ps_service_id]

    if len(ps_endpoints) != 1:
        log.warning("found {} product-streams endpoints in region {},"
                    " expecting one - not updating"
                    " endpoint".format(len(ps_endpoints), region))
        return

    create_args = dict(region=region,
                       service_id=ps_service_id,
                       publicurl=catalog['publicURL'],
                       adminurl=catalog['adminURL'],
                       internalurl=catalog['internalURL'])
    ps_endpoint = ps_endpoints[0]

    endpoint_already_exists = all(
        create_args[key] == ps_endpoint.get(key) for key in create_args)

    if endpoint_already_exists:
        log.info("Endpoint already set to desired values, moving on.")
    else:
        status_set('maintenance', 'Updating product-streams endpoint')

        log.info("Deleting existing product-streams endpoint: ")
        ksc.endpoints.delete(ps_endpoints[0]['id'])
        log.info("creating product-streams endpoint: {}".format(create_args))
        ksc.endpoints.create(**create_args)
        update_endpoint_urls(region, catalog['publicURL'],
                             catalog['adminURL'],
                             catalog['internalURL'])


def juju_run_cmd(cmd):
    '''Execute the passed commands under the local unit context'''
    id_conf, _ = get_conf()
    unit_name = id_conf['unit_name']
    _cmd = ['juju-run', unit_name, ' '.join(cmd)]
    log.info("Executing command: {}".format(_cmd))
    return subprocess.check_output(_cmd)


def status_set(status, message):
    try:
        juju_run_cmd(['status-set', status,
                      '"{}"'.format(message)])
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
        for k, v in relation_data.iteritems():
            _cmd.append('{}={}'.format(k, v))
        juju_run_cmd(_cmd)


class StatusExchange:
    """Wrapper for rabbitmq status exchange connection.

    If no connection exists, this attempts to create a connection
    before sending each message.
    """

    def __init__(self):
        self.conn = None
        self.exchange = None

        self._setup_connection()

    def _setup_connection(self):
        """Returns True if a valid connection exists already, or if one can be
        created."""

        if self.conn:
            return True

        id_conf = read_conf(ID_CONF_FILE_NAME)

        # The indentity.yaml file contains either a singular string variable
        # 'rabbit_host', or a comma separated list in the plural variable
        # 'rabbit_hosts'
        host = None
        hosts = id_conf.get('rabbit_hosts', None)
        if hosts is not None:
            host = hosts.split(",")[0]
        else:
            host = id_conf.get('rabbit_host', None)

        if host is None:
            log.warning("no host info in configuration, can't set up rabbit.")
            return False

        try:
            url = "amqp://{}:{}@{}/{}".format(id_conf['rabbit_userid'],
                                              id_conf['rabbit_password'],
                                              host,
                                              id_conf['rabbit_virtual_host'])

            self.conn = kombu.BrokerConnection(url)
            self.exchange = kombu.Exchange("glance-simplestreams-sync-status")
            status_queue = kombu.Queue("glance-simplestreams-sync-status",
                                       exchange=self.exchange)

            status_queue(self.conn.channel()).declare()

        except:
            log.exception("Exception during kombu setup")
            return False

        return True

    def send_message(self, msg):
        if not self._setup_connection():
            log.warning("No rabbitmq connection available for msg"
                        "{}. Message will be lost.".format(str(msg)))
            return

        with self.conn.Producer(exchange=self.exchange) as producer:
            producer.publish(msg)

    def close(self):
        if self.conn:
            self.conn.close()


def cleanup():
    try:
        os.unlink(SYNC_RUNNING_FLAG_FILE_NAME)
    except OSError as e:
        if e.errno != 2:
            raise e


def main():

    log.info("glance-simplestreams-sync started.")

    lockfile = open(SYNC_RUNNING_FLAG_FILE_NAME, 'w')
    atexit.register(cleanup)

    try:
        fcntl.flock(lockfile, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except IOError:
        log.info("{} is locked, exiting".format(SYNC_RUNNING_FLAG_FILE_NAME))
        sys.exit(0)

    lockfile.write(str(os.getpid()))

    id_conf, charm_conf = get_conf()

    set_openstack_env(id_conf, charm_conf)

    ksc = get_keystone_client(id_conf['api_version'])
    services = [s._info for s in ksc.services.list()]
    servicenames = [s['name'] for s in services]
    ps_service_exists = PRODUCT_STREAMS_SERVICE_NAME in servicenames
    swift_exists = 'swift' in servicenames

    log.info("ps_service_exists={}, charm_conf['use_swift']={}"
             ", swift_exists={}".format(ps_service_exists,
                                        charm_conf['use_swift'],
                                        swift_exists))

    try:
        if not swift_exists and charm_conf['use_swift']:
            # If use_swift is set, we need to wait for swift to become
            # available.
            log.info("Swift not yet ready.")
            return

        if ps_service_exists and charm_conf['use_swift'] and swift_exists:
            log.info("Updating product streams service.")
            update_product_streams_service(ksc, services, charm_conf['region'])
        else:
            log.info("Not updating product streams service.")

        status_exchange = StatusExchange()

        log.info("Beginning image sync")
        status_set('maintenance', 'Synchronising images')

        status_exchange.send_message({"status": "Started",
                                      "message": "Sync starting."})
        do_sync(charm_conf, status_exchange)
        ts = time.strftime("%x %X")
        completed_msg = "Sync completed at {}".format(ts)
        status_exchange.send_message({"status": "Done",
                                      "message": completed_msg})
        status_set('active', completed_msg)

        status_exchange.close()

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
    except Exception as e:
        log.exception("Exception during syncing:")
        status_exchange.send_message(
            {"status": "Error", "message": traceback.format_exc()})
        status_set('blocked', 'Image sync failed, retrying soon.')

    log.info("sync done.")


if __name__ == "__main__":
    main()
