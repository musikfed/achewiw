"""Compare actual protected router endpoint registrations in test and host."""
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"app"))

class RouteRegistry(unittest.TestCase):
    def test_print_routes_registered(self):
        from main import app,api
        routes=sorted({getattr(route,"path","") for route in app.routes})
        raw=sorted({getattr(route,"path","") for route in api.routes})
        print("APP PRINT ROUTES:",[x for x in routes if "print" in x or "fit" in x])
        print("API PRINT ROUTES:",[x for x in raw if "print" in x or "fit" in x])
        expected=[
          "/api/projects/{project_id}/print-proof/preflight",
          "/api/projects/{project_id}/print-proof/preview-fit",
          "/api/projects/{project_id}/print-proof/grounded-fit",
          "/api/projects/{project_id}/print-proof/fit-reviews",
        ]
        for path in expected:
            with self.subTest(path=path):
                self.assertIn(path,routes)
if __name__=="__main__":
    unittest.main()
