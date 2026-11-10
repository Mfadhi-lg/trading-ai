"""Exact source-text conversion, independent of optional PostgreSQL drivers."""
import unittest
from scrapper.db.postgresql_source_text import encode_source_text, decode_source_text

class SourceTextTests(unittest.TestCase):
    def test_exact_roundtrip(self):
        for text in (None, '', 'plain', '₹漢字😀', '\0', '\0a', 'a\0', 'a\0\0b', r'\x00\0'):
            with self.subTest(text=repr(text)):
                self.assertEqual(decode_source_text(encode_source_text(text)), text)

    def test_invalid_types_and_utf8_fail_without_substitution(self):
        with self.assertRaises(UnicodeDecodeError):
            decode_source_text(b'\xff')
        with self.assertRaises(TypeError):
            encode_source_text(b'not text')
        with self.assertRaises(TypeError):
            decode_source_text(42)
