"""Fresh sdist→wheel consumers of tracked current bytes; not T18 release proof."""

import json
import subprocess
import sys

import pytest

from tests.test_packaging import execute


def consumer(tmp_path, candidate_distribution, *, viewer):
    _, wheel, environment = candidate_distribution
    venv = tmp_path / ("viewer consumer" if viewer else "core consumer")
    execute(
        ["uv", "venv", "--python", sys.executable, str(venv)],
        cwd=tmp_path,
        environment=environment,
    )
    python, console = venv / "bin/python", venv / "bin/rgraph"
    requirement = (
        f"researchgraph[test,viewer] @ {wheel.as_uri()}" if viewer else str(wheel)
    )
    execute(
        ["uv", "pip", "install", "--python", str(python), requirement],
        cwd=tmp_path,
        environment=environment,
    )
    return venv, python, console, environment


def test_core_wheel_lazy_help_commands_and_missing_viewer(
    tmp_path, candidate_distribution
):
    venv, python, console, environment = consumer(
        tmp_path, candidate_distribution, viewer=False
    )
    script = r"""
import importlib.util,json,subprocess,sys
from pathlib import Path
from researchgraph.cli import main
import researchgraph
venv,console=map(Path,sys.argv[1:])
assert Path(researchgraph.__file__).resolve().is_relative_to(venv)
assert all(importlib.util.find_spec(name) is None for name in ('mcp','starlette','uvicorn'))
root=Path('core-project').resolve()
audit_root=Path('import-audit-project').resolve()
for directory in (root,audit_root):
    directory.mkdir()
    for args in (('init','-q'),('config','user.name','Wheel human'),('config','user.email','wheel@example.test')):
        subprocess.run(['git','-C',str(directory),*args],check=True,capture_output=True)
commands=[['--help'],['serve','--help'],['--project',str(root),'init','--profile','tasks'],['--project',str(root),'propose','Task','Core wheel task'],['--project',str(root),'--json','graph']]
for arguments in commands:
    result=subprocess.run([str(console),*arguments],capture_output=True,text=True,timeout=15)
    assert result.returncode==0,(arguments,result.stdout,result.stderr)
    # Invoke the real Click command in this interpreter too, inspecting actual
    # imports after every ordinary command (not just dependency availability).
    try:
        main.main(args=[str(audit_root) if value==str(root) else value for value in arguments],prog_name='rgraph',standalone_mode=False)
    except SystemExit as error:
        assert error.code in (0,None)
    assert not any(name.split('.')[0] in {'mcp','starlette','uvicorn'} for name in sys.modules)
print(json.dumps({'core_import_isolation':True,'installed':str(researchgraph.__file__)}))
"""
    checked = execute(
        [str(python), "-c", script, str(venv), str(console)],
        cwd=tmp_path,
        environment=environment,
    )
    assert json.loads(checked.stdout.splitlines()[-1])["core_import_isolation"] is True
    missing = subprocess.run(
        [
            str(console),
            "serve",
            "--project",
            str(tmp_path / "core-project"),
            "--no-open",
        ],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert missing.returncode == 2 and missing.stdout == ""
    error = json.loads(missing.stderr)
    assert error["rule"] == "schema" and "researchgraph[viewer]" in error["message"]


@pytest.mark.http
def test_viewer_wheel_real_console_http_sse_and_owner_release(
    tmp_path, candidate_distribution
):
    venv, python, console, environment = consumer(
        tmp_path, candidate_distribution, viewer=True
    )
    script = r"""
import hashlib,importlib.util,json,os,re,select,signal,subprocess,sys,time
from contextlib import contextmanager
from datetime import UTC,datetime
from importlib.resources import files
from pathlib import Path
from urllib.parse import parse_qs,quote,urlsplit
import httpx
import httpx2
import researchgraph
from starlette.testclient import TestClient
from researchgraph.model import Actor
from researchgraph.project import Project
from researchgraph.registry import OPERATIONS
venv,console=map(Path,sys.argv[1:])
assert Path(researchgraph.__file__).resolve().is_relative_to(venv)
assert importlib.util.find_spec('mcp') is None
assert Path(httpx2.__file__).resolve().is_relative_to(venv)
assets={name:files('researchgraph').joinpath('viewer','static',name).read_bytes() for name in ('index.html','bootstrap.js','style.css','research-graph.js','inspector.js','review-queue.js','dashboard.js','events.js','api.js','dom.js')}
assert all(assets.values())
assert b'bootstrap.js' in assets['index.html'] and b'style.css' in assets['index.html']
root=Path('viewer-project').resolve()
root.mkdir()
for args in (('init','-q'),('config','user.name','Wheel human'),('config','user.email','wheel@example.test')):
    subprocess.run(['git','-C',str(root),*args],check=True,capture_output=True)
human=Actor('human','Wheel human','wheel@example.test')
worker=Actor('agent','wheel-worker')
now=datetime.now(UTC).replace(microsecond=0)
payload=b'<script>not active</script>\x00\xff'
with Project.init(root,'tasks',actor=human,now=now) as project:
    node=project.propose('Task','registered wheel artifact',actor=human,now=now)
    project.claim(node['id'],actor=worker,now=now)
    relative='artifacts/'+node['id']+'/literal%25.html'
    path=root/relative
    path.parent.mkdir()
    path.write_bytes(payload)
    project.complete(node['id'],'wheel evidence',artifacts=[relative],actor=worker,now=now)
    (path.parent/'unregistered.html').write_bytes(payload)

@contextmanager
def launch(*options):
    process=subprocess.Popen([str(console),'serve','--project',str(root),'--no-open',*options],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,env=os.environ.copy())
    try:
        assert select.select([process.stdout],[],[],10)[0], 'no startup URL'
        line=process.stdout.readline().strip()
        match=re.fullmatch(r'http://127\.0\.0\.1:([1-9][0-9]*)/#token=([A-Za-z0-9_-]+)',line)
        assert match,(line,process.poll())
        parsed=urlsplit(line)
        origin=f'{parsed.scheme}://{parsed.netloc}'
        token=parse_qs(parsed.fragment)['token'][0]
        deadline=time.monotonic()+10
        with httpx.Client(base_url=origin,timeout=5,trust_env=False) as client:
            while True:
                assert process.poll() is None, 'server exited during startup'
                try:
                    response=client.get('/')
                    assert response.status_code==200
                    break
                except httpx.TransportError:
                    assert time.monotonic()<deadline,'server not ready'
                    time.sleep(.02)
            yield client,origin,token,process
    finally:
        if process.poll() is None:
            process.send_signal(signal.SIGINT)
        stdout,stderr=process.communicate(timeout=10)
        assert process.returncode==0,(process.returncode,stdout,stderr)
        assert stdout=='' and stderr=='',(stdout,stderr)

def login(client,origin,token):
    response=client.post('/api/session',json={'token':token},headers={'Origin':origin})
    assert response.status_code==200,response.text
    cookie=response.headers['set-cookie'].lower()
    assert 'httponly' in cookie and 'samesite=strict' in cookie
    assert 'domain=' not in cookie and 'max-age=' not in cookie
    assert client.cookies
    return dict(client.cookies)

def event(stream):
    fields={}
    for line in stream.iter_lines():
        if not line and fields:
            data=json.loads(fields['data'])
            assert fields['id']==data['cursor']
            assert fields['event']==data['kind']
            assert fields['id']==f"v1:{data['generation']}:{data['seq']}"
            return data
        if line and not line.startswith(':'):
            name,_,value=line.partition(':')
            fields[name]=value.lstrip()
    raise AssertionError('SSE ended without event')

with launch() as (client,origin,token,process):
    assert client.get('/').content==assets['index.html']
    for name in ('bootstrap.js','style.css','research-graph.js','inspector.js','review-queue.js','dashboard.js','events.js','api.js','dom.js'):
        response=client.get('/assets/'+name)
        assert response.status_code==200 and response.content==assets[name]
    assert client.get('/api/graph').status_code==403
    assert client.get('/api/events').status_code==403
    assert client.get('/artifacts/'+node['id']+'/literal%2525.html').status_code==403
    for headers,body in (({}, {'token':token}),({'Origin':'https://evil.test'},{'token':token}),({'Origin':origin},{'token':'wrong'})):
        assert client.post('/api/session',json=body,headers=headers).status_code==403
    old_cookie=login(client,origin,token)
    assert client.post('/api/session',json={'token':token},headers={'Origin':origin}).status_code==403
    listed=client.get('/api/operations')
    assert listed.status_code==200 and listed.json()==list(OPERATIONS) and len(listed.json())==37
    profile=client.get('/api/profile')
    assert profile.status_code==200
    with Project.open(root,read_only=True) as project:
        assert profile.json()==project.profile_info(actor=human,now=now)
    graph=client.get('/api/graph')
    assert graph.status_code==200
    cursor=graph.headers['X-Researchgraph-Cursor']
    assert re.fullmatch(r'v1:[0-9a-f]{32}:[1-9][0-9]*',cursor)
    with Project.open(root,read_only=True) as project:
        assert graph.json()==project.graph(actor=human,now=now)
    for headers,params in (({'Host':'evil.test'},{}),({'Origin':'https://evil.test'},{}),({}, {'token':token})):
        assert client.get('/api/graph',headers=headers,params=params).status_code==403
    assert client.get('/api/graph',params={'actor':json.dumps('intruder')}).status_code==403
    assert client.get('/api/graph',params={'unknown':'true'}).status_code==422
    arguments={'type':'Task','title':'persisted HTTP wheel task'}
    assert client.post('/api/propose',json=arguments).status_code==403
    for key,value in (('actor',{}),('project',str(root.parent)),('read_only',False),('as_agent','intruder')):
        assert client.post('/api/propose',json={**arguments,key:value},headers={'Origin':origin}).status_code==403
    with client.stream('GET','/api/events',headers={'Last-Event-ID':cursor}) as stream:
        assert stream.status_code==200
        assert stream.headers['content-type'].startswith('text/event-stream')
        with httpx.Client(base_url=origin,cookies=client.cookies,trust_env=False,timeout=5) as writer:
            created=writer.post('/api/propose',json=arguments,headers={'Origin':origin})
        assert created.status_code==200,created.text
        created=created.json()
        assert created['author']==human.to_dict()
        envelope=event(stream)
        assert envelope['kind']=='node' and envelope['node']==created
        assert envelope['generation']==cursor.split(':')[1]
        assert envelope['seq']>int(cursor.rsplit(':',1)[1])
    response=client.get('/artifacts/'+node['id']+'/'+quote('literal%25.html',safe=''))
    assert response.status_code==200 and response.content==payload
    assert response.headers['content-type']=='application/octet-stream'
    assert response.headers['content-disposition'].startswith('attachment;')
    assert response.headers['x-content-type-options']=='nosniff'
    assert client.get('/artifacts/'+node['id']+'/unregistered.html').status_code==404
    assert client.get('/artifacts/'+created['id']+'/'+quote('literal%25.html',safe='')).status_code==404
    for response in (listed,profile,graph,response):
        assert response.headers['cache-control']=='no-store'
        assert response.headers['referrer-policy']=='no-referrer'
    old_token=token
# Opening and mutating independently after process exit proves durable state
# and that the server no longer owns the project/index resources.
with Project.open(root) as project:
    assert project.get_node(created['id']).title==arguments['title']
    after=project.propose('Task','after owner release',actor=human,now=datetime.now(UTC).replace(microsecond=0))
with launch('--read-only') as (client,origin,token,process):
    client.cookies.update(old_cookie)
    assert client.get('/api/graph').status_code==403
    assert client.post('/api/session',json={'token':old_token},headers={'Origin':origin}).status_code==403
    client.cookies.clear()
    login(client,origin,token)
    assert client.get('/api/graph').headers['X-Researchgraph-Read-Only']=='true'
    for operation in OPERATIONS:
        if operation['kind']=='write':
            refused=client.post('/api/'+operation['name'],json={'now':'invalid'},headers={'Origin':origin})
            assert refused.status_code==422 and refused.json()['rule']=='read_only'
    with client.stream('GET','/api/events',headers={'Last-Event-ID':cursor}) as stream:
        reset=event(stream)
        assert reset['kind']=='index' and reset['reset'] is True
        assert reset['generation']!=cursor.split(':')[1]
        # Keep the real installed server's SSE connection open while signalling
        # shutdown: a generic Uvicorn server does not drain this idle stream.
        process.send_signal(signal.SIGINT)
        assert process.wait(timeout=5)==0
with Project.open(root) as project:
    assert project.get_node(after['id']).title=='after owner release'
print(json.dumps({'candidate_only':True,'installed':str(researchgraph.__file__),'http_sse_owner_release':True}))
"""
    result = execute(
        [str(python), "-c", script, str(venv), str(console)],
        cwd=tmp_path,
        environment=environment,
        timeout=90,
    )
    receipt = json.loads(result.stdout)
    assert receipt["candidate_only"] is True
    assert receipt["http_sse_owner_release"] is True
    assert result.stderr == ""
