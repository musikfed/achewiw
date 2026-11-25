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
        print("APP ALL first+last:",routes[:15],routes[-15:],"LEN",len(routes))
        print("API routes LEN:",len(raw))
        print("APP OpenAPI print routes:",[p for p in app.openapi()["paths"] if "print" in p or "fit" in p])
        print("Included Router attrs:",[(type(r).__name__,list(vars(r).keys())[:15]) for r in app.routes if not getattr(r,"path",None)])
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
