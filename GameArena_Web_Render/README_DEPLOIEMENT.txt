GAMEARENA WEB BETA POUR RENDER

1. Crée un nouveau dépôt GitHub et envoie le CONTENU de ce dossier à la racine.
2. Render > New > Web Service > GitHub > sélectionne le dépôt.
3. Runtime Python, Build: pip install -r requirements.txt
4. Start: gunicorn wsgi:app --bind 0.0.0.0:$PORT --workers 1 --threads 4
5. Déploie et ouvre ton adresse .onrender.com.

ATTENTION : base SQLite sur disque éphémère. Sur Render gratuit les comptes et tournois seront PERDUS lors des redéploiements/redémarrages. Version DEMO uniquement. Pour une vraie plateforme publique durable, migrer vers PostgreSQL, ajouter limitation des requêtes, anti-spam, sécurité de session, modération et politique de confidentialité. Ne pas utiliser de vrais mots de passe personnels ni de données sensibles.

Cette version WEB est indépendante de GameArena.exe. Les données V4 Windows ne sont pas importées.
