# Hydro-Québec MFA automatique — conception

## Intention

Depuis le 5 novembre 2026, Hydro-Québec exige un code de vérification envoyé par courriel à chaque
connexion au portail. L'intégration officielle [hydroqc-ha](https://github.com/hydroqc/hydroqc-ha)
(domaine `hydroqc`, v0.9.0) gère ce code, mais seulement à la main : la session dure environ 24 h,
puis Home Assistant affiche « Réauthentification requise » et attend qu'Olivier saisisse le code.

`hydroqc_mfa` est une petite intégration compagnon, écrite et maintenue par Olivier, qui fait cette
réauthentification à sa place : elle remplit le flux de réauthentification officiel, va chercher
le code dans Gmail et le soumet. La raison d'écrire la sienne plutôt que d'utiliser le fork
communautaire (vsaintloup/hydroqc-ha, renouvellement par Gmail) : savoir exactement ce que fait
le code qui accède à sa boîte courriel et à son compte Hydro-Québec, et rester sur la version
officielle de hydroqc-ha.

## Objectifs

- Aucune saisie manuelle du code au quotidien.
- Ne jamais mettre le compte Hydro-Québec en danger : **une seule tentative par réauthentification,
  aucune relance automatique**.
- En cas d'échec : une notification persistante HA **et** une notification push sur le téléphone.
- Ne pas modifier hydroqc-ha ; ne dépendre que de son flux de configuration public.

## Non-objectifs

- Pas de prise en charge d'autres fournisseurs courriel que Gmail.
- Pas de gestion de plusieurs comptes Hydro-Québec (une seule entrée `hydroqc` en prod ; si
  plusieurs existent, voir « Laissé au constructeur »).
- Pas de nouvelles entités de mesure ; la compagnon n'expose rien d'autre que son état (voir plus bas).

## Anti-objectifs (échec même si « ça marche »)

- Un code, un mot de passe ou le contenu d'un courriel qui apparaît dans les journaux, les
  événements HA, les attributs d'entités ou les notifications.
- Plusieurs demandes de code envoyées pour une même expiration (boucle, redémarrage, rechargement).
- Toucher à d'autres courriels que celui du code utilisé.

## Fonctionnement

1. **Détection.** Quand la session expire, hydroqc-ha lève `ConfigEntryAuthFailed` et HA ouvre un
   flux de configuration `hydroqc` de source `reauth`. La compagnon repère ce flux en cours
   (`hass.config_entries.flow.async_progress_by_handler("hydroqc")`, contexte `source == "reauth"`).
   Mécanisme de détection (écoute d'événement ou vérification périodique, p. ex. chaque minute) :
   au choix du constructeur.
2. **Point de départ Gmail.** Connexion IMAP, `INBOX` en lecture seule, mémorisation de
   `UIDVALIDITY`/`UIDNEXT` **avant** la demande de code.
3. **Verrou anti-relance (par entrée `hydroqc`, pas par flux).** Les flux ne survivent pas à un
   redémarrage : HA en ouvre un nouveau, avec un autre `flow_id`. Le verrou porte donc sur
   l'`entry_id` et est enregistré dans un `Store` HA **avant** toute requête à Hydro-Québec
   (état « tentative en cours »). Il passe à « armé » seulement quand l'entrée `hydroqc` revient
   chargée et fonctionnelle (succès automatique **ou** réauthentification faite à la main par
   Olivier). Tant qu'il n'est pas réarmé — après un échec, ou un redémarrage/plantage en pleine
   tentative — la compagnon n'agit sur aucun nouveau flux de cette entrée. *(Mon choix ; il découle
   de « ne jamais retenter ».)*
   **Flux déjà entamé :** la compagnon n'agit que sur un flux à l'étape `reauth_confirm` sans
   erreur affichée. Un flux déjà à l'étape `mfa`, ou portant des erreurs (essai manuel en cours),
   est laissé à Olivier. *(Mon choix.)*
4. **Étape mot de passe.** `async_configure(flow_id, {"password": <mot de passe de l'entrée hydroqc>})`
   sur l'étape `reauth_confirm`. Le mot de passe est lu dans `entry.data["password"]` de l'entrée
   hydroqc concernée ; il n'est jamais demandé à Olivier.
   - Résultat attendu : formulaire `mfa` (Hydro-Québec a envoyé le code).
   - Résultat `abort` `reauth_successful` (session encore valide) : succès, rien d'autre à faire.
   - Toute autre réponse (formulaire `reauth_confirm` avec erreur = mot de passe refusé, étape
     inconnue, exception) : **échec**, on s'arrête.
   - Le flux `reauth` s'ouvre aussi quand Hydro-Québec refuse le mot de passe (pas seulement pour
     le code), et son contexte ne distingue pas les deux cas. La compagnon fait alors **une**
     tentative avec le mot de passe enregistré, constate le refus, notifie et se verrouille. Une
     connexion refusée de plus est jugée acceptable. *(Mon choix.)*
5. **Lecture du code.** Interroger Gmail toutes les ~5 s pendant **10 minutes maximum**, en ne
   regardant que les messages avec UID ≥ point de départ. Un courriel n'est retenu que si :
   un seul expéditeur, exactement `hydro-quebec@communications.hydro.qc.ca` ; sujet exactement
   « Votre code de vérification pour votre identifiant Hydro-Québec » (comparaison normalisée) ;
   un et un seul code distinct à 6 chiffres dans le texte visible. Lecture avec `BODY.PEEK`
   (le message reste non lu), taille bornée. Plusieurs codes distincts = échec (ambigu).
6. **Étape code.** `async_configure(flow_id, {"mfa_code": code})`.
   - `abort` `reauth_successful` : succès. Mettre le courriel du code à la corbeille (libellé
     Gmail `\Trash`, jamais d'EXPUNGE, jamais un autre message).
   - Autre résultat : échec, y compris le formulaire `mfa` revenu avec une erreur
     (`invalid_mfa_code`, `mfa_code_resent`, `mfa_expired`…). hydroqc-ha peut lui-même renvoyer
     un code quand le précédent a expiré ; la compagnon ne soumet **pas** de deuxième code.
7. **Fin.** Fermer la connexion IMAP. En cas d'échec, laisser le flux de réauthentification ouvert
   pour qu'Olivier le termine à la main, et notifier.

### Notifications d'échec

- Notification persistante HA (identifiant stable, remplacée à chaque échec, effacée au succès
  suivant) avec un lien vers `/config/integrations/integration/hydroqc`.
- Push via un service `notify` choisi dans les options (en prod : `notify.mobile_app_iphone_de_olivier`
  ou `…_2`).
- Le texte donne la raison sous forme courte et fixe (« code non reçu en 10 minutes », « mot de
  passe Hydro-Québec refusé », « code refusé », « Gmail inaccessible », « formulaire hydroqc
  inattendu »), jamais une réponse brute de serveur.

### Configuration (flux de configuration de `hydroqc_mfa`)

- Adresse Gmail et mot de passe d'application Gmail ; validés à la création en se connectant et en
  sélectionnant `INBOX` (sans lire de message ni demander de code).
- Options : service `notify` du téléphone ; mettre le courriel à la corbeille après succès
  (activé par défaut) ; interrupteur pour suspendre l'automatisme.

### Visibilité

Mon choix, modifiable : un capteur `sensor.hydroqc_mfa_dernier_renouvellement` (horodatage du
dernier succès) et un capteur de résultat de la dernière tentative (`succès`/raison d'échec), pour
pouvoir les afficher dans un tableau de bord.

## Contraintes

- HA ≥ 2026.11 ; distribué comme dépôt personnel via Marketplace, sur le modèle de
  `cesbron-dev/ha-adguard-clients` (`hacs.json`, `custom_components/hydroqc_mfa`, tests pytest,
  workflows `tests.yml`/`validate.yml`).
- Bibliothèque standard uniquement pour l'IMAP (`imaplib`, `email`), appels bloquants dans
  l'executor HA, délais réseau bornés (10 s).
- Dépendance déclarée `after_dependencies: ["hydroqc"]`.
- Noms d'étapes et de champs (`reauth_confirm`/`password`, `mfa`/`mfa_code`,
  `reauth_successful`) vérifiés sur la branche principale de hydroqc-ha ; à revérifier contre
  la version installée en prod (v0.9.0) au moment du plan.
- Journaux : aucun code, mot de passe, contenu de courriel ni réponse serveur. Le logger
  `hydroqc.httpclient` de hydroqc-ha est déjà verbeux (il a écrit l'URL de vérification dans le
  journal en prod) : la compagnon ne doit pas l'aggraver ; filtrer ce logger pendant la tentative
  est une option à évaluer.
- Tests : faux serveur IMAP et faux flux `hydroqc` (pas d'accès réseau), couvrant chaque branche
  d'échec et l'absence de relance après redémarrage.
- Messages de commit en français.

## Inspiration

Le fork vsaintloup (`gmail.py`, `auto_mfa.py`) sert de référence pour : filtrage strict
expéditeur/sujet/code unique, extraction du texte visible du HTML, point de départ UIDNEXT,
`BODY.PEEK`, corbeille par libellé `\Trash`, verrou enregistré avant toute requête, erreurs à
raison fixe. On n'en reprend pas l'intégration dans le coordinateur de hydroqc.

## Laissé au constructeur

- Détection par événement ou par sondage.
- Si plusieurs entrées `hydroqc` existent : traiter chacune indépendamment (un verrou par flux).
- Libellés exacts, traductions fr/en.
- Durée de conservation du verrou (il suffit qu'un même `flow_id` ne soit jamais tenté deux fois).
