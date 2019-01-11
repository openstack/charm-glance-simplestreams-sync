#!/usr/bin/env python

# Copyright 2018 Canonical Ltd.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
import ipaddress
import itertools
import os
import socket
import tempfile

import six

import cert as _cert

ISSUER_NAME = u'OSCI'

CERT_DIR = tempfile.gettempdir()


def determine_CIDR_EXT():
    ip = socket.gethostbyname(socket.getfqdn())
    if ip.startswith('10.5'):
        # running in a bastion
        return u"10.5.0.0/24"
    else:
        # running on UOSCI
        return u"172.17.107.0/24"


def write_cert(path, filename, data, mode=0o600):
    """
    Helper function for writing certificate data to disk.

    :param path: Directory file should be put in
    :type path: str
    :param filename: Name of file
    :type filename: str
    :param data: Data to write
    :type data: any
    :param mode: Create mode (permissions) of file
    :type mode: Octal(int)
    """
    with os.fdopen(os.open(os.path.join(path, filename),
                           os.O_WRONLY | os.O_CREAT, mode), 'wb') as f:
        f.write(data)


# We need to restrain the number of SubjectAlternativeNames we attempt to put
# in the certificate.  There is a hard limit for what length the sum of all
# extensions in the certificate can have.
#
# - 2^11 ought to be enough for anybody
def generate_certs(cert_dir=CERT_DIR):
    alt_names = []
    for addr in itertools.islice(
            ipaddress.IPv4Network(determine_CIDR_EXT()), 2**11):

        if six.PY2:
            alt_names.append(unicode(addr))  # NOQA -- py3 doesn't have unicode
        else:
            alt_names.append(str(addr))

    (cakey, cacert) = _cert.generate_cert(ISSUER_NAME,
                                          generate_ca=True)
    (key, cert) = _cert.generate_cert(u'*.serverstack',
                                      alternative_names=alt_names,
                                      issuer_name=ISSUER_NAME,
                                      signing_key=cakey)

    write_cert(cert_dir, 'cacert.pem', cacert)
    write_cert(cert_dir, 'ca.key', cakey)
    write_cert(cert_dir, 'cert.pem', cert)
    write_cert(cert_dir, 'cert.key', key)


if __name__ == '__main__':
    generate_certs()
