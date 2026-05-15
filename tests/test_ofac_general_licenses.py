import unittest


class OFACGeneralLicenseScraperTests(unittest.TestCase):
    def test_extracts_venezuela_general_license_links(self):
        from src.scraper.ofac_general_licenses import (
            OFAC_VENEZUELA_URL,
            _extract_license_links,
        )

        html = """
        <html><body>
          <ul>
            <li>
              <a href="/media/932451/download?inline">
                Venezuela General License 5T
              </a>
              Authorizing certain transactions involving the PdVSA 2020 bond.
            </li>
            <li>
              <a href="/media/not-cuba/download?inline">
                Cuba General License 2
              </a>
            </li>
          </ul>
        </body></html>
        """

        rows = _extract_license_links(html, OFAC_VENEZUELA_URL)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["number"], "GL 5T")
        self.assertIn("debt", rows[0]["scope"])
        self.assertTrue(rows[0]["ofac_url"].startswith("https://ofac.treasury.gov/"))


if __name__ == "__main__":
    unittest.main()
