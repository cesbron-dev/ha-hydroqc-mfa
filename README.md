# Hydro-Québec MFA

Intégration Home Assistant compagnon de [hydroqc-ha](https://github.com/hydroqc/hydroqc-ha). Depuis
le 5 novembre 2026, Hydro-Québec envoie un code de vérification par courriel à chaque connexion ;
quand la session expire, hydroqc-ha demande une réauthentification manuelle. `hydroqc_mfa` fait
cette réauthentification à votre place : elle remplit le flux officiel de réauthentification, lit
le code dans Gmail et le soumet.

Combien de fois cela arrive dépend du client de connexion choisi dans hydroqc-ha (Configurer,
Avancé, Client de connexion), disponible depuis sa version 0.9.1 :

- **Application mobile** (par défaut, recommandé) : la session dure 90 jours et se prolonge à
  chaque connexion. Une réauthentification devient rare (session expirée ou révoquée par
  Hydro-Québec) ; `hydroqc_mfa` sert alors de filet de sécurité.
- **Portail web** : la session dure 24 heures, donc une réauthentification par jour, que
  `hydroqc_mfa` fait automatiquement.

Changer de client demande aussi une réauthentification, que `hydroqc_mfa` prend en charge.

## Prérequis

- Home Assistant 2026.11 ou plus récent.
- [hydroqc-ha](https://github.com/hydroqc/hydroqc-ha) version 0.9.0 ou plus récente (0.9.1 recommandée pour le client mobile), déjà configuré.
- Un compte Gmail qui reçoit les courriels d'Hydro-Québec, avec IMAP activé.
- Un [mot de passe d'application Google](https://myaccount.google.com/apppasswords) (jamais le
  mot de passe habituel du compte).

## Installation

1. Dans le Marketplace de Home Assistant, ajoutez ce dépôt comme dépôt personnalisé :
   `https://github.com/cesbron-dev/ha-hydroqc-mfa` (catégorie Intégration).
2. Installez **Hydro-Québec MFA** et redémarrez Home Assistant.
3. Ajoutez l'intégration (Paramètres, Appareils et services) et saisissez l'adresse Gmail et le
   mot de passe d'application. Une seule instance est possible.

Options : service `notify` du téléphone pour les alertes, mise à la corbeille du courriel utilisé
(activée par défaut), interrupteur pour suspendre l'automatisme.

## Ce que l'intégration lit, et ne lit pas

- Elle se connecte à `imap.gmail.com` (TLS vérifié) et n'ouvre que la boîte `INBOX`.
- Elle ne regarde que les messages arrivés **après** sa demande de code, envoyés par
  `hydro-quebec@communications.hydro.qc.ca`, avec le sujet exact « Votre code de vérification pour
  votre identifiant Hydro-Québec », et contenant un seul code à 6 chiffres.
- Les messages sont lus sans être marqués comme lus. Aucun autre courriel n'est lu, modifié ou
  supprimé.
- Si l'option est activée, seul le courriel du code utilisé est mis à la corbeille (jamais
  supprimé définitivement).
- Aucun code, mot de passe ni contenu de courriel n'est écrit dans les journaux, les événements,
  les attributs ou les notifications.
- Le mot de passe Hydro-Québec est lu dans l'entrée hydroqc existante ; il n'est jamais redemandé.

## En cas d'échec

- **Une seule tentative** par réauthentification : aucune relance automatique, pour ne pas mettre
  le compte Hydro-Québec en danger.
- Une notification persistante et, si configuré, une notification push donnent la raison
  (code non reçu en 10 minutes, mot de passe refusé, Gmail inaccessible, etc.).
- Le flux de réauthentification reste ouvert : vous le terminez à la main.
- L'intégration se réarme seulement quand l'intégration hydroqc refonctionne, que ce soit après un
  succès automatique ou après une réauthentification manuelle.
- Un redémarrage en pleine tentative compte comme un échec (raison « interrompue »), sans relance.

## Capteurs

- `sensor.hydroqc_mfa_dernier_renouvellement` : horodatage du dernier renouvellement réussi.
- `sensor.hydroqc_mfa_derniere_tentative` : résultat de la dernière tentative (succès ou raison
  d'échec).

Les deux conservent leur valeur après un redémarrage.

## Tester la connexion Gmail

Le bouton `button.hydroqc_mfa_tester_gmail` (section « Diagnostic » de l'appareil) se connecte à
Gmail et ouvre la boîte de réception, comme à la création de l'intégration, puis y cherche les
courriels de code d'Hydro-Québec (même expéditeur et même sujet que ci-dessus, parmi les 50 plus
récents de l'expéditeur). De ces courriels, il ne lit que le sujet et la date de réception : aucun
contenu, aucun code, rien n'est marqué comme lu ni déplacé. Il ne demande rien à Hydro-Québec et
ne touche ni au verrou anti-relance ni aux deux capteurs ci-dessus.

Le résultat va dans `sensor.hydroqc_mfa_test_gmail` :

- « Connexion OK » : accès à la boîte validé et au moins un courriel de code trouvé ;
- « Connexion OK, aucun courriel Hydro-Québec » : accès validé, mais aucun courriel de code dans
  la boîte de réception. Ce n'est pas un échec : avec la mise à la corbeille activée, chaque
  courriel utilisé quitte la boîte de réception ;
- « Gmail inaccessible », « Connexion Gmail refusée » ou « Erreur inattendue » en cas d'échec.

Attributs : `hq_messages` (nombre de courriels de code trouvés), `hq_last_received` (date de
réception du plus récent), `detail` (étape et type d'erreur, par exemple
`connexion : socket.gaierror`) et `tested_at`. En cas d'échec, un message s'affiche aussi dans
l'interface et une ligne est écrite dans le journal.

Comme pour les tentatives, le journal ne contient jamais le message de l'erreur ni une réponse du
serveur : seulement l'étape et le type d'exception, de quoi distinguer un problème réseau
(`socket.gaierror`, `TimeoutError`…) d'un mot de passe d'application refusé.

## Limites

Les journaux de hydroqc-ha lui-même (notamment `hydroqc.httpclient`) échappent au contrôle de
cette intégration, qui n'en filtre que les messages émis pendant sa propre tentative.
