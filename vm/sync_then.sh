#!/bin/bash
# Tire la file depuis GitHub, lance la commande, repousse ce qu'elle a changé.
#
#   bash vm/sync_then.sh venv/bin/python dispatch.py --send
#
# ⚠ SANS LUI, LE RÉVEIL DE 7H NE TROUVAIT RIEN, EN SILENCE (constaté le 2026-10-15). Le bouton
#   « Envoyer » met la candidature en file sur le Mac et pousse `outbox/` sur GitHub ; mais
#   `dispatch.py` ne fait aucun `git pull`, et depuis que le crontab a perdu les jobs de nuit
#   (2026-10-08) PLUS RIEN ne tirait sur la VM. La file n'atteignait jamais la machine qui envoie.
# ⚠ PAS DE PREFLIGHT ICI, et c'est voulu : ce sont SES candidatures, relues et approuvées. Les
#   cinq refus de `smtp_send.py` jugent chaque envoi ; un contrôle de santé raté ailleurs ne doit
#   pas retenir un email qu'elle a validé.
# ⚠ Ce n'est PAS un `vm/run_*` : ceux-là lancent Claude, et preflight interdit d'y faire passer
#   un job de jour.
set -uo pipefail

DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$DIR"
source venv/bin/activate
mkdir -p logs

source "$DIR/vm/git_sync.sh"
sync_pull || true   # alerte en interne ; on envoie quand même ce qui est déjà là

"$@"
rc=$?

# Le statut de chaque élément (sent / refused) et la ligne du suivi reviennent sur le Mac.
sync_push "dispatch: $(date -u +%F)" outbox/ contacts.xlsx || true
exit $rc
