import os
import tempfile
import unittest
from datetime import date


class InvestmentFactsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.NamedTemporaryFile(delete=False)
        cls.tmp.close()
        os.environ["DATABASE_URL"] = f"sqlite:///{cls.tmp.name}"

        from src.models import init_db

        init_db(force=True)

    @classmethod
    def tearDownClass(cls):
        try:
            os.unlink(cls.tmp.name)
        except OSError:
            pass

    def test_audit_finds_stale_prone_claims(self):
        from scripts.audit_investment_facts import audit

        findings = audit(("templates/venezuela_bonds_restructuring.html.j2",))
        categories = {item["category"] for item in findings}
        self.assertIn("bond_price", categories)
        self.assertIn("money_amount", categories)

    def test_seed_and_load_defaults(self):
        from src.investment_facts import load_investment_fact_map, seed_investment_facts
        from src.models import InvestmentFact, SessionLocal

        db = SessionLocal()
        try:
            seed_investment_facts(db)
            db.commit()
            self.assertGreaterEqual(db.query(InvestmentFact).count(), 10)
        finally:
            db.close()

        facts = load_investment_fact_map()
        self.assertIn("venez_etf_status", facts)
        self.assertIn("source_date", facts["venez_etf_status"])

    def test_refresh_runs_without_sources(self):
        from src.investment_facts import refresh_investment_facts

        result = refresh_investment_facts(date.today())
        self.assertIn("sources_scanned", result)
        self.assertIn("updated", result)


if __name__ == "__main__":
    unittest.main()
