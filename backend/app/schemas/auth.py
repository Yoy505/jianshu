"""后台认证相关 DTO。"""

from pydantic import BaseModel


class AdminLoginRequest(BaseModel):
    username: str
    password: str


class AdminProfile(BaseModel):
    id: int
    username: str
    name: str
    role: str


class AdminLoginResponse(BaseModel):
    access_token: str
    refresh_token: str
    admin: AdminProfile


class RefreshRequest(BaseModel):
    refresh_token: str


class RefreshAdminResponse(BaseModel):
    access_token: str
    refresh_token: str


class RefreshUserResponse(BaseModel):
    token: str
    refresh_token: str
