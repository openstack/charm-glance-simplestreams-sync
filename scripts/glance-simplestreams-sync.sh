#!/bin/bash
test -f /home/ubuntu/.juju-proxy && source /home/ubuntu/.juju-proxy
exec /usr/share/glance-simplestreams-sync/glance-simplestreams-sync.py
