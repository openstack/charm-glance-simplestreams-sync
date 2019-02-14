#!/bin/bash
if [ -f /etc/juju-proxy.conf ]; then
    source /etc/juju-proxy.conf
elif [ -f /home/ubuntu/.juju-proxy ]; then
    source /home/ubuntu/.juju-proxy
fi
exec /usr/share/glance-simplestreams-sync/glance-simplestreams-sync.py
