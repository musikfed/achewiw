"""Step 08: invariant checks for additive studio chat tabs and history API."""
from html.parser import HTMLParser
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"app"))
from editorial_review import snapshot
import chat_conversations as chats
import chat_editorial_version_bridge as versions
import chat_linked_edits as linked

class NodeCollector(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids={}
        self.tabs=[]
        self.scripts=[]
    def handle_starttag(self,tag,attrs):
        x=dict(attrs)
        if x.get("id"):self.ids[x["id"]]=self.ids.get(x["id"],0)+1
        if x.get("data-tab"):self.tabs.append(x["data-tab"])
        if tag=="script":self.scripts.append(x.get("src"))

class StudioUITests(unittest.TestCase):
    def setUp(self):
        self.html=(ROOT/"app/static/index.html").read_text(encoding="utf-8")
        self.js=(ROOT/"app/static/chat_studio.js").read_text(encoding="utf-8")
        self.appjs=(ROOT/"app/static/app.js").read_text(encoding="utf-8")
        self.main=(ROOT/"app/main.py").read_text(encoding="utf-8")

    def test_all_eight_tabs_keep_original_panes(self):
        parser=NodeCollector();parser.feed(self.html)
        self.assertEqual(parser.tabs,[
            "brief","structure","write","illustrations","audit","book",
            "chat-generation","chat-editorial"])
        self.assertTrue(all(parser.ids.get(x)==1 for x in parser.tabs))
        self.assertEqual(parser.ids.get("editor"),1)
        self.assertIn("['brief','structure','write','illustrations','audit','book','chat-generation','chat-editorial']",self.appjs)

    def test_new_project_has_an_in_page_title_form(self):
        parser=NodeCollector();parser.feed(self.html)
        for element in ("projectCreateDialog","projectCreateForm","projectCreateTitle"):
            self.assertEqual(parser.ids.get(element),1)
        self.assertIn('method="dialog"',self.html)
        create=self.appjs.split("async function createProject(kind){",1)[1].split(
            "function clearDeletedProjectBrowserState",1)[0]
        self.assertIn("dialog.showModal()",create)
        self.assertNotIn("prompt(",create)

    def test_project_switch_clears_book_controls_before_restoring_job(self):
        opening=self.appjs.split("async function loadProject(id){",1)[1].split(
            "async function createProject",1)[0]
        reset=self.appjs.split("function resetBookView(){",1)[1].split(
            "async function restoreLatestBook",1)[0]
        self.assertLess(opening.index("resetBookView()"),opening.index("showTab("))
        self.assertLess(opening.index("resetBookView()"),opening.index("await restoreLatestBook()"))
        self.assertIn("bookJobId=null",reset)
        for item in ("bookPause","bookAccept","bookDraftPdfIds"):
            self.assertIn(item,reset)
        self.assertNotIn("/ai/jobs?mode=book",self.appjs)

    def test_assets_and_no_dynamic_html_from_model(self):
        parser=NodeCollector();parser.feed(self.html)
        self.assertEqual(len([s for s in parser.scripts if s and "chat_studio.js" in s]),1)
        self.assertIn('"chat_studio.js"',self.main)
        self.assertNotIn("innerHTML",self.js)
        self.assertIn("textContent=result.diff",self.js)
        self.assertIn("textContent=message",self.js)

    def test_author_action_is_explicit_and_history_restorable(self):
        for marker in ("data-chat-send","data-chat-generate","data-chat-approve",
                       "data-chat-reject","data-chat-preview","data-chat-mode",
                       "data-chat-version","data-chat-scene"):
            self.assertIn(marker,self.html)
        self.assertIn("chat-edition",self.js)
        self.assertIn("recoverPreview",self.js)
        self.assertIn("/decision",self.js)
        self.assertIn("edit",self.js)

    def test_saved_candidate_restored_after_reload_without_manuscript_change(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            project=SimpleNamespace(id="test-ui",title="Мини HDD",target_length="100")
            first="Головка HDD считывает магнитные изменения."
            proposed="Головка HDD регистрирует изменения намагниченности поверхности."
            edition=snapshot(root,project,[{"scene_id":"scene1","title":"Глава 1",
                         "text":first,"complete":True}],source_kind="manuscript")
            versions.attach(root,project_id=project.id,version_id=edition["id"])
            thread=chats.create_thread(root,project_id=project.id,workspace="editorial")
            cmd=chats.submit(root,project_id=project.id,thread_id=thread["id"],
                            message="Уточни формулировку",scope="chapter",
                            source_ref="scene1",source_text=first)
            linked.prepare(root,project_id=project.id,thread_id=thread["id"],
                           chat_proposal_id=cmd["proposal_id"],
                           base_version_id=edition["id"],scene_id="scene1",
                           candidate_text=proposed)
            restored=linked.pending_candidate(root,project_id=project.id,
                         thread_id=thread["id"],chat_proposal_id=cmd["proposal_id"])
            self.assertEqual(restored["status"],"proposed")
            self.assertIn("+Головка",restored["diff"])
            self.assertEqual(versions.current(root,project_id=project.id)["chapters"][0]["text"],first)
            with self.assertRaises(ValueError):
                linked.pending_candidate(root,project_id="another",thread_id=thread["id"],
                                         chat_proposal_id=cmd["proposal_id"])


if __name__=="__main__":
    unittest.main()
