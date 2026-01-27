import sys
from unittest.mock import MagicMock

# Mock settings BEFORE any app imports
mock_settings = MagicMock()
mock_settings.settings = MagicMock()
mock_settings.settings.local_upload_dir = "local_uploads"
sys.modules["app.core.settings"] = mock_settings

import pytest
from uuid import uuid4
from unittest.mock import MagicMock, AsyncMock
from app.services.storage.key_generator import KeyGenerator
from app.schemas.assets import UploadSessionRequest
from app.models.asset import Asset

# We need to manually import AssetService after mocking settings
# But wait, AssetService imports LocalFileSystemAdapter which imports Path...
# And KeyGenerator is pure logic.

# Let's import AssetService safely
from app.services.storage.service import AssetService

# Mock storage adapter to avoid using real settings/local IO
class MockAdapter:
    def generate_upload_url(self, object_key, content_type, size_bytes):
        return {
            "upload_url": f"http://mock-upload/{object_key}",
            "headers": {}
        }
    
    def object_exists(self, object_key):
        return True
        
    def generate_download_url(self, object_key, expires_in=3600):
        return f"http://mock-download/{object_key}"

@pytest.fixture
def mock_db():
    session = AsyncMock()
    session.add = MagicMock()
    session.commit = AsyncMock()
    session.refresh = AsyncMock()
    
    # Mock get
    async def mock_get(model, id):
        if model == Asset:
            # Return a mock asset if queried
            asset = Asset(
                id=id,
                org_id="org-1",
                status="pending",
                object_key="mock/key",
                filename="test.pdf"
            )
            return asset
        return None
    
    session.get = mock_get
    return session

def test_key_generator():
    org_id = "org-1"
    asset_id = uuid4()
    
    # 1. Org Template
    key = KeyGenerator.generate_object_key(
        org_id, "org_template", asset_id, "file.docx",
        {"template_id": "tpl-1"}
    )
    assert key == f"orgs/{org_id}/organization-templates/tpl-1/original/file.docx"
    
    # 2. Display Image
    key = KeyGenerator.generate_object_key(
        org_id, "display_image_thumb", asset_id, "ignored.png",
        {"user_id": "user-1"}
    )
    assert key == f"orgs/{org_id}/users/user-1/display-image/{asset_id}/thumb.png"
    
    # 3. Loan Document
    key = KeyGenerator.generate_object_key(
        org_id, "loan_document", asset_id, "contract.pdf",
        {"user_id": "user-1", "loan_id": "loan-1"}
    )
    assert key == f"orgs/{org_id}/users/user-1/loans/loan-1/loan-documents/{asset_id}/contract.pdf"

@pytest.mark.asyncio
async def test_asset_service_create_session(mock_db, monkeypatch):
    # Patch the adapter factory
    monkeypatch.setattr("app.services.storage.service.get_storage_adapter", lambda config=None: MockAdapter())
    
    service = AssetService(mock_db)
    
    req = UploadSessionRequest(
        org_id="org-1",
        kind="loan_document",
        owner_refs={"user_id": "user-1", "loan_id": "loan-1"},
        filename="test.pdf",
        content_type="application/pdf",
        size_bytes=100
    )
    
    resp = await service.create_upload_session(req)
    
    assert resp.asset_id is not None
    assert "orgs/org-1/users/user-1/loans/loan-1/loan-documents" in resp.upload_url
    
    # Verify DB interaction
    assert mock_db.add.called
    assert mock_db.commit.called

@pytest.mark.asyncio
async def test_asset_service_finalize(mock_db, monkeypatch):
    monkeypatch.setattr("app.services.storage.service.get_storage_adapter", lambda config=None: MockAdapter())
    
    service = AssetService(mock_db)
    asset_id = uuid4()
    
    # We rely on mock_db.get returning an asset
    asset = await service.finalize_upload(asset_id)
    
    assert asset.status == "uploaded"
    assert mock_db.commit.called

@pytest.mark.asyncio
async def test_asset_service_download(mock_db, monkeypatch):
    monkeypatch.setattr("app.services.storage.service.get_storage_adapter", lambda config=None: MockAdapter())
    
    service = AssetService(mock_db)
    asset_id = uuid4()
    
    # Mock asset needs to be uploaded for download to work
    async def mock_get_uploaded(model, id):
        return Asset(id=id, status="uploaded", object_key="mock/key", org_id="org-1", filename="f.txt")
        
    mock_db.get = mock_get_uploaded
    
    url = await service.get_download_url(asset_id)
    assert url == "http://mock-download/mock/key"
