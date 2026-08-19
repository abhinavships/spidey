/* Shared page shell: identity in the top bar, sign out, tiny fetch helpers. */
const SVG_LOGO = `<svg viewBox="0 0 24 24" fill="none" stroke="#38bdf8" stroke-width="1.7" stroke-linecap="round">
  <circle cx="5" cy="5" r="3"/><circle cx="19" cy="5" r="3"/><circle cx="5" cy="19" r="3"/><circle cx="19" cy="19" r="3"/>
  <path d="M7.2 7.2 10 10m4 4 2.8 2.8M16.8 7.2 14 10m-4 4-2.8 2.8"/><rect x="9.5" y="9.5" width="5" height="5" rx="1.3"/></svg>`;

async function shell() {
  const bar = document.querySelector('header.topbar');
  const me = await fetch('/drone/api/me').then(r => r.ok ? r.json() : null).catch(() => null);
  bar.innerHTML = `<span class="brand">${SVG_LOGO} SkyLoop Flight Academy</span>
    <span class="spacer"></span>
    <span class="who" data-testid="signed-in-as"><b>${me ? me.name : 'Guest'}</b>${me ? me.title : ''}</span>
    <form method="post" action="/drone/logout" style="margin:0">
      <button class="ghost" type="submit" data-testid="sign-out">Sign out</button></form>`;
  return me;
}

const getJSON = (url) => fetch(url).then(r => r.json());
const postJSON = (url, body) => fetch(url, {
  method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)
}).then(r => r.json());
