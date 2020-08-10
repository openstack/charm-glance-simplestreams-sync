# Overview

The glance-simplestreams-sync charm keeps OpenStack cloud images (in Glance)
synchronised with the latest available images from a Simplestreams mirror(s).
It uses Cron to do this.

The charm places simplestreams metadata in Object storage for future use by
Juju. It then publishes the URL for that metadata as the endpoints of a new
OpenStack service called 'product-streams'.

The charm installs Simplestreams from a [snap][snap-upstream].

# Usage

# Configuration

This section covers common and/or important configuration options. See file
`config.yaml` for the full list of options, along with their descriptions and
default values. See the [Juju documentation][juju-docs-config-apps] for details
on configuring applications.

#### `run`

The `run` option enables the synchronisation cron script. This option accepts
Boolean values ('true' or 'false') with the default value being 'false'.
Changing the value from 'false' to 'true' will immediately schedule an image
sync.

> **Note**: Enabling this option at cloud deploy time may cause a race
  condition with the set up of a possible storage backend for Glance.

#### `frequency`

The `frequency` option controls how often the sync cron job is run. It is used
to link the cron script into `/etc/cron.<frequency>`. Valid string values are:
'hourly', 'daily', and 'weekly'. The default is 'daily'.

#### `region`

The `region` option states the OpenStack region to operate in. The default
value is 'RegionOne'.

#### `mirror_list`

The `mirror_list` option is a YAML-formatted list of Simplestreams mirrors and
their configuration properties. The default behaviour is to download images
from [https://cloud-images.ubuntu.com][cloud-images.ubuntu.com].

#### `ssl_ca`

The `ssl_ca` option verifies (optionally) the certificates when in SSL mode for
Keystone and Glance. This should be provided as a base64 encoded PEM
certificate.

## Deployment

To deploy to an existing OpenStack cloud (that already includes Glance, Object
storage, and Keystone):

    juju deploy glance-simplestreams-sync
    juju add-relation glance-simplestreams-sync:identity-service keystone:identity-service

> **Note**: Charmed OpenStack commonly employs Ceph-backed Object storage (see
  the [ceph-radosgw charm][ceph-radosgw-charm]). Otherwise, a vanilla
  Swift-based solution can be used (see the [swift-proxy charm][swift-proxy-charm]).

## Actions

Juju [actions][juju-docs-actions] allow specific operations to be performed on
a per-unit basis. This charm supports the single action `sync-images`, which
allows for a one-time image sync from the currently configured mirror list.

# Bugs

Please report bugs on [Launchpad][lp-bugs-charm-glance-simplestreams-sync].

For general charm questions refer to the [OpenStack Charm Guide][cg].

<!-- LINKS -->

[cg]: https://docs.openstack.org/charm-guide
[cdg]: https://docs.openstack.org/project-deploy-guide/charm-deployment-guide
[lp-bugs-charm-glance-simplestreams-sync]: https://bugs.launchpad.net/charm-glance-simplestreams-sync/+filebug
[juju-docs-actions]: https://juju.is/docs/working-with-actions
[juju-docs-config-apps]: https://juju.is/docs/configuring-applications
[snap-upstream]: https://snapcraft.io/
[cloud-images.ubuntu.com]: https://cloud-images.ubuntu.com
[ceph-radosgw-charm]: https://jaas.ai/ceph-radosgw
[swift-proxy-charm]: https://jaas.ai/swift-proxy
