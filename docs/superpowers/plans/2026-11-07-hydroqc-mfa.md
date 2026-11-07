# hydroqc_mfa Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Intégration HA compagnon qui remplit automatiquement le flux de réauthentification de hydroqc-ha (mot de passe, puis code MFA lu dans Gmail), une seule tentative, notification en cas d'échec.

**Architecture:** Modules à responsabilité unique : `gmail.py` (IMAP synchrone, exécuté dans l'executor), `lock.py` (verrou persistant par entrée hydroqc), `renewer.py` (orchestration : repère le flux `reauth` de `hydroqc`, le pilote via `hass.config_entries.flow.async_configure`), `notify.py` (notification persistante + push), `config_flow.py`, `sensor.py`. Aucune dépendance au code interne de hydroqc-ha : uniquement ses noms d'étapes et de champs publics.

**Tech Stack:** Python 3.14, Home Assistant 2026.11, `imaplib`/`email` (stdlib), pytest + `pytest-homeassistant-custom-component`.

**Spec:** `docs/superpowers/specs/2026-11-07-hydroqc-mfa-design.md`

## Global Constraints

- Domaine `hydroqc_mfa` ; dossier `custom_components/hydroqc_mfa/` ; `after_dependencies: ["hydroqc"]` ; `config_flow: true` ; `iot_class: "cloud_polling"` ; `requirements: []`.
- `hacs.json` : `{"name": "Hydro-Québec MFA", "homeassistant": "2026.11.0", "render_readme": true}`.
- Squelette de dépôt calqué sur `../ha-adguard-clients` : `pyproject.toml`, `requirements_test.txt` (`pytest-homeassistant-custom-component==0.13.375` ou version plus récente compatible HA 2026.11), `.github/workflows/tests.yml` et `validate.yml` copiés, `tests/conftest.py` avec `auto_enable_custom_integrations`.
- Contrat hydroqc-ha (v0.9.0, vérifié) : domaine `hydroqc` ; flux de source `reauth` avec `context["entry_id"]` ; étape `reauth_confirm`, champ `password` ; étape `mfa`, champ `mfa_code` ; succès = `type == FlowResultType.ABORT` et `reason == "reauth_successful"`. Le mot de passe est dans `entry.data["password"]` de l'entrée hydroqc.
- Gmail : `imap.gmail.com:993`, TLS vérifié (`ssl.create_default_context()`), délai réseau 10 s. Expéditeur exact `hydro-quebec@communications.hydro.qc.ca` ; sujet exact « Votre code de vérification pour votre identifiant Hydro-Québec » (comparaison NFKC + casefold + espaces normalisés) ; exactement un code distinct `(?<!\w)[0-9]{6}(?!\w)` dans le texte visible ; message borné à 262 144 octets ; lecture par `BODY.PEEK` ; corbeille par `+X-GM-LABELS (\Trash)`, jamais d'EXPUNGE.
- Attente du code : sondage toutes les 5 s, 600 s maximum.
- Jamais de code, mot de passe, contenu de courriel ni réponse serveur dans les journaux, exceptions remontées, événements, attributs, notifications. Les erreurs portent une raison fixe.
- Une seule tentative par entrée hydroqc tant que le verrou n'est pas réarmé ; réarmement uniquement quand l'entrée hydroqc passe à l'état `LOADED`.
- Libellés et notifications en français (`strings.json` en anglais, `translations/fr.json` et `en.json`).
- Messages de commit en français, terminés par `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Rulings (décisions prises pour ce plan)

- **« Flux sans erreur affichée »** : l'API publique de progression des flux ne donne que `step_id`, pas les erreurs. La compagnon agit sur tout flux `hydroqc`/`reauth` à l'étape `reauth_confirm` dont l'entrée est armée. Un flux déjà à `mfa` est laissé à Olivier. Coût si faux : un essai manuel interrompu au formulaire mot de passe pourrait être pris en charge ; acceptable.
- **Réarmement** : via `entry.async_on_state_change` de l'entrée hydroqc ; un passage à `ConfigEntryState.LOADED` implique un premier rafraîchissement réussi (hydroqc-ha lève `ConfigEntryAuthFailed` au setup sinon).
- **Détection** : sondage toutes les 60 s (`async_track_time_interval`) + vérification immédiate au démarrage et à chaque changement d'état de l'entrée hydroqc.
- **Ordre Gmail avant Hydro-Québec** : connexion IMAP et point de départ pris *avant* la soumission du mot de passe ; une panne Gmail n'entraîne donc aucune demande de code.
- **Démarrage avec verrou `in_progress`** (HA arrêté en pleine tentative) : convertir en `failed` avec la raison `interrupted` et notifier.
- **Filtre de journal** : un `logging.Filter` installé sur le logger `hydroqc.httpclient` ignore les enregistrements émis pendant une tentative (drapeau `ContextVar`, positionné dans la tâche qui appelle `async_configure`).

## Review Focus

1. Redémarrage de HA en pleine tentative → aucune nouvelle demande de code au démarrage, notification « tentative interrompue » (Task 3, `test_startup_in_progress_becomes_failed`; Task 5, `test_no_attempt_after_restart_mid_attempt`).
2. Un ancien courriel de code déjà présent dans la boîte → ignoré, seul un message arrivé après le point de départ compte (Task 2, `test_poll_ignores_messages_before_checkpoint`).
3. Deux vérifications simultanées (sondage + changement d'état) → une seule tentative (Task 5, `test_concurrent_checks_single_attempt`).
4. Automatisme suspendu dans les options → aucun flux touché ; une fois réactivé, le flux en attente est traité une fois (Task 5, `test_paused_does_nothing`).
5. Déchargement de la compagnon pendant l'attente du code → tâche annulée, connexion IMAP fermée, verrou laissé `failed` (Task 5, `test_unload_cancels_attempt`).

---

### Task 1: Squelette du dépôt et manifeste

**Files:**
- Create: `custom_components/hydroqc_mfa/manifest.json`, `custom_components/hydroqc_mfa/const.py`, `custom_components/hydroqc_mfa/__init__.py` (setup/unload minimaux, renvoient `True`), `hacs.json`, `pyproject.toml`, `requirements_test.txt`, `.github/workflows/tests.yml`, `.github/workflows/validate.yml`, `LICENSE` (copie de ha-adguard-clients), `tests/__init__.py`, `tests/conftest.py`
- Test: `tests/test_init.py`

**Interfaces:**
- Produces (`const.py`) : `DOMAIN = "hydroqc_mfa"`, `HYDROQC_DOMAIN = "hydroqc"`, `CONF_GMAIL_USERNAME = "gmail_username"`, `CONF_GMAIL_APP_PASSWORD = "gmail_app_password"`, `CONF_NOTIFY_SERVICE = "notify_service"`, `CONF_TRASH = "trash_used_email"`, `CONF_PAUSED = "paused"`, `POLL_SECONDS = 5`, `WAIT_SECONDS = 600`, `CHECK_INTERVAL_SECONDS = 60`, `NETWORK_TIMEOUT = 10`, `SIGNAL_RESULT = "hydroqc_mfa_result"`, et `REASON_MESSAGES: dict[str, str]` (raison → texte français) pour : `code_timeout` « code non reçu en 10 minutes », `password_rejected` « mot de passe Hydro-Québec refusé », `code_rejected` « code refusé », `gmail_unavailable` « Gmail inaccessible », `gmail_auth_failed` « connexion Gmail refusée », `ambiguous` « plusieurs codes différents reçus », `unexpected_form` « formulaire hydroqc inattendu », `interrupted` « tentative interrompue (redémarrage) », `unexpected` « erreur inattendue ».

- [ ] **Step 1:** Test `test_setup_and_unload` : une `MockConfigEntry(domain="hydroqc_mfa", data={...})` se charge (`ConfigEntryState.LOADED`) puis se décharge.
- [ ] **Step 2:** `pytest tests/test_init.py -v` → FAIL (intégration absente).
- [ ] **Step 3:** Créer les fichiers ci-dessus.
- [ ] **Step 4:** `pip install -r requirements_test.txt` dans `.venv`, puis `pytest -q` → PASS.
- [ ] **Step 5:** Commit `chore: squelette de l'intégration hydroqc_mfa`.

### Task 2: Client Gmail (`gmail.py`)

**Files:**
- Create: `custom_components/hydroqc_mfa/gmail.py`
- Test: `tests/test_gmail.py`, `tests/fake_imap.py`

**Interfaces:**
- Produces :
  - `class GmailError(Exception)` avec `reason: str` (une des clés de `REASON_MESSAGES` : `gmail_unavailable`, `gmail_auth_failed`, `ambiguous`) ; `str(err) == reason`.
  - `@dataclass(frozen=True) class GmailCode: uid: int; validity: bytes; code: str = field(repr=False)`.
  - `def extract_code(raw: bytes) -> str | None` — `None` si l'expéditeur/sujet ne correspond pas ou s'il n'y a aucun code ; `GmailError("ambiguous")` si plusieurs codes distincts.
  - `class GmailInbox(username: str, password: str)` : `connect() -> None`, `checkpoint() -> None` (INBOX en lecture seule, mémorise UIDVALIDITY/UIDNEXT), `poll() -> GmailCode | None` (UID ≥ UIDNEXT mémorisé, filtre `FROM`, `BODY.PEEK`, UID déjà examinés ignorés), `trash(receipt: GmailCode) -> None` (refuse si UIDVALIDITY a changé), `close() -> None`.
  - `def validate_gmail(username: str, password: str) -> None` (connect + checkpoint + close).
- `tests/fake_imap.py` : `FakeImap` remplaçant `imaplib.IMAP4_SSL` (patché dans `custom_components.hydroqc_mfa.gmail.imaplib.IMAP4_SSL`), avec une liste de messages `(uid, raw_bytes)`, `uidvalidity`, `uidnext`, un mot de passe attendu, et l'enregistrement des commandes reçues (pour vérifier `BODY.PEEK`, `\Trash`, absence d'`EXPUNGE`). Helper `hq_message(code: str, *, sender=..., subject=..., html=False) -> bytes`.
- Le fork vsaintloup (`custom_components/hydroqc/gmail.py`) sert de référence d'implémentation.

- [ ] **Step 1:** Tests :
  - `test_extract_code_accepts_expected_message` : `extract_code(hq_message("123456")) == "123456"`.
  - `test_extract_code_html_visible_text_only` : code dans le HTML visible accepté ; un nombre à 6 chiffres dans un attribut ou un `<style>` ignoré.
  - `test_extract_code_rejects_wrong_sender` / `test_extract_code_rejects_wrong_subject` → `None`.
  - `test_extract_code_ambiguous` : deux codes distincts → `GmailError` avec `reason == "ambiguous"`.
  - `test_connect_bad_password` → `GmailError("gmail_auth_failed")` ; message d'erreur ne contient pas le mot de passe.
  - `test_poll_ignores_messages_before_checkpoint` : message de code avec UID < UIDNEXT au checkpoint → `poll() is None` ; nouveau message ajouté ensuite → `GmailCode(code="654321")`.
  - `test_poll_uses_peek` : toutes les commandes FETCH contiennent `BODY.PEEK`.
  - `test_trash_uses_label_never_expunge` : `trash()` envoie `STORE <uid> +X-GM-LABELS (\Trash)` et aucune commande `EXPUNGE`/`DELETE`.
  - `test_gmail_code_repr_hides_code` : `"123456" not in repr(GmailCode(1, b"1", "123456"))`.
- [ ] **Step 2:** `pytest tests/test_gmail.py -v` → FAIL.
- [ ] **Step 3:** Implémenter `gmail.py`.
- [ ] **Step 4:** `pytest tests/test_gmail.py -v` → PASS.
- [ ] **Step 5:** Commit `feat: lecture du code Hydro-Québec dans Gmail`.

### Task 3: Verrou persistant (`lock.py`)

**Files:**
- Create: `custom_components/hydroqc_mfa/lock.py`
- Test: `tests/test_lock.py`

**Interfaces:**
- Produces : `class RenewalLock(hass: HomeAssistant)` stocké via `Store(hass, 1, "hydroqc_mfa.lock")`, contenu `{entry_id: {"state": "armed"|"in_progress"|"failed", "reason": str | None}}` :
  - `async def async_load() -> list[str]` — charge ; convertit tout `in_progress` en `failed` raison `interrupted` et renvoie la liste des `entry_id` convertis (pour notifier).
  - `def is_armed(entry_id: str) -> bool` (entrée inconnue = armée).
  - `async def async_begin(entry_id: str) -> None` (écrit `in_progress` et **attend** la sauvegarde ; `async_save`, pas `async_delay_save`).
  - `async def async_fail(entry_id: str, reason: str) -> None`.
  - `async def async_arm(entry_id: str) -> None`.

- [ ] **Step 1:** Tests `test_unknown_entry_is_armed`, `test_begin_then_fail_not_armed`, `test_arm_after_fail`, `test_startup_in_progress_becomes_failed` (store pré-rempli `in_progress` → `async_load()` renvoie `[entry_id]`, `is_armed` faux, raison `interrupted`), `test_begin_is_persisted_before_return` (relire le Store via une nouvelle instance juste après `async_begin`).
- [ ] **Step 2:** `pytest tests/test_lock.py -v` → FAIL.
- [ ] **Step 3:** Implémenter.
- [ ] **Step 4:** PASS.
- [ ] **Step 5:** Commit `feat: verrou anti-relance par entrée hydroqc`.

### Task 4: Notifications (`notify.py`)

**Files:**
- Create: `custom_components/hydroqc_mfa/notify.py`
- Test: `tests/test_notify.py`

**Interfaces:**
- Produces :
  - `NOTIFICATION_ID = "hydroqc_mfa_failure"`.
  - `async def async_notify_failure(hass: HomeAssistant, notify_service: str | None, reason: str) -> None` — `persistent_notification.async_create(hass, message, title="Hydro-Québec : réauthentification à faire", notification_id=NOTIFICATION_ID)` ; message = `REASON_MESSAGES[reason]` + « Termine la réauthentification à la main : » + lien `/config/integrations/integration/hydroqc`. Si `notify_service` (ex. `"notify.mobile_app_iphone_de_olivier"`) : appel du service avec `title` et `message`, et `data: {"url": "/config/integrations/integration/hydroqc"}`. Une panne du service push est journalisée (sans détail) et n'empêche pas la notification persistante.
  - `async def async_clear_failure(hass: HomeAssistant) -> None`.

- [ ] **Step 1:** Tests `test_failure_creates_persistent_and_push` (`async_mock_service(hass, "notify", "mobile_app_test")`, vérifier titre/message/url et la notification persistante), `test_failure_without_push_service`, `test_push_failure_still_creates_persistent`, `test_clear_removes_notification`.
- [ ] **Step 2:** FAIL. **Step 3:** Implémenter. **Step 4:** PASS.
- [ ] **Step 5:** Commit `feat: notifications d'échec (persistante et push)`.

### Task 5: Orchestration de la réauthentification (`renewer.py`) et branchement dans `__init__.py`

**Files:**
- Create: `custom_components/hydroqc_mfa/renewer.py`
- Modify: `custom_components/hydroqc_mfa/__init__.py`
- Test: `tests/test_renewer.py`, `tests/fake_hydroqc.py`

**Interfaces:**
- Consumes : `GmailInbox`, `GmailError`, `GmailCode` (Task 2) ; `RenewalLock` (Task 3) ; `async_notify_failure`, `async_clear_failure` (Task 4) ; constantes (Task 1).
- Produces :
  - `class Renewer(hass, entry: ConfigEntry, lock: RenewalLock, inbox_factory: Callable[[], GmailInbox])` :
    - `async def async_start() -> None` — charge le verrou (notifie `interrupted` pour les entrées converties), s'abonne à `async_on_state_change` de chaque entrée `hydroqc` (passage à `LOADED` → `lock.async_arm` + `async_clear_failure` si l'entrée n'était pas armée), lance le sondage toutes les `CHECK_INTERVAL_SECONDS`, puis `async_check()`.
    - `async def async_stop() -> None` — désabonne, annule la tentative en cours et attend sa fin.
    - `async def async_check() -> None` — si `entry.options[CONF_PAUSED]` : rien. Sinon, pour chaque flux de `hass.config_entries.flow.async_progress_by_handler("hydroqc", match_context={"source": "reauth"})` à l'étape `reauth_confirm`, dont `context["entry_id"]` est armé et sans tentative en cours : lancer `async_renew`. Un `asyncio.Lock` par `entry_id` garantit une seule tentative.
    - `async def async_renew(flow_id: str, hydro_entry_id: str) -> str` — renvoie `"success"` ou une raison. Séquence : `lock.async_begin` → (executor) `inbox.connect()` + `inbox.checkpoint()` → `async_configure(flow_id, {"password": hydro_entry.data["password"]})` → si ABORT `reauth_successful` : succès ; si FORM `mfa` sans erreurs : sondage `inbox.poll()` toutes les `POLL_SECONDS` jusqu'à `WAIT_SECONDS` → `async_configure(flow_id, {"mfa_code": code})` → ABORT `reauth_successful` = succès (puis `inbox.trash` si `CONF_TRASH`, échec de corbeille seulement journalisé) ; FORM `reauth_confirm` avec erreurs → `password_rejected` ; tout autre résultat (dont FORM `mfa` avec erreurs) → `code_rejected` après soumission du code, `unexpected_form` avant ; `UnknownFlow`/exception → `unexpected`. Toujours `inbox.close()` (executor). En échec : `lock.async_fail`, `async_notify_failure(hass, options[CONF_NOTIFY_SERVICE], reason)`. En succès : `lock.async_arm`, `async_clear_failure`. Dans tous les cas : `async_dispatcher_send(hass, SIGNAL_RESULT, reason, dt_util.utcnow())`. Le filtre de journal `hydroqc.httpclient` est actif pendant toute la tentative.
  - `__init__.py` : `async_setup_entry` crée `Renewer` (inbox_factory lit `CONF_GMAIL_USERNAME`/`CONF_GMAIL_APP_PASSWORD` dans `entry.data`), le range dans `entry.runtime_data`, appelle `async_start`, transfère vers la plateforme `sensor` (Task 7) ; `async_unload_entry` appelle `async_stop`. Rechargement sur modification des options.
- `tests/fake_hydroqc.py` : enregistre un faux `ConfigFlow` de domaine `hydroqc` (`mock_integration` + `mock_platform(hass, "hydroqc.config_flow", None)` + `mock_config_flow`) avec `async_step_reauth` → `reauth_confirm` (champ `password`) → `mfa` (champ `mfa_code`) → `async_abort(reason="reauth_successful")`. Comportement paramétrable : mot de passe attendu, code attendu, `abort` direct après mot de passe, compteur d'appels de chaque étape. Une `MockConfigEntry(domain="hydroqc", data={"password": "hq-pass", ...})` et un helper qui ouvre le flux de réauth (`entry.async_start_reauth(hass)`).
- Les tests patchent `POLL_SECONDS`/`WAIT_SECONDS` à de petites valeurs et utilisent `FakeImap` (Task 2) via `inbox_factory`.

- [ ] **Step 1:** Tests :
  - `test_success_submits_password_then_code` : flux ouvert, courriel ajouté au FakeImap après la soumission du mot de passe → résultat `success`, faux flux a reçu `password == "hq-pass"` puis `mfa_code == "123456"`, verrou armé, message mis à la corbeille.
  - `test_checkpoint_before_password` : l'ordre des appels montre `checkpoint` avant l'étape mot de passe.
  - `test_code_timeout_notifies_and_locks` : aucun courriel → `code_timeout`, notification persistante créée, verrou `failed`, l'étape `mfa` n'a jamais reçu de code.
  - `test_password_rejected` : faux flux renvoie `reauth_confirm` avec `errors={"base": "invalid_auth"}` → `password_rejected`, pas de sondage Gmail au-delà du checkpoint.
  - `test_code_rejected_no_second_submission` : faux flux renvoie `mfa` avec `errors={"base": "invalid_mfa_code"}` → `code_rejected`, une seule soumission de code.
  - `test_gmail_down_no_hydro_request` : `connect` échoue → `gmail_unavailable`, l'étape mot de passe n'a jamais été appelée.
  - `test_no_retry_after_failure` : après un échec, nouveau flux de réauth ouvert → `async_check()` ne fait rien (0 appel supplémentaire).
  - `test_rearm_on_loaded` : après un échec, l'entrée hydroqc passe à `LOADED` → verrou armé, notification effacée, un nouveau flux est traité.
  - `test_ignores_flow_at_mfa_step` : flux déjà à l'étape `mfa` → aucune action.
  - Review Focus : `test_no_attempt_after_restart_mid_attempt`, `test_concurrent_checks_single_attempt`, `test_paused_does_nothing`, `test_unload_cancels_attempt`.
  - `test_no_secrets_in_logs` : avec `caplog` au niveau DEBUG sur une tentative réussie et une échouée, ni `"123456"`, ni `"hq-pass"`, ni le mot de passe Gmail n'apparaissent ; un enregistrement émis sur `hydroqc.httpclient` pendant la tentative est filtré.
- [ ] **Step 2:** `pytest tests/test_renewer.py -v` → FAIL.
- [ ] **Step 3:** Implémenter `renewer.py` et brancher `__init__.py`.
- [ ] **Step 4:** `pytest -q` → PASS.
- [ ] **Step 5:** Commit `feat: réauthentification automatique du flux hydroqc`.

### Task 6: Flux de configuration et options (`config_flow.py`)

**Files:**
- Create: `custom_components/hydroqc_mfa/config_flow.py`, `strings.json`, `translations/fr.json`, `translations/en.json`
- Test: `tests/test_config_flow.py`

**Interfaces:**
- Consumes : `validate_gmail`, `GmailError` (Task 2) ; constantes (Task 1).
- Produces : étape `user` (champs `gmail_username` (texte, e-mail), `gmail_app_password` (mot de passe)) ; validation par `validate_gmail` dans l'executor ; erreurs `gmail_auth_failed` / `gmail_unavailable` ; une seule entrée (`async_set_unique_id(DOMAIN)` + `_abort_if_unique_id_configured`) ; titre « Hydro-Québec MFA ». Options : `notify_service` (sélecteur de texte optionnel, ou `SelectSelector` des services `notify.mobile_app_*` existants — au choix de l'implémenteur), `trash_used_email` (bool, défaut `True`), `paused` (bool, défaut `False`). Étape `reconfigure` pour changer le mot de passe d'application.

- [ ] **Step 1:** Tests `test_user_flow_creates_entry` (validate_gmail patché), `test_user_flow_bad_password` (erreur `gmail_auth_failed`, mot de passe absent du résultat), `test_single_instance`, `test_options_flow` (défauts puis valeurs enregistrées), `test_reconfigure_updates_password`.
- [ ] **Step 2:** FAIL. **Step 3:** Implémenter + traductions fr/en. **Step 4:** PASS.
- [ ] **Step 5:** Commit `feat: configuration Gmail et options`.

### Task 7: Capteurs et documentation

**Files:**
- Create: `custom_components/hydroqc_mfa/sensor.py`, `README.md`
- Modify: `custom_components/hydroqc_mfa/__init__.py` (plateforme `sensor`), traductions
- Test: `tests/test_sensor.py`

**Interfaces:**
- Consumes : `SIGNAL_RESULT` émis par `Renewer` (Task 5) avec `(reason: str, when: datetime)`.
- Produces : `sensor.hydroqc_mfa_dernier_renouvellement` (`device_class: timestamp`, mis à jour seulement sur `success`, restauré au redémarrage via `RestoreEntity`) et `sensor.hydroqc_mfa_derniere_tentative` (`device_class: enum`, options = `"success"` + clés de `REASON_MESSAGES`, traduites en français, restauré au redémarrage). `has_entity_name = True`, `translation_key` dédiés.

- [ ] **Step 1:** Tests `test_sensors_update_on_success`, `test_failure_updates_only_result`, `test_sensors_restored` (`mock_restore_cache`).
- [ ] **Step 2:** FAIL. **Step 3:** Implémenter. README : rôle, prérequis (hydroqc-ha ≥ 0.9.0, mot de passe d'application Gmail : https://myaccount.google.com/apppasswords), ce que l'intégration lit et ne lit pas, comportement en cas d'échec (une seule tentative, notification, réarmement après réauthentification manuelle). **Step 4:** `pytest -q` → PASS.
- [ ] **Step 5:** Commit `feat: capteurs de suivi et README`.
