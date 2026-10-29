"""Synthetic transport fixtures for research-client contracts."""
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from research import collect, request_spec, write_receipt

class Response(io.BytesIO):
    status = 200
    headers = {'Content-Type': 'application/json'}

class Opener:
    def __init__(self, body):
        self.body, self.calls = body, []
    def open(self, request, timeout):
        self.calls.append((request, timeout))
        if isinstance(self.body, Exception):
            raise self.body
        return Response(self.body)

class ResearchTests(unittest.TestCase):
    def test_success_is_metadata_not_verified(self):
        opener = Opener(b'{"code":0,"data":{"title":"synthetic"}}')
        r = collect('inspect', 'BV1kt411a7oz', opener=opener)
        self.assertEqual(r['status'], 'collected')
        self.assertEqual(r['access_level'], 'metadata')
        self.assertEqual(len(opener.calls), 1)
        self.assertNotIn('Cookie', opener.calls[0][0].headers)
    def test_api_refusal_is_not_retried(self):
        opener = Opener(b'{"code":-412,"message":"refused"}')
        r = collect('search', '合成测试', opener=opener)
        self.assertEqual(r['status'], 'refused')
        self.assertEqual(len(opener.calls), 1)
    def test_http_refusal_is_preserved(self):
        opener = Opener(HTTPError('https://api.bilibili.com', 412, 'blocked', {}, None))
        r = collect('search', '合成测试', opener=opener)
        self.assertEqual((r['status'], r['http_status']), ('refused', 412))
    def test_malformed_response_is_unavailable(self):
        for data in [b'not json', b'[]', b'{"code":true}', b'{"code":0,"data":null}']:
            self.assertEqual(collect('search', '测试', opener=Opener(data))['status'], 'unavailable')
    def test_duplicate_fields_and_nonstandard_numbers_are_unavailable(self):
        bodies = (
            b'{"code":-412,"code":0,"data":{"title":"synthetic"}}',
            b'{"code":0,"data":{"value":NaN}}',
            b'{"code":0,"data":{"value":Infinity}}',
        )
        for body in bodies:
            opener = Opener(body)
            with self.subTest(body=body):
                receipt = collect('search', '合成测试', opener=opener)
                self.assertEqual(receipt['status'], 'unavailable')
                self.assertEqual(receipt['attempts'], 1)
                self.assertEqual(len(opener.calls), 1)
                self.assertNotIn('metadata', receipt)
    def test_numeric_responses_always_preserve_a_serializable_receipt(self):
        import research
        with tempfile.TemporaryDirectory() as folder:
            for index, (number, expected_code, expected_status) in enumerate((
                    ('1e308', 0, 'collected'), ('-1e308', 0, 'collected'),
                    ('1e309', 3, 'unavailable'), ('-1e309', 3, 'unavailable'),
                    ('NaN', 3, 'unavailable'), ('Infinity', 3, 'unavailable'),
                    ('-Infinity', 3, 'unavailable'))):
                body = ('{"code":0,"data":{"value":' + number + '}}').encode('ascii')
                opener = Opener(body)
                destination = Path(folder) / f'number-{index}.json'
                with self.subTest(number=number), patch.object(research, 'build_opener', return_value=opener):
                    self.assertEqual(research.main(['search', '合成数值', '--output', str(destination)]), expected_code)
                    receipt = json.loads(destination.read_bytes())
                    self.assertEqual(receipt['status'], expected_status)
                    self.assertEqual(len(opener.calls), 1)
                    self.assertEqual(receipt['body_bytes'], len(body))
                    self.assertIn('body_sha256', receipt)
                    if expected_code:
                        self.assertNotIn('metadata', receipt)

    def test_arbitrary_target_rejected(self):
        for action, value in [('inspect','https://localhost'),('search',''),('other','test')]:
            with self.assertRaises(ValueError): request_spec(action, value)
    def test_receipt_cannot_overwrite(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'receipt.json'
            write_receipt(path, {'status':'test'})
            with self.assertRaises(FileExistsError): write_receipt(path, {})
            self.assertEqual(json.loads(path.read_text()), {'status':'test'})

    def test_receipt_cannot_be_written_anywhere_inside_skill_root(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)/'bilibili-meme-atlas';(root/'tests').mkdir(parents=True)
            path=root/'tests/receipt.json'
            with self.assertRaisesRegex(ValueError,'技能目录'):
                write_receipt(path,{'status':'test'},root=root)
            self.assertFalse(path.exists())

    def test_deep_json_preserves_unavailable_receipt(self):
        import research
        import update
        body=b'{"code":0,"data":{"x":'+b'['*10000+b'0'+b']'*10000+b'}}'
        with tempfile.TemporaryDirectory() as folder:
            for module,args in ((research,['search','合成']),(update,['discover'])):
                path=Path(folder)/(module.__name__+'.json')
                opener=Opener(body)
                with patch.object(module,'build_opener',return_value=opener):code=module.main(args+['--output',str(path)])
                self.assertEqual(code,3 if module==research else 2)
                self.assertEqual(json.loads(path.read_text())['status'],'unavailable')
                self.assertEqual(len(opener.calls),1)

    def test_refused_http_body_timeout_still_saves_known_refusal(self):
        import research
        class BrokenBody(io.BytesIO):
            def read(self,*args): raise TimeoutError('synthetic rejection body timeout')
        opener=Opener(HTTPError('https://api.bilibili.com',412,'refused',{},BrokenBody()))
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'receipt.json'
            with patch.object(research,'build_opener',return_value=opener):
                self.assertEqual(research.main(['search','合成','--output',str(path)]),3)
            receipt=json.loads(path.read_text())
            self.assertEqual((receipt['status'],receipt['http_status']),('refused',412))
            self.assertIn('error_body_read_error',receipt)

    def test_refused_http_body_is_not_copied_into_receipt(self):
        body=io.BytesIO(b'synthetic private rejection detail')
        receipt=collect('search','合成测试',opener=Opener(
            HTTPError('https://api.bilibili.com',412,'refused',{},body)))
        self.assertEqual(receipt['status'],'refused')
        self.assertNotIn('error_body_excerpt',receipt)
        self.assertNotIn('synthetic private rejection detail',json.dumps(receipt))

    def test_json_depth_limit_precedes_receipt_serialization(self):
        import research
        with tempfile.TemporaryDirectory() as folder:
            for depth,expected in ((10,0),(1000,3),(1500,3)):
                body=b'{"code":0,"data":{"x":'+b'['*depth+b'0'+b']'*depth+b'}}'
                path=Path(folder)/f'depth-{depth}.json'
                with patch.object(research,'build_opener',return_value=Opener(body)):
                    self.assertEqual(research.main(['search','合成','--output',str(path)]),expected)
                data=json.loads(path.read_text())
                self.assertEqual(data['status'],'collected' if expected==0 else 'unavailable')
                if expected:self.assertLess(path.stat().st_size,5000)

    def test_unserializable_receipt_does_not_create_output(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'invalid.json';cyclic={};cyclic['self']=cyclic
            with self.assertRaises(ValueError):write_receipt(path,cyclic)
            self.assertFalse(path.exists())
