from __future__ import annotations

import base64
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from connectors_hub.app import create_app
from connectors_hub.approvals import ActionError, ApprovalStore, _validate_path
from connectors_hub.config import ConfigurationError, Settings, validate_supabase_url
from connectors_hub.credentials import CredentialRejected, CredentialVault, VaultError
from connectors_hub.crypto import PayloadCipher
from connectors_hub.http_api import ProviderError, _assert_trusted_base
from connectors_hub.models import (
    ActionProposalRequest,
    ConnectorId,
    ReadRequest,
    SearchRequest,
)
from connectors_hub.read import read_one
from connectors_hub.search import search_many, search_one


class MemoryKeyring:
    """In-memory test double that satisfies the secure-backend interface."""

    priority = 1.0

    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self.values.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.values[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        self.values.pop((service, username), None)


class PlaintextKeyring(MemoryKeyring):
    priority = 1.0


class ConfigAndCredentialTests(unittest.TestCase):
    def test_supabase_search_columns_default_to_empty(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "CONNECTORS_HUB_DATA_DIR": tempfile.gettempdir(),
                "CONNECTORS_HUB_SUPABASE_URL": "",
                "CONNECTORS_HUB_SUPABASE_SEARCH_TABLES": "",
                "CONNECTORS_HUB_PORT": "8765",
            },
            clear=True,
        ):
            settings = Settings.from_env()
        self.assertEqual(settings.supabase_search_columns, ())
        self.assertEqual(settings.supabase_search_tables, frozenset())

    def test_supabase_url_is_limited_to_project_host_or_loopback(self) -> None:
        self.assertEqual(
            validate_supabase_url("https://example.supabase.co/"),
            "https://example.supabase.co",
        )
        self.assertEqual(
            validate_supabase_url("http://localhost:54321"), "http://localhost:54321"
        )
        for unsafe in (
            "http://example.supabase.co",
            "https://example.supabase.co/path",
            "https://example.supabase.co.evil.test",
            "https://user@example.supabase.co",
            "https://example.supabase.co/?token=value",
        ):
            with self.subTest(unsafe=unsafe), self.assertRaises(ConfigurationError):
                validate_supabase_url(unsafe)

    def test_runtime_supabase_base_is_revalidated(self) -> None:
        settings = Settings(
            data_dir=Path(tempfile.gettempdir()),
            supabase_url="https://attacker.invalid",
        )
        with self.assertRaises(ProviderError):
            _assert_trusted_base(
                __import__("connectors_hub.catalog", fromlist=["SPECS"]).SPECS[
                    ConnectorId.SUPABASE
                ],
                settings,
            )

    def test_vault_refuses_plaintext_backend_and_supabase_admin_keys(self) -> None:
        with self.assertRaises(VaultError):
            CredentialVault(PlaintextKeyring()).set("github_token", "not-stored")
        with (
            patch("keyring.get_keyring", return_value=MemoryKeyring()),
            self.assertRaises(VaultError),
        ):
            CredentialVault().get("github_token")

        vault = CredentialVault(MemoryKeyring())
        with self.assertRaises(CredentialRejected):
            vault.set("supabase_publishable_key", "sb_secret_example")
        with self.assertRaises(CredentialRejected):
            vault.set("supabase_user_jwt", "service_role_example")

        claims = (
            base64.urlsafe_b64encode(json.dumps({"role": "service_role"}).encode())
            .decode()
            .rstrip("=")
        )
        with self.assertRaises(CredentialRejected):
            vault.set("supabase_user_jwt", f"header.{claims}.signature")

    def test_cipher_authenticates_payload_and_does_not_recreate_missing_key_on_read(
        self,
    ) -> None:
        backend = MemoryKeyring()
        vault = CredentialVault(backend)
        cipher = PayloadCipher(vault)
        sealed = cipher.encrypt_json(
            {"body": "private-value"}, associated_data=b"action-id"
        )
        self.assertNotIn("private-value", sealed)
        self.assertEqual(
            cipher.decrypt_json(sealed, associated_data=b"action-id"),
            {"body": "private-value"},
        )
        with self.assertRaises(VaultError):
            cipher.decrypt_json(sealed, associated_data=b"different-id")

        vault.delete("action_encryption_key")
        with self.assertRaises(VaultError):
            cipher.decrypt_json(sealed, associated_data=b"action-id")
        self.assertIsNone(vault.get("action_encryption_key"))


class ApprovalStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temp.name)
        self.vault = CredentialVault(MemoryKeyring())
        self.vault.set("github_token", "github-test-token")
        self.store = ApprovalStore(self.data_dir / "hub.sqlite3", self.vault)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _proposal(self, *, path: str = "/repos/example/repo/issues"):
        return ActionProposalRequest(
            connector=ConnectorId.GITHUB,
            summary="Create a test issue",
            method="POST",
            path=path,
            payload={"title": "encrypted-sentinel", "body": "test body"},
        )

    def test_proposal_payload_is_encrypted_and_bad_phrase_never_sends(self) -> None:
        action = self.store.propose(self._proposal())
        with sqlite3.connect(self.data_dir / "hub.sqlite3") as conn:
            encrypted = conn.execute(
                "SELECT encrypted_request FROM actions WHERE id=?", (action.id,)
            ).fetchone()[0]
        self.assertNotIn("encrypted-sentinel", encrypted)

        preview = self.store.get_preview(action.id)
        self.assertEqual(preview.payload["title"], "encrypted-sentinel")
        with self.assertRaises(ActionError):
            self.store.execute_after_local_approval(
                action.id,
                "APPROVE-XXXXXXXX",
                settings=Settings(data_dir=self.data_dir),
            )
        self.assertEqual(self.store.get_status(action.id).status, "pending_approval")

    def test_local_approval_sends_once_and_success_is_terminal(self) -> None:
        action = self.store.propose(self._proposal())
        sent: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            sent.append(request)
            return httpx.Response(201, json={"number": 7})

        client = httpx.Client(transport=httpx.MockTransport(handler))
        result = self.store.execute_after_local_approval(
            action.id,
            f"APPROVE-{action.id[:8].upper()}",
            settings=Settings(data_dir=self.data_dir),
            client=client,
        )
        self.assertEqual(result.status, "executed")
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0].method, "POST")
        self.assertEqual(sent[0].url.path, "/repos/example/repo/issues")
        self.assertIn("Bearer github-test-token", sent[0].headers["authorization"])
        with self.assertRaises(ActionError):
            self.store.execute_after_local_approval(
                action.id,
                f"APPROVE-{action.id[:8].upper()}",
                settings=Settings(data_dir=self.data_dir),
                client=client,
            )
        self.assertEqual(len(sent), 1)
        client.close()

    def test_missing_credential_leaves_action_pending_without_sending(self) -> None:
        self.vault.delete("github_token")
        action = self.store.propose(self._proposal())
        self.store.get_status(action.id)
        with self.assertRaises(ActionError):
            self.store.execute_after_local_approval(
                action.id,
                f"APPROVE-{action.id[:8].upper()}",
                settings=Settings(data_dir=self.data_dir),
            )
        self.assertEqual(self.store.get_status(action.id).status, "pending_approval")

    def test_uncertain_http_outcome_is_not_reported_as_failure_or_retried(self) -> None:
        action = self.store.propose(self._proposal())
        attempts = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            return httpx.Response(503, json={"error": "may have been applied"})

        client = httpx.Client(transport=httpx.MockTransport(handler))
        result = self.store.execute_after_local_approval(
            action.id,
            f"APPROVE-{action.id[:8].upper()}",
            settings=Settings(data_dir=self.data_dir),
            client=client,
        )
        self.assertEqual((result.status, result.response_status), ("unknown", 503))
        self.assertEqual(attempts, 1)
        client.close()

    def test_rejects_path_traversal_encoded_controls_and_secret_query_parameters(
        self,
    ) -> None:
        for path in (
            "//example.test/a",
            "/../users",
            "/%2e%2e/users",
            "/%252e%252e/users",
            "/files/%0Aheader",
            "/files/" + chr(0x202E) + "txt.exe",
            "https://example.test/path",
            "/users?token=secret",
        ):
            with self.subTest(path=path), self.assertRaises(ActionError):
                _validate_path(path)

        with self.assertRaises(ActionError):
            self.store.propose(
                ActionProposalRequest(
                    connector=ConnectorId.GITHUB,
                    summary="Create test issue",
                    method="POST",
                    path="/repos/example/repo/issues",
                    query={"access_token": "must-not-be-stored"},
                )
            )
        with self.assertRaises(ActionError):
            self.store.propose(
                ActionProposalRequest(
                    connector=ConnectorId.GITHUB,
                    summary="        ",
                    method="POST",
                    path="/repos/example/repo/issues",
                )
            )
        with self.assertRaises(ActionError):
            self.store.get_preview("not-an-action-id")

    def test_integrity_mismatch_prevents_preview(self) -> None:
        action = self.store.propose(self._proposal())
        with sqlite3.connect(self.data_dir / "hub.sqlite3") as conn:
            conn.execute(
                "UPDATE actions SET path='/repos/attacker/repo/issues' WHERE id=?",
                (action.id,),
            )
        with self.assertRaises(ActionError):
            self.store.get_preview(action.id)


class SearchTests(unittest.IsolatedAsyncioTestCase):
    async def test_all_twelve_search_dispatchers_work_with_mocked_provider_responses(
        self,
    ) -> None:
        backend = MemoryKeyring()
        vault = CredentialVault(backend)
        for name, value in (
            ("dropbox_access_token", "dropbox-token"),
            ("github_token", "github-token"),
            ("google_access_token", "google-token"),
            ("linear_api_key", "linear-token"),
            ("notion_integration_token", "notion-token"),
            ("supabase_publishable_key", "sb_publishable-test"),
            ("supabase_user_jwt", "user-scoped-test-jwt"),
            ("vercel_token", "vercel-token"),
        ):
            vault.set(name, value)
        settings = Settings(
            data_dir=Path(tempfile.gettempdir()),
            supabase_url="https://sample.supabase.co",
            supabase_search_tables=frozenset({"documents"}),
            supabase_search_columns=("title", "content"),
        )
        seen: list[httpx.Request] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            path = request.url.path
            if path.endswith("/files/search_v2"):
                return httpx.Response(
                    200,
                    json={
                        "matches": [
                            {
                                "metadata": {
                                    "metadata": {"id": "dbx-1", "name": "Roadmap.txt"}
                                }
                            }
                        ]
                    },
                )
            if path.endswith("/search/issues"):
                return httpx.Response(
                    200,
                    json={
                        "items": [
                            {
                                "id": 1,
                                "title": "Issue",
                                "html_url": "https://github.com/example/repo/issues/1",
                            }
                        ]
                    },
                )
            if path.endswith("/search/repositories"):
                return httpx.Response(
                    200,
                    json={
                        "items": [
                            {
                                "id": 2,
                                "full_name": "example/roadmap",
                                "html_url": "https://github.com/example/roadmap",
                            }
                        ]
                    },
                )
            if path.endswith("/gmail/v1/users/me/messages"):
                return httpx.Response(
                    200, json={"messages": [{"id": "gmail-message-1"}]}
                )
            if path.endswith("/gmail/v1/users/me/messages/gmail-message-1"):
                return httpx.Response(
                    200,
                    json={
                        "snippet": "Gmail snippet",
                        "internalDate": "1700000000",
                        "payload": {
                            "headers": [
                                {"name": "Subject", "value": "Mail result"},
                                {"name": "From", "value": "sender@example.test"},
                            ]
                        },
                    },
                )
            if path.endswith("/calendars/primary/events"):
                return httpx.Response(
                    200,
                    json={"items": [{"id": "event-1", "summary": "Calendar result"}]},
                )
            if path.endswith("/drive/v3/files"):
                return httpx.Response(
                    200, json={"files": [{"id": "drive-1", "name": "Drive result"}]}
                )
            if path.endswith("/graphql"):
                return httpx.Response(
                    200,
                    json={
                        "data": {
                            "issueSearch": {
                                "nodes": [
                                    {
                                        "id": "linear-1",
                                        "identifier": "LIN-1",
                                        "title": "Linear result",
                                    }
                                ]
                            }
                        }
                    },
                )
            if path.endswith("/v1/search"):
                return httpx.Response(
                    200,
                    json={
                        "results": [
                            {
                                "id": "notion-1",
                                "object": "page",
                                "title": [{"plain_text": "Notion result"}],
                            }
                        ]
                    },
                )
            if path.endswith("/rest/v1/documents"):
                return httpx.Response(
                    200,
                    json=[
                        {
                            "id": "row-1",
                            "title": "Supabase result",
                            "content": "document",
                        }
                    ],
                )
            if path.endswith("/v9/projects"):
                return httpx.Response(
                    200,
                    json={"projects": [{"id": "project-1", "name": "Vercel result"}]},
                )
            raise AssertionError(f"Unexpected mocked path: {path}")

        found: set[ConnectorId] = set()
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            for connector in ConnectorId:
                with self.subTest(connector=connector.value):
                    response = await search_one(
                        SearchRequest(query="roadmap", resource="documents", limit=5),
                        connector,
                        client=client,
                        settings=settings,
                        vault=vault,
                    )
                    self.assertTrue(response, f"No mock results for {connector.value}")
                    self.assertTrue(
                        all(result.connector == connector for result in response)
                    )
                    found.add(connector)

        self.assertEqual(set(ConnectorId), found)
        self.assertGreaterEqual(
            len(seen), 13
        )  # Gmail hydration plus GitHub's two search endpoints.

    async def test_empty_connector_list_does_not_expand_to_all_services(self) -> None:
        response = await search_many(
            SearchRequest(query="anything", connectors=[]),
            settings=Settings(data_dir=Path(tempfile.gettempdir())),
            vault=CredentialVault(MemoryKeyring()),
        )
        self.assertEqual(response.searched, [])
        self.assertEqual(response.skipped, [])

    async def test_supabase_is_skipped_without_explicit_table_and_columns(self) -> None:
        vault = CredentialVault(MemoryKeyring())
        vault.set("supabase_publishable_key", "sb_publishable-test")
        vault.set("supabase_user_jwt", "user-jwt")
        settings = Settings(
            data_dir=Path(tempfile.gettempdir()),
            supabase_url="https://sample.supabase.co",
        )
        request = SearchRequest(query="anything", connectors=[ConnectorId.SUPABASE])
        response = await search_many(request, settings=settings, vault=vault)
        self.assertEqual(response.searched, [])
        self.assertEqual(response.skipped, [ConnectorId.SUPABASE])

    async def test_supabase_search_rejects_filter_injection_and_untrusted_hosts(
        self,
    ) -> None:
        vault = CredentialVault(MemoryKeyring())
        vault.set("supabase_publishable_key", "sb_publishable-test")
        vault.set("supabase_user_jwt", "user-jwt")
        settings = Settings(
            data_dir=Path(tempfile.gettempdir()),
            supabase_url="https://sample.supabase.co",
            supabase_search_tables=frozenset({"documents"}),
            supabase_search_columns=("title",),
        )

        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=[])

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with self.assertRaises(ProviderError):
                await search_one(
                    SearchRequest(
                        query="x),admin=true",
                        connectors=[ConnectorId.SUPABASE],
                        resource="documents",
                    ),
                    ConnectorId.SUPABASE,
                    client=client,
                    settings=settings,
                    vault=vault,
                )

            unsafe_settings = Settings(
                data_dir=Path(tempfile.gettempdir()),
                supabase_url="https://attacker.invalid",
                supabase_search_tables=frozenset({"documents"}),
                supabase_search_columns=("title",),
            )
            with self.assertRaises(ProviderError):
                await search_one(
                    SearchRequest(query="roadmap", resource="documents"),
                    ConnectorId.SUPABASE,
                    client=client,
                    settings=unsafe_settings,
                    vault=vault,
                )


class ReadTests(unittest.IsolatedAsyncioTestCase):
    async def test_read_dispatchers_cover_all_twelve_services_with_mocked_data(
        self,
    ) -> None:
        vault = CredentialVault(MemoryKeyring())
        for name, value in (
            ("dropbox_access_token", "dropbox-token"),
            ("github_token", "github-token"),
            ("google_access_token", "google-token"),
            ("linear_api_key", "linear-token"),
            ("notion_integration_token", "notion-token"),
            ("supabase_publishable_key", "sb_publishable-test"),
            ("supabase_user_jwt", "user-scoped-test-jwt"),
            ("vercel_token", "vercel-token"),
        ):
            vault.set(name, value)
        settings = Settings(
            data_dir=Path(tempfile.gettempdir()),
            supabase_url="https://sample.supabase.co",
            supabase_search_tables=frozenset({"documents"}),
            supabase_search_columns=("title", "content"),
        )
        gmail_text = base64.urlsafe_b64encode(b"Hello from Gmail").decode().rstrip("=")
        github_file_text = base64.b64encode(b"Hello from GitHub").decode()
        seen: list[httpx.Request] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            path = request.url.path
            if request.url.host == "api.dropboxapi.com" and path.endswith(
                "/files/get_metadata"
            ):
                return httpx.Response(
                    200, json={"name": "notes.txt", "size": 17, "id": "dbx-1"}
                )
            if request.url.host == "content.dropboxapi.com":
                return httpx.Response(200, content=b"Dropbox text file")
            if request.url.host == "api.github.com" and path.endswith("/issues/7"):
                return httpx.Response(200, json={"id": 7, "title": "GitHub issue"})
            if request.url.host == "api.github.com" and path.endswith(
                "/contents/README.md"
            ):
                return httpx.Response(
                    200,
                    json={
                        "encoding": "base64",
                        "content": github_file_text,
                        "size": 16,
                        "name": "README.md",
                        "path": "README.md",
                    },
                )
            if request.url.host == "api.github.com" and path.endswith(
                "/repos/acme/repo"
            ):
                return httpx.Response(200, json={"id": 10, "full_name": "acme/repo"})
            if request.url.host == "gmail.googleapis.com":
                return httpx.Response(
                    200,
                    json={
                        "id": "message-1",
                        "threadId": "thread-1",
                        "snippet": "mail snippet",
                        "payload": {
                            "mimeType": "text/plain",
                            "headers": [{"name": "Subject", "value": "Gmail subject"}],
                            "body": {"data": gmail_text},
                        },
                    },
                )
            if request.url.host == "www.googleapis.com" and path.endswith(
                "/events/event-1"
            ):
                return httpx.Response(
                    200, json={"id": "event-1", "summary": "Calendar event"}
                )
            if request.url.host == "www.googleapis.com" and path.endswith(
                "/files/file-1"
            ):
                if request.url.params.get("alt") == "media":
                    return httpx.Response(
                        200,
                        content=b"Drive text",
                        headers={"content-type": "text/plain"},
                    )
                return httpx.Response(
                    200,
                    json={
                        "id": "file-1",
                        "name": "notes.txt",
                        "mimeType": "text/plain",
                        "size": "10",
                    },
                )
            if request.url.host == "docs.googleapis.com":
                return httpx.Response(
                    200,
                    json={
                        "documentId": "doc-1",
                        "title": "Docs document",
                        "body": {
                            "content": [
                                {
                                    "paragraph": {
                                        "elements": [
                                            {"textRun": {"content": "Docs text"}}
                                        ]
                                    }
                                }
                            ]
                        },
                    },
                )
            if request.url.host == "sheets.googleapis.com":
                return httpx.Response(
                    200, json={"range": "Sheet1!A1:Z100", "values": [["Sheet cell"]]}
                )
            if request.url.host == "slides.googleapis.com":
                return httpx.Response(
                    200,
                    json={
                        "presentationId": "slides-1",
                        "title": "Slides deck",
                        "slides": [
                            {
                                "objectId": "slide-1",
                                "pageElements": [
                                    {
                                        "shape": {
                                            "text": {
                                                "textElements": [
                                                    {
                                                        "textRun": {
                                                            "content": "Slide text"
                                                        }
                                                    }
                                                ]
                                            }
                                        }
                                    }
                                ],
                            }
                        ],
                    },
                )
            if request.url.host == "api.linear.app":
                return httpx.Response(
                    200,
                    json={
                        "data": {
                            "issue": {
                                "id": "linear-1",
                                "identifier": "LIN-7",
                                "title": "Linear issue",
                            }
                        }
                    },
                )
            if request.url.host == "api.notion.com" and path.endswith("/pages/page-1"):
                return httpx.Response(
                    200,
                    json={
                        "id": "page-1",
                        "url": "https://notion.so/page-1",
                        "properties": {},
                    },
                )
            if request.url.host == "api.notion.com" and path.endswith(
                "/blocks/page-1/children"
            ):
                return httpx.Response(
                    200,
                    json={
                        "results": [
                            {
                                "id": "block-1",
                                "type": "paragraph",
                                "paragraph": {
                                    "rich_text": [{"plain_text": "Notion text"}]
                                },
                            }
                        ],
                        "has_more": False,
                    },
                )
            if request.url.host == "sample.supabase.co":
                return httpx.Response(
                    200,
                    json=[
                        {"id": "row-1", "title": "Supabase row", "content": "row text"}
                    ],
                )
            if request.url.host == "api.vercel.com":
                return httpx.Response(
                    200, json={"id": "prj-1", "name": "Vercel project"}
                )
            raise AssertionError(f"Unexpected mocked read URL: {request.url}")

        requests = [
            ReadRequest(
                connector=ConnectorId.DROPBOX,
                resource_type="file",
                resource_id="/notes.txt",
            ),
            ReadRequest(
                connector=ConnectorId.GITHUB,
                resource_type="issue",
                resource_id="acme/repo#7",
            ),
            ReadRequest(
                connector=ConnectorId.GITHUB,
                resource_type="repository",
                resource_id="acme/repo",
            ),
            ReadRequest(
                connector=ConnectorId.GITHUB,
                resource_type="file",
                resource_id="acme/repo/README.md",
            ),
            ReadRequest(
                connector=ConnectorId.GMAIL,
                resource_type="message",
                resource_id="message-1",
            ),
            ReadRequest(
                connector=ConnectorId.GOOGLE_CALENDAR,
                resource_type="event",
                resource_id="event-1",
            ),
            ReadRequest(
                connector=ConnectorId.GOOGLE_DRIVE,
                resource_type="file",
                resource_id="file-1",
            ),
            ReadRequest(
                connector=ConnectorId.GOOGLE_DOCS,
                resource_type="document",
                resource_id="doc-1",
            ),
            ReadRequest(
                connector=ConnectorId.GOOGLE_SHEETS,
                resource_type="spreadsheet",
                resource_id="sheet-1",
            ),
            ReadRequest(
                connector=ConnectorId.GOOGLE_SLIDES,
                resource_type="presentation",
                resource_id="slides-1",
            ),
            ReadRequest(
                connector=ConnectorId.LINEAR, resource_type="issue", resource_id="LIN-7"
            ),
            ReadRequest(
                connector=ConnectorId.NOTION, resource_type="page", resource_id="page-1"
            ),
            ReadRequest(
                connector=ConnectorId.SUPABASE,
                resource_type="row",
                resource_id="row-1",
                resource="documents",
            ),
            ReadRequest(
                connector=ConnectorId.VERCEL,
                resource_type="project",
                resource_id="prj-1",
            ),
        ]
        results = []
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            for request in requests:
                with self.subTest(
                    connector=request.connector.value, kind=request.resource_type
                ):
                    results.append(
                        await read_one(
                            request, client=client, settings=settings, vault=vault
                        )
                    )

            with self.assertRaises(ProviderError):
                await read_one(
                    ReadRequest(
                        connector=ConnectorId.GMAIL,
                        resource_type="event",
                        resource_id="event-1",
                    ),
                    client=client,
                    settings=settings,
                    vault=vault,
                )

        self.assertEqual({result.connector for result in results}, set(ConnectorId))
        self.assertEqual(results[0].data["text"], "Dropbox text file")
        self.assertEqual(results[2].data["full_name"], "acme/repo")
        self.assertEqual(results[3].data["text"], "Hello from GitHub")
        self.assertEqual(results[4].data["body_text"], "Hello from Gmail")
        self.assertEqual(results[6].data["text"], "Drive text")
        self.assertEqual(results[7].data["text"], "Docs text")
        self.assertEqual(results[8].data["values"], [["Sheet cell"]])
        self.assertEqual(results[9].data["slides"][0]["text"], "Slide text")
        self.assertEqual(results[11].data["blocks"][0]["text"], "Notion text")
        self.assertEqual(results[12].data["title"], "Supabase row")
        self.assertGreaterEqual(len(seen), 16)


class ApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_local_api_auth_catalog_and_proposals_are_queue_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            vault = CredentialVault(MemoryKeyring())
            vault.set("github_token", "github-read-test-token")
            store = ApprovalStore(root / "hub.sqlite3", vault)
            provider_requests: list[httpx.Request] = []

            async def provider_handler(request: httpx.Request) -> httpx.Response:
                provider_requests.append(request)
                return httpx.Response(
                    200, json={"id": 1, "number": 1, "title": "Read-only issue"}
                )

            def provider_client_factory() -> httpx.AsyncClient:
                return httpx.AsyncClient(
                    transport=httpx.MockTransport(provider_handler)
                )

            app = create_app(
                settings=Settings(data_dir=root),
                vault=vault,
                api_token="local-test-token",
                action_store=store,
                async_client_factory=provider_client_factory,
            )
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://hub.test"
            ) as client:
                self.assertEqual((await client.get("/health")).status_code, 200)
                self.assertEqual((await client.get("/")).status_code, 401)
                self.assertEqual((await client.get("/v1/connectors")).status_code, 401)

                headers = {"Authorization": "Bearer local-test-token"}
                catalog = await client.get("/v1/connectors", headers=headers)
                self.assertEqual(catalog.status_code, 200)
                self.assertEqual(len(catalog.json()), 12)
                self.assertTrue(all(item["read_supported"] for item in catalog.json()))

                read_response = await client.post(
                    "/v1/read",
                    headers=headers,
                    json={
                        "connector": "github",
                        "resource_type": "issue",
                        "resource_id": "example/repo#1",
                    },
                )
                self.assertEqual(read_response.status_code, 200)
                self.assertEqual(
                    read_response.json()["data"]["title"], "Read-only issue"
                )
                self.assertEqual(len(provider_requests), 1)

                proposal = await client.post(
                    "/v1/actions",
                    headers=headers,
                    json={
                        "connector": "github",
                        "summary": "Create test issue",
                        "method": "POST",
                        "path": "/repos/example/repo/issues",
                        "payload": {"title": "test"},
                    },
                )
                self.assertEqual(proposal.status_code, 200)
                self.assertEqual(proposal.json()["status"], "pending_approval")
                self.assertIn(
                    "cannot approve", proposal.json()["approval_instructions"].lower()
                )
                self.assertEqual(
                    store.get_status(proposal.json()["id"]).status, "pending_approval"
                )
                self.assertEqual(len(store.audit_events()), 1)
                self.assertEqual(
                    len(provider_requests), 1
                )  # Proposal creation never calls the provider.

                unauthorized_search = await client.post(
                    "/v1/search", json={"query": "roadmap"}
                )
                self.assertEqual(unauthorized_search.status_code, 401)


if __name__ == "__main__":
    unittest.main()
