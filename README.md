# Overview

This charm provides a service that syncs your OpenStack cloud's
available OS images in OpenStack Glance with the available images from
a set of simplestreams mirrors, by default using
cloud-images.ubuntu.com.

It will create a user named 'image-stream' in the 'services' tenant.
If swift is enabled, glance will store its images in swift using the
image-stream username.

It can optionally also store simplestreams metadata into Swift for
future use by juju. If enabled, it publishes the URL for that metadata
as the endpoints of a new OpenStack service called 'product-streams'.
If using Swift is not enabled, the product-streams service will still
exist, but nothing will respond to requests to its endpoints.

The charm installs a cron job that repeatedly checks the
status of related services and begins syncing image data from your
configured mirrors as soon as all services are in place.

It can be deployed at any time, and upon deploy (or changing the 'run'
config setting), it will attempt to contact keystone and glance and
start a sync every minute until a successful sync occurs.

# Requirements

This charm requires a juju relation to Keystone. It also requires a
running Glance instance, but not a relation - it connects with glance
via its endpoint as published in Keystone.

# Usage

    juju deploy glance-simplestreams-sync [--config optional-config.yaml]
    juju add-relation keystone glance-simplestreams-sync

# Configuration

The charm has the following configuration variables:

## `run`

`run` is a boolean that enables or disables the sync cron script.  It
is True by default, and changing it from False to True will schedule
an immediate attempt to sync images.

## `use_swift`

`use_swift` is a boolean that determines whether or not to store data
in swift and publish the path to product metadata via the
'product-streams' endpoint.

*NOTE* Changing the value will only affect the next sync, and does not
 currently remove an existing product-streams service or delete
 potentially stale product data.

## `frequency`

`frequency` is a string, and must be one of 'hourly', 'daily',
'weekly'.  It controls how often the sync cron job is run - it is used
to link the script into `/etc/cron.$frequency`.

## `region`

`region` is the OpenStack region in which the product-streams endpoint
will be created.

## `mirror_list`

`mirror_list` is a yaml-formatted list of options to be passed to
Simplestreams. It defaults to settings for downloading images from
cloud-images.ubuntu.com, and is not yet tested with other mirror
locations. If you have set up your own Simplestreams mirror, you
should be able to set the necessary configuration values.


# Copyright

The glance-simplestreams sync charm is free software: you can
redistribute it and/or modify it under the terms of the GNU Affero General
Public License as published by the Free Software Foundation, either
version 3 of the License, or (at your option) any later version.

The charm is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU Affero General Public License for more details.

You should have received a copy of the GNU Affero General Public License
along with this charm.  If not, see <http://www.gnu.org/licenses/>.
