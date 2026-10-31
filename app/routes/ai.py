from fastapi import APIRouter, HTTPException

from app.config import get_settings
from app.providers.nebius import NebiusProvider
from app.schemas import SmokeRequest, SmokeResponse

router = APIRouter(prefix="/ai", tags=["ai"])


@router.post("/smoke", response_model=SmokeResponse)
def smoke(request: SmokeRequest) -> SmokeResponse:
    settings = get_settings()
    try:
        provider = NebiusProvider(settings)
        content = provider.complete(request.message, enable_thinking=False)
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Nebius request failed: {type(exc).__name__}") from exc

    return SmokeResponse(provider=provider.provider_name, model=provider.model_name, content=content)
