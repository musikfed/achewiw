"""Step 15: publication remains blocked until real office refresh and TOC match."""
from pathlib import Path
import sys
import unittest
from io import BytesIO
from zipfile import ZipFile,ZIP_DEFLATED
from pydantic import BaseModel,Field

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"app"))
from print_release_gate import release_preflight,inspect_docx_toc
from publishing_exports import manuscript_pdf,manuscript_docx

class Scene(BaseModel):
    id:str
    title:str
    text:str

class Book(BaseModel):
    id:str="release-check"
    title:str="Тестовый справочник HDD"
    author_name:str="Тестовый автор"
    publisher_name:str=""
    publication_year:int=2026
    edition_label:str="r04"
    scenes:list[Scene]=Field(default_factory=list)

def book():
    return Book(scenes=[Scene(id="s1",title="Магнитная запись",
                text=("Запись на магнитном диске требует намагниченной поверхности. "
                      "Головка управляет чтением дорожки. ")*20),
             Scene(id="s2",title="Позиционирование",
                text=("Перемещение головки и ожидание сектора относятся к различным этапам. "
                      "Контроллер определяет порядок команд чтения. ")*20)])

class GateTests(unittest.TestCase):
    def test_docx_contains_dynamic_toc(self):
        result=inspect_docx_toc(manuscript_docx(book()))
        self.assertTrue(result["toc_field"],result)
        self.assertTrue(result["update_on_open"])

    def test_true_pdf_is_blocked_without_office_refresh(self):
        p=book()
        result=release_preflight(docx=manuscript_docx(p),
            final_pdf=manuscript_pdf(p,print_proof=True),
            chapter_titles=[x.title for x in p.scenes],office_refreshed=False)
        self.assertEqual(result["status"],"BLOCKED")
        self.assertIn("OFFICE_REFRESH_NOT_VERIFIED",
                      [x["code"] for x in result["blocking"]])

    def test_full_automated_reportlab_proof_check(self):
        # In-process control: ReportLab's own generated PDF carries a real TOC;
        # office_refreshed=True here is *only an attested test fixture*.
        p=book()
        result=release_preflight(docx=manuscript_docx(p),
            final_pdf=manuscript_pdf(p,print_proof=True),
            chapter_titles=[x.title for x in p.scenes],office_refreshed=True)
        self.assertEqual(result["status"],"PASS",result["blocking"])
        self.assertTrue(all(x["match"] is True for x in result["headings"]))
        self.assertGreaterEqual(result["toc_links"]["total_links"],2)

    def test_office_resave_clears_update_flag_but_real_refreshed_toc_can_pass(self):
        # Office sometimes removes the Word-specific update-on-open preference
        # after it actually refreshed the table. This must not falsify the
        # measured PDF / author-approved publication gate.
        original=manuscript_docx(book())
        stream=BytesIO()
        with ZipFile(BytesIO(original)) as source, ZipFile(stream,"w",ZIP_DEFLATED) as target:
            for member in source.namelist():
                data=source.read(member)
                if member=="word/settings.xml":
                    data=data.replace(b'updateFields w:val="true"',b'updateFields w:val="false"')
                    data=data.replace(b'updateFields w:val="1"',b'updateFields w:val="false"')
                target.writestr(member,data)
        refreshed=stream.getvalue()
        self.assertFalse(inspect_docx_toc(refreshed)["update_on_open"])
        pdf=manuscript_pdf(book(),print_proof=True)
        allowed=release_preflight(docx=refreshed,final_pdf=pdf,
            chapter_titles=[x.title for x in book().scenes],office_refreshed=True)
        self.assertEqual(allowed["status"],"PASS",allowed["blocking"])
        self.assertTrue(any(x["code"]=="UPDATE_FLAG_CLEARED_BY_OFFICE"
                            for x in allowed["warnings"]))
        forbidden=release_preflight(docx=refreshed,final_pdf=pdf,
            chapter_titles=[x.title for x in book().scenes],office_refreshed=False)
        self.assertEqual(forbidden["status"],"BLOCKED")

    def test_missing_chapter_prevents_publication(self):
        p=book()
        result=release_preflight(docx=manuscript_docx(p),
            final_pdf=manuscript_pdf(p,print_proof=True),
            chapter_titles=["Несуществующая глава"],office_refreshed=True)
        self.assertFalse(result["publication_allowed"])
        self.assertIn("TOC_MISMATCH_OR_UNKNOWN",
                      [x["code"] for x in result["blocking"]])

if __name__=="__main__":
    unittest.main()
