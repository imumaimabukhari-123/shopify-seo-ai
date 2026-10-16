// Shared by every page.
// Asks the server who is looking (owner or visitor), adds "Owner login" /
// "Log out" to the top bar, and exposes the answer as window.mePromise.
window.mePromise = fetch("/api/me")
  .then(r => r.json())
  .catch(() => ({ owner: false, can_view: false, login_enabled: false }));

window.mePromise.then(me => {
  document.body.classList.toggle("is-owner", me.owner);
  document.body.classList.toggle("is-visitor", !me.owner);

  // Online, buyers go to the Fiverr gig (HIRE_URL) instead of the email address
  if (me.hire_url) {
    document.querySelectorAll("[data-contact]").forEach(a => {
      a.href = me.hire_url;
      a.target = "_blank";
      a.rel = "noopener noreferrer";
      a.textContent = me.hire_label + " →";
    });
    document.querySelectorAll("[data-contact-hide]").forEach(a => (a.hidden = true));
  }
  const links = document.querySelector(".nav-links");
  if (!links) return;

  // Version 3: the owner gets a "Stores" link; a client store is shown in the bar
  if (me.owner) {
    const stores = document.createElement("a");
    stores.href = "/stores";
    stores.textContent = "Stores";
    if (location.pathname === "/stores") stores.className = "active";
    links.append(stores);
    if (!me.is_main_store) {
      const badge = document.createElement("a");
      badge.href = "/stores";
      badge.className = "nav-store";
      badge.title = "You're working on a client store. Click to switch.";
      badge.textContent = "● " + me.store.replace(".myshopify.com", "");
      links.prepend(badge);
    }
  }
  if (!me.login_enabled) return;   // running locally without a password

  const a = document.createElement("a");
  a.href = "#";
  if (me.owner) {
    a.textContent = "Log out";
    a.onclick = async e => {
      e.preventDefault();
      await fetch("/api/logout", { method: "POST" });
      location.reload();
    };
  } else {
    a.innerHTML = "<span class=\"hide-sm\">Owner </span>Login";
    a.href = "/login?next=" + encodeURIComponent(location.pathname + location.hash);
  }
  a.className = "nav-login";
  links.append(a);
});
