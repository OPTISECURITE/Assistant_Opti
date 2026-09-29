#!/usr/bin/env python3
"""
Administration des comptes locaux de l'Assistant Opti.

Usage (depuis /opt/assistant-opti) :
    venv/bin/python backend/manage.py create-user m.chaput "Maxime Chaput" --admin
    venv/bin/python backend/manage.py list-users
    venv/bin/python backend/manage.py set-password m.chaput
    venv/bin/python backend/manage.py disable-user j.dupont      (et enable-user)
    venv/bin/python backend/manage.py delete-user j.dupont       (supprime aussi ses conversations)
    venv/bin/python backend/manage.py claim-chats m.chaput       (récupère les conversations de l'étape 2)
    venv/bin/python backend/manage.py test-sandbox               (vérifie le bac à sable d'analyse)
"""
import argparse
import asyncio
import tempfile
from pathlib import Path
import getpass
import sys
from datetime import datetime

from sqlalchemy import delete, select, update

from app.auth import hash_password
from app.db import SessionLocal, init_db
from app.models import Chat, Session, User

MIN_LENGTH = 12


def ask_password() -> str:
    while True:
        pw = getpass.getpass("Mot de passe : ")
        if len(pw) < MIN_LENGTH:
            print(f"  Au moins {MIN_LENGTH} caractères.")
            continue
        if pw != getpass.getpass("Confirmation : "):
            print("  Les deux saisies diffèrent.")
            continue
        return pw


def get_user(db, username: str) -> User:
    user = db.scalar(select(User).where(User.username == username.lower()))
    if not user:
        sys.exit(f"Compte introuvable : {username}")
    return user


def main():
    p = argparse.ArgumentParser(description="Comptes de l'Assistant Opti")
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create-user"); c.add_argument("username"); c.add_argument("display_name"); c.add_argument("--admin", action="store_true")
    sub.add_parser("list-users")
    sub.add_parser("test-sandbox")
    for name in ("set-password", "disable-user", "enable-user", "delete-user", "claim-chats"):
        sub.add_parser(name).add_argument("username")
    args = p.parse_args()

    if args.cmd == "test-sandbox":
        return test_sandbox()

    init_db()
    with SessionLocal() as db:
        if args.cmd == "create-user":
            username = args.username.strip().lower()
            if db.scalar(select(User).where(User.username == username)):
                sys.exit(f"Le compte {username} existe déjà.")
            db.add(User(username=username, display_name=args.display_name.strip(),
                        password_hash=hash_password(ask_password()), is_admin=args.admin))
            db.commit()
            print(f"[✓] Compte {username} créé{' (administrateur)' if args.admin else ''}.")

        elif args.cmd == "list-users":
            users = db.scalars(select(User).order_by(User.username)).all()
            if not users:
                print("Aucun compte.")
            for u in users:
                last = datetime.fromtimestamp(u.last_login_at / 1000).strftime("%d/%m/%Y %H:%M") if u.last_login_at else "jamais"
                flags = ", ".join(f for f, on in (("admin", u.is_admin), ("DÉSACTIVÉ", not u.active)) if on)
                print(f"{u.username:<25} {u.display_name:<30} {u.source:<6} dernière connexion : {last}  {flags}")

        elif args.cmd == "set-password":
            user = get_user(db, args.username)
            user.password_hash = hash_password(ask_password())
            db.execute(delete(Session).where(Session.user_id == user.id))   # déconnecte partout
            db.commit()
            print("[✓] Mot de passe modifié, sessions existantes fermées.")

        elif args.cmd in ("disable-user", "enable-user"):
            user = get_user(db, args.username)
            user.active = args.cmd == "enable-user"
            if not user.active:
                db.execute(delete(Session).where(Session.user_id == user.id))
            db.commit()
            print(f"[✓] Compte {user.username} {'réactivé' if user.active else 'désactivé'}.")

        elif args.cmd == "delete-user":
            user = get_user(db, args.username)
            if input(f"Supprimer {user.username} ET toutes ses conversations ? (oui/non) ") != "oui":
                sys.exit("Annulé.")
            db.execute(delete(Chat).where(Chat.owner_id == user.id))
            db.delete(user)
            db.commit()
            print("[✓] Compte supprimé.")

        elif args.cmd == "claim-chats":
            user = get_user(db, args.username)
            n = db.execute(update(Chat).where(Chat.owner_id == "local").values(owner_id=user.id)).rowcount
            db.commit()
            print(f"[✓] {n} conversation(s) rattachée(s) à {user.username}.")


def test_sandbox():
    """Vérifie que le bac à sable calcule correctement ET qu'il est bien isolé."""
    from app import config, sandbox
    print(f"Mode : {config.SANDBOX_MODE} · image : {config.SANDBOX_IMAGE}")
    with tempfile.TemporaryDirectory() as tmp:
        csv = Path(tmp) / "test.csv"
        csv.write_text("site,alarmes\nLimoges,3\nBrive,5\nTulle,2\n", encoding="utf-8")
        csv.chmod(0o644)
        Path(tmp).chmod(0o755)
        tests = [
            ("Calcul pandas sur le fichier", "import pandas as pd\nprint(pd.read_csv('/data/test.csv')['alarmes'].sum())", True, "10"),
            ("Réseau coupé", "import socket\nsocket.create_connection(('1.1.1.1', 53), timeout=3)\nprint('RESEAU OUVERT')", False, None),
            ("Fichier en lecture seule", "open('/data/test.csv', 'a').write('x')\nprint('ECRITURE POSSIBLE')", False, None),
            ("Pas de privilèges root", "import os\nprint('uid', os.getuid())", True, "uid 10001"),
            ("Pas d'accès aux données de l'application", "import os\nprint(os.path.exists('/opt/assistant-opti'))", True, "False"),
        ]
        failures = 0
        for label, code, want_ok, expect in tests:
            ok, out = asyncio.run(sandbox.run_code(code, [(csv, "test.csv")]))
            good = (ok == want_ok) and (expect is None or expect in out) and "OUVERT" not in out and "POSSIBLE" not in out
            failures += not good
            print(f"  [{'✓' if good else '✗'}] {label}" + ("" if good else f"\n      → {out[-300:]}"))
    if config.SANDBOX_MODE != "docker":
        print("  [!] Mode subprocess : AUCUNE isolation réelle. Ne pas utiliser en production.")
    print("Bac à sable OK." if not failures else f"{failures} test(s) en échec.")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
