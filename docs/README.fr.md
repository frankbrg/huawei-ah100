# Huawei AH100 pour Home Assistant

[English](../README.md) | [Français](README.fr.md)

![Illustration de l’intégration Huawei AH100](images/hero.svg)

Une intégration Bluetooth locale et non officielle pour lire dans Home Assistant les mesures de composition corporelle d’une balance Huawei AH100.

[![CI](https://github.com/frankbrg/huawei-ah100/actions/workflows/validate.yaml/badge.svg?branch=main)](https://github.com/frankbrg/huawei-ah100/actions/workflows/validate.yaml)
[![Dernière version](https://img.shields.io/github/v/release/frankbrg/huawei-ah100?display_name=tag&sort=semver)](https://github.com/frankbrg/huawei-ah100/releases)
[![Licence](https://img.shields.io/badge/license-GPL--3.0--or--later-blue.svg)](../LICENSE)

[![Ouvrir ce dépôt dans HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=frankbrg&repository=huawei-ah100&category=integration)
[![Ajouter l’intégration à Home Assistant](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=huawei_ah100)

> **État de validation :** le protocole BLE a été validé avec une balance Huawei AH100/CH100 équipée d’un contrôleur Chipsea CST34M97 et du firmware 1050, à l’aide d’un Raspberry Pi. La validation complète de l’intégration native Home Assistant avec le matériel n’est pas encore terminée.

## Fonctionnalités

L’intégration expose les valeurs directement rapportées par la balance :

| Entité | Unité | Signification |
| --- | --- | --- |
| Poids | kg | Poids mesuré en direct |
| Masse grasse | % | Valeur de masse grasse rapportée par la balance |
| Impédance | Ω | Impédance électrique rapportée par la balance |
| Dernière mesure | UTC | Heure de réception de la dernière mesure valide par Home Assistant |

Les attributs de mesure comprennent `user_id`, l’horodatage local de la balance (`device_measured_at`), son champ de jour de la semaine (`weekday`) et les octets de fin non interprétés (`unknown_tail_hex`). Ces octets restent explicitement inconnus : ils ne sont pas présentés comme de la graisse viscérale ou une autre mesure corporelle. L’intégration ne calcule pas l’eau, les muscles, les os, l’IMC, le métabolisme de base ni d’autres valeurs dérivées.

Le poids initial sert uniquement d’indication lors de la synchronisation du profil utilisateur de la balance. Ce n’est pas une mesure et il n’est jamais publié comme valeur d’un capteur. Après une mesure réelle, l’intégration peut réutiliser ce poids mesuré comme prochaine indication de profil.

## Prérequis et état du projet

- Home Assistant **2026.10.0 ou plus récent**.
- Une balance Huawei AH100/CH100 prise en charge et un adaptateur Bluetooth accessible par Home Assistant.
- L’application Huawei Health doit être déconnectée de la balance pendant la configuration et les mesures. La balance n’accepte qu’une connexion Bluetooth centrale active à la fois.

La configuration initiale demande au Bluetooth de Home Assistant de rechercher activement les balances CH100 connectables à proximité et affiche un choix lorsqu’il en trouve une. Après configuration, la détection Bluetooth de Home Assistant repère le réveil de la balance. Les proxies Bluetooth n’ont pas été vérifiés. L’intégration n’a besoin ni de MQTT, ni de Docker, ni d’un service compagnon, ni d’un compte cloud.

Ce projet communautaire indépendant n’est pas affilié à Huawei. La version de l’intégration est **0.1.2**. La demande d’ajout au catalogue HACS par défaut est [ouverte et en attente de l’examen des mainteneurs](https://github.com/hacs/default/pull/12666) ; en attendant son acceptation, ajoutez le dépôt comme dépôt personnalisé avec le bouton ci-dessus.

## Installation

### HACS

1. Installez [HACS](https://hacs.xyz/docs/use/) si ce n’est pas déjà fait dans Home Assistant.
2. Sélectionnez le bouton HACS ci-dessus. Si demandé, ajoutez `frankbrg/huawei-ah100` comme dépôt personnalisé de catégorie **Integration**.
3. Téléchargez l’intégration puis redémarrez Home Assistant.
4. Sélectionnez le bouton **Ajouter l’intégration** ci-dessus, ou ouvrez **Paramètres → Appareils et services → Ajouter une intégration** et recherchez **Huawei AH100**. Home Assistant lance alors une recherche à proximité.

### Installation manuelle

1. Copiez `custom_components/huawei_ah100` de ce dépôt dans `<config>/custom_components/huawei_ah100` de votre répertoire de configuration Home Assistant.
2. Redémarrez Home Assistant.
3. Ajoutez **Huawei AH100** depuis **Paramètres → Appareils et services**.

## Configurer la balance

Fermez Huawei Health sur les téléphones proches, placez Home Assistant à portée Bluetooth et montez brièvement sur la balance pour la réveiller. Dans la liste des appareils proches, sélectionnez la balance CH100 connectable. Si Home Assistant a déjà détecté la balance et affiche une carte d’intégration, sélectionnez **Configurer** ; le formulaire de profil s’ouvre sans demander d’adresse MAC.

Si la recherche ne trouve aucune balance, réveillez-la à nouveau puis sélectionnez **Relancer la recherche**, ou choisissez **Configuration manuelle** et saisissez son adresse MAC Bluetooth. Certains adaptateurs passifs reçoivent les annonces Bluetooth sans pouvoir récupérer le nom annoncé nécessaire au premier choix de l’appareil ; la configuration manuelle sert alors de solution de repli. Une fois la balance configurée, la détection passive suffit pour repérer son réveil.

Dans le formulaire, renseignez le profil de la personne qui utilisera la balance :

- **Date de naissance :** la date réelle de la personne, au format `JJ/MM/AAAA` (par exemple, date fictive `31/01/2000`).
- **Taille :** la taille de la personne en centimètres entiers.
- **Sexe :** la valeur de profil correspondant à la personne qui utilisera la balance.
- **Poids initial indicatif :** une valeur de départ pour le profil de la balance. Ce n’est pas une mesure ni une valeur de capteur.
- **Identifiant utilisateur de la balance :** `1` par défaut ; gardez cette valeur sauf si vous savez que le profil utilise un autre emplacement.
- **Autoriser l’association :** activez cette option lors de la première configuration si cet adaptateur Home Assistant n’a pas été autorisé par la balance. L’association peut modifier celle déjà enregistrée ; laissez l’option désactivée si l’adaptateur est déjà autorisé.

Validez le formulaire une seule fois. Pour la première mesure, fermez Huawei Health, montez pieds nus sur la balance et restez immobile jusqu’à la fin de la pesée. Les capteurs peuvent rester indisponibles tant que Home Assistant n’a pas reçu une première mesure réelle ; le poids indicatif ne les alimente pas. Pour les mesures suivantes, réveillez la balance et laissez Home Assistant s’y connecter en Bluetooth. Le formulaire d’options permet ensuite de modifier le profil et la préférence d’association. Aucune commande de réinitialisation d’usine ou de suppression n’est utilisée.

## Dépannage

- **La liste des appareils proches est vide ou sans nom :** fermez Huawei Health, réveillez la balance et relancez la recherche à portée Bluetooth. Vérifiez que l’adaptateur Bluetooth de Home Assistant est en mode **Automatique** ou **Actif** pour que la recherche initiale récupère le nom annoncé. Avec un adaptateur uniquement passif, utilisez **Configuration manuelle** et saisissez l’adresse MAC.
- **Les capteurs sont indisponibles :** l’intégration a besoin d’une vraie mesure avant que les capteurs de mesure aient des valeurs. Réveillez la balance, effectuez une pesée pieds nus complète puis laissez Home Assistant s’y connecter.
- **L’authentification est refusée :** si cet adaptateur n’a pas été autorisé par la balance, activez **Autoriser l’association** dans les options de l’intégration, puis réveillez à nouveau la balance. L’association peut modifier celle déjà enregistrée ; n’activez cette option qu’en cas de besoin.
- **Home Assistant ne se connecte pas :** vérifiez que Huawei Health et les autres téléphones ou clients sont déconnectés de la balance, puis vérifiez l’adresse MAC sélectionnée ou saisie.

## Confidentialité et traitement des données

La communication se fait directement entre Home Assistant et la balance en Bluetooth. L’intégration n’envoie ni les mesures ni les valeurs de profil vers un service cloud. Les champs de profil servent à configurer la balance. La dernière mesure réelle acceptée est enregistrée dans le stockage privé de l’intégration Home Assistant afin de restaurer les entités et de réutiliser le poids mesuré comme prochaine indication de profil. L’enregistrement décodé complet n’est pas publié dans les états ou attributs des capteurs ; leurs attributs exposent uniquement les métadonnées de mesure documentées.

Ne publiez pas l’adresse MAC de votre balance, votre date de naissance, vos valeurs de profil ni des captures Bluetooth non expurgées. Pour signaler un problème, indiquez le modèle et le firmware de la balance ainsi que la version de Home Assistant, sans identifiant personnel.

## Contribution

Consultez [CONTRIBUTING.md](../CONTRIBUTING.md) pour l’environnement de développement, les tests et les contrôles de style. Les notes de protocole et les limites de validation sont dans [protocol.md](protocol.md).

## Licence et attribution

Ce projet est distribué sous licence **GPL-3.0-or-later**. L’implémentation du protocole est un portage Kotlin vers Python basé sur [`HuaweiAhCh100Handler.kt`](https://github.com/oliexdev/openScale/blob/39a0c5c25a0abc26b8e24c90f36b5a2c6ff07a4a/android_app/app/src/main/java/com/health/openscale/core/bluetooth/scales/HuaweiAhCh100Handler.kt) du projet openScale, au commit [`39a0c5c25a0abc26b8e24c90f36b5a2c6ff07a4a`](https://github.com/oliexdev/openScale/commit/39a0c5c25a0abc26b8e24c90f36b5a2c6ff07a4a). Consultez [protocol.md](protocol.md) pour le périmètre du protocole et les détails d’attribution.
