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
PUSH_OK=0
sync_push "dispatch: $(date -u +%F)" outbox/ contacts.xlsx && PUSH_OK=1

# ⚠ LE BATTEMENT DE CŒUR VIT ICI (2026-10-30). Il n'était émis que par run_agent.sh, qui n'est
#   plus planifié depuis le 2026-10-08 : même configuré, rien n'aurait pingé, et une VM arrêtée
#   n'aurait rien dit — toutes les autres alertes sont envoyées PAR la VM. Ce job tourne chaque
#   matin. Ping seulement si l'envoi a tourné ET si GitHub a reçu le résultat, pour que le
#   chien de garde aboie aussi quand la synchronisation casse. Inerte sans HEALTHCHECK_URL.
_HC="$(grep -E '^HEALTHCHECK_URL=' "$DIR/.env" | cut -d= -f2- || true)"
if [ -n "$_HC" ] && [ "$PUSH_OK" = "1" ] && [ "$rc" -eq 0 ]; then
  curl -fsS -m 10 --retry 3 "$_HC" >/dev/null 2>&1 || true
fi

# ⚠ LE CONTRÔLE DE SANTÉ QUOTIDIEN (2026-10-30), APRÈS l'envoi et sans jamais le retenir. Le seul
#   job qui lançait preflight sur la VM était le scrape — qui ne tourne plus pilote éteint. Sans
#   ceci, une clé Hunter morte ou un contrôle cassé ne lui serait plus signalé du tout. Une fois
#   par jour ; l'échec et les avertissements partent par email (les avertissements tous les 3 j).
STAMP_H="$DIR/cache/.daily_health"
if [ "$(cat "$STAMP_H" 2>/dev/null)" != "$(date -u +%F)" ]; then
  date -u +%F > "$STAMP_H"
  source "$DIR/vm/preflight_gate.sh"
  preflight_report "daily" "logs/health.log" \
    "the morning dispatch already ran (it never waits on this check), but something is broken and will cost results until fixed." \
    || true
fi
exit $rc
