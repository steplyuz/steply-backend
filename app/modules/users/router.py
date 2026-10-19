from fastapi import APIRouter, Depends, UploadFile, File, status, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List
import uuid

from app.core.database import get_db
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.models import User
from app.modules.users.service import UserService
from app.modules.users import schemas

router = APIRouter(
    prefix="/users/me",
    tags=["My Profile"]
)

async def get_service(
    db: AsyncSession = Depends(get_db)
) -> UserService:
    return UserService(db)

@router.get(
    "",
    response_model=schemas.UserResponse
)
async def read_my_profile(
    current_user: User = Depends(get_current_user)
):
    return current_user

@router.put(
    "/profile",
    response_model=schemas.UserResponse
)
async def update_my_profile(
    payload: schemas.ProfileUpdate,
    current_user: User = Depends(get_current_user),
    service: UserService = Depends(get_service)
):
    return await service.update_profile(
        current_user.id,
        payload
    )
    

@router.post(
    "/avatar",
    response_model=schemas.AvatarUpdateResponse
)
async def upload_avatar(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    service: UserService = Depends(get_service)
):
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(400, "Faqat rasm yuklash mumkin")

    avatar_url = await service.upload_avatar(
        current_user.id,
        file
    )

    return schemas.AvatarUpdateResponse(
        avatar_url=avatar_url
    )
    
@router.get(
    "/contacts",
    response_model=List[schemas.ContactSchema]
)
async def get_my_contacts(
    current_user: User = Depends(get_current_user),
    service: UserService = Depends(get_service)
):
    return await service.get_user_contacts(current_user.id)

@router.post(
    "/contacts"
)
async def add_contact(
    payload: schemas.AddContactSchema,
    current_user: User = Depends(get_current_user),
    service: UserService = Depends(get_service)
):
    return await service.add_contact_start(
        user_id=current_user.id,
        value=payload.value,
        contact_type=payload.contact_type
    )
    
    
@router.patch(
    "/contacts/{contact_id}/primary"
)
async def set_primary_contact(
    contact_id: int,
    current_user: User = Depends(get_current_user),
    service: UserService = Depends(get_service)
):
    return await service.set_primary_contact(
        current_user.id,
        contact_id
    )
    
@router.delete(
    "/contacts/{contact_id}",
    status_code=status.HTTP_204_NO_CONTENT
)
async def delete_contact(
    contact_id: int,
    current_user: User = Depends(get_current_user),
    service: UserService = Depends(get_service)
):
    await service.delete_contact(
        current_user.id,
        contact_id
    )
    
@router.get(
    "/sessions",
    response_model=List[schemas.UserSessionResponse]
)
async def get_active_sessions(
    current_user: User = Depends(get_current_user),
    service: UserService = Depends(get_service)
):
    return await service.get_active_sessions(
        current_user.id
    )
    
@router.delete(
    "/sessions/{session_id}"
)
async def revoke_session(
    session_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    service: UserService = Depends(get_service)
):
    return await service.revoke_session(
        current_user.id,
        session_id
    )
    
