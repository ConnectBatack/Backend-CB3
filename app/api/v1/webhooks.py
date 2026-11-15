from fastapi import APIRouter, Depends, Header, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.services.stripe_service import process_stripe_webhook

router = APIRouter(prefix="/webhooks", tags=["Webhooks"])


@router.post("/stripe", summary="Réception des notifications signées Stripe")
async def stripe_webhook(
    request: Request,
    stripe_signature: str | None = Header(default=None, alias="stripe-signature"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    payload = await request.body()
    return await process_stripe_webhook(db, payload=payload, signature=stripe_signature)

