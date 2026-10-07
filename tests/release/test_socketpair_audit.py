"""Exercise CPython's TCP socketpair under the real offline audit hook."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from setup.release import smoke_bundle as smoke


@pytest.mark.parametrize("allow_internal_pair", [False, True])
@pytest.mark.parametrize("fallback_layout", ["private-helper", "public-only"])
def test_asyncio_tcp_socketpair_has_no_general_loopback_permission(
    tmp_path: Path, allow_internal_pair: bool, fallback_layout: str,
):
    sandbox = tmp_path / "socketpair sandbox"
    sandbox.mkdir()
    program = f"""
import ast, asyncio, pathlib, socket, sys
from types import SimpleNamespace
sys.path.insert(0, {str(Path(smoke.__file__).resolve().parents[2])!r})
from setup.release import smoke_bundle as smoke
sandbox = pathlib.Path({str(sandbox)!r})
# On Unix some stdlib versions only define the TCP fallback in the inactive
# Windows branch. Compile that function from this interpreter's own socket.py,
# without copying its implementation or executing unrelated module code.
socket_source = pathlib.Path(socket.__file__).read_text(encoding='utf-8')
socket_syntax = ast.parse(socket_source)
fallback_definitions = [
    definition for definition in ast.walk(socket_syntax)
    if isinstance(definition, ast.FunctionDef)
    and definition.name in {{'_fallback_socketpair', 'socketpair'}}
    and any(isinstance(node, ast.Name) and node.id == 'csock' for node in ast.walk(definition))
]
assert len(fallback_definitions) == 1, 'stdlib TCP socketpair contract changed'
definition = fallback_definitions[0]
definition.name = '_fallback_socketpair' if {fallback_layout!r} == 'private-helper' else 'socketpair'
fallback_namespace = dict(vars(socket))
exec(compile(ast.Module(body=[definition], type_ignores=[]), socket.__file__, 'exec'), fallback_namespace)
socket.socketpair = fallback_namespace[definition.name]
if {fallback_layout!r} == 'private-helper':
    socket._fallback_socketpair = socket.socketpair
elif hasattr(socket, '_fallback_socketpair'):
    del socket._fallback_socketpair
assert hasattr(socket, '_fallback_socketpair') == ({fallback_layout!r} == 'private-helper')
smoke.sys = SimpleNamespace(platform='win32', _getframe=sys._getframe, addaudithook=sys.addaudithook)
audit = smoke.OfflineAudit(sandbox / 'bundle', sandbox, pathlib.Path(sys.executable))
assert audit.socketpair_code is socket.socketpair.__code__
if not {allow_internal_pair!r}:
    audit.socketpair_code = None
audit.install()
if not {allow_internal_pair!r}:
    try:
        socket.socketpair()
    except smoke.SmokeBlocked as error:
        assert str(error) == 'audit-unregistered-loopback-network'
    else:
        raise AssertionError('old audit must block the internal connect')
    print('original-failure-reproduced')
    raise SystemExit(0)
reader, writer = socket.socketpair()
with reader, writer:
    internal_address = reader.getsockname()
    writer.sendall(b'fixture')
    assert reader.recv(7) == b'fixture'
async def fixture_coroutine():
    await asyncio.sleep(0)
    return 'event-loop-ready'
assert asyncio.run(fixture_coroutine()) == 'event-loop-ready'
assert audit.allowed_ports == set()
assert not (sandbox / 'audit-violations.jsonl').exists()
with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
    listener.bind(('127.0.0.1', 0))
    listener.listen()
    unrelated_address = listener.getsockname()
    for address in (internal_address, unrelated_address, ('127.0.0.1', 9481), ('203.0.113.1', 443)):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client:
            try:
                client.connect(address)
            except smoke.SmokeBlocked as error:
                expected = 'audit-unregistered-loopback-network' if address[0] == '127.0.0.1' else 'audit-non-loopback-network'
                assert str(error) == expected
            else:
                raise AssertionError('socketpair permission escaped its call')
assert audit.allowed_ports == set()
print('internal-pair-and-asyncio-ready-other-network-blocked')
"""
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", program], cwd=sandbox,
        capture_output=True, text=True, encoding="utf-8", timeout=15,
    )
    assert result.returncode == 0, result.stderr
    expected = "internal-pair-and-asyncio-ready-other-network-blocked" if allow_internal_pair else "original-failure-reproduced"
    assert result.stdout.strip() == expected
