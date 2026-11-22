from fastapi import APIRouter

from app.api.v1 import (
    ahp,
    audit,
    auth,
    content,
    donations,
    health,
    media,
    plans,
    projects,
    public,
    registry,
    settings,
    subscriptions,
    tasks,
    transfers,
    users,
)

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(users.router)
api_router.include_router(settings.router)
api_router.include_router(audit.router)
api_router.include_router(media.router)
api_router.include_router(projects.router)
api_router.include_router(content.router)
api_router.include_router(public.router)
api_router.include_router(donations.router)
api_router.include_router(subscriptions.router)
api_router.include_router(registry.router)
api_router.include_router(transfers.router)
api_router.include_router(tasks.router)
api_router.include_router(ahp.router)
api_router.include_router(plans.router)
