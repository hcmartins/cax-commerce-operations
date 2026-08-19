from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from commerce_operations.integrations.repository_1.contracts import (
    ApprovedProductRequestV1,
    ApprovedProductResponseV1,
)
from commerce_operations.integrations.repository_1.service import (
    ApprovedProductConflictError,
    ApprovedProductIngestionService,
)
from commerce_operations.persistence.database import get_session

router = APIRouter(tags=["commerce-intelligence"])
SessionDependency = Annotated[Session, Depends(get_session)]
IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=255)]


def get_ingestion_service() -> ApprovedProductIngestionService:
    return ApprovedProductIngestionService()


ServiceDependency = Annotated[ApprovedProductIngestionService, Depends(get_ingestion_service)]


@router.post(
    "/approved-products",
    response_model=ApprovedProductResponseV1,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Receive an approved Commerce Intelligence opportunity",
)
def ingest_approved_product(
    request: ApprovedProductRequestV1,
    idempotency_key: IdempotencyKey,
    session: SessionDependency,
    service: ServiceDependency,
) -> ApprovedProductResponseV1:
    try:
        response = service.ingest(
            session,
            request,
            request_idempotency_key=idempotency_key,
        )
        session.commit()
        return response
    except ApprovedProductConflictError as exc:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except IntegrityError as exc:
        # Another request may have committed the same source product between lookup and insert.
        session.rollback()
        try:
            return service.find_existing_after_conflict(session, request)
        except ApprovedProductConflictError as conflict:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(conflict)) from exc
    except Exception:
        session.rollback()
        raise
