"""A stand-in for the ``tailscale`` command, driven by a JSON state file named in FAKE_TAILSCALE_STATE.

It mimics only what ACC uses: ``status --json``, ``serve status --json``, ``serve --bg --yes --https=PORT
TARGET`` and ``serve --yes --https=PORT off``. Switches in the state file simulate failures.
"""
import json
import os
import sys

path = os.environ['FAKE_TAILSCALE_STATE']
state = json.load(open(path))


def save():
    json.dump(state, open(path, 'w'))


args = sys.argv[1:]
if args[:2] == ['status', '--json']:
    print(json.dumps({'BackendState': state.get('backend', 'Running'),
                      'Self': {'DNSName': state.get('dns', 'box.tail1234.ts.net.')}}))
elif args[:3] == ['serve', 'status', '--json']:
    print(json.dumps({'TCP': state.get('tcp', {}), 'Web': state.get('web', {}), 'AllowFunnel': state.get('funnel', {})}))
elif args[:1] == ['serve'] and '--bg' in args:
    if state.get('fail_serve'):
        print('serve not permitted for this user', file=sys.stderr)
        sys.exit(1)
    port = next(a.split('=')[1] for a in args if a.startswith('--https='))
    target = args[-1]
    if not state.get('serve_noop'):
        host = state.get('dns', 'box.tail1234.ts.net.').rstrip('.')
        state.setdefault('tcp', {})[port] = {'HTTPS': True}
        state.setdefault('web', {})[f'{host}:{port}'] = {'Handlers': {'/': {'Proxy': target}}}
        save()
elif args[:1] == ['serve'] and args[-1] == 'off':
    port = next(a.split('=')[1] for a in args if a.startswith('--https='))
    host = state.get('dns', 'box.tail1234.ts.net.').rstrip('.')
    state.get('tcp', {}).pop(port, None)
    state.get('web', {}).pop(f'{host}:{port}', None)
    save()
else:
    print('unsupported fake command: ' + ' '.join(args), file=sys.stderr)
    sys.exit(2)
