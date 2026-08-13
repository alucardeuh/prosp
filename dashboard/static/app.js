/* Console de prospection — logique côté navigateur.
   Tout passe par fetch : plus aucune action ne recharge la page à froid,
   et les actions longues affichent leur progression en direct. */

// ---------------------------------------------------------------- toasts

function toast(message, type) {
  const zone = document.getElementById("toasts");
  const el = document.createElement("div");
  el.className = "toast " + (type || "");
  el.textContent = message;
  zone.appendChild(el);
  setTimeout(() => el.remove(), 4200);
}

// ---------------------------------------------------------------- actions simples

async function action(url, corps, options) {
  options = options || {};
  try {
    const reponse = await fetch(url, {
      method: options.methode || "POST",
      headers: { "Content-Type": "application/json" },
      body: corps ? JSON.stringify(corps) : null,
    });
    const donnees = await reponse.json();
    if (!reponse.ok) {
      toast(donnees.erreur || "Une erreur est survenue.", "erreur");
      return null;
    }
    if (donnees.message) toast(donnees.message, "succes");
    if (options.recharger) setTimeout(() => location.reload(), options.delai || 600);
    return donnees;
  } catch (e) {
    toast("Le serveur ne répond pas — vérifie qu'il tourne toujours.", "erreur");
    return null;
  }
}

// ---------------------------------------------------------------- jobs de fond

let jobEnCours = null;

function afficherBandeau(job) {
  const bandeau = document.getElementById("bandeau-job");
  bandeau.classList.add("visible");
  bandeau.querySelector(".titre-job").textContent = job.titre;
  const pct = job.total ? Math.round((100 * job.fait) / job.total) : 0;
  bandeau.querySelector(".barre > div").style.width = pct + "%";
  bandeau.querySelector(".compte").textContent = job.fait + " / " + job.total;
  const log = job.log || [];
  bandeau.querySelector(".dernier-log").textContent = log.length ? log[log.length - 1] : "Démarrage...";
}

function masquerBandeau() {
  document.getElementById("bandeau-job").classList.remove("visible");
}

async function suivreJob(jobId) {
  jobEnCours = jobId;
  const minuterie = setInterval(async () => {
    let job;
    try {
      const reponse = await fetch("/api/jobs/" + jobId);
      job = await reponse.json();
    } catch (e) {
      return; // serveur momentanément injoignable : on réessaie au tick suivant
    }
    afficherBandeau(job);
    if (job.etat !== "en_cours") {
      clearInterval(minuterie);
      jobEnCours = null;
      const erreurs = job.erreurs ? " (" + job.erreurs + " erreur(s), détail dans le bandeau)" : "";
      toast(
        job.etat === "termine" ? "Terminé : " + job.titre + erreurs : "Échec : " + job.titre,
        job.etat === "termine" && !job.erreurs ? "succes" : "erreur"
      );
      setTimeout(() => { masquerBandeau(); location.reload(); }, job.erreurs || job.etat === "echec" ? 3500 : 1000);
    }
  }, 900);
}

async function lancerJob(url, corps, bouton) {
  if (jobEnCours) { toast("Une action est déjà en cours.", "erreur"); return; }
  if (bouton) bouton.disabled = true;
  const donnees = await action(url, corps);
  if (bouton) bouton.disabled = false;
  if (donnees && donnees.job_id) suivreJob(donnees.job_id);
}

// À l'ouverture d'une page : si un job tourne déjà (autre onglet, retour
// en arrière...), on raccroche le bandeau dessus.
document.addEventListener("DOMContentLoaded", async () => {
  try {
    const reponse = await fetch("/api/jobs/actif");
    const job = await reponse.json();
    if (job && job.id) { afficherBandeau(job); suivreJob(job.id); }
  } catch (e) { /* pas grave */ }
});

// ---------------------------------------------------------------- envoi / brouillons

function donneesBrouillon(prospectId) {
  const carte = document.getElementById("carte-" + prospectId);
  return {
    objet: carte.querySelector(".objet").value,
    corps: carte.querySelector(".corps").value,
  };
}

async function envoyerEmail(prospectId, bouton) {
  bouton.disabled = true;
  const donnees = await action("/api/prospects/" + prospectId + "/envoyer", donneesBrouillon(prospectId));
  if (donnees && donnees.ok) {
    const carte = document.getElementById("carte-" + prospectId);
    carte.style.opacity = "0.35";
    carte.querySelectorAll("button, input, textarea").forEach((el) => (el.disabled = true));
    majQuota(donnees.quota_restant);
  } else {
    bouton.disabled = false;
  }
}

async function sauverBrouillon(prospectId, bouton) {
  bouton.disabled = true;
  await action("/api/prospects/" + prospectId + "/brouillon", donneesBrouillon(prospectId));
  bouton.disabled = false;
}

async function passerBrouillon(prospectId) {
  const donnees = await action("/api/prospects/" + prospectId + "/passer");
  if (donnees && donnees.ok) {
    const carte = document.getElementById("carte-" + prospectId);
    if (carte) carte.remove();
  }
}

function majQuota(restant) {
  document.querySelectorAll(".js-quota-restant").forEach((el) => {
    el.textContent = restant;
  });
}

// ---------------------------------------------------------------- prospects

async function changerStatut(prospectId, select) {
  await action("/api/prospects/" + prospectId + "/statut", { statut: select.value });
}

async function supprimerProspect(prospectId) {
  if (!confirm("Supprimer définitivement ce prospect et tout son historique ?")) return;
  const donnees = await action("/api/prospects/" + prospectId, null, { methode: "DELETE" });
  if (donnees && donnees.ok) window.location.href = "/";
}

async function changerProfil(select) {
  const donnees = await action("/api/profil", { profil: select.value });
  if (donnees && donnees.ok) location.reload();
}

// ---------------------------------------------------------------- recherche instantanée

function filtrerTable(champ) {
  const motif = champ.value.trim().toLowerCase();
  const lignes = document.querySelectorAll("#table-prospects tbody tr");
  let visibles = 0;
  lignes.forEach((tr) => {
    const ok = !motif || tr.dataset.recherche.includes(motif);
    tr.style.display = ok ? "" : "none";
    if (ok) visibles++;
  });
  const compteur = document.getElementById("compteur-visible");
  if (compteur) compteur.textContent = visibles;
}
