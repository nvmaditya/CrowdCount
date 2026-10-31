"""
Authentication Module
Provides JWT-based authentication and password hashing utilities.
"""

import os
import json
import threading
from datetime import datetime, timedelta
from typing import Optional, List
from pathlib import Path

from jose import JWTError, jwt
from passlib.context import CryptContext

from .models import (
    UserInDB, UserCreate, UserResponse, UserRole,
    TokenData, LoginRequest
)


# ==================== Configuration ====================

# Secret key for JWT (in production, use environment variable)
SECRET_KEY = os.getenv("JWT_SECRET_KEY", "your-super-secret-key-change-in-production-2024")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24  # 24 hours

# Password hashing context
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Data file paths
DATA_DIR = Path.cwd() / "data"
USERS_FILE = DATA_DIR / "users.json"

# Thread lock for file operations
_file_lock = threading.RLock()

# Token blacklist (for logout functionality)
_token_blacklist: set = set()
_blacklist_lock = threading.Lock()


# ==================== Password Utilities ====================

def hash_password(password: str) -> str:
    """Hash a password using bcrypt"""
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against its hash"""
    return pwd_context.verify(plain_password, hashed_password)


# ==================== JWT Utilities ====================

def create_access_token(user: UserInDB, expires_delta: Optional[timedelta] = None) -> str:
    """Create a JWT access token for a user"""
    if expires_delta is None:
        expires_delta = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    
    expire = datetime.utcnow() + expires_delta
    
    to_encode = {
        "sub": user.id,
        "username": user.username,
        "role": user.role.value,
        "exp": expire
    }
    
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


def decode_token(token: str) -> Optional[TokenData]:
    """Decode and validate a JWT token"""
    try:
        # Check if token is blacklisted
        with _blacklist_lock:
            if token in _token_blacklist:
                return None
        
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        
        user_id = payload.get("sub")
        username = payload.get("username")
        role = payload.get("role")
        exp = payload.get("exp")
        
        if user_id is None or username is None or role is None:
            return None
        
        return TokenData(
            user_id=user_id,
            username=username,
            role=UserRole(role),
            exp=datetime.fromtimestamp(exp)
        )
    except JWTError:
        return None


def blacklist_token(token: str):
    """Add a token to the blacklist (for logout)"""
    with _blacklist_lock:
        _token_blacklist.add(token)


def is_token_blacklisted(token: str) -> bool:
    """Check if a token is blacklisted"""
    with _blacklist_lock:
        return token in _token_blacklist


# ==================== User Storage Utilities ====================

def _ensure_data_dir():
    """Ensure data directory exists"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def _load_users() -> List[dict]:
    """Load users from JSON file"""
    _ensure_data_dir()
    
    if not USERS_FILE.exists():
        # Create default admin user
        _create_default_admin()
    
    with _file_lock:
        try:
            with open(USERS_FILE, 'r') as f:
                data = json.load(f)
                return data.get("users", [])
        except (json.JSONDecodeError, FileNotFoundError):
            return []


def _save_users(users: List[dict]):
    """Save users to JSON file"""
    _ensure_data_dir()
    
    with _file_lock:
        with open(USERS_FILE, 'w') as f:
            json.dump({"users": users}, f, indent=2, default=str)


def _create_default_admin():
    """Create default admin user if no users exist"""
    default_admin = UserInDB(
        id="admin-default-001",
        username="admin",
        password_hash=hash_password("admin123"),
        role=UserRole.ADMIN,
        created_at=datetime.now()
    )
    
    _save_users([default_admin.dict()])
    print("Created default admin user: admin / admin123")


# ==================== User CRUD Operations ====================

def get_user_by_username(username: str) -> Optional[UserInDB]:
    """Get a user by username"""
    users = _load_users()
    for user_data in users:
        if user_data.get("username") == username:
            return UserInDB(**user_data)
    return None


def get_user_by_id(user_id: str) -> Optional[UserInDB]:
    """Get a user by ID"""
    users = _load_users()
    for user_data in users:
        if user_data.get("id") == user_id:
            return UserInDB(**user_data)
    return None


def get_all_users() -> List[UserResponse]:
    """Get all users (without passwords)"""
    users = _load_users()
    return [
        UserResponse(
            id=u["id"],
            username=u["username"],
            role=UserRole(u["role"]),
            created_at=u.get("created_at", datetime.now().isoformat()),
            last_login=u.get("last_login")
        )
        for u in users
    ]


def create_user(user_create: UserCreate) -> Optional[UserResponse]:
    """Create a new user"""
    # Check if username exists
    if get_user_by_username(user_create.username):
        return None
    
    new_user = UserInDB(
        username=user_create.username,
        password_hash=hash_password(user_create.password),
        role=user_create.role,
        created_at=datetime.now()
    )
    
    users = _load_users()
    users.append(new_user.dict())
    _save_users(users)
    
    return UserResponse(
        id=new_user.id,
        username=new_user.username,
        role=new_user.role,
        created_at=new_user.created_at,
        last_login=new_user.last_login
    )


def delete_user(user_id: str) -> bool:
    """Delete a user by ID"""
    users = _load_users()
    original_len = len(users)
    users = [u for u in users if u.get("id") != user_id]
    
    if len(users) < original_len:
        _save_users(users)
        return True
    return False


def update_user_login(user_id: str):
    """Update user's last login timestamp"""
    users = _load_users()
    for user in users:
        if user.get("id") == user_id:
            user["last_login"] = datetime.now().isoformat()
            break
    _save_users(users)


# ==================== Authentication Functions ====================

def authenticate_user(username: str, password: str) -> Optional[UserInDB]:
    """Authenticate a user with username and password"""
    user = get_user_by_username(username)
    if not user:
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user


def login(login_request: LoginRequest) -> Optional[dict]:
    """
    Process login request.
    Returns token response dict or None if authentication fails.
    """
    user = authenticate_user(login_request.username, login_request.password)
    if not user:
        return None
    
    # Update last login
    update_user_login(user.id)
    
    # Create access token
    access_token = create_access_token(user)
    
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "expires_in": ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        "user": UserResponse(
            id=user.id,
            username=user.username,
            role=user.role,
            created_at=user.created_at,
            last_login=datetime.now()
        )
    }
