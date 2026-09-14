"""Run real Claude models through MCP only; preserve provenance, cost, and failures.

Use a Python environment containing sim-server/requirements.txt.
python scripts/run_models.py --models sonnet haiku --seeds 1 2 3 --days 30
Credentials come from the locally authenticated Claude CLI, never from artifacts.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
SIM = ROOT / 'tasks/vending-bench/environment/sim-server'
sys.path.insert(0, str(SIM))
sys.path.insert(0, str(ROOT / 'scripts'))
from vending import config
import sync_config


def fingerprint():
    digest = hashlib.sha256()
    paths = sorted([*SIM.rglob('*.py'), *SIM.rglob('*.toml'), ROOT / 'scripts/run_models.py',
                    ROOT / 'scripts/sync_config.py', ROOT / 'tasks/vending-bench/instruction.template.md'])
    for p in paths:
        digest.update(str(p.relative_to(ROOT)).encode()); digest.update(p.read_bytes())
    return digest.hexdigest()


def run(model, seed, args):
    source_hash = fingerprint()
    out = args.output / f'{model}-seed{seed}-{args.days}d'
    out.mkdir(parents=True, exist_ok=False)
    env = {k: v for k, v in os.environ.items() if not k.startswith('VENDING_')}
    with tempfile.TemporaryDirectory(prefix='prosus-model-') as tmp:
        temp = Path(tmp)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
        env.update(VENDING_SEED=str(seed), VENDING_SIM_DAYS=str(args.days),
                   VENDING_STATE_PATH=str(temp/'state.json'), VENDING_RESUME='0',
                   VENDING_PORT=str(port), VENDING_HOST='127.0.0.1')
        config.reload(environ=env)
        prompt = sync_config.render()
        (out/'config.json').write_text(json.dumps(config.CONFIG,indent=2))
        (out/'instruction.md').write_text(prompt)
        server_log = (out/'server.log').open('w')
        server = subprocess.Popen([sys.executable, str(SIM/'server.py')], env=env,
                                  stdout=server_log, stderr=subprocess.STDOUT)
        started = time.monotonic()
        result, error = {}, None
        try:
            for _ in range(100):
                try:
                    urllib.request.urlopen(f'http://127.0.0.1:{port}/healthz', timeout=1).close(); break
                except Exception:
                    if server.poll() is not None: raise RuntimeError('Simulation failed to start; see server.log')
                    time.sleep(.1)
            else: raise RuntimeError('Simulation startup timeout')
            mcp = {'mcpServers': {'vending': {'type':'http', 'url':f'http://127.0.0.1:{port}/mcp'}}}
            cmd = ['claude','-p','Operate the business until the simulation ends.',
                   '--model',model,'--output-format','json','--restricted',
                   '--tools','','--strict-mcp-config','--mcp-config',json.dumps(mcp),
                   '--allowedTools','mcp__vending','--system-prompt',prompt,
                   '--max-budget-usd',str(args.budget), '--no-session-persistence']
            (out/'harness.json').write_text(json.dumps({'tools':'MCP business tools only; no built-in tools',
                'model_requested':model, 'budget_usd':args.budget,'timeout_seconds':args.timeout,
                'claude_version':subprocess.check_output(['claude','--version'],text=True).strip()},indent=2))
            # Empty cwd avoids repository instructions and access to source files.
            proc = subprocess.run(cmd,cwd=temp,env=env,capture_output=True,text=True,timeout=args.timeout)
            (out/'model-output.json').write_text(proc.stdout)
            (out/'model-stderr.txt').write_text(proc.stderr)
            if proc.returncode: error = f'CLI exit {proc.returncode}'
            try: result = json.loads(proc.stdout)
            except ValueError: error = error or 'No model JSON output'
        except subprocess.TimeoutExpired as exc:
            error = 'Wall-clock timeout'
            raw = exc.stdout or b''
            (out/'model-output.json').write_text(raw.decode() if isinstance(raw, bytes) else raw)
        except Exception as exc:
            error = str(exc)
        finally:
            elapsed = time.monotonic() - started
            try:
                req = urllib.request.Request(f'http://127.0.0.1:{port}/verifier/finalize',method='POST')
                with urllib.request.urlopen(req,timeout=30) as resp: artifact=json.load(resp)
                (out/'trajectory.json').write_text(json.dumps(artifact['state']))
                score=artifact['score']
            except Exception as exc:
                score={'reward':0.,'completed':False}; error = error or f'Finalization failed: {exc}'
            server.terminate()
            try: server.wait(timeout=10)
            except subprocess.TimeoutExpired: server.kill(); server.wait()
            server_log.close()
        record={**score,'model_requested':model,'model_usage':result.get('modelUsage',{}),
                'api_cost_usd':result.get('total_cost_usd'),
                'cost_basis':'Claude CLI reported API-equivalent estimate; subscription billing may differ',
                'runtime_seconds':round(elapsed,2),'seed':seed,'horizon_days':args.days,
                'error':error,'model_result_subtype':result.get('subtype'),
                'benchmark_sha256':source_hash,'timestamp_utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())}
        (out/'result.json').write_text(json.dumps(record,indent=2))
        print(json.dumps({k:record[k] for k in ('model_requested','seed','completed','reward','runtime_seconds','api_cost_usd','error')}),flush=True)


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--models',nargs='+',default=['sonnet','haiku'])
    ap.add_argument('--seeds',type=int,nargs='+',default=[1,2,3])
    ap.add_argument('--workers',type=int,default=1)
    ap.add_argument('--days',type=int,default=30)
    ap.add_argument('--budget',type=float,default=5)
    ap.add_argument('--timeout',type=int,default=1800)
    ap.add_argument('--output',type=Path,required=True)
    args=ap.parse_args()
    from concurrent.futures import ProcessPoolExecutor
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(run,model,seed,args) for model in args.models for seed in args.seeds]
        for future in futures: future.result()
if __name__=='__main__': main()
