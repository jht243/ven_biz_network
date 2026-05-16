import unittest
from types import SimpleNamespace


class SDNTrackerTests(unittest.TestCase):
    def test_general_license_notice_is_not_a_designation(self):
        from src.data.sdn_tracker import is_sdn_designation_row

        row = SimpleNamespace(
            article_type="OFAC General License",
            extra_metadata={
                "number": "GL 2A",
                "title": "Venezuela General License 2A",
            },
        )

        self.assertFalse(is_sdn_designation_row(row))

    def test_sdn_diff_row_is_a_designation(self):
        from src.data.sdn_tracker import is_sdn_designation_row

        row = SimpleNamespace(
            article_type="SDN addition",
            extra_metadata={
                "uid": "12345",
                "name": "Example Blocked Person",
                "program": "VENEZUELA-EO13884",
            },
        )

        self.assertTrue(is_sdn_designation_row(row))


if __name__ == "__main__":
    unittest.main()
