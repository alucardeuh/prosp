/* Console de prospection — logique côté navigateur.
   Tout passe par fetch : plus aucune action ne recharge la page à froid,
   et les actions longues affichent leur progression en direct. */

// ---------------------------------------------------------------- thème clair/sombre

function majBoutonTema() {
  const sombre = document.documentElement.getAttribute("data-tema") === "sombre";
  const bouton = document.querySelector(".bascule-tema");
  if (!bouton) return;
  bouton.querySelector(".icone-tema").textContent = sombre ? "☀️" : "🌙";
  bouton.querySelector(".libelle-tema").textContent = sombre ? "Clair" : "Sombre";
}

function basculerTema() {
  const sombre = document.documentElement.getAttribute("data-tema") === "sombre";
  if (sombre) {
    document.documentElement.removeAttribute("data-tema");
    localStorage.removeItem("tema");
  } else {
    document.documentElement.setAttribute("data-tema", "sombre");
    localStorage.setItem("tema", "sombre");
  }
  majBoutonTema();
}

document.addEventListener("DOMContentLoaded", majBoutonTema);

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
  const bouton = document.querySelector("#bandeau-job .annuler-job");
  if (bouton) bouton.disabled = false;
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
      let message, type;
      if (job.etat === "annule") { message = "Annulé : " + job.titre + erreurs; type = "erreur"; }
      else if (job.etat === "termine") { message = "Terminé : " + job.titre + erreurs; type = job.erreurs ? "erreur" : "succes"; }
      else { message = "Échec : " + job.titre; type = "erreur"; }
      toast(message, type);
      setTimeout(() => { masquerBandeau(); location.reload(); }, job.etat === "termine" && !job.erreurs ? 1000 : 3500);
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

async function annulerJobActif() {
  if (!jobEnCours) return;
  const bouton = document.querySelector("#bandeau-job .annuler-job");
  if (bouton) bouton.disabled = true;
  await action("/api/jobs/" + jobEnCours + "/annuler");
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
    majQuota(donnees.quota_restant);
    const carte = document.getElementById("carte-" + prospectId);
    if (carte) {
      // Avant : la carte restait affichée en entier (objet + corps toujours
      // lisibles) jusqu'à ce qu'on change de page. Elle disparaît maintenant
      // juste après confirmation d'envoi, comme "Passer".
      carte.querySelectorAll("button, input, textarea").forEach((el) => (el.disabled = true));
      carte.style.transition = "opacity 0.3s";
      carte.style.opacity = "0";
      setTimeout(() => carte.remove(), 320);
    }
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
  await action("/api/prospects/" + prospectId + "/passer", null, { recharger: true, delai: 400 });
}

async function reprendreBrouillon(prospectId, bouton) {
  bouton.disabled = true;
  const donnees = await action("/api/prospects/" + prospectId + "/reprendre", null, { recharger: true, delai: 400 });
  if (!donnees) bouton.disabled = false;
}

async function supprimerBrouillonDefinitif(prospectId, bouton) {
  if (!confirm("Supprimer ce brouillon pour de bon ? Impossible de revenir en arrière.")) return;
  bouton.disabled = true;
  const donnees = await action("/api/prospects/" + prospectId + "/supprimer-brouillon", null, { recharger: true, delai: 400 });
  if (!donnees) bouton.disabled = false;
}

function pastilleProgrammee(prospectId, dateAffichee) {
  return (
    '<span class="pastille pastille-attente">Programmé : ' + dateAffichee + '</span>' +
    '<button onclick="annulerProgrammation(' + prospectId + ', this)">Annuler</button>'
  );
}

function champProgrammation(prospectId) {
  return (
    '<input type="datetime-local" class="champ-date-programmation">' +
    '<button onclick="programmerEnvoi(' + prospectId + ', this)">Programmer</button>'
  );
}

async function programmerEnvoi(prospectId, bouton) {
  const zone = document.getElementById("programmation-" + prospectId);
  const champ = zone.querySelector(".champ-date-programmation");
  if (!champ || !champ.value) { toast("Choisis une date et une heure.", "erreur"); return; }
  bouton.disabled = true;
  const donnees = await action("/api/prospects/" + prospectId + "/programmer", { date_envoi: champ.value });
  bouton.disabled = false;
  if (donnees && donnees.ok) {
    zone.innerHTML = pastilleProgrammee(prospectId, champ.value.replace("T", " "));
  }
}

async function annulerProgrammation(prospectId, bouton) {
  bouton.disabled = true;
  const donnees = await action("/api/prospects/" + prospectId + "/programmer", { date_envoi: "" });
  bouton.disabled = false;
  if (donnees && donnees.ok) {
    const zone = document.getElementById("programmation-" + prospectId);
    zone.innerHTML = champProgrammation(prospectId);
  }
}

function majQuota(restant) {
  document.querySelectorAll(".js-quota-restant").forEach((el) => {
    el.textContent = restant;
  });
}

// ---------------------------------------------------------------- réglages de génération (page /envoi)
// Lus une seule fois au moment de lancer une génération — jamais mémorisés,
// propres au lot en cours (contexte texte libre + niveau de recherche).

function lireReglagesGeneration() {
  const champContexte = document.getElementById("contexte-batch");
  const champNiveau = document.getElementById("niveau-recherche");
  return {
    contexte_batch: champContexte ? champContexte.value : "",
    niveau_recherche: champNiveau ? champNiveau.value : undefined,
  };
}

async function lancerGenerationAuto(type, bouton) {
  const corps = Object.assign({ type: type }, lireReglagesGeneration());
  await lancerJob("/api/jobs/generer-brouillons", corps, bouton);
}

async function regenererUn(prospectId, type, bouton) {
  const corps = Object.assign({ type: type }, lireReglagesGeneration());
  await lancerJob("/api/prospects/" + prospectId + "/generer", corps, bouton);
}

// ---------------------------------------------------------------- onglets (page /envoi)

function basculerOngletEnvoi(nom) {
  const noms = ["brouillons", "generer", "cote"];
  noms.forEach((n) => {
    const zone = document.getElementById("onglet-" + n);
    const bouton = document.getElementById("onglet-" + n + "-btn");
    if (zone) zone.style.display = n === nom ? "block" : "none";
    if (bouton) bouton.classList.toggle("actif", n === nom);
  });
}

function basculerBrouillon(prospectId) {
  const detail = document.getElementById("detail-" + prospectId);
  if (!detail) return;
  const ouvert = detail.classList.toggle("ouvert");
  const ligne = detail.closest(".ligne-brouillon");
  if (ligne) ligne.classList.toggle("ouvert", ouvert);
}

// ---------------------------------------------------------------- sélection sur mesure (page /envoi)

function toutCocher(caseATout) {
  document.querySelectorAll("#table-selection .case-selection").forEach((c) => {
    if (c.closest("tr").style.display !== "none") c.checked = caseATout.checked;
  });
  majCompteurSelection();
}

function majCompteurSelection() {
  const cochees = document.querySelectorAll("#table-selection .case-selection:checked").length;
  document.getElementById("compteur-selection").textContent = cochees;
  document.getElementById("bouton-generer-selection").disabled = cochees === 0;
}

function filtrerSelection() {
  const poste = document.getElementById("filtre-poste").value.trim().toLowerCase();
  const statut = document.getElementById("filtre-statut").value;
  const envois = document.getElementById("filtre-envois").value;
  document.querySelectorAll("#table-selection tbody tr").forEach((tr) => {
    let ok = !poste || tr.dataset.poste.includes(poste);
    if (ok && statut) ok = tr.dataset.statut === statut;
    if (ok && envois !== "") ok = parseInt(tr.dataset.envois, 10) >= parseInt(envois, 10);
    tr.style.display = ok ? "" : "none";
    if (!ok) tr.querySelector(".case-selection").checked = false;
  });
  document.getElementById("tout-cocher").checked = false;
  majCompteurSelection();
}

async function genererSelection(type, bouton) {
  const ids = Array.from(document.querySelectorAll("#table-selection .case-selection:checked"))
    .map((c) => parseInt(c.value, 10));
  if (!ids.length) return;
  const corps = Object.assign({ type: type, ids: ids }, lireReglagesGeneration());
  await lancerJob("/api/jobs/generer-brouillons", corps, bouton);
}

document.addEventListener("click", (e) => {
  const ligne = e.target.closest("#table-selection tbody tr");
  if (!ligne || e.target.matches("input, a")) return;
  const case_ = ligne.querySelector(".case-selection");
  if (case_) { case_.checked = !case_.checked; majCompteurSelection(); }
});

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
