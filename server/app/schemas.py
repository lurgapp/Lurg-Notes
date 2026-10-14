from datetime import datetime
from pydantic import BaseModel, EmailStr, Field

class UserCreate(BaseModel):
    email: EmailStr
    name: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=8, max_length=128)

class Login(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class EmailRegistrationRequest(UserCreate):
    pass


class EmailAddress(BaseModel):
    email: EmailStr


class EmailCode(EmailAddress):
    code: str = Field(pattern=r"^\d{6}$")


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=20, max_length=256)

class PasswordResetConfirm(EmailAddress):
    password: str = Field(min_length=8, max_length=128)
    reset_token: str = Field(min_length=20, max_length=2048)

class ProfileUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=120)

class UserPreferences(BaseModel):
    theme: str = Field(default="dark", pattern="^(dark|system)$")
    language: str = Field(default="ru", pattern="^(ru|en)$")
    notifications_enabled: bool = True
    auto_use_graph: bool = True
    allow_web_search: bool = True
    haptics_enabled: bool = True

class DeleteAccountRequest(BaseModel):
    password: str = Field(min_length=8, max_length=128)

class AnalyzeRequest(BaseModel):
    text: str = Field(min_length=2, max_length=10000)

class SearchRequest(BaseModel):
    query: str = Field(min_length=2, max_length=2000)
    limit: int = Field(default=8, ge=1, le=20)
    date_from: datetime | None = None
    date_to: datetime | None = None

class ChatCreate(BaseModel):
    title: str | None = Field(default=None, max_length=160)

class ChatUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=160)
    pinned: bool | None = None

class ChatMessageCreate(BaseModel):
    thread_id: str | None = None
    message: str = Field(min_length=1, max_length=20000)
    use_graph: bool | None = None
    use_internet: bool | None = None

class GraphFactConfirm(BaseModel):
    text: str = Field(min_length=2, max_length=10000)

class GraphChangeDecision(BaseModel):
    confirmed: bool


class GraphChangeEdit(BaseModel):
    label: str = Field(min_length=1, max_length=300)
