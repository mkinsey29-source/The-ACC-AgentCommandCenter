"""Transport only: waits for a live agent's response; generates no model decisions."""
import argparse
import json
from pathlib import Path
import time

p = argparse.ArgumentParser()
p.add_argument('--packet', required=True)
p.add_argument('--queue', required=True)
p.add_argument('--role', required=True)
a = p.parse_args()
packet_path = Path(a.packet).resolve()
packet = json.loads(packet_path.read_text())
run_id = packet_path.parent.name
queue = Path(a.queue)
queue.mkdir(parents=True, exist_ok=True)
notice = queue / (run_id + '.request.json')
response = queue / (run_id + '.response.json')
notice.write_text(json.dumps({'run_id': run_id, 'role': a.role, 'packet': str(packet_path),
                              'response': str(response)}, indent=2))
print('Waiting for live agent: ' + a.role, flush=True)
deadline = time.monotonic() + 850
while time.monotonic() < deadline:
    if response.exists():
        # The live agent writes via temporary file + rename; never accept partial JSON.
        value = json.loads(response.read_text())
        output = Path(packet['result_file'])
        temporary = output.with_suffix('.tmp')
        temporary.write_text(json.dumps(value))
        temporary.replace(output)
        print('Live agent response delivered.', flush=True)
        break
    time.sleep(.1)
else:
    raise SystemExit('Timed out waiting for live agent response.')
