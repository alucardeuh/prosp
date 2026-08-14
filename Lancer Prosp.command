#!/bin/bash
# Lancer Prosp.command
#
# Double-clique ce fichier depuis le Finder pour tout démarrer d'un coup :
# vérifie les dépendances, lance le serveur, ouvre le navigateur — sans
# taper une seule commande.
#
# Première ouverture seulement : macOS affichera probablement un
# avertissement "développeur non identifié" (Gatekeeper, appliqué à tout
# fichier téléchargé). Clic droit sur ce fichier -> Ouvrir -> Ouvrir, une
# seule fois. Les fois suivantes, un double-clic normal suffit.

cd "$(dirname "$0")"

echo "=================================================="
echo "  Prosp — démarrage"
echo "=================================================="
echo ""

# Installe les dépendances seulement si elles manquent (ne ralentit pas les
# lancements suivants une fois que tout est déjà en place).
if ! python3 -c "import flask, anthropic, yaml, dotenv" 2>/dev/null; then
    echo "Première installation des dépendances (un peu plus long cette fois)..."
    pip3 install -r requirements.txt --break-system-packages 2>/dev/null \
        || pip3 install -r requirements.txt
    echo ""
fi

# Coupe proprement un ancien serveur resté ouvert sur le port 5001, s'il y en a un.
lsof -ti:5001 2>/dev/null | xargs kill 2>/dev/null

python3 -m dashboard.app &
SERVER_PID=$!

echo "Démarrage du serveur..."
PRET=0
for i in $(seq 1 40); do
    if curl -s -o /dev/null http://127.0.0.1:5001/ 2>/dev/null; then
        PRET=1
        break
    fi
    sleep 0.5
done

if [ "$PRET" = "1" ]; then
    open "http://127.0.0.1:5001"
    echo ""
    echo "Prosp est ouvert dans ton navigateur."
else
    echo ""
    echo "Le serveur met plus de temps que prévu à démarrer — ouvre"
    echo "manuellement http://127.0.0.1:5001 dans ton navigateur dans"
    echo "quelques secondes. S'il y a une erreur, elle s'affiche ci-dessus."
fi

echo ""
echo "Laisse cette fenêtre ouverte tant que tu utilises l'app."
echo "Pour tout arrêter : ferme cette fenêtre, ou Ctrl+C."
echo ""

wait $SERVER_PID
