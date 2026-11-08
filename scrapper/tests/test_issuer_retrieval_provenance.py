from __future__ import annotations

import unittest

from scrapper.pipeline.reconcile_issuer_retrieval_provenance import issuer_host_matches


class IssuerRetrievalProvenanceTests(unittest.TestCase):
    def test_exact_and_subdomain_match(self) -> None:
        self.assertTrue(issuer_host_matches("www.example.com", "example.com"))
        self.assertTrue(issuer_host_matches("investors.example.com", "www.example.com"))

    def test_sibling_and_suffix_confusion_do_not_match(self) -> None:
        self.assertFalse(issuer_host_matches("example-cdn.com", "example.com"))
        self.assertFalse(issuer_host_matches("notexample.com", "example.com"))


if __name__ == "__main__":
    unittest.main()
