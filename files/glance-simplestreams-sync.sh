#!/bin/bash
if [ -f /etc/profile.d/juju-proxy.sh ]; then
    source /etc/profile.d/juju-proxy.sh
elif [ -f /etc/juju-proxy.conf ]; then
    source /etc/juju-proxy.conf
elif [ -f /home/ubuntu/.juju-proxy ]; then
    source /home/ubuntu/.juju-proxy
fi

source /etc/lsb-release
if [[ $DISTRIB_RELEASE > "18.04" ]]; then
    PYTHON=python3
else
    PYTHON=python
fi

$PYTHON /usr/share/glance-simplestreams-sync/glance-simplestreams-sync.py
