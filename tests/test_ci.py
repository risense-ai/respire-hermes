"""Portable provider tests with temporary profiles and exact subprocess allowlists."""
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
ALLOWED = {}
DENIED = []


def allowed_process(executable, argv, expected, windows):
    if executable != expected[0] and not (windows and executable is None):
        return False
    if isinstance(argv, (list, tuple)):
        return list(argv) == expected
    return windows and isinstance(argv, str) and argv == subprocess.list2cmdline(expected)


def audit(event, args):
    denied = False
    if event == 'subprocess.Popen':
        executable, argv, cwd, env = args
        denied = not any(allowed_process(executable, argv, expected, os.name == 'nt')
                         for choices in ALLOWED.values() for expected in choices)
    elif event.startswith('socket.') or event in ('os.system', 'os.exec', 'os.posix_spawn'):
        denied = True
    if denied:
        DENIED.append(event)
        raise SystemExit('Isolation denied an external action')


class Provider(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='hermes-ci-')
        self.case = Path(self.temp.name)
        self.home = self.case / 'profile'
        self.home.mkdir()
        self.calls = self.case / 'calls.jsonl'
        self.mode = self.case / 'mode'
        self.mode.write_text('ok', encoding='utf-8')
        self.log = self.case / 'provider.log'
        self.spy = self.case / 'spy.py'
        self.spy.write_text(r'''import json, os, sys, datetime
from pathlib import Path
case = Path(os.environ['RESPIRE_CI_ROOT'])
with (case/'calls.jsonl').open('a',encoding='utf-8') as f:
    f.write(json.dumps(sys.argv[1:])+'\n')
mode=(case/'mode').read_text(encoding='utf-8')
if mode=='dead':sys.exit(7)
if mode=='malformed':print('invalid json');sys.exit(0)
sub=sys.argv[1]
if sub=='recall':
    print(json.dumps({'status':'skip','details':[]} if mode=='miss' else
          {'status':'ok','details':[{'id':'cccccccccccccccc','score':0.9,'title':'verified canned'}]}))
elif sub=='status':
    print(json.dumps({'status':'ok','summary':{'session':{'has_token':True},'local_total':42}}))
elif sub=='list':
    ts=datetime.datetime.now(datetime.timezone.utc).isoformat() if mode=='recent' else '2020-01-01T00:00:00Z'
    print(json.dumps({'status':'ok','details':[{'id':'record','updated_at':ts}]}))
else:sys.exit(1)
''', encoding='utf-8')
        if os.name == 'nt':
            self.engine = self.case / 'rsrs-spy.cmd'
            self.engine.write_text(f'@echo off\n"{sys.executable}" "{self.spy}" %*\n', encoding='utf-8')
        else:
            self.engine = self.case / 'rsrs-spy'
            self.engine.write_text(f'#!{sys.executable}\n' + self.spy.read_text(encoding='utf-8'), encoding='utf-8')
        self.engine.chmod(0o700)
        (self.home / 'respire.json').write_text(json.dumps({'rsrs':str(self.engine)}), encoding='utf-8')
        self.saved_env = dict(os.environ)
        keep = {k:v for k,v in self.saved_env.items() if k.upper() in
                {'PATH','SYSTEMROOT','SYSTEMDRIVE','WINDIR','PATHEXT','TEMP','TMP','COMSPEC'}}
        os.environ.clear()
        os.environ.update(keep)
        os.environ.update(HOME=str(self.case/'home'), USERPROFILE=str(self.case/'home'),
                          HERMES_HOME=str(self.home), RESPITE_LOG=str(self.log),
                          XDG_CONFIG_HOME=str(self.case/'config'), XDG_DATA_HOME=str(self.case/'data'),
                          RESPIRE_CI_ROOT=str(self.case), PYTHONDONTWRITEBYTECODE='1', PYTHONUTF8='1')
        ALLOWED[str(self.engine)] = [[str(self.engine),'status','--json'],
                                     [str(self.engine),'list','--limit','3','--json']]
        spec = importlib.util.spec_from_file_location('hermes_ci_provider', ROOT / '__init__.py')
        self.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.mod)
        self.provider = self.mod.RespireMemoryProvider()
        self.provider.initialize('ci', hermes_home=self.home)
        self.provider.shutdown()  # finish the fixture-only initial status probe

    def tearDown(self):
        self.provider.shutdown()
        ALLOWED.pop(str(self.engine), None)
        os.environ.clear()
        os.environ.update(self.saved_env)
        self.temp.cleanup()
        self.assertEqual(DENIED, [])

    def recorded(self):
        return [json.loads(line) for line in self.calls.read_text(encoding='utf-8').splitlines()] if self.calls.exists() else []

    def prefetch(self, query):
        ALLOWED[str(self.engine)].append([str(self.engine),'recall',query,'--limit','5','--titles','--json'])
        return self.provider.prefetch(query)

    def positive(self):
        before = len(self.recorded())
        out = self.prefetch('ordinary positive control')
        self.assertEqual(len(self.recorded()), before + 1)
        self.assertIsInstance(out, str)
        self.assertIn('[0.90] verified canned', out)
        return out

    def test_live_spy_returns_valid_canned_context(self):
        self.positive()

    def test_credentials_zero_calls_and_no_disclosure(self):
        self.positive()
        before = self.recorded()
        secret = 'synthetic' + 'qv7xk2mwje'
        for query in ('Bearer '+secret, 'api_key = '+secret, '记：数据库密码 = '+secret):
            out = self.prefetch(query)
            self.assertEqual(out, self.mod.SECRET_SKIP)
            self.assertEqual(self.recorded(), before)
            self.assertNotIn(secret, out + self.log.read_text(encoding='utf-8'))
        skipped = [json.loads(line) for line in self.log.read_text(encoding='utf-8').splitlines()
                   if json.loads(line)['out']=='secret_skip']
        self.assertEqual(len(skipped), 3)
        self.assertTrue(all('d' not in entry for entry in skipped))

    def test_dead_spy_cannot_pass_positive_control(self):
        self.positive()
        self.mode.write_text('dead', encoding='utf-8')
        with self.assertRaises(AssertionError):
            self.positive()

    def test_malformed_response_cannot_pass_positive_control(self):
        self.positive()
        self.mode.write_text('malformed', encoding='utf-8')
        with self.assertRaises(AssertionError):
            self.positive()

    def test_empty_recall_is_silent(self):
        self.mode.write_text('miss', encoding='utf-8')
        self.assertIsNone(self.prefetch('no matching fixture'))

    def test_nudge_uses_turn_and_cached_write_evidence(self):
        self.provider._turns = 1
        self.provider._last_store_ts = time.time()-3600
        self.assertIn('Memory check', self.positive())
        self.provider._last_store_ts = time.time()-30
        self.assertNotIn('Memory check', self.positive())
        self.provider._last_store_ts = time.time()-3600
        self.provider._cfg['store_nudge_every'] = 0
        self.assertNotIn('Memory check', self.positive())

    def test_refresh_reads_only_fixture_list(self):
        self.mode.write_text('recent', encoding='utf-8')
        self.provider._refresh_last_store()
        self.assertLess(time.time()-self.provider._last_store_ts, 120)

    def test_cached_status_guidance(self):
        self.provider._probe = (False, 0, True)
        out = self.provider.system_prompt_block()
        self.assertIn('not signed in', out)
        self.assertIn('memory store is empty', out)

    def test_disabled_and_nomem_have_no_calls(self):
        before = self.recorded()
        self.assertIsNone(self.prefetch('/nomem ordinary query'))
        self.provider._cfg['enabled'] = False
        self.assertIsNone(self.prefetch('ordinary disabled query'))
        self.assertEqual(self.provider.system_prompt_block(), '')
        self.assertEqual(self.recorded(), before)

    def test_sidecar_omits_synthetic_credentials(self):
        os.environ.update(OPENAI_API_KEY='synthetic-value', RSRS_SUPER='synthetic-value')
        env = self.mod.sidecar_env()
        self.assertNotIn('OPENAI_API_KEY', env)
        self.assertNotIn('RSRS_SUPER', env)
        self.assertEqual(env['HOME'], str(self.case/'home'))
        if os.name == 'nt':
            self.assertIn('SystemRoot', env)


def assert_versions(root):
    manifest = re.search(r'^version:\s*(\S+)\s*$', (root/'plugin.yaml').read_text(encoding='utf-8'), re.M).group(1)
    provider = re.search(r'^VERSION = "([^"]+)"', (root/'__init__.py').read_text(encoding='utf-8'), re.M).group(1)
    if manifest != provider:
        raise ValueError(f'plugin.yaml {manifest} != provider VERSION {provider}')


class Versions(unittest.TestCase):
    def test_actual_product_versions_agree(self):
        assert_versions(ROOT)  # Require actual product equality; never skip an inconsistent version.

    def test_each_version_mismatch_is_rejected(self):
        for component in ('manifest','provider'):
            with self.subTest(component=component), tempfile.TemporaryDirectory() as directory:
                fixture = Path(directory)
                for name in ('plugin.yaml','__init__.py'):
                    shutil.copy2(ROOT/name, fixture/name)
                version = re.search(r'^version:\s*(\S+)', (fixture/'plugin.yaml').read_text(encoding='utf-8'), re.M).group(1)
                provider = fixture/'__init__.py'
                provider.write_text(re.sub(r'^VERSION = "[^"]+"', f'VERSION = "{version}"',
                                          provider.read_text(encoding='utf-8'), count=1, flags=re.M), encoding='utf-8')
                assert_versions(fixture)  # aligned synthetic baseline, never the actual product
                path = fixture/('plugin.yaml' if component=='manifest' else '__init__.py')
                pattern, replacement = (r'^version:\s*\S+', 'version: 999.0.0') if component=='manifest' else (r'^VERSION = "[^"]+"','VERSION = "999.0.0"')
                path.write_text(re.sub(pattern,replacement,path.read_text(encoding='utf-8'),count=1,flags=re.M), encoding='utf-8')
                with self.assertRaises(ValueError):
                    assert_versions(fixture)


class AuditPolicy(unittest.TestCase):
    def test_exact_windows_and_posix_invocations(self):
        expected = [r'C:\temp\space name\rsrs.cmd','recall','ordinary query','--json']
        self.assertTrue(allowed_process(None,subprocess.list2cmdline(expected),expected,True))
        self.assertTrue(allowed_process(expected[0],expected,expected,False))

    def test_other_executables_extra_arguments_and_shells_rejected(self):
        expected = [r'C:\temp\rsrs.cmd','recall','ordinary query','--json']
        encoded = subprocess.list2cmdline(expected)
        for executable, argv in [('cmd.exe',encoded),('python.exe',encoded),(None,encoded+' & echo unwanted'),
                                 (None,encoded+' extra'),(None,'cmd.exe /c '+encoded)]:
            self.assertFalse(allowed_process(executable,argv,expected,True))


if __name__ == '__main__':
    sys.addaudithook(audit)
    unittest.main(verbosity=2)
