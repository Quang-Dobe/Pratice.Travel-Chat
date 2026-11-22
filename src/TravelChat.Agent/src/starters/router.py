from fastapi import APIRouter, Request, Response, status
from pydantic import BaseModel, StrictBool

from src.auth import CurrentUserDep
from src.starters import repository
from src.starters.pills import starter_texts

STARTER_LIMIT = 5

router = APIRouter(prefix="/conversations/starters", tags=["starters"])


class Starters(BaseModel):
    enabled: bool
    items: list[str]


class StartersSetting(BaseModel):
    enabled: StrictBool


@router.get("")
async def get_starters(request: Request, user: CurrentUserDep) -> Starters:
    pool = request.app.state.pool
    enabled = await repository.get_enabled(pool, user.uid)
    if not enabled:
        return Starters(enabled=False, items=[])
    rows = await repository.newest_summaries(pool, user.uid, STARTER_LIMIT)
    return Starters(enabled=True, items=starter_texts(rows))


@router.put("/setting")
async def put_setting(
    body: StartersSetting, request: Request, user: CurrentUserDep
) -> StartersSetting:
    enabled = await repository.set_enabled(request.app.state.pool, user.uid, body.enabled)
    return StartersSetting(enabled=enabled)


@router.delete("/history", status_code=status.HTTP_204_NO_CONTENT)
async def delete_history(request: Request, user: CurrentUserDep) -> Response:
    await repository.clear_summaries(request.app.state.pool, user.uid)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
