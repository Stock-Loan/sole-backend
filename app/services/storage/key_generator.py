from uuid import UUID
from pathlib import Path
import re


class KeyGenerator:
    @staticmethod
    def _safe_filename(filename: str) -> str:
        # Simple sanitization
        s = re.sub(r"[^a-zA-Z0-9_.-]", "_", filename)
        return s

    @staticmethod
    def generate_object_key(
        org_id: str, kind: str, asset_id: UUID, filename: str, owner_refs: dict[str, str]
    ) -> str:
        safe_filename = KeyGenerator._safe_filename(filename)

        if kind == "org_template":
            template_id = owner_refs.get("template_id")
            if not template_id:
                raise ValueError("template_id required for org_template")
            return f"orgs/{org_id}/organization-templates/{template_id}/original/{safe_filename}"

        elif kind.startswith("display_image"):
            user_id = owner_refs.get("user_id")
            if not user_id:
                raise ValueError("user_id required for display_image")

            variant = "original"
            if kind == "display_image_thumb":
                variant = "thumb"
            elif kind == "display_image_medium":
                variant = "medium"

            ext = Path(safe_filename).suffix
            # Filename is ignored for display images, used strictly formatted names
            return f"orgs/{org_id}/users/{user_id}/display-image/{asset_id}/{variant}{ext}"

        elif kind == "loan_document":
            user_id = owner_refs.get("user_id")
            loan_id = owner_refs.get("loan_id")
            if not user_id or not loan_id:
                raise ValueError("user_id and loan_id required for loan_document")
            return f"orgs/{org_id}/users/{user_id}/loans/{loan_id}/loan-documents/{asset_id}/{safe_filename}"

        elif kind == "repayment_receipt":
            user_id = owner_refs.get("user_id")
            loan_id = owner_refs.get("loan_id")
            repayment_id = owner_refs.get("repayment_id")
            if not user_id or not loan_id or not repayment_id:
                raise ValueError("user_id, loan_id, repayment_id required for repayment_receipt")
            return f"orgs/{org_id}/users/{user_id}/loans/{loan_id}/repayments/{repayment_id}/receipts/{asset_id}/{safe_filename}"

        else:
            raise ValueError(f"Unknown asset kind: {kind}")
