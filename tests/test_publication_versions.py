"""Publication project editorial branches/editions; pure in-memory SQLite tests."""
import json
import sqlite3
import unittest
import uuid

import publication_versions as p


class ProjectEditionTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("CREATE TABLE projects (id TEXT PRIMARY KEY, data TEXT, updated_at TEXT)")
        p.initialize(self.conn)
        self.pid = uuid.uuid4().hex
        self.project = {"id": self.pid, "title": "The comic", "scenes": [
            {"id": uuid.uuid4().hex, "text": "Original text"}]}
        self.conn.execute("INSERT INTO projects VALUES(?,?,?)", (
            self.pid, json.dumps(self.project), "2026-11-24"
        ))
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def test_branch_edits_do_not_modify_main_project(self):
        main = p.create_branch(self.conn, self.pid, "Авторская", medium="comic")
        starting = main["head"]["id"]
        modified = json.loads(json.dumps(self.project))
        modified["scenes"][0]["text"] = "My alternate version"
        next_point = p.checkpoint(self.conn, self.pid, main["id"], "Исправление",
                                  starting, {"pages": [{"number": 1,
                                  "sha256": "abc"}]}, modified,
                                  note="Изменил вступление", important=True,
                                  page_label="Глава 1 · страница 3")
        annotation=p.read_checkpoint(self.conn,self.pid,next_point["id"])
        self.assertEqual(annotation["note"],"Изменил вступление")
        self.assertTrue(annotation["important"])
        self.assertEqual(annotation["page_label"],"Глава 1 · страница 3")
        self.assertEqual(p.read_checkpoint(self.conn, self.pid, starting)[
                         "project"]["scenes"][0]["text"], "Original text")
        self.assertEqual(p.read_checkpoint(self.conn, self.pid, next_point["id"])[
                         "project"]["scenes"][0]["text"], "My alternate version")
        db_state = json.loads(self.conn.execute(
            "SELECT data FROM projects WHERE id=?", (self.pid,)
        ).fetchone()[0])
        self.assertEqual(db_state, self.project)

    def test_fork_old_checkpoint_and_seal_edition(self):
        branch = p.create_branch(self.conn, self.pid, "Набросок", medium="comic")
        old_id = branch["head"]["id"]
        edited = json.loads(json.dumps(self.project))
        edited["title"] = "Changed later"
        new = p.checkpoint(self.conn, self.pid, branch["id"], "Later", old_id,
                           project_state=edited)
        fork = p.create_branch(self.conn, self.pid, "Из редакции 1",
                               medium="comic", from_checkpoint=old_id)
        self.assertEqual(p.read_checkpoint(self.conn, self.pid, fork["head"]["id"])[
                         "project"]["title"], "The comic")
        release = p.seal_edition(self.conn, self.pid, old_id, "Первое издание",
                                 "comic")
        self.assertEqual(release["edition_number"], 1)
        self.assertEqual(p.list_editions(self.conn, self.pid)[0]["checkpoint_id"],
                         old_id)
        self.assertEqual(p.read_checkpoint(self.conn, self.pid, new["id"])[
                         "project"]["title"], "Changed later")

    def test_stale_head_rejected(self):
        branch = p.create_branch(self.conn, self.pid, "Edit")
        old = branch["head"]["id"]
        p.checkpoint(self.conn, self.pid, branch["id"], "A", old)
        with self.assertRaises(p.EditionError) as exc:
            p.checkpoint(self.conn, self.pid, branch["id"], "B", old)
        self.assertEqual(exc.exception.status, 409)

    def test_cross_project_checkpoint_blocked(self):
        other = uuid.uuid4().hex
        self.conn.execute("INSERT INTO projects VALUES(?,?,?)",
                          (other, json.dumps({"id":other,"title":"Other"}), "now"))
        self.conn.commit()
        branch = p.create_branch(self.conn, self.pid, "Edit")
        with self.assertRaises(p.EditionError) as exc:
            p.create_branch(self.conn,other,"Copied",
                            from_checkpoint=branch["head"]["id"])
        self.assertEqual(exc.exception.status, 404)

    def test_bad_manifest_and_wrong_project_state(self):
        branch=p.create_branch(self.conn,self.pid,"Edit")
        with self.assertRaises(p.EditionError):
            p.checkpoint(self.conn,self.pid,branch["id"],"Bad",
                         branch["head"]["id"],manifest={"items":float("nan")})
        with self.assertRaises(p.EditionError):
            p.checkpoint(self.conn,self.pid,branch["id"],"Bad",
                         branch["head"]["id"],
                         project_state={"id":uuid.uuid4().hex})
        self.assertEqual(len(p.list_checkpoints(self.conn,self.pid,branch["id"])),1)

    def test_milestone_metadata_validation_and_history(self):
        branch=p.create_branch(self.conn,self.pid,"Авторская")
        old=branch["head"]["id"]
        point=p.checkpoint(self.conn,self.pid,branch["id"],"Глава 5",old,
                           note="Решил изменить финал",important=True,
                           page_label="Глава 5")
        listed=p.list_checkpoints(self.conn,self.pid,branch["id"])
        self.assertEqual(listed[0]["note"],"Решил изменить финал")
        self.assertEqual(listed[0]["important"],1)
        self.assertEqual(listed[0]["page_label"],"Глава 5")
        with self.assertRaises(p.EditionError):
            p.checkpoint(self.conn,self.pid,branch["id"],"Bad",point["id"],
                         important=1)
        self.assertEqual(len(p.list_checkpoints(self.conn,self.pid,branch["id"])),2)

    def test_checkpoint_checksum_detects_tampering(self):
        branch=p.create_branch(self.conn,self.pid,"Edit")
        cid=branch["head"]["id"]
        self.conn.execute("UPDATE publication_checkpoints SET project_json=? "
                          "WHERE id=?", ('{"id":"forged"}',cid))
        self.conn.commit()
        with self.assertRaises(p.EditionError) as exc:
            p.read_checkpoint(self.conn,self.pid,cid)
        self.assertEqual(exc.exception.status,409)


if __name__ == "__main__":
    unittest.main()
