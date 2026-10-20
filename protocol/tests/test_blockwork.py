"""The AI work in a real development-network block decodes to readable text."""
from pathlib import Path
import tempfile
import unittest

from noet.blockwork import Detokenizer, block_work, parse_block
from noet.indexer import Index

RAW = bytes.fromhex((Path(__file__).parent / 'fixtures/block-private-work-250.hex').read_text().strip())
GENESIS = '301587d540aafc312cf4606e700d9662e038e7e43ca6cca92779d6b4baa81b4e'


class BlockWorkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.detokenize = Detokenizer()

    def test_real_block_decodes_prompt_and_proven_window(self):
        block = parse_block(RAW)
        self.assertEqual(block['transactions'], 1)
        work = block_work(RAW, self.detokenize)
        self.assertEqual(work['model'], 'Qwen/Qwen3-0.6B@c1899de289a04d12100db370d81485cdf75e47ca')
        self.assertEqual(work['sampler'], {'temperature': 1.0, 'topK': 50, 'topP': 1.0, 'repetitionPenalty': 1.0})
        self.assertEqual(work['outputTokens'], 256)
        self.assertTrue(work['prompt'].startswith('Research case 250.'))
        self.assertIn('hexadecimal', work['output'])

    def test_byte_level_tokens_round_trip_to_utf8(self):
        tokens = self.detokenize.tokens
        ids = [tokens.index(t) for t in ('Hello', 'Ġworld', 'ĠcafÃ©', '<|im_end|>')]
        self.assertEqual(self.detokenize(ids), 'Hello world café<|im_end|>')
        self.assertEqual(self.detokenize(ids, [0, 1, 0, 0]), 'Hello café<|im_end|>')

    def test_malformed_blocks_are_refused(self):
        for bad in (RAW[:-1], RAW[:200], RAW + b'\x00', b''):
            with self.subTest(size=len(bad)), self.assertRaises(ValueError):
                parse_block(bad)

    def test_indexer_stores_and_serves_the_work(self):
        with tempfile.TemporaryDirectory() as folder:
            index = Index(Path(folder) / 'index.sqlite3', GENESIS)
            with index.db:
                index.db.execute("INSERT INTO blocks VALUES(0,?,NULL,0,'0',0)", (GENESIS,))
                index.db.execute("INSERT INTO blocks VALUES(250,?,?,0,'0',0)", ('ab' * 32, GENESIS))
            index._record_work(lambda method, *args: RAW.hex(), 250, 'ab' * 32)
            self.assertIn('hexadecimal', index.block('250')['work']['output'])
            # An undecodable block is skipped, never fatal.
            index._record_work(lambda method, *args: 'zz', 1, 'cd' * 32)
            self.assertIsNone(index.work(1))


if __name__ == '__main__':
    unittest.main()
