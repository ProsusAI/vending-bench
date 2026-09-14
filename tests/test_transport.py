"""Exercise the real MCP/HTTP boundary, including destructive verifier sealing."""
import asyncio
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.request
import urllib.error
import pytest
from fastmcp import Client

ROOT=Path(__file__).resolve().parents[1]


@pytest.fixture
def server(tmp_path):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0)); port=sock.getsockname()[1]
    env={k:v for k,v in os.environ.items() if not k.startswith('VENDING_')}
    env.update(VENDING_HOST='127.0.0.1',VENDING_PORT=str(port),VENDING_STATE_PATH=str(tmp_path/'state.json'),VENDING_RESUME='0')
    proc=subprocess.Popen([sys.executable,str(ROOT/'tasks/vending-bench/environment/sim-server/server.py')],env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    url=f'http://127.0.0.1:{port}'
    try:
        for _ in range(100):
            try:
                urllib.request.urlopen(url+'/healthz',timeout=1).close();break
            except Exception: time.sleep(.1)
        else: pytest.fail('server did not start')
        yield url
    finally:
        proc.terminate();proc.wait(timeout=10)


def test_live_state_is_unavailable_and_finalizer_freezes_world(server):
    for path in ('/state','/score'):
        with pytest.raises(urllib.error.HTTPError) as err:
            urllib.request.urlopen(server+path)
        assert err.value.code==404
    async def play():
        async with Client(server+'/mcp') as client:
            tools=await client.list_tools()
            assert len(tools)==22
            names={t.name for t in tools}
            assert {'check_offers','order_goods','run_marketing','swap_item'} <= names
            assert 'collect_cash' not in names
            result=await client.call_tool('get_status',{})
            assert '1,500.00' in result.content[0].text
    asyncio.run(play())
    request=urllib.request.Request(server+'/verifier/finalize',method='POST')
    with urllib.request.urlopen(request) as r: first=json.load(r)
    assert first['score']['reward']==0 and first['state']['sealed']
    async def try_change():
        async with Client(server+'/mcp') as client:
            result=await client.call_tool('run_marketing',{'location':'AI-HOUSE','channel':'mail','message':'Hello'})
            assert 'SIMULATION OVER' in result.content[0].text
    asyncio.run(try_change())
    with urllib.request.urlopen(request) as r: second=json.load(r)
    assert first==second
