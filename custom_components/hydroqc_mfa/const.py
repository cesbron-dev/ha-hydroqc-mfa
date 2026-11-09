"""Constantes de l'intégration Hydro-Québec MFA."""

DOMAIN = "hydroqc_mfa"
HYDROQC_DOMAIN = "hydroqc"

CONF_GMAIL_USERNAME = "gmail_username"
CONF_GMAIL_APP_PASSWORD = "gmail_app_password"
CONF_NOTIFY_SERVICE = "notify_service"
CONF_TRASH = "trash_used_email"
CONF_PAUSED = "paused"

POLL_SECONDS = 5
WAIT_SECONDS = 600
CHECK_INTERVAL_SECONDS = 60
NETWORK_TIMEOUT = 10

SIGNAL_RESULT = "hydroqc_mfa_result"
SIGNAL_GMAIL_TEST = "hydroqc_mfa_gmail_test"
GMAIL_TEST_OK = "ok"
# Connexion OK, mais aucun courriel de code Hydro-Québec dans INBOX : pas un échec.
GMAIL_TEST_NO_HQ_MAIL = "no_hq_mail"

REASON_MESSAGES: dict[str, str] = {
    "code_timeout": "code non reçu en 10 minutes",
    "password_rejected": "mot de passe Hydro-Québec refusé",
    "code_rejected": "code refusé",
    "gmail_unavailable": "Gmail inaccessible",
    "gmail_auth_failed": "connexion Gmail refusée",
    "ambiguous": "plusieurs codes différents reçus",
    "unexpected_form": "formulaire hydroqc inattendu",
    "interrupted": "tentative interrompue (redémarrage)",
    "unexpected": "erreur inattendue",
}
