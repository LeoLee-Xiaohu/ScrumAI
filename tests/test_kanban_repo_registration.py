import argparse
import io
import json
from contextlib import redirect_stdout

import main
from mcp_adapter import McpClient, McpOrganization, McpProject


class _FakeHttpResponse:
    def __init__(self, status: int, body: str):
        self.status = status
        self._body = body.encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_register_repo_backend_posts_expected_payload(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["method"] = request.get_method()
        captured["body"] = request.data.decode("utf-8")
        return _FakeHttpResponse(
            200,
            json.dumps(
                {
                    "success": True,
                    "data": {
                        "id": "repo-1",
                        "name": "NeckFlappy",
                        "path": "/tmp/NeckFlappy",
                    },
                }
            ),
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    client = McpClient.__new__(McpClient)
    success, repo, message = client.register_repo(
        path="/tmp/NeckFlappy",
        display_name="NeckFlappy",
        backend_url="http://127.0.0.1:63861",
    )

    assert success is True
    assert repo["id"] == "repo-1"
    assert message == ""
    assert captured["url"] == "http://127.0.0.1:63861/api/repos"
    assert captured["method"] == "POST"
    assert json.loads(captured["body"]) == {
        "path": "/tmp/NeckFlappy",
        "display_name": "NeckFlappy",
    }


def test_register_kanban_repo_reports_backend_connectivity_failure(monkeypatch):
    class FakeClient:
        def resolve_backend_url(self, backend_url=None):
            return "http://127.0.0.1:63861"

        def check_backend_health(self, backend_url=None):
            return False, "Could not connect to Vibe Kanban backend at http://127.0.0.1:63861: [Errno 61] Connection refused"

        def close(self):
            pass

    monkeypatch.setattr("mcp_adapter.McpClient", lambda: FakeClient())

    args = argparse.Namespace(
        path="/tmp/NeckFlappy",
        display_name="NeckFlappy",
        default_branch="main",
        project_name="NeckFlappy",
        project_id=None,
        backend_url=None,
    )

    out = io.StringIO()
    try:
        with redirect_stdout(out):
            main.cmd_register_kanban_repo(args)
    except SystemExit as exc:
        assert exc.code == 1
    else:
        raise AssertionError("Expected SystemExit when backend is unreachable")

    output = out.getvalue()
    assert "Vibe Kanban backend is not reachable." in output
    assert "Optional check: curl http://127.0.0.1:63861/api/health" in output
    assert "--backend-url http://127.0.0.1:<port>" in output


def test_register_kanban_repo_binds_repo_to_project_defaults(monkeypatch):
    class FakeClient:
        def __init__(self):
            self.updated_repo = None
            self.saved_defaults = None

        def resolve_backend_url(self, backend_url=None):
            return "http://127.0.0.1:61052"

        def check_backend_health(self, backend_url=None):
            assert backend_url == "http://127.0.0.1:61052"
            return True, "OK"

        def register_repo(self, path, display_name=None, backend_url=None):
            assert path == "/tmp/NeckFlappy"
            assert display_name == "NeckFlappy"
            return True, {"id": "repo-1", "name": "NeckFlappy", "path": path}, ""

        def update_repo_http(self, repo_id, updates, backend_url=None):
            self.updated_repo = (repo_id, updates)
            return True, {"id": repo_id, "default_target_branch": updates["default_target_branch"]}, ""

        def list_organizations(self):
            return [McpOrganization(id="org-1", name="Personal")]

        def list_projects(self, organization_id):
            assert organization_id == "org-1"
            return [McpProject(id="project-1", name="NeckFlappy", created_at="", updated_at="")]

        def get_project_repo_defaults(self, project_id, backend_url=None):
            assert project_id == "project-1"
            return True, [{"repo_id": "repo-old", "target_branch": "develop"}], ""

        def set_project_repo_defaults(self, project_id, repos, backend_url=None):
            self.saved_defaults = (project_id, repos)
            return True, {"id": project_id}, ""

        def close(self):
            pass

    fake_client = FakeClient()
    monkeypatch.setattr("mcp_adapter.McpClient", lambda: fake_client)

    args = argparse.Namespace(
        path="/tmp/NeckFlappy",
        display_name="NeckFlappy",
        default_branch="main",
        project_name="NeckFlappy",
        project_id=None,
        backend_url=None,
    )

    out = io.StringIO()
    with redirect_stdout(out):
        main.cmd_register_kanban_repo(args)

    assert fake_client.updated_repo == (
        "repo-1",
        {"default_target_branch": "main"},
    )
    assert fake_client.saved_defaults == (
        "project-1",
        [
            {"repo_id": "repo-old", "target_branch": "develop"},
            {"repo_id": "repo-1", "target_branch": "main"},
        ],
    )
    assert "Set project repo default: project=NeckFlappy" in out.getvalue()


def test_resolve_backend_url_uses_discovered_port(monkeypatch):
    client = McpClient.__new__(McpClient)
    monkeypatch.delenv("VIBE_BACKEND_URL", raising=False)
    monkeypatch.setattr(McpClient, "_discover_backend_url", staticmethod(lambda: "http://127.0.0.1:61052"))

    assert client.resolve_backend_url(None) == "http://127.0.0.1:61052"


def test_get_project_repo_defaults_treats_missing_scratch_as_empty(monkeypatch):
    client = McpClient.__new__(McpClient)

    monkeypatch.setattr(
        client,
        "_backend_request",
        lambda path, method, payload=None, backend_url=None: (
            False,
            None,
            "HTTP 400: Scratch not found",
        ),
    )

    success, repos, message = client.get_project_repo_defaults("project-1")

    assert success is True
    assert repos is None
    assert message == "HTTP 400: Scratch not found"
