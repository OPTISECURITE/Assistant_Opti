"""
Tables : une conversation (chat) contient une liste ordonnée de messages.
owner_id est prévu dès maintenant pour l'authentification (étape 3).
"""
import time
import uuid

from sqlalchemy import BigInteger, Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def now_ms() -> int:
    return int(time.time() * 1000)


def new_id() -> str:
    return str(uuid.uuid4())


class Chat(Base):
    __tablename__ = "chat"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    owner_id: Mapped[str] = mapped_column(String(255), index=True)
    title: Mapped[str] = mapped_column(String(200), default="Nouvelle conversation")
    pinned: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[int] = mapped_column(BigInteger, default=now_ms)
    updated_at: Mapped[int] = mapped_column(BigInteger, default=now_ms, index=True)

    messages: Mapped[list["Message"]] = relationship(
        back_populates="chat", order_by="Message.position",
        cascade="all, delete-orphan", passive_deletes=True,
    )


class Message(Base):
    __tablename__ = "message"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    chat_id: Mapped[str] = mapped_column(ForeignKey("chat.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    role: Mapped[str] = mapped_column(String(16))           # user | assistant
    content: Mapped[str] = mapped_column(Text, default="")
    model: Mapped[str | None] = mapped_column(String(200), nullable=True)
    done: Mapped[bool] = mapped_column(Boolean, default=True)  # False = réponse interrompue
    created_at: Mapped[int] = mapped_column(BigInteger, default=now_ms)

    chat: Mapped[Chat] = relationship(back_populates="messages")


class User(Base):
    """
    Compte utilisateur. source = 'local' (mot de passe stocké, haché Argon2)
    ou 'ldap' (étape ultérieure : mot de passe vérifié par l'AD, jamais stocké).
    """
    __tablename__ = "user"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    username: Mapped[str] = mapped_column(String(150), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(200))
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source: Mapped[str] = mapped_column(String(16), default="local")
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[int] = mapped_column(BigInteger, default=now_ms)
    last_login_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)


class Session(Base):
    """Session de connexion. Seule l'empreinte SHA-256 du jeton est stockée."""
    __tablename__ = "session"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[int] = mapped_column(BigInteger, default=now_ms)
    expires_at: Mapped[int] = mapped_column(BigInteger, index=True)
