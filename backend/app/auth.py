"""
Identité de l'utilisateur courant.
Étape 2 : utilisateur unique « local ». L'étape 3 remplacera cette fonction
par la vérification de la session LDAP, sans toucher au reste du code.
"""
from dataclasses import dataclass


@dataclass
class CurrentUser:
    id: str
    display_name: str


def current_user() -> CurrentUser:
    return CurrentUser(id="local", display_name="Invité")
