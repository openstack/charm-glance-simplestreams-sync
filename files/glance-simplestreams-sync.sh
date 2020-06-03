#!/bin/bash

if [ -z "$HOME" ]; then
    export HOME=/root
fi

set -e

if [ -f /etc/profile.d/juju-proxy.sh ]; then
    source /etc/profile.d/juju-proxy.sh
elif [ -f /etc/juju-proxy.conf ]; then
    source /etc/juju-proxy.conf
elif [ -f /home/ubuntu/.juju-proxy ]; then
    source /home/ubuntu/.juju-proxy
fi

source /etc/lsb-release
if dpkg --compare-versions $DISTRIB_RELEASE gt "18.04"; then
    PYTHON=python3
else
    PYTHON=python
fi

$PYTHON /usr/share/glance-simplestreams-sync/glance_simplestreams_sync.py
