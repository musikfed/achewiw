"""Verify public FastAPI router, including lazy _IncludedRouter on FastAPI 0.165+."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

class RouteRegistry(unittest.TestCase):
    def test_print_routes_registered(self):
        from main import app, api
        routes={getattr(route, "path", "") for route in api.routes}
        public_paths=set(app.openapi()["paths"])
        expected={
            "/api/projects/{project_id}/print-proof/preflight",
            "/api/projects/{project_id}/print-proof/preview-fit",
            "/api/projects/{project_id}/print-proof/grounded-fit",
            "/api/projects/{project_id}/print-proof/fit-reviews",
        }
        print("FastAPI/OpenAPI print endpoints:",
              sorted(p for p in public_paths if "print-proof" in p))
        self.assertTrue(expected.issubset(routes),
                        f"Not registered in protected router: {expected-routes}")
        self.assertTrue(expected.issubset(public_paths),
                        f"Not published in OpenAPI: {expected-public_paths}")

if __name__=="__main__":
    unittest.main()
