from unittest.mock import patch, AsyncMock
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app, headers={"X-Brain-Client": "test"})


def _scan_body(files, full=True, markers=None):
    return {"machine": "WORK-PC", "root_id": "git", "abs_path": r"C:\work\repos",
            "scanned_at": "2026-09-04T10:00:00Z", "full": full, "files": files, "markers": markers or []}


def test_scan_then_map_then_bind_then_read(tmp_db):
    r = client.post("/workspace/scan", json=_scan_body(
        [{"rel_path": "Daily Reports/TODO-DailyReports.md", "size": 5, "mtime": "2026-09-03T00:00:00Z",
          "sha256": "a", "title": "TO-DO"},
         {"rel_path": "Other/x.txt"}],
        markers=[{"rel_path": "MemoryBrain", "project": "memorybrain"}]))
    assert r.status_code == 200 and r.json()["added"] == 2 and r.json()["markers_bound"] == 1
    r = client.post("/workspace/map", json={"root_id": "git", "folders": [
        {"rel_path": "Daily Reports", "project": "daily-reports", "role": "primary", "confirmed": True},
        {"rel_path": "Other", "project": ""}]})
    assert r.status_code == 200 and r.json()["bound"] == 1 and r.json()["seen"] == 1
    r = client.post("/workspace/bind", json={"project": "daily-reports", "how": "cwd",
                                             "abs_path": r"C:\work\repos\Shared-DailyReports-Map"})
    assert r.status_code == 200 and r.json()["written"] is True and r.json()["row"]["how"] == "cwd"
    r = client.post("/workspace/bind", json={"project": "side-project", "how": "cwd", "abs_path": r"C:\work\repos\side-project",
                                             "confidence": 0.5})
    assert r.json()["row"]["confidence"] == 0.5
    r = client.post("/workspace/bind", json={"project": "x", "how": "cwd", "abs_path": r"D:\nowhere"})
    assert r.status_code == 200 and r.json()["written"] is False and r.json()["reason"] == "path_outside_known_roots"
    m = client.get("/workspace/map").json()
    assert [u["rel_path"] for u in m["unmapped_folders"]] == ["Other"]
    files = client.get("/workspace/files", params={"project": "daily-reports", "sort": "path"}).json()
    assert files["count"] == 1 and files["files"][0]["rel_path"] == "Daily Reports/TODO-DailyReports.md"
    hit = client.get("/workspace/find", params={"query": "todo"}).json()["files"][0]
    ctx = client.get(f"/workspace/file/{hit['file_id']}").json()
    assert ctx["project"] == "daily-reports" and ctx["file"]["title"] == "TO-DO"
    assert client.get("/workspace/file/nope").status_code == 404


def test_project_info_and_admin_routes(tmp_db):
    client.post("/workspace/scan", json=_scan_body([{"rel_path": "Daily Reports/Tools/ReportFlow/a.md"}]))
    r = client.post("/projects/daily-reports/info", json={
        "name": "Daily Reports", "description": "Acme's reporting job.",
        "home_path": r"C:\work\repos\Daily Reports\Tools\ReportFlow", "label": "ReportFlow", "role": "area"})
    assert r.status_code == 200
    body = r.json()
    assert body["identity"]["description_source"] == "tool" and body["files_under"] == 1
    assert body["header"].startswith("**Daily Reports** (daily-reports). Acme's reporting job.")
    r = client.post("/admin/rebuild-file-links")
    assert r.status_code == 200 and set(r.json()) == {"memories", "edges", "dangling", "files_touched"}
    with patch("app.workspace.routes.backfill_descriptions", new_callable=AsyncMock) as m:
        m.return_value = {"drafted": [], "skipped": ["daily-reports"]}
        r = client.post("/admin/backfill-project-descriptions")
    assert r.status_code == 200 and r.json()["skipped"] == ["daily-reports"]


def test_scan_rejects_bad_body():
    assert client.post("/workspace/scan", json={"files": []}).status_code == 422


def test_cwd_bind_never_binds_the_root_and_never_guesses_inside_a_bound_folder(tmp_db):
    client.post("/workspace/scan", json=_scan_body([{"rel_path": "Daily Reports/Tickets/T1/notes.md"}]))
    client.post("/workspace/map", json={"root_id": "git", "folders": [
        {"rel_path": "Daily Reports", "project": "daily-reports", "confirmed": True}]})
    # 1. a session opened at the workspace root must not bind the root
    r = client.post("/workspace/bind", json={"project": "git", "how": "cwd", "abs_path": r"C:\work\repos",
                                             "confidence": 0.5})
    assert r.status_code == 200 and r.json()["written"] is False and r.json()["reason"] == "root_not_bound_from_cwd"
    # 2. a guessed slug inside an owned folder must not create a nested binding
    r = client.post("/workspace/bind", json={"project": "t1", "how": "cwd",
                                             "abs_path": r"C:\work\repos\Daily Reports\Tickets\T1", "confidence": 0.5})
    assert r.json()["written"] is False and r.json()["reason"] == "inside_bound_folder"
    assert r.json()["row"]["project"] == "daily-reports"
    ctx_owner = client.get("/workspace/map").json()["folders"]
    assert not any(f["rel_path"] == "Daily Reports/Tickets/T1" for f in ctx_owner)
    # 3. an explicit marker slug (no confidence) may still declare a nested area
    r = client.post("/workspace/bind", json={"project": "daily-reports", "how": "cwd",
                                             "abs_path": r"C:\work\repos\Daily Reports\Tickets\T1"})
    assert r.json()["written"] is True and r.json()["row"]["rel_path"] == "Daily Reports/Tickets/T1"


def test_cwd_bind_requires_an_absolute_path(tmp_db):
    client.post("/workspace/scan", json=_scan_body([{"rel_path": "Daily Reports/notes.md"}]))
    r = client.post("/workspace/bind", json={"project": "cwd", "how": "cwd", "abs_path": "{{cwd}}", "confidence": 0.5})
    assert r.status_code == 200 and r.json()["written"] is False and r.json()["reason"] == "cwd_path_not_absolute"
    assert not any(f["rel_path"] == "{{cwd}}" for f in client.get("/workspace/map").json()["folders"])


def test_bind_map_and_info_reject_invalid_slugs(tmp_db):
    client.post("/workspace/scan", json=_scan_body([{"rel_path": "Metrics/a.md"}]))
    assert client.post("/workspace/bind", json={"project": "Daily Reports", "how": "tool",
                                                "abs_path": r"C:\work\repos\Metrics"}).status_code == 422
    assert client.post("/workspace/map", json={"root_id": "git", "folders": [
        {"rel_path": "Metrics", "project": "Bad Slug", "confirmed": True}]}).status_code == 422
    assert client.post("/projects/Daily%20Reports/info", json={"description": "x"}).status_code == 422
    slugs = {p["slug"] for p in client.get("/workspace/map").json()["projects"]}
    assert "Daily Reports" not in slugs and "Bad Slug" not in slugs
