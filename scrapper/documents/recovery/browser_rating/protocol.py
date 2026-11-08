"""JSON-lines browser bridge protocol, with an opt-in version 1 envelope.

Omitting protocol_version retains the original messages. Version 1 uses the
same action payloads (claim, release, rate_limit, import, finish), echoing
protocol_version on successful responses. Errors retain the legacy error key.
Unsupported versions are rejected before ledger/heartbeat/persistence work.
No handshake, automatic downgrade, retry, or new ownership guarantee is added.
"""
import json

VERSION = 1
ACTIONS = frozenset(('claim', 'release', 'rate_limit', 'import', 'finish', 'shared_setup',
    'host_check','host_acquire','host_dispatch','host_renew','host_owned','host_finish','host_defer'))


def validate_request(request):
    # Preserve legacy parsing and validation order when no version is supplied.
    if isinstance(request, dict) and 'protocol_version' in request:
        version = request['protocol_version']
        if type(version) is not int or version != VERSION:
            raise ValueError('Unsupported bridge protocol version')
        if request.get('action') not in ACTIONS:
            raise ValueError('Unknown bridge action')


def response(value, request):
    if request.get('protocol_version') == VERSION:
        return dict(value, protocol_version=VERSION)
    return value


def exchange(bridge, request, *, version=None):
    """Exchange one line on an already-open configurable transport.

    The caller owns process startup/shutdown. Default messages remain legacy;
    explicit v1 clients require a v1 reply and never silently downgrade.
    """
    if version is not None:
        request = dict(request, protocol_version=version)
    validate_request(request)
    bridge.stdin.write(json.dumps(request) + '\n')
    bridge.stdin.flush()
    line = bridge.stdout.readline()
    if not line:
        raise RuntimeError('Live database bridge disconnected')
    result = json.loads(line)
    if result.get('error'):
        raise ValueError(result['error'])
    if 'protocol_version' in request and (type(result.get('protocol_version')) is not int or result['protocol_version'] != VERSION):
        raise ValueError('Unsupported bridge response protocol version')
    return result
