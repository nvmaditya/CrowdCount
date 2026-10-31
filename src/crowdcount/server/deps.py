"""
Authentication Middleware
Provides FastAPI dependencies for route protection and role-based access control.
"""

from fastapi import Depends, HTTPException, status, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from typing import Optional

from .auth import decode_token, is_token_blacklisted
from .models import TokenData, UserRole


# Security scheme for JWT Bearer token
security = HTTPBearer(auto_error=False)


async def get_client_ip(request: Request) -> str:
    """Extract client IP address from request"""
    # Check for forwarded headers (when behind proxy)
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    
    real_ip = request.headers.get("X-Real-IP")
    if real_ip:
        return real_ip
    
    # Fall back to direct client IP
    if request.client:
        return request.client.host
    return "unknown"


async def get_token_optional(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security)
) -> Optional[str]:
    """Get token from request, returns None if not present"""
    if credentials is None:
        return None
    return credentials.credentials


async def get_current_user_optional(
    token: Optional[str] = Depends(get_token_optional)
) -> Optional[TokenData]:
    """Get current user from token, returns None if not authenticated"""
    if token is None:
        return None
    
    if is_token_blacklisted(token):
        return None
    
    token_data = decode_token(token)
    return token_data


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security)
) -> TokenData:
    """
    Get current authenticated user.
    Raises 401 if not authenticated.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    
    if credentials is None:
        raise credentials_exception
    
    token = credentials.credentials
    
    if is_token_blacklisted(token):
        raise credentials_exception
    
    token_data = decode_token(token)
    if token_data is None:
        raise credentials_exception
    
    return token_data


async def get_current_active_user(
    current_user: TokenData = Depends(get_current_user)
) -> TokenData:
    """
    Get current active user.
    Can be extended to check if user is active/not banned.
    """
    # Here you could add additional checks like:
    # - Is user still active in database?
    # - Is user banned?
    # For now, just return the user
    return current_user


async def require_admin(
    current_user: TokenData = Depends(get_current_user)
) -> TokenData:
    """
    Require admin role for access.
    Raises 403 if user is not an admin.
    """
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin privileges required"
        )
    return current_user


async def require_user_or_admin(
    current_user: TokenData = Depends(get_current_user)
) -> TokenData:
    """
    Require at least user role (user or admin).
    """
    if current_user.role not in [UserRole.USER, UserRole.ADMIN]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User privileges required"
        )
    return current_user


class RoleChecker:
    """
    Reusable role checker dependency.
    Usage: Depends(RoleChecker([UserRole.ADMIN]))
    """
    def __init__(self, allowed_roles: list):
        self.allowed_roles = allowed_roles
    
    def __call__(self, current_user: TokenData = Depends(get_current_user)) -> TokenData:
        if current_user.role not in self.allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role {current_user.role} not authorized. Required: {self.allowed_roles}"
            )
        return current_user


# Pre-configured role checkers
admin_only = RoleChecker([UserRole.ADMIN])
user_or_admin = RoleChecker([UserRole.USER, UserRole.ADMIN])
