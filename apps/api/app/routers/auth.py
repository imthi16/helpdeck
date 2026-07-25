"""Auth endpoints: signup, login, refresh, me. Tokens live in httpOnly cookies."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.core.db import async_session_factory, get_app_session_factory
from app.core.security import (
    TokenError,
    create_access_token,
    create_refresh_token,
    decode_token,
)
from app.models import AuditAction
from app.schemas.auth import LoginRequest, SignupRequest, UserResponse
from app.schemas.member import AcceptInviteRequest, InvitePreview
from app.services import audit as audit_service
from app.services import members as members_service
from app.services.auth import (
    EmailAlreadyExists,
    InvalidCredentials,
    authenticate,
    load_user_response,
    signup,
)

router = APIRouter(prefix="/auth", tags=["auth"])

AuditFactoryDep = Annotated[async_sessionmaker[AsyncSession], Depends(get_app_session_factory)]

ACCESS_COOKIE = "helpdeck_access"
REFRESH_COOKIE = "helpdeck_refresh"


def get_auth_sessionmaker() -> async_sessionmaker[AsyncSession]:
    return async_session_factory


def _set_auth_cookies(response: Response, access: str, refresh: str) -> None:
    settings = get_settings()
    common = {
        "httponly": True,
        "secure": settings.cookie_secure,
        "samesite": "lax",
        "domain": settings.cookie_domain,
    }
    response.set_cookie(
        ACCESS_COOKIE,
        access,
        max_age=settings.access_token_ttl_minutes * 60,
        path="/",
        **common,
    )
    response.set_cookie(
        REFRESH_COOKIE,
        refresh,
        max_age=settings.refresh_token_ttl_days * 24 * 3600,
        # Refresh cookie is only sent to the refresh endpoint.
        path="/auth/refresh",
        **common,
    )


def _clear_auth_cookies(response: Response) -> None:
    response.delete_cookie(ACCESS_COOKIE, path="/")
    response.delete_cookie(REFRESH_COOKIE, path="/auth/refresh")


async def get_current_user(
    request: Request,
    sessionmaker: Annotated[async_sessionmaker[AsyncSession], Depends(get_auth_sessionmaker)],
) -> UserResponse:
    token = request.cookies.get(ACCESS_COOKIE)
    if not token:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[len("Bearer ") :]
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="not authenticated")
    try:
        user_id = decode_token(token, "access")
    except TokenError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    async with sessionmaker() as session:
        user = await load_user_response(session, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="user not found")
    return user


@router.post("/signup", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def signup_endpoint(
    payload: SignupRequest,
    request: Request,
    response: Response,
    sessionmaker: Annotated[async_sessionmaker[AsyncSession], Depends(get_auth_sessionmaker)],
    audit_factory: AuditFactoryDep,
) -> UserResponse:
    async with sessionmaker() as session:
        try:
            user = await signup(
                session,
                email=payload.email,
                password=payload.password,
                name=payload.name,
                org_name=payload.org_name,
            )
        except EmailAlreadyExists as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="email already registered"
            ) from exc
        user_response = await load_user_response(session, user.id)

    assert user_response is not None
    _set_auth_cookies(response, create_access_token(user.id), create_refresh_token(user.id))
    await audit_service.record(
        AuditAction.auth_signup,
        org_id=user_response.memberships[0].org_id if user_response.memberships else None,
        actor=user_response,
        target_type="user",
        target_id=user.id,
        request=request,
        session_factory=audit_factory,
    )
    return user_response


@router.post("/login", response_model=UserResponse)
async def login_endpoint(
    payload: LoginRequest,
    request: Request,
    response: Response,
    sessionmaker: Annotated[async_sessionmaker[AsyncSession], Depends(get_auth_sessionmaker)],
    audit_factory: AuditFactoryDep,
) -> UserResponse:
    async with sessionmaker() as session:
        try:
            user = await authenticate(session, email=payload.email, password=payload.password)
        except InvalidCredentials as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid credentials"
            ) from exc
        user_response = await load_user_response(session, user.id)

    assert user_response is not None
    _set_auth_cookies(response, create_access_token(user.id), create_refresh_token(user.id))
    await audit_service.record(
        AuditAction.auth_login,
        org_id=user_response.memberships[0].org_id if user_response.memberships else None,
        actor=user_response,
        target_type="user",
        target_id=user.id,
        request=request,
        session_factory=audit_factory,
    )
    return user_response


@router.post("/refresh", response_model=UserResponse)
async def refresh_endpoint(
    request: Request,
    response: Response,
    sessionmaker: Annotated[async_sessionmaker[AsyncSession], Depends(get_auth_sessionmaker)],
) -> UserResponse:
    token = request.cookies.get(REFRESH_COOKIE)
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="no refresh token")
    try:
        user_id = decode_token(token, "refresh")
    except TokenError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    async with sessionmaker() as session:
        user_response = await load_user_response(session, user_id)
    if user_response is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="user not found")

    _set_auth_cookies(response, create_access_token(user_id), create_refresh_token(user_id))
    return user_response


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout_endpoint(response: Response) -> None:
    _clear_auth_cookies(response)


@router.get("/me", response_model=UserResponse)
async def me_endpoint(
    current_user: Annotated[UserResponse, Depends(get_current_user)],
) -> UserResponse:
    return current_user


async def _optional_current_user(
    request: Request,
    sessionmaker: Annotated[async_sessionmaker[AsyncSession], Depends(get_auth_sessionmaker)],
) -> UserResponse | None:
    """The signed-in user if there is one; ``None`` instead of a 401."""
    try:
        return await get_current_user(request, sessionmaker)
    except HTTPException:
        return None


@router.get("/invites/{token}", response_model=InvitePreview)
async def preview_invite_endpoint(
    token: str,
    sessionmaker: Annotated[async_sessionmaker[AsyncSession], Depends(get_auth_sessionmaker)],
) -> InvitePreview:
    """What an invite link points at, so the accept page can render itself."""
    async with sessionmaker() as session:
        try:
            invitation = await members_service.preview_invite(session, token)
        except members_service.InviteInvalid as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        existing = await members_service.find_user_by_email(session, invitation.email)
        return InvitePreview(
            org_name=invitation.organization.name,
            email=invitation.email,
            role=invitation.role,
            needs_account=existing is None,
        )


@router.post("/invites/{token}/accept", response_model=UserResponse)
async def accept_invite_endpoint(
    token: str,
    payload: AcceptInviteRequest,
    request: Request,
    response: Response,
    sessionmaker: Annotated[async_sessionmaker[AsyncSession], Depends(get_auth_sessionmaker)],
    audit_factory: AuditFactoryDep,
    current_user: Annotated[UserResponse | None, Depends(_optional_current_user)],
) -> UserResponse:
    """Redeem an invite.

    Signed in: the session's email must be the invited one. Signed out: a
    password creates the account for the invited email and signs it in. An
    existing account must log in first — holding the link never bypasses its
    password.
    """
    async with sessionmaker() as session:
        try:
            user = await members_service.accept_invite(
                session,
                token=token,
                user_id=current_user.id if current_user else None,
                name=payload.name,
                password=payload.password,
            )
        except members_service.InviteEmailMismatch as exc:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"this invite is for {exc}; sign in as that user to accept it",
            ) from exc
        except members_service.MemberError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        user_response = await load_user_response(session, user.id)

    assert user_response is not None
    if current_user is None:
        _set_auth_cookies(response, create_access_token(user.id), create_refresh_token(user.id))
    joined = user_response.memberships[-1] if user_response.memberships else None
    await audit_service.record(
        AuditAction.member_joined,
        org_id=joined.org_id if joined else None,
        actor=user_response,
        target_type="user",
        target_id=user.id,
        metadata={"role": str(joined.role)} if joined else {},
        request=request,
        session_factory=audit_factory,
    )
    return user_response
